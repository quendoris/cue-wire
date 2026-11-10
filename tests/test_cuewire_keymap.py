"""M7.4 key assignments: pure logic, isolated files, no real input/audio.

Tests MUST NOT open evdev, capture keystrokes, contact compositor,
change global binds, execute Rust or modify user PipeWire configuration.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import cuewire_keymap as k


class KeymapTests(unittest.TestCase):
    def test_default_actions_preserve_11_original_cue_and_stop_slots(self):
        m=k.defaults()
        self.assertEqual(len(m),11)
        self.assertEqual(m["cue:4"],"KP_4")
        self.assertEqual(m["stop"],"KP_Decimal")
        self.assertEqual(k.validate(m),m)

    def test_normalizes_safe_alternative_keys(self):
        self.assertEqual(k.parse_key("f8"),"F8")
        self.assertEqual(k.parse_key("  ctrl + alt + 4 "),"CTRL + ALT + 4")
        self.assertEqual(k.parse_key("shift+super+f12"),"SHIFT + SUPER + F12")
        self.assertEqual(k.parse_key("KP_Decimal"),"KP_Decimal")

    def test_protects_super_c_and_disallows_unmodified_typing(self):
        for key in ("SUPER + C","SUPER+SHIFT+C","c","4","space",
                    "CTRL+CTRL+F8","F25","KP_31","MOUSE_B1",
                    'F8"); os.execute("rm -rf ~")'):
            with self.subTest(key=key),self.assertRaises(k.KeymapError):
                k.parse_key(key)

    def test_same_key_cannot_be_bound_to_two_actions(self):
        m=k.defaults()
        m["cue:4"]="KP_5"
        with self.assertRaisesRegex(k.KeymapError,"Conflict"):
            k.validate(m)
        m["cue:4"]="F8"
        self.assertEqual(k.validate(m)["cue:4"],"F8")

    def test_unknown_actions_and_types_are_rejected(self):
        m=k.defaults()
        m["panic"]="F1"
        with self.assertRaises(k.KeymapError):
            k.validate(m)
        with self.assertRaises(k.KeymapError):
            k.validate({"cue:4": ["KP_4"]})

    def test_generated_lua_uses_toggle_and_never_collects_keycodes(self):
        m=k.defaults()
        m["cue:4"]="F8"
        program=k.render_lua(m)
        self.assertEqual(program.count("hl.bind("),12)
        self.assertIn('hl.bind("F8"',program)
        self.assertIn(" ctl toggle 4",program)
        self.assertIn(" ctl stop",program)
        self.assertIn('hl.bind("SUPER + C"',program)
        self.assertNotIn(" ctl play 4",program)
        self.assertNotIn("io.popen",program)
        self.assertNotIn("os.execute",program)
        self.assertNotIn("hyprctl eval",program)
        self.assertIn("dont_inhibit = true",program)
        self.assertIn("ignore_mods = true",program)
        self.assertIn("Profile SHA256",program.replace("profile SHA256","Profile SHA256"))

    def test_keymap_compilation_deterministic(self):
        m=k.defaults()
        one=k.render_lua(m)
        two=k.render_lua(dict(reversed(list(m.items()))))
        self.assertEqual(one,two)
        self.assertEqual(k.digest(m),k.digest(dict(reversed(list(m.items())))))

    def test_conflict_detects_installed_key_and_reserved_emergency(self):
        current=[
            {"key":"F8","modmask":0,"dispatcher":"exec"},
            {"key":"C","modmask":64,"dispatcher":"exec"},
            {"key":"F7","modmask":0,"dispatcher":"exec"},
        ]
        m=k.defaults()
        m["cue:4"]="F8"
        found=k.conflicts(m,current)
        self.assertEqual(len(found),2)
        self.assertTrue(any("F8" in x for x in found))
        self.assertTrue(any("emergency" in x for x in found))

    def test_previous_authenticated_cuewire_bind_is_safe_but_foreign_is_not(self):
        own={
            "key":"F8",
            "dispatcher":"exec",
            "description":"CueWire M7.4 cue:4",
            "arg":str(k.ROOT/"target/release/cuewire")+" ctl toggle 4",
        }
        m=k.defaults()
        m["cue:4"]="F8"
        self.assertEqual(k.conflicts(m,[own]),[])
        fake=dict(own,description="Other application")
        self.assertEqual(len(k.conflicts(m,[fake])),1)
        fake=dict(own,arg="other-cmd; injected")
        self.assertEqual(len(k.conflicts(m,[fake])),1)

    def test_preview_and_dry_run_do_not_write_any_local_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/".audit"
            with patch.object(k,"DIRECTORY",directory), \
                 patch.object(k,"PROFILE",directory/"cuewire-keymap-v1.json"), \
                 patch.object(k,"GENERATED",directory/"cuewire-keymap-v1.lua"), \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(k.main(["set","cue:4","F8","--dry-run"]),0)
                self.assertEqual(k.main(["preview"]),0)
            self.assertFalse(directory.exists())
            self.assertIn("DRY RUN",output.getvalue())

    def test_explicit_profile_write_is_private_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/".audit"
            with patch.object(k,"DIRECTORY",directory), \
                 patch.object(k,"PROFILE",directory/"cuewire-keymap-v1.json"), \
                 patch.object(k,"GENERATED",directory/"cuewire-keymap-v1.lua"):
                output=io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(k.main(["set","cue:4","F8"]),0)
                self.assertIn("NOT reloaded",output.getvalue())
                self.assertTrue(k.PROFILE.is_file())
                self.assertEqual(k.PROFILE.stat().st_mode&0o777,0o600)
                self.assertEqual(k.read_profile()["cue:4"],"F8")
                self.assertFalse(k.GENERATED.exists())
                with patch.object(k.previous,"load_hyprctl",return_value=[]), \
                     redirect_stdout(io.StringIO()):
                    self.assertEqual(k.main(["compile"]),0)
                self.assertTrue(k.GENERATED.exists())
                self.assertEqual(k.GENERATED.stat().st_mode&0o777,0o600)
                self.assertIn('hl.bind("F8"',k.GENERATED.read_text())

    def test_symlink_and_unsafe_perms_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/".audit"
            directory.mkdir()
            real=directory/"orig"
            real.write_text("hello")
            profile=directory/"cuewire-keymap-v1.json"
            profile.symlink_to(real)
            with patch.object(k,"DIRECTORY",directory), \
                 patch.object(k,"PROFILE",profile):
                with self.assertRaises(k.KeymapError):
                    k.read_profile()
                with self.assertRaises(k.KeymapError):
                    k.save_profile(k.defaults())
            profile.unlink()
            directory.chmod(0o777)
            with patch.object(k,"DIRECTORY",directory):
                with self.assertRaises(k.KeymapError):
                    k.private_directory()

    def test_compile_refuses_existing_conflicts_without_touching_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/".audit"
            with patch.object(k,"DIRECTORY",directory), \
                 patch.object(k,"PROFILE",directory/"cuewire-keymap-v1.json"), \
                 patch.object(k,"GENERATED",directory/"cuewire-keymap-v1.lua"), \
                 patch.object(k.previous,"load_hyprctl",
                              return_value=[{"key":"KP_0","modmask":0}]), \
                 redirect_stderr(io.StringIO()) as err:
                result=k.main(["compile"])
            self.assertEqual(result,2)
            self.assertIn("REFUSED",err.getvalue())
            self.assertFalse(directory.exists())

    def test_compile_refuses_when_hyprland_cannot_be_checked(self):
        with patch.object(k.previous,"load_hyprctl",
                          side_effect=RuntimeError("hyprctl unavailable")), \
             redirect_stderr(io.StringIO()):
            self.assertEqual(k.main(["check"]),2)

    def test_unset_clears_assignment_without_disabling_emergency(self):
        m=k.defaults()
        m.pop("cue:4")
        t=k.render_lua(m)
        self.assertEqual(t.count("hl.bind("),11)
        self.assertNotIn(" ctl toggle 4",t)
        self.assertIn('hl.bind("SUPER + C"',t)


if __name__=="__main__":
    unittest.main()
