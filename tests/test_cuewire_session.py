"""No-hardware synthetic tests for the M7.3 single-scope CueWire launcher.

All systemd, wpctl, pw-dump, Rust/audio and TTY effects are mocked.
No real PipeWire, input device, daemon, keybind registration, WAV
recording or system-default modifications.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import cuewire_session as s


def cp(argv,content:bytes=b"",code:int=0):
    return subprocess.CompletedProcess(argv,code,content,b"")


def nodes():
    return json.dumps([
        {"info":{"props":{"node.name":"alsa_input.real_mic",
                          "media.class":"Audio/Source"}}},
        {"info":{"props":{"node.name":"alsa_output.real_speakers",
                          "media.class":"Audio/Sink"}}},
        {"info":{"props":{"node.name":"cuewire.microphone.44",
                          "media.class":"Audio/Source"}}},
    ]).encode()


class SessionDefaultsAndScope(unittest.TestCase):
    def test_default_node_only_reads_wireplumber_and_validates_name(self):
        with patch.object(s,"command",return_value=cp(
                [],b'id 112\n  * node.name = "alsa_input.real_mic"\n')) as cmd:
            self.assertEqual(s.default_node(
                "@DEFAULT_AUDIO_SOURCE@","Audio/Source"),
                "alsa_input.real_mic")
        cmd.assert_called_once_with(
            ["wpctl","inspect","@DEFAULT_AUDIO_SOURCE@"],timeout=2.0)

    def test_reject_untrusted_virtual_default_and_ambiguous_node(self):
        with patch.object(s,"command",return_value=cp(
                [],b' * node.name = "cuewire.microphone.123"\n')):
            with self.assertRaisesRegex(s.SessionError,"virtual default"):
                s.default_node("@DEFAULT_AUDIO_SOURCE@","Audio/Source")
        with patch.object(s,"command",return_value=cp(
                [],b' * node.name = "a"\n * node.name = "b"\n')):
            with self.assertRaisesRegex(s.SessionError,"ambiguous"):
                s.default_node("@DEFAULT_AUDIO_SOURCE@","Audio/Source")

    def test_graph_exact_matching_and_default_class(self):
        def fake(argv,timeout=3):
            if argv[0]=="wpctl":
                name=("alsa_input.real_mic" if "SOURCE" in argv[-1]
                      else "alsa_output.real_speakers")
                return cp(argv,('  * node.name = "'+name+'"\n').encode())
            self.assertEqual(argv,["pw-dump"])
            return cp(argv,nodes())
        with patch.object(s,"command",side_effect=fake):
            self.assertEqual(s.resolve_nodes(None,None),(
                "alsa_input.real_mic","alsa_output.real_speakers"))
        with patch.object(s,"command",return_value=cp([],nodes())):
            with self.assertRaisesRegex(s.SessionError,"wrong media.class"):
                s.resolve_nodes("alsa_output.real_speakers",
                                "alsa_input.real_mic")

    def test_disallow_recursive_default_or_duplicate_name(self):
        with patch.object(s,"command",return_value=cp([],nodes())):
            for x,y in (("cuewire.microphone.44","alsa_output.real_speakers"),
                        ("same","same")):
                with self.subTest(x=x),self.assertRaises(s.SessionError):
                    s.resolve_nodes(x,y)

    def test_cue_file_requires_absolute_unique_wav_no_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/"cue.wav"
            file.write_bytes(b"RIFF"+b"\x00"*4+b"WAVE"+b"\x00"*48)
            self.assertEqual(
                s.parse_cues(["4="+str(file)]),["4="+str(file)])
            with self.assertRaisesRegex(s.SessionError,"duplicated"):
                s.parse_cues(["4="+str(file),"4="+str(file)])
            with self.assertRaisesRegex(s.SessionError,"absolute"):
                s.parse_cues(["4=relative.wav"])
            link=Path(root)/"alias.wav"
            link.symlink_to(file)
            with self.assertRaisesRegex(s.SessionError,"non-symlink"):
                s.parse_cues(["4="+str(link)])

    def test_scope_mark_checks_both_transient_and_label(self):
        good=cp([],b"Description=CueWire Hotkeys Scoped Foreground\nTransient=yes\n")
        bad=cp([],b"Description=Unrelated Process\nTransient=yes\n")
        with patch.object(s,"command",return_value=good):
            self.assertTrue(s.scope_marked())
        with patch.object(s,"command",return_value=bad):
            self.assertFalse(s.scope_marked())

    def test_existing_owned_socket_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            os.chmod(temp,0o700)
            p=Path(temp)/"cuewire-v0.1.sock"
            p.write_text("Never delete!")
            with patch.dict(os.environ,{"XDG_RUNTIME_DIR":temp}), \
                 patch.object(s,"scope_active",return_value=False):
                with self.assertRaisesRegex(s.SessionError,"already exists"):
                    s.refuse_existing()
            self.assertEqual(p.read_text(),"Never delete!")

    def test_check_only_reads_defaults_and_does_not_start(self):
        output=io.StringIO()
        with patch.object(s,"resolve_nodes",return_value=(
             "alsa_input.real_mic","alsa_output.real_speakers")), \
             patch.object(s,"scope_active",return_value=False), \
             patch.object(s.subprocess,"run",
                          side_effect=AssertionError("must not start engine")), \
             redirect_stdout(output):
            self.assertEqual(s.main(["--check"]),0)
        self.assertIn("READ ONLY",output.getvalue())

    def test_explicit_run_creates_one_transient_scope_with_both_parts(self):
        ready=MagicMock()
        ready.returncode=0
        with patch.object(s,"resolve_nodes",return_value=(
             "alsa_input.real_mic","alsa_output.real_speakers")), \
             patch.object(s,"parse_cues",return_value=["4=/tmp/test.wav"]), \
             patch.object(s,"refuse_existing"), \
             patch.object(s.Path,"is_file",return_value=True), \
             patch.object(s.os,"access",return_value=True), \
             patch.object(s.subprocess,"run",return_value=ready) as proc:
            self.assertEqual(s.main(
                ["--run","--cue","4=/tmp/test.wav"]),0)
        args=proc.call_args.args[0]
        self.assertIn("--scope",args)
        self.assertIn("--collect",args)
        self.assertIn("--unit="+s.SCOPE,args)
        self.assertIn("--inside-scope",args)
        self.assertIn("4=/tmp/test.wav",args)
        self.assertNotIn("set-default",args)

    def test_inside_scope_requires_marker_and_never_steals_socket(self):
        with patch.object(s,"scope_active",return_value=True), \
             patch.object(s,"scope_marked",return_value=False):
            with self.assertRaisesRegex(s.SessionError,"unscoped"):
                s.run_inside("a","b",["0=/dummy.wav"],25,False,False)

    def test_normal_cleanup_requests_rust_quit_not_random_process_kill(self):
        child=MagicMock()
        child.poll.return_value=None
        child.wait.return_value=0
        import cuewire_tui_live as live
        with patch.object(live,"read_live",return_value=
             live.LiveReport(True,"ok")), \
             patch.object(live,"send_live",return_value="OK shutting down") as sent, \
             patch.object(s,"command",side_effect=
                          AssertionError("no systemctl SIGKILL")):
            s.stop_engine(child)
        sent.assert_called_once_with("quit")
        child.terminate.assert_not_called()

    def test_keyboard_profile_only_declares_opt_in_lua_binds(self):
        lua=(s.ROOT/"tools/cuewire_hyprland.lua").read_text()
        self.assertIn("hl.bind(",lua)
        self.assertIn("KP_Decimal",lua)
        self.assertIn("SUPER + C",lua)
        self.assertIn("ignore_mods = true",lua)
        self.assertIn(" emergency",lua)
        self.assertNotIn("hyprctl eval",lua)
        self.assertNotIn("killall",lua)
        self.assertNotIn("pkill",lua)


if __name__=="__main__":
    unittest.main()
