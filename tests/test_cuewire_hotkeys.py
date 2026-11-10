"""No-device Hyprland Lua bind preflight tests.

The compositor is NEVER contacted by these tests; only synthetic
bind lists, pure snippet rendering and mocked subprocess failures.
"""
from __future__ import annotations

from pathlib import Path
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout,redirect_stderr
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import cuewire_hotkeys as h


class CuewireHotkeySafetyTests(unittest.TestCase):
    def test_empty_bind_set_has_no_conflicts(self):
        self.assertEqual(h.normalize_bindings([]),[])

    def test_numlock_and_other_modifiers_are_conservative_conflicts(self):
        result=h.normalize_bindings([
            {"key":"KP_4","modmask":0,"dispatcher":"exec"},
            {"key":"KP_Decimal","modmask":4,"dispatcher":"exec"},
            {"key":"T","modmask":64},
        ])
        self.assertEqual({c["key"] for c in result},{"KP_4","KP_Decimal"})

    def test_existing_super_c_is_detected_before_install(self):
        result=h.normalize_bindings([
            {"key":"C","modmask":64,"dispatcher":"exec"},
        ])
        self.assertEqual(len(result),1)

    def test_rendered_lua_does_not_block_config_or_log_input(self):
        items=h.commands(Path("/data/cue-wire/target/release/cuewire"),
                         Path("/data/cue-wire/tools/cuewire_scope.sh"))
        out=h.render_lua(items)
        self.assertEqual(out.count("hl.bind("),12)
        self.assertIn('hl.bind("KP_4"',out)
        self.assertIn('hl.bind("KP_Decimal"',out)
        self.assertIn('hl.bind("SUPER + C"',out)
        self.assertIn("dont_inhibit = true",out)
        self.assertNotIn("hyprctl eval",out)
        self.assertNotIn("os.execute(",out)
        self.assertNotIn("keycode",out)

    def test_live_bind_inspection_handles_timeout_fail_closed(self):
        with patch.object(h.subprocess,"run",
                          side_effect=subprocess.TimeoutExpired(
                              ["hyprctl","-j","binds"],4)):
            with self.assertRaisesRegex(RuntimeError,"Cannot inspect"):
                h.load_hyprctl()

    def test_live_bind_inspection_parses_safe_json(self):
        result=subprocess.CompletedProcess(
            ["hyprctl","-j","binds"],0,
            stdout=json.dumps([{"key":"KP_1","modmask":0}]),stderr="")
        with patch.object(h.subprocess,"run",return_value=result):
            self.assertEqual(h.load_hyprctl()[0]["key"],"KP_1")

    def test_readonly_checker_refuses_when_live_hyprland_unavailable(self):
        with patch.object(h,"load_hyprctl",
                          side_effect=RuntimeError("Hyprland unavailable")), \
             patch.object(sys,"argv",["hotkeys","check"]), \
             redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as exit_:
                h.main()
        self.assertEqual(exit_.exception.code,2)
        self.assertIn("REFUSED",err.getvalue())

    def test_check_with_fixture_detects_conflict_and_does_not_modify_config(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"binds.json"
            p.write_text(json.dumps([{"key":"KP_0","modmask":0}]))
            with patch.object(sys,"argv",["hotkeys","check",
                                         "--binds-json",str(p),
                                         "--repo","/data/cue-wire"]), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    h.main()
            self.assertEqual(result.exception.code,3)
            self.assertTrue(p.is_file())


if __name__=="__main__":
    unittest.main()
