"""M7.4 legacy Hyprland .conf tests; NEVER mutate a real user config.

All "hyprctl version"/"hyprctl -j binds" are synthetic mocks.
The only config writes happen in TemporaryDirectory created by tests.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import cuewire_hypr_conf as h
import cuewire_keymap as k


def fake_legacy_version():
    return subprocess.CompletedProcess(
        ["hyprctl","version"],0,
        "Hyprland 0.54.0 built from branch main\n","",
    )


class LegacyConfTests(unittest.TestCase):
    def test_version_gate_accepts_legacy_and_blocks_lua_versions(self):
        self.assertEqual(h.version_from_text("Hyprland 0.54.0"),(0,54,0))
        self.assertEqual(h.version_from_text("Tag: v0.53.1"),(0,53,1))
        with patch.object(h.subprocess,"run",return_value=fake_legacy_version()):
            self.assertEqual(h.require_legacy_hyprland(),(0,54,0))
        with patch.object(h.subprocess,"run",return_value=
                          subprocess.CompletedProcess(
                              ["hyprctl","version"],0,"Hyprland 0.55.1","")):
            with self.assertRaisesRegex(h.ConfError,"requires Lua"):
                h.require_legacy_hyprland()
        with self.assertRaisesRegex(h.ConfError,"Cannot determine"):
            h.version_from_text("some unknown compositor")

    def test_conf_uses_legacy_supported_bind_flags_and_toggle(self):
        output=h.render_conf(k.defaults())
        self.assertIn("bindipd = , KP_4, CueWire M7.4 cue:4, exec, ",output)
        self.assertIn(" ctl toggle 4",output)
        self.assertIn("bindipd = , KP_Decimal, CueWire M7.4 stop",output)
        self.assertIn("bindpd = SUPER, C, CueWire M7.4 emergency",output)
        self.assertNotIn("dofile(",output)
        self.assertNotIn("hl.bind(",output)
        self.assertEqual(output.count("\n"),5+12)

    def test_custom_chord_uses_exact_modifiers_and_not_ignore_mods(self):
        mapping=k.defaults()
        mapping["cue:4"]="CTRL+ALT+F8"
        output=h.render_conf(mapping)
        self.assertIn("bindpd = CTRL ALT, F8, CueWire M7.4 cue:4",output)
        self.assertNotIn("bindipd = CTRL ALT, F8",output)

    def test_insert_source_once_and_preserve_all_original_user_lines(self):
        original=(
            "monitor = , preferred, auto, 1\n"
            "input {\n  kb_layout = us,ru\n}\n"
            "bind = SUPER, Q, exec, kitty\n"
        )
        with patch.object(h,"INCLUDED",Path("/data/cue-wire/.audit/keys.conf")):
            edited=h.merged_config(original)
            self.assertTrue(edited.startswith(original))
            self.assertEqual(edited.count("source = /data/cue-wire/.audit/keys.conf"),1)
            self.assertEqual(edited.count(h.START),1)
            self.assertEqual(edited.count(h.END),1)
            self.assertEqual(h.merged_config(edited),edited)

    def test_refuse_ambiguous_user_edits_and_double_includes(self):
        with patch.object(h,"INCLUDED",Path("/data/cue-wire/.audit/keys.conf")):
            original="bind = SUPER, Q, exec, kitty\n"
            source=h.merged_config(original)
            with self.assertRaisesRegex(h.ConfError,"edited"):
                h.merged_config(source.replace(
                    "source = /data/cue-wire/.audit/keys.conf",
                    "source = /random/hijack.conf"))
            with self.assertRaisesRegex(h.ConfError,"Ambiguous"):
                h.merged_config(source+source)
            with self.assertRaisesRegex(h.ConfError,"already included"):
                h.merged_config(
                    original+"source = /data/cue-wire/.audit/keys.conf\n")

    def test_refuse_symlinked_main_config_and_unowned_file(self):
        with tempfile.TemporaryDirectory() as root:
            plain=Path(root)/"hyprland.conf"
            plain.write_text("bind = SUPER, Q, exec, kitty\n")
            link=Path(root)/"alias.conf"
            link.symlink_to(plain)
            with self.assertRaisesRegex(h.ConfError,"symlinked"):
                h.current_config_path(str(link))
            self.assertEqual(h.current_config_path(str(plain)),plain)

    def test_backup_unique_private_and_exact_original_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            plain=Path(root)/"hyprland.conf"
            data=b"monitor = , preferred, auto, 1\nbind = SUPER, Q, exec, kitty\n"
            plain.write_bytes(data)
            first=h.backup_original(plain,data)
            second=h.backup_original(plain,data)
            self.assertNotEqual(first,second)
            self.assertEqual(first.read_bytes(),data)
            self.assertEqual(first.stat().st_mode&0o777,0o600)
            self.assertTrue(second.is_file())

    def test_preview_produces_diff_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            plain=Path(root)/"hyprland.conf"
            plain.write_text("bind = SUPER, Q, exec, kitty\n")
            folder=Path(root)/".audit"
            generated=folder/"cuewire-keymap-v1.conf"
            with patch.object(h,"require_legacy_hyprland",
                              return_value=(0,54,0)), \
                 patch.object(h,"current_config_path",return_value=plain), \
                 patch.object(h.keymap,"DIRECTORY",folder), \
                 patch.object(h.keymap,"read_profile",return_value=k.defaults()), \
                 patch.object(h,"INCLUDED",generated), \
                 redirect_stdout(io.StringIO()) as shown:
                self.assertEqual(h.run(["preview"]),0)
            self.assertIn("PREVIEW ONLY",shown.getvalue())
            self.assertIn("source = ",shown.getvalue())
            self.assertEqual(plain.read_text(),"bind = SUPER, Q, exec, kitty\n")
            self.assertFalse(folder.exists())

    def test_apply_writes_source_and_generated_file_without_reload(self):
        with tempfile.TemporaryDirectory() as root:
            plain=Path(root)/"hyprland.conf"
            original="bind = SUPER, Q, exec, kitty\n"
            plain.write_text(original)
            folder=Path(root)/".audit"
            generated=folder/"cuewire-keymap-v1.conf"
            with patch.object(h,"require_legacy_hyprland",
                              return_value=(0,54,0)), \
                 patch.object(h,"current_config_path",return_value=plain), \
                 patch.object(h.keymap,"DIRECTORY",folder), \
                 patch.object(h.keymap,"read_profile",return_value=k.defaults()), \
                 patch.object(h,"INCLUDED",generated), \
                 patch.object(h,"foreign_conflicts",return_value=[]), \
                 redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(h.run(["apply"]),0)
                self.assertEqual(h.run(["apply"]),0)
            self.assertEqual(plain.read_text().count(h.START),1)
            self.assertIn("bindipd",generated.read_text())
            self.assertIn("Backup:",printed.getvalue())
            self.assertEqual(generated.stat().st_mode&0o777,0o600)
            backups=list(Path(root).glob("*.cuewire-*.bak"))
            self.assertEqual(len(backups),2)
            self.assertTrue(all(p.read_text()==original for p in backups))

    def test_apply_refuses_foreign_hotkeys_without_writing_anything(self):
        with tempfile.TemporaryDirectory() as root:
            plain=Path(root)/"hyprland.conf"
            original="bind = SUPER, Q, exec, kitty\n"
            plain.write_text(original)
            folder=Path(root)/".audit"
            generated=folder/"cuewire-keymap-v1.conf"
            with patch.object(h,"require_legacy_hyprland",
                              return_value=(0,54,0)), \
                 patch.object(h,"current_config_path",return_value=plain), \
                 patch.object(h.keymap,"DIRECTORY",folder), \
                 patch.object(h,"INCLUDED",generated), \
                 patch.object(h.keymap,"read_profile",return_value=k.defaults()), \
                 patch.object(h,"foreign_conflicts",return_value=["KP_4 busy"]), \
                 redirect_stderr(io.StringIO()):
                self.assertEqual(h.run(["apply"]),2)
            self.assertEqual(plain.read_text(),original)
            self.assertFalse(folder.exists())
            self.assertEqual(list(Path(root).glob("*.cuewire-*.bak")),[])

    def test_actual_version_gate_prevents_writes_on_lua_hyprland(self):
        with tempfile.TemporaryDirectory() as root:
            plain=Path(root)/"hyprland.conf"
            original="bind = SUPER, Q, exec, kitty\n"
            plain.write_text(original)
            with patch.object(h.subprocess,"run",return_value=
                              subprocess.CompletedProcess(
                                  ["hyprctl","version"],0,"Hyprland 0.55.0","")), \
                 patch.object(h,"current_config_path",return_value=plain), \
                 redirect_stderr(io.StringIO()) as err:
                self.assertEqual(h.run(["apply"]),2)
            self.assertEqual(plain.read_text(),original)
            self.assertIn("requires Lua",err.getvalue())

    def test_render_conf_is_independent_of_real_hyprctl(self):
        with patch.object(h.subprocess,"run",
                          side_effect=AssertionError("must not use compositor")):
            config=h.render_conf({"cue:4":"F8"})
        self.assertIn("bindpd = , F8, CueWire M7.4 cue:4",config)


if __name__=="__main__":
    unittest.main()
