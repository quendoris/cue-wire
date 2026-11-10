"""M7.2 LIVE integration tests with an entirely synthetic local CueWire socket.

No real PipeWire daemon, no microphone, no evdev, no audio routing. Test
socket is in a private tempfile directory and created by this test process.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import cuewire_console as protocol
import cuewire_tui as ui
import cuewire_tui_live as live


def status(cue: int | None = None) -> str:
    actual="None" if cue is None else f"Some({cue})"
    return (
        "OK mic=alsa_input.usb-test_mic "
        "input=cuewire.mix.321 "
        "virtual_source=cuewire.microphone.321 "
        "monitor=bluez_output.test_monitor "
        f"active_cue={actual}"
    )


def valid_graph() -> bytes:
    specs=(
        ("alsa_input.usb-test_mic","Audio/Source"),
        ("bluez_output.test_monitor","Audio/Sink"),
        ("cuewire.microphone.321","Audio/Source"),
        ("cuewire.mix.321","Audio/Sink"),
    )
    return json.dumps([
        {"info":{"props":{"node.name":name,"media.class":klass}}}
        for name,klass in specs
    ]).encode("utf-8")


class SyntheticCuewireEngine:
    """Simple stateful v0.1 protocol responder over test-only UNIX socket."""
    def __init__(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.dir=Path(self.tmp.name)
        os.chmod(self.dir,0o700)
        self.path=self.dir/protocol.SOCKET_NAME
        self.server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        os.chmod(self.path,0o600)
        self.server.settimeout(.15)
        self.server.listen(5)
        self.running=True
        self.cue=None
        self.commands=[]
        self.worker=threading.Thread(target=self._serve,daemon=True)
        self.worker.start()

    def _serve(self):
        while self.running:
            try: conn,_=self.server.accept()
            except socket.timeout:continue
            except OSError:break
            with conn:
                conn.settimeout(1)
                buff=b""
                while b"\n" not in buff and len(buff)<128:
                    try: chunk=conn.recv(64)
                    except OSError:break
                    if not chunk:break
                    buff+=chunk
                cmd=buff.partition(b"\n")[0].decode("ascii","replace")
                self.commands.append(cmd)
                if cmd=="status":
                    response=status(self.cue)
                elif cmd=="stop":
                    self.cue=None
                    response="OK cue stopped"
                elif cmd.startswith("play ") and len(cmd)==6 and cmd[-1].isdigit():
                    self.cue=int(cmd[-1])
                    response=f"OK playing cue {self.cue}"
                elif cmd=="quit":
                    self.running=False
                    response="OK shutting down"
                else:
                    response="ERROR unknown command"
                try:conn.sendall((response+"\n").encode("utf-8"))
                except OSError:pass

    def __enter__(self):return self

    def __exit__(self,*_):
        self.running=False
        self.server.close()
        self.worker.join(timeout=1)
        try:self.path.unlink(missing_ok=True)
        finally:self.tmp.cleanup()

    def environment(self):
        return patch.dict(os.environ,{"XDG_RUNTIME_DIR":str(self.dir)})


class M7LiveTests(unittest.TestCase):
    def test_absent_socket_is_explicit_offline_not_simulated(self):
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder,0o700)
            with patch.dict(os.environ,{"XDG_RUNTIME_DIR":folder}):
                r=live.read_live()
                self.assertFalse(r.connected)
                self.assertEqual(r.socket_acl,"FAIL")
                self.assertEqual(r.active_cue,"unknown")
                checks=live.doctor(r)
                self.assertIn("FAIL",[c.result for c in checks])
                self.assertIn("SKIP",[c.result for c in checks])

    def test_strict_live_status_contains_real_reported_fields(self):
        with SyntheticCuewireEngine() as stub,stub.environment():
            report=live.read_live()
            self.assertTrue(report.connected)
            self.assertEqual(report.socket_acl,"PASS")
            self.assertEqual(report.protocol,"PASS")
            self.assertEqual(report.mic,"alsa_input.usb-test_mic")
            self.assertEqual(report.virtual_source,"cuewire.microphone.321")
            self.assertEqual(report.mix_input,"cuewire.mix.321")
            self.assertEqual(report.active_cue,"none")
            self.assertEqual(stub.commands,["status"])

    def test_live_model_operates_stub_without_starting_audio(self):
        with SyntheticCuewireEngine() as stub,stub.environment():
            model=ui.LiveModel(motion=False)
            model.refresh(announce=True)
            self.assertTrue(model.report.connected)
            self.assertEqual(model.mode,"LIVE")
            self.assertIn("responding"," ".join(ui.panel_lines(model,"Runtime")))
            self.assertIn("alsa_input"," ".join(ui.panel_lines(model,"Devices")))
            model.execute("play 4")
            self.assertEqual(stub.cue,4)
            self.assertEqual(model.report.active_cue,"Some(4)")
            self.assertIn("Some(4)"," ".join(ui.panel_lines(model,"Cue State")))
            model.execute("stop")
            self.assertIsNone(stub.cue)
            self.assertEqual(model.report.active_cue,"none")
            model.execute("exit")
            self.assertTrue(model.terminated)
            self.assertTrue(stub.running)
            self.assertNotIn("quit",stub.commands)

    def test_live_quit_needs_confirm_esc_and_stops_only_synthetic_owner(self):
        with SyntheticCuewireEngine() as stub,stub.environment():
            model=ui.LiveModel()
            model.refresh()
            model.execute("quit")
            self.assertTrue(model.waiting_quit)
            self.assertNotIn("quit",stub.commands)
            ui.apply_key(model,"ESC")
            self.assertFalse(model.waiting_quit)
            self.assertTrue(stub.running)
            model.execute("quit")
            model.execute("NO")
            self.assertTrue(stub.running)
            self.assertFalse(model.terminated)
            model.execute("quit")
            model.execute("CONFIRM")
            self.assertTrue(model.terminated)
            self.assertIn("quit",stub.commands)

    def test_tab_cancels_live_shutdown_confirmation(self):
        m=ui.LiveModel()
        m.focus="command"
        m.waiting_quit=True
        ui.apply_key(m,"TAB")
        self.assertEqual(m.focus,"window")
        self.assertFalse(m.waiting_quit)
        self.assertFalse(m.terminated)

    def test_disconnection_discards_previously_connected_state(self):
        model=ui.LiveModel()
        old=live.LiveReport(True,"initial",mic="old-name",
                            active_cue="Some(4)",protocol="PASS",socket_acl="PASS")
        disconnected=live.LiveReport(
            False,"engine disappeared",socket_acl="FAIL",protocol="SKIP")
        with patch.object(live,"read_live",side_effect=[
            old,disconnected,disconnected
        ]):
            model.refresh()
            self.assertTrue(model.report.connected)
            model.refresh()
        self.assertFalse(model.report.connected)
        self.assertIn("OFFLINE"," ".join(ui.panel_lines(model,"Runtime")))
        self.assertNotIn("old-name"," ".join(ui.panel_lines(model,"Devices")))
        self.assertIn("unknown"," ".join(ui.panel_lines(model,"Cue State")))
        with patch.object(live,"send_live",side_effect=AssertionError("must refuse")), \
             patch.object(live,"read_live",return_value=disconnected):
            model.execute("stop")
        self.assertIn("REFUSED",model.feed[-1])

    def test_unsafe_socket_mode_is_refused_before_status_command(self):
        with SyntheticCuewireEngine() as stub,stub.environment():
            os.chmod(stub.path,0o666)
            r=live.read_live()
            self.assertFalse(r.connected)
            self.assertEqual(r.socket_acl,"FAIL")
            self.assertEqual(stub.commands,[])

    def test_live_doctor_only_invokes_readonly_pw_dump_and_checks_all_nodes(self):
        with SyntheticCuewireEngine() as stub,stub.environment():
            report=live.read_live()
            result=subprocess.CompletedProcess(["pw-dump"],0,valid_graph(),b"")
            with patch.object(live.subprocess,"run",return_value=result) as run:
                checks=live.doctor(report)
            run.assert_called_once()
            args,kwargs=run.call_args
            self.assertEqual(args[0],["pw-dump"])
            self.assertIs(kwargs["stdin"],subprocess.DEVNULL)
            self.assertEqual(kwargs["timeout"],live.GRAPH_TIMEOUT_S)
            self.assertEqual(sum(c.result=="PASS" for c in checks),6)
            self.assertEqual(checks[-1].result,"SKIP")
            self.assertEqual(checks[-1].name,"live M6 / latency")

    def test_live_doctor_fails_if_virtual_source_missing_or_wrong_class(self):
        report=live.LiveReport(True,"test",mic="mic",monitor="monitor",
                               virtual_source="vmic",mix_input="mix",
                               socket_acl="PASS",protocol="PASS")
        graph=[
            {"info":{"props":{"node.name":"mic","media.class":"Audio/Source"}}},
            {"info":{"props":{"node.name":"monitor","media.class":"Audio/Sink"}}},
            {"info":{"props":{"node.name":"vmic","media.class":"Audio/Sink"}}},
            {"info":{"props":{"node.name":"mix","media.class":"Audio/Sink"}}},
        ]
        blob=json.dumps(graph).encode()
        with patch.object(live.subprocess,"run",return_value=
                          subprocess.CompletedProcess(["pw-dump"],0,blob,b"")):
            checks=live.doctor(report)
        virtual=[c for c in checks if c.name=="virtual source"]
        self.assertEqual(len(virtual),1)
        self.assertEqual(virtual[0].result,"FAIL")

    def test_pw_dump_timeout_is_failure_never_healthy(self):
        connected=live.LiveReport(
            True,"ok",mic="mic",monitor="monitor",
            virtual_source="vmic",mix_input="mix",protocol="PASS",socket_acl="PASS")
        with patch.object(live.subprocess,"run",side_effect=
                          subprocess.TimeoutExpired(["pw-dump"],2.0)):
            checks=live.doctor(connected)
        self.assertTrue(any(c.name=="PipeWire node graph"
                            and c.result=="FAIL" for c in checks))
        self.assertFalse(any(c.name=="PipeWire node graph"
                             and c.result=="PASS" for c in checks))

    def test_doctor_in_live_model_cannot_claim_offline_m6_active(self):
        model=ui.LiveModel()
        report=live.LiveReport(
            True,"ok",mic="mic",monitor="monitor",
            virtual_source="vmic",mix_input="mix",protocol="PASS",socket_acl="PASS")
        good=[live.Check("same-user socket 0600","PASS","ok"),
              live.Check("CueWire v0.1 protocol","PASS","ok")]
        good.extend(live.Check(name,"PASS","ok") for name in (
            "physical mic reported","monitor reported","virtual source","mix input"))
        good.append(live.Check("live M6 / latency","SKIP","not implemented"))
        with patch.object(live,"read_live",return_value=report), \
             patch.object(live,"doctor",return_value=tuple(good)):
            model.execute("doctor")
        self.assertEqual(model.selected,5)
        self.assertIn("PASS / 4 nodes registered",model.checks)
        self.assertIn("SKIP / audio/DSP/latency",model.checks)
        self.assertEqual(len(model.graph_checks),7)

    def test_snapshot_and_demo_stay_isolated_after_live_integration(self):
        with patch.object(live,"read_live",side_effect=
                          AssertionError("snapshot/demo must not contact socket")), \
             patch.object(live,"send_live",side_effect=
                          AssertionError("snapshot/demo must not send")):
            out=io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(ui.main(["--snapshot"]),0)
            self.assertIn("M6-007",out.getvalue())
            demo=ui.Model()
            demo.execute("play 4")
            self.assertEqual(demo.active_cue,4)
            self.assertIn("SIMULATED",demo.feed[-1])

    def test_graph_audit_persists_briefly_then_expires_without_false_pass(self):
        report=live.LiveReport(
            True,"ok",mic="mic",monitor="monitor",
            virtual_source="vmic",mix_input="mix",
            socket_acl="PASS",protocol="PASS")
        checks=tuple(
            [live.Check("same-user socket 0600","PASS","ok"),
             live.Check("CueWire v0.1 protocol","PASS","ok")]
            +[live.Check(name,"PASS","ok") for name in (
                "physical mic reported","monitor reported",
                "virtual source","mix input")]
            +[live.Check("live M6 / latency","SKIP","not measured")]
        )
        m=ui.LiveModel()
        with patch.object(live,"read_live",return_value=report), \
             patch.object(live,"doctor",return_value=checks):
            m.execute("doctor")
            m.refresh()
            self.assertIn("PASS / 4 nodes registered",m.checks)
            m.graph_checked_at-=21.0
            m.refresh()
            self.assertIn("SKIP / graph (run doctor)",m.checks)
            self.assertFalse(m.graph_checks)

    def test_status_poll_and_commands_have_distinct_bounded_timeouts(self):
        with patch.object(live.protocol,"validate_socket_location"), \
             patch.object(live.protocol,"request",
                          return_value=status(None)) as requested:
            self.assertTrue(live.read_live().connected)
            requested.assert_called_once_with(
                "status",timeout_seconds=0.5)
        with patch.object(live.protocol,"request",
                          return_value="OK cue stopped") as requested:
            self.assertIn("cue stopped",live.send_live("stop"))
            requested.assert_called_once_with(
                "stop",timeout_seconds=1.2)

    def test_live_check_fails_closed_when_absent(self):
        snapshot=live.LiveReport(False,"not running",socket_acl="FAIL",protocol="SKIP")
        with patch.object(live,"read_live",return_value=snapshot), \
             patch.object(live,"doctor",return_value=(
                 live.Check("same-user socket 0600","FAIL","missing"),
                 live.Check("CueWire v0.1 protocol","SKIP","missing"),
                 live.Check("PipeWire node graph","SKIP","unavailable"),
                 live.Check("live M6 / latency","SKIP","not implemented"),
             )), redirect_stdout(io.StringIO()):
            self.assertEqual(ui.main(["--live-check"]),2)

    def test_live_check_all_nodes_pass_but_dsp_remains_skip(self):
        snapshot=live.LiveReport(True,"ok",socket_acl="PASS",protocol="PASS")
        checks=tuple(
            [live.Check("same-user socket 0600","PASS","ok"),
             live.Check("CueWire v0.1 protocol","PASS","ok")]
            +[live.Check(name,"PASS","ok") for name in (
                "physical mic reported","monitor reported","virtual source","mix input")]
            +[live.Check("live M6 / latency","SKIP","not measured")]
        )
        out=io.StringIO()
        with patch.object(live,"read_live",return_value=snapshot), \
             patch.object(live,"doctor",return_value=checks), \
             redirect_stdout(out):
            self.assertEqual(ui.main(["--live-check"]),0)
        self.assertIn("SKIP live M6 / latency",out.getvalue())


if __name__=="__main__":
    unittest.main()
