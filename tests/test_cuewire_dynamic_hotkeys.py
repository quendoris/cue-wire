"""M7.6 synthetic regressions; never contact the real compositor/audio stack."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import cuewire_dynamic_hotkeys as h
import cuewire_keymap as k
import cuewire_console as protocol
import cuewire_session as session

TOKEN="a"*32
INVOCATION="b"*32


def rows(mapping=None):
    mapping=k.defaults() if mapping is None else mapping
    return [
        {"key":h.split(chord)[0],"modmask":h.split(chord)[1],
         "dispatcher":"__lua","arg":str(i+1),
         "description":h.description(TOKEN,suffix)}
        for i,(action,chord,suffix) in enumerate(h.registrations(mapping))
    ]


class DynamicHotkeysTests(unittest.TestCase):
    def test_default_map_contains_ten_toggles_stop_and_panic(self):
        mapping=h.desired(k.defaults())
        self.assertEqual(len(mapping),12)
        self.assertEqual(mapping["cue:4"],"KP_4")
        self.assertEqual(mapping["stop"],"KP_Decimal")
        self.assertEqual(mapping["panic"],"SUPER + C")

    def test_migrated_super_shift_c_is_not_super_c(self):
        shifted={"key":"C","modmask":65,"dispatcher":"__lua"}
        self.assertEqual(h.conflicts(k.defaults(),[shifted]),[])
        self.assertTrue(h.conflicts(k.defaults(),[dict(shifted,modmask=64)]))
        self.assertTrue(h.conflicts(k.defaults(),[dict(shifted,ignore_mods=True)]))

    def test_foreign_numpad_and_alias_conflict_even_with_modifiers(self):
        for name in ("KP_4","KP_Left","KP_Decimal","KP_Delete"):
            with self.subTest(name=name):
                self.assertTrue(h.conflicts(k.defaults(),[
                    {"key":name,"modmask":4}]))
        self.assertFalse(h.conflicts(k.defaults(),[
            {"key":"4","modmask":64},{"key":"left","modmask":64}]))
        self.assertTrue(h.conflicts(k.defaults(),[
            {"key":"code:83","keycode":0,"modmask":0}]))

    def test_static_and_stale_own_binds_are_never_exempted(self):
        self.assertTrue(h.conflicts(k.defaults(),rows()))
        self.assertTrue(h.conflicts(k.defaults(),[
            {"key":"KP_4","modmask":0,"description":"CueWire M7.4 cue:4"}]))

    def test_lua_codegen_is_session_scoped_and_no_input_reader(self):
        program=h.render_install(k.defaults(),TOKEN,INVOCATION)
        self.assertEqual(program.count("hl.bind("),23)
        self.assertEqual(program.count("ignore_mods = true"),22)
        self.assertIn("repeating = false",program)
        self.assertIn("dont_inhibit = true",program)
        self.assertIn(INVOCATION,program)
        self.assertLess(program.index('hl.bind("SUPER + C"'),
                        program.index('hl.bind("KP_0"'))
        self.assertNotIn("os.execute",program)
        self.assertNotIn("io.popen",program)
        self.assertNotIn("hl.is_key_down",program)
        with self.assertRaises(h.HotkeyError):
            h.render_install(k.defaults(),"unsafe';quit",INVOCATION)

    def test_keypad_aliases_are_same_action_and_exactly_verified(self):
        mapping=k.defaults()
        registrations=h.registrations(mapping)
        self.assertEqual(len(registrations),23)
        self.assertIn(("cue:4","KP_Left","cue:4 alias"),registrations)
        self.assertIn(("stop","KP_Delete","stop alias"),registrations)
        self.assertEqual(len({chord for _,chord,_ in registrations}),23)
        self.assertEqual(len({suffix for _,_,suffix in registrations}),23)
        program=h.render_install(mapping,TOKEN,INVOCATION)
        self.assertIn('hl.bind("KP_Left"',program)
        self.assertIn('hl.bind("KP_Delete"',program)
        self.assertEqual(program.count("dispatch "+INVOCATION+" cue:4"),2)
        self.assertEqual(h.verification(mapping,TOKEN,rows()),[])
        self.assertTrue(h.verification(mapping,TOKEN,[
            row for row in rows() if row["key"]!="KP_LEFT"]))
        custom={"cue:4":"F8","stop":"CTRL + KP_Decimal"}
        self.assertEqual(len(h.registrations(custom)),4)
        self.assertIn(("stop","CTRL + KP_Delete","stop alias"),h.registrations(custom))

    def test_v056_lua_registry_ids_and_exact_masks_are_verified(self):
        self.assertEqual(h.verification(k.defaults(),TOKEN,rows()),[])
        self.assertTrue(h.verification(k.defaults(),TOKEN,rows()[:-1]))
        changed=rows()
        changed[0]=dict(changed[0],dispatcher="exec")
        self.assertTrue(h.verification(k.defaults(),TOKEN,changed))
        changed=rows()
        changed[0]=dict(changed[0],modmask=0)
        self.assertTrue(h.verification(k.defaults(),TOKEN,changed))

    def test_install_requires_scope_and_conflict_free_lua(self):
        with patch.object(h,"require_inside",side_effect=h.HotkeyError("outside")), \
             patch.object(h,"lua_eval") as install:
            with self.assertRaises(h.HotkeyError):
                h.install(k.defaults(),TOKEN)
            install.assert_not_called()
        with patch.object(h,"require_inside"),patch.object(h,"require_lua"), \
             patch.object(h,"invocation",return_value=INVOCATION), \
             patch.object(h,"bindings",return_value=[{"key":"KP_4","modmask":0}]), \
             patch.object(h,"lua_eval") as install:
            with self.assertRaisesRegex(h.HotkeyError,"Conflicting"):
                h.install(k.defaults(),TOKEN)
            install.assert_not_called()

    def test_install_checks_real_registration_after_eval(self):
        with patch.object(h,"require_inside"),patch.object(h,"require_lua"), \
             patch.object(h,"invocation",return_value=INVOCATION), \
             patch.object(h,"bindings",side_effect=[[],rows()]), \
             patch.object(h,"lua_eval") as install:
            h.install(k.defaults(),TOKEN)
            install.assert_called_once()
        with patch.object(h,"require_inside"),patch.object(h,"require_lua"), \
             patch.object(h,"invocation",return_value=INVOCATION), \
             patch.object(h,"bindings",side_effect=[[],[]]), \
             patch.object(h,"lua_eval"):
            with self.assertRaisesRegex(h.HotkeyError,"not verified"):
                h.install(k.defaults(),TOKEN)

    def test_release_only_uses_our_handles_and_checks_absence(self):
        foreign={"key":"C","modmask":65}
        with patch.object(h,"bindings",side_effect=[rows()+[foreign],[foreign]]), \
             patch.object(h,"lua_eval") as release:
            h.release(TOKEN)
            code=release.call_args.args[0]
            self.assertIn(TOKEN,code)
            self.assertIn("h:unbind()",code)
            self.assertNotIn("hl.unbind",code)
        with patch.object(h,"bindings",return_value=rows()), \
             patch.object(h,"lua_eval"):
            with self.assertRaisesRegex(h.HotkeyError,"still registered"):
                h.release(TOKEN)

    def test_foreign_duplicate_mid_session_disables_not_removes(self):
        duplicate={"key":"KP_4","modmask":0,"description":"other"}
        with patch.object(h,"bindings",return_value=rows()+[duplicate]), \
             patch.object(h,"lua_eval") as release:
            with self.assertRaisesRegex(h.HotkeyError,"disabled"):
                h.release(TOKEN)
            self.assertIn("h:set_enabled(false)",release.call_args.args[0])
            self.assertNotIn("h:unbind()",release.call_args.args[0])

    def test_dispatch_uses_guarded_atomic_toggle_and_stale_invocation_refused(self):
        with patch.object(h,"invocation",return_value=INVOCATION), \
             patch.object(protocol,"request",return_value="OK playing cue 4") as request:
            self.assertEqual(h.dispatch(INVOCATION,"cue:4"),0)
            request.assert_called_once_with("toggle 4",timeout_seconds=.5)
        with patch.object(h,"invocation",return_value=TOKEN), \
             patch.object(protocol,"request") as request:
            with self.assertRaisesRegex(h.HotkeyError,"Stale"):
                h.dispatch(INVOCATION,"cue:4")
            request.assert_not_called()

    def test_panic_targets_existing_marked_scope_script(self):
        with patch.object(h,"invocation",return_value=INVOCATION), \
             patch.object(h,"call",return_value="ok") as call:
            self.assertEqual(h.dispatch(INVOCATION,"panic"),0)
            call.assert_called_once_with(
                ["bash",str(h.ROOT/"tools/cuewire_scope.sh"),"emergency"],timeout=3)

    def test_stale_socket_cleanup_refuses_listener_and_regular_file(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ,{"XDG_RUNTIME_DIR":temp}), \
             patch.object(h,"require_inactive"):
            p=Path(temp)/"cuewire-v0.1.sock"
            p.write_text("not a socket")
            with self.assertRaises(h.HotkeyError):h.clean_stale_socket()
            self.assertTrue(p.is_file())
            p.unlink()
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                sock.bind(str(p))
                os.chmod(p,0o600)
                sock.listen(1)
                with self.assertRaisesRegex(h.HotkeyError,"listening"):
                    h.clean_stale_socket()
            h.clean_stale_socket()
            self.assertFalse(p.exists())

    def test_outer_guardian_releases_after_killed_inner_scope(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(session,"socket_path",return_value=Path(temp)/"cuewire-v0.1.sock"), \
             patch.object(session,"refuse_existing"), \
             patch.object(h,"require_lua"), \
             patch.object(k,"read_profile",return_value=k.defaults()), \
             patch.object(h,"bindings",return_value=[]), \
             patch.object(session,"TTYGuardian"), \
             patch.object(session.subprocess,"run",
                          return_value=subprocess.CompletedProcess([],137)) as run, \
             patch.object(h,"release") as release, \
             patch.object(h,"clean_stale_socket") as cleanup:
            self.assertEqual(session.run_with_hotkeys(["systemd-run"]),137)
        args=run.call_args.args[0]
        self.assertEqual(args[-2],"--hotkeys-token")
        self.assertRegex(args[-1],r"^[0-9a-f]{32}$")
        release.assert_called_once_with(args[-1])
        cleanup.assert_called_once()

    def test_outer_guardian_cleanup_failure_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(session,"socket_path",return_value=Path(temp)/"cuewire-v0.1.sock"), \
             patch.object(session,"refuse_existing"),patch.object(h,"require_lua"), \
             patch.object(k,"read_profile",return_value=k.defaults()), \
             patch.object(h,"bindings",return_value=[]), \
             patch.object(session,"TTYGuardian"), \
             patch.object(session.subprocess,"run",
                          return_value=subprocess.CompletedProcess([],0)), \
             patch.object(h,"release",side_effect=h.HotkeyError("compositor unavailable")), \
             patch.object(h,"clean_stale_socket"):
            self.assertEqual(session.run_with_hotkeys(["systemd-run"]),2)


if __name__=="__main__":
    unittest.main()
