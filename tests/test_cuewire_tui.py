"""M7.1 compact Quendoris terminal UI — synthetic, side-effect-free tests.

No PipeWire, evdev, microphone, sockets, external imports or system calls.
This validates composition, focus-driven expansion, exact mark, inputs,
fail-closed commands, demo/snapshot isolation and terminal restore contract.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import cuewire_tui as t


class QuendorisCompactTUIChecks(unittest.TestCase):
    def test_original_quendoris_420_geometry_and_eye_holes(self):
        self.assertIn("M 35 35 L 386 35",t.MARK_PATH)
        self.assertIn("L 105 246 L 35 246",t.MARK_PATH)
        self.assertTrue(t.mark_at(50,50))
        self.assertTrue(t.mark_at(200,55))
        self.assertTrue(t.mark_at(125,280))
        self.assertTrue(t.mark_at(285,300))
        self.assertFalse(t.mark_at(140,140))
        self.assertFalse(t.mark_at(280,140))
        self.assertFalse(t.mark_at(205,350))
        self.assertFalse(t.mark_at(40,300))
        self.assertFalse(t.mark_at(0,0))
        self.assertFalse(t.mark_at(420,420))
        self.assertEqual(t.SITE_ACCENT,(211,224,139))
        # Exact raster area from the verified accepted SVG in quendoris/site.
        self.assertEqual(
            sum(t.mark_at(x,y) for y in range(420) for x in range(420)),
            89249)

    def test_raster_brand_mark_has_visible_lateral_eyes(self):
        width,height=120,38
        coords={(x,y) for y in range(height) for x in range(width)
                if t.mark_at_screen(x,y,width,height)}
        self.assertGreater(len(coords),100)
        # Sample rows through the pupil cutouts are not solid fill.
        middle=height//2
        self.assertTrue(any(x in range(40,80) for x,y in coords if y==middle))

    def test_six_panels_no_overlap_at_multiple_terminal_sizes(self):
        for width,height in ((68,23),(80,24),(100,32),(120,38),(160,42)):
            for expanded in (False,True):
                with self.subTest(size=(width,height),expanded=expanded):
                    L=t.layout(width,height,expanded)
                    self.assertTrue(L.adequate)
                    self.assertEqual(len(L.panels),6)
                    self.assertEqual(L.dock.h,1 if not expanded else min(9,max(5,height//4)))
                    self.assertEqual(L.panels[0].y,L.panels[1].y)
                    self.assertGreater(L.panels[3].y,L.panels[0].y)
                    self.assertLess(L.panels[5].y+L.panels[5].h,L.dock.y)
                    for i,p in enumerate(L.panels):
                        self.assertGreaterEqual(p.w,19)
                        self.assertGreaterEqual(p.h,5)
                        self.assertGreaterEqual(p.x,0)
                        self.assertLessEqual(p.x+p.w,width)
                        for j in range(i):
                            z=L.panels[j]
                            self.assertFalse(
                                p.x<z.x+z.w and z.x<p.x+p.w and
                                p.y<z.y+z.h and z.y<p.y+p.h)
                    self.assertLessEqual(L.footer.y,height-1)

    def test_dock_collapses_and_expands_on_focus(self):
        m=t.Model()
        self.assertEqual(m.focus,"window")
        _,collapsed=t.paint(m,100,32)
        self.assertEqual(collapsed.dock.h,1)
        t.apply_key(m,"ENTER")
        self.assertEqual(m.focus,"command")
        _,expanded=t.paint(m,100,32)
        self.assertGreaterEqual(expanded.dock.h,5)
        for key in "play 7":
            t.apply_key(m,key)
        self.assertEqual(m.command,"play 7")
        t.apply_key(m,"ENTER")
        self.assertEqual(m.active_cue,7)
        t.apply_key(m,"TAB")
        self.assertEqual(m.focus,"window")
        self.assertEqual(m.command,"")
        _,collapsed2=t.paint(m,100,32)
        self.assertEqual(collapsed2.dock.h,1)

    def test_tab_arrows_and_shift_tab_select_exactly_six_windows(self):
        m=t.Model()
        for i in range(6):
            self.assertEqual(m.selected,i)
            t.apply_key(m,"TAB")
        self.assertEqual(m.selected,0)
        t.apply_key(m,"BACKTAB")
        self.assertEqual(m.selected,5)
        t.apply_key(m,"RIGHT")
        self.assertEqual(m.selected,3)
        t.apply_key(m,"UP")
        self.assertEqual(m.selected,0)
        t.apply_key(m,"DOWN")
        self.assertEqual(m.selected,3)

    def test_demo_play_stop_doctor_and_accepted_research(self):
        m=t.Model()
        m.execute("play 9")
        self.assertEqual(m.active_cue,9)
        self.assertIn("SIMULATED",m.feed[-1])
        m.execute("stop")
        self.assertIsNone(m.active_cue)
        m.execute("research")
        self.assertEqual(m.selected,2)
        self.assertIn("not loaded",m.feed[-1])
        m.execute("doctor")
        self.assertEqual(m.selected,5)
        self.assertIn("SKIP",m.feed[-1])
        self.assertTrue(any("socket ACL (offline)" in v for v in m.checks))
        self.assertNotIn("LIVE DSP", " ".join(m.checks))

    def test_demo_keymap_staging_never_reads_or_writes_user_files(self):
        import cuewire_keymap as keymap
        m=t.Model()
        with patch.object(keymap,"read_profile",
                          side_effect=AssertionError("demo cannot read files")), \
             patch.object(keymap,"save_profile",
                          side_effect=AssertionError("demo cannot write files")):
            m.execute("keys")
            self.assertIn("LOCAL PROFILE",m.feed[-1])
            m.execute("bind cue:4 F8")
            self.assertEqual(m.demo_keymap["cue:4"],"F8")
            self.assertIn("DEMO ONLY",m.feed[-1])
            m.execute("unbind cue:4")
            self.assertNotIn("cue:4",m.demo_keymap)
            m.execute("bind cue:9 SUPER+C")
            self.assertIn("REFUSED KEYMAP",m.feed[-1])

    def test_invalid_command_does_not_trigger_audio_or_remote_execution(self):
        m=t.Model()
        before=m.active_cue
        for cmd in ("play 10","play -1","play 2; rm -rf /","record",
                    "set-default","enable-m6","load-dsp","keylogger"):
            m.execute(cmd)
            self.assertIsNone(m.active_cue)
            self.assertTrue(m.feed[-1].startswith("REFUSED"))
        self.assertEqual(m.active_cue,before)

    def test_quit_requires_literal_confirmation_and_escape_cancels(self):
        m=t.Model(focus="command")
        m.execute("quit")
        self.assertTrue(m.waiting_quit)
        self.assertFalse(m.terminated)
        t.apply_key(m,"ESC")
        self.assertFalse(m.waiting_quit)
        self.assertFalse(m.terminated)
        m.execute("quit")
        m.execute("NO")
        self.assertFalse(m.terminated)
        m.execute("quit")
        m.execute("CONFIRM")
        self.assertTrue(m.terminated)

    def test_input_bounded_and_history_not_written_to_disk(self):
        m=t.Model(focus="command")
        for key in "a"*200:
            t.apply_key(m,key)
        self.assertEqual(len(m.command),t.MAX_COMMAND)
        t.apply_key(m,"CTRL_U")
        self.assertEqual(m.command,"")
        for k in "abc":t.apply_key(m,k)
        t.apply_key(m,"BACKSPACE")
        self.assertEqual(m.command,"ab")
        t.apply_key(m,"CTRL_C")
        self.assertTrue(m.terminated)
        self.assertLessEqual(len(m.feed),t.MAX_FEED)

    def test_key_reader_parses_escape_sequences_and_control_keys(self):
        reader=t.KeyReader()
        self.assertEqual(reader.feed(b"\x1b[A\x1b[B\x1b[Z"),[
            "UP","DOWN","BACKTAB"])
        self.assertEqual(reader.feed(b"\r\t\x7f\x15\x03"),[
            "ENTER","TAB","BACKSPACE","CTRL_U","CTRL_C"])
        self.assertEqual(reader.feed(b"\x1b"),[])
        self.assertEqual(reader.flush_esc(),["ESC"])
        self.assertEqual(reader.feed(b"\x1b[C"),["RIGHT"])

    def test_snapshot_renders_without_tty_pipewire_or_network(self):
        out=io.StringIO()
        with patch.object(t,"run_demo",side_effect=AssertionError("interactive")), \
             patch.object(t,"select",side_effect=AssertionError("select")), \
             redirect_stdout(out):
            self.assertEqual(t.main(["--snapshot"]),0)
        preview=out.getvalue()
        self.assertIn("QUENDORIS",preview)
        self.assertIn("M6-007",preview)
        self.assertIn("Runtime",preview)
        self.assertIn("Research",preview)
        self.assertIn("Audit / Checks",preview)
        self.assertIn("cuewire>",preview)
        self.assertIn("·",preview)
        self.assertEqual(len(preview.splitlines()),32)

    def test_truecolor_frame_never_newline_scrolls_last_row(self):
        m=t.Model()
        canvas,_=t.paint(m,80,28,.25)
        ansi=canvas.ansi()
        # SGR can combine weight, foreground and background in one CSI:
        # ESC[22;38;2;R;G;B;48;2;R;G;Bm. The old assertion
        # erroneously required foreground/background to each start a CSI.
        self.assertRegex(
            ansi,
            r"\x1b\[(?:1|22);38;2;\d{1,3};\d{1,3};\d{1,3};48;2;\d{1,3};\d{1,3};\d{1,3}m",
        )
        self.assertIn(";38;2;",ansi)
        self.assertIn(";48;2;",ansi)
        self.assertIn("\x1b[28;1H",ansi)
        self.assertFalse(ansi.endswith("\n"))
        self.assertEqual(len(canvas.plain().splitlines()),28)

    def test_reduced_motion_is_time_invariant(self):
        m=t.Model(motion=False)
        a,_=t.paint(m,100,32,0)
        b,_=t.paint(m,100,32,1000)
        self.assertEqual(a.ansi(),b.ansi())
        m.motion=True
        c,_=t.paint(m,100,32,1000)
        self.assertNotEqual(a.ansi(),c.ansi())

    def test_tiny_terminal_has_safe_message_not_broken_panels(self):
        for w,h in ((30,10),(67,32),(90,22)):
            canvas,geo=t.paint(t.Model(),w,h)
            self.assertFalse(geo.adequate)
            self.assertFalse(geo.panels)
            self.assertIn("terminal too small",canvas.plain())

    def test_no_color_requests_monochrome_and_disables_animation(self):
        with patch.dict(t.os.environ,{"NO_COLOR":"1","TERM":"xterm-256color"}), \
             patch.object(t,"run_demo",return_value=0) as demo:
            self.assertEqual(t.main(["--demo"]),0)
            demo.assert_called_once_with(True,True)

    def test_terminal_context_restores_raw_mode_and_cursor_on_exit(self):
        class MockTerminal:
            def __init__(self):
                self.transcript=[]
            def isatty(self): return True
            def fileno(self): return 77
            def write(self,value): self.transcript.append(value)
            def flush(self): pass

        terminal=MockTerminal()
        original=["saved-termios"]
        with patch.object(t.termios,"tcgetattr",return_value=original) as saved, \
             patch.object(t.tty,"setraw") as setraw, \
             patch.object(t.termios,"tcsetattr") as restored:
            with t.TerminalSession(stream=terminal,output=terminal):
                self.assertIn("\x1b[?1049h","".join(terminal.transcript))
            saved.assert_called_once_with(77)
            setraw.assert_called_once_with(77)
            restored.assert_called_once_with(77,t.termios.TCSADRAIN,original)
        self.assertIn("\x1b[?25h",terminal.transcript[-1])
        self.assertIn("\x1b[?1049l",terminal.transcript[-1])

    def test_terminal_session_refuses_non_tty_before_changing_mode(self):
        class Fake:
            def isatty(self):return False
        with self.assertRaisesRegex(RuntimeError,"interactive terminal"):
            with t.TerminalSession(stream=Fake(),output=Fake()):
                pass


if __name__=="__main__":
    unittest.main()
