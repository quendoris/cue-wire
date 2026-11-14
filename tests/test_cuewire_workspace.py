"""M7.5 workspace regressions: synthetic only, no real devices or global binds."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import cuewire_tui as ui
import cuewire_workspace as w
import cuewire_keymap as keymap
import cuewire_console as protocol
import cuewire_tui_live as backend
import cuewire_session as session


class WorkspaceTests(unittest.TestCase):
    def test_all_tabs_keep_original_dock_and_footer_cells(self):
        for width,height in ((68,23),(80,24),(100,32),(130,42)):
            for focus in ("window","command"):
                model=ui.Model(motion=False,focus=focus)
                model.command="status" if focus=="command" else ""
                work=w.Workspace(model)
                original,geometry=ui.paint(model,width,height)
                for tab in range(5):
                    with self.subTest(size=(width,height),focus=focus,tab=tab):
                        work.switch(tab)
                        new,geo=w.paint(work,width,height)
                        self.assertEqual(geo,geometry)
                        self.assertEqual(new.cells[geo.dock.y:],original.cells[geo.dock.y:])
                        self.assertEqual(len(new.cells),height)
                        self.assertTrue(all(len(row)==width for row in new.cells))

    def test_mixer_keys_grow_with_width_and_height_without_crossing_dock(self):
        small=w.mixer_geometry(ui.Rect(2,4,76,19))
        wide=w.mixer_geometry(ui.Rect(2,4,136,19))
        tall=w.mixer_geometry(ui.Rect(2,4,136,36))
        self.assertGreater(wide[4],small[4])
        self.assertGreater(tall[5],wide[5])
        for width in range(68,181,4):
            for height in range(20,51):
                for expanded in (False,True):
                    geo=ui.layout(width,height,expanded)
                    if not geo.adequate:continue
                    area=ui.Rect(2,4,width-4,geo.dock.y-5)
                    left,lw,right,rw,kw,kh=w.mixer_geometry(area)
                    if kw:
                        self.assertLessEqual(3*kw+4,rw)
                        self.assertLessEqual(4*kh+7,area.h)
                    self.assertLess(left+lw,right)
                    self.assertLessEqual(right+rw,area.x+area.w-2)

    def test_tab_navigation_and_existing_command_focus(self):
        m=ui.Model()
        work=w.Workspace(m)
        for n in range(1,5):
            work.handle("TAB")
            self.assertEqual(work.tab,n)
        work.handle("ENTER")
        self.assertEqual(m.focus,"command")
        for ch in "status":work.handle(ch)
        work.handle("ENTER")
        self.assertIn("simulated engine",m.feed[-1])
        work.handle("TAB")
        self.assertEqual(m.focus,"window")
        self.assertEqual(work.tab,4)
        work.handle("TAB")
        self.assertEqual(work.tab,0)

    def test_mixer_demo_slider_is_bounded_and_independent(self):
        m=ui.Model()
        work=w.Workspace(m)
        work.switch(4)
        for _ in range(40):work.handle("LEFT")
        self.assertEqual(work.gains,[0,45,35,80])
        work.handle("DOWN")
        for _ in range(40):work.handle("RIGHT")
        self.assertEqual(work.gains,[0,100,35,80])
        self.assertIsNone(m.active_cue)

    def test_mixer_live_gains_disabled_no_fake_telemetry(self):
        m=ui.LiveModel()
        work=w.Workspace(m)
        work.switch(4)
        with patch.object(backend,"send_live",side_effect=AssertionError("no gain setter")):
            work.handle("RIGHT")
        self.assertEqual(work.gains,[100,45,35,80])
        self.assertIn("unavailable",m.feed[-1])
        frame,_=w.paint(work,100,32)
        text=frame.plain()
        self.assertIn("UNAVAILABLE",text)
        self.assertIn("N/A",text)
        self.assertNotIn("100%",text)

    def test_demo_num_pad_toggle_replace_stop_and_display(self):
        m=ui.Model()
        work=w.Workspace(m)
        work.switch(4)
        work.handle("4")
        self.assertEqual(m.active_cue,4)
        text=w.paint(work,100,32)[0].plain()
        self.assertIn("Demo - Thunderstruck.wav",text)
        work.handle("4")
        self.assertIsNone(m.active_cue)
        work.handle("7")
        work.handle("8")
        self.assertEqual(m.active_cue,8)
        work.handle(".")
        self.assertIsNone(m.active_cue)

    def test_live_num_pad_uses_atomic_toggle_and_only_reported_state(self):
        m=ui.LiveModel()
        m.report=backend.LiveReport(True,"ready",active_cue="none")
        work=w.Workspace(m,("4=/test/actual.wav",))
        work.switch(4)
        with patch.object(m,"execute") as execute:
            work.handle("4")
            execute.assert_called_once_with("toggle 4")
        self.assertIsNone(w.cue_number(m))
        m.report=backend.LiveReport(True,"ready",active_cue="Some(4)")
        self.assertEqual(w.cue_number(m),4)
        self.assertIn("actual.wav",w.paint(work,100,32)[0].plain())
        m.report=backend.LiveReport(False,"lost")
        self.assertIsNone(w.cue_number(m))
        self.assertNotIn("actual.wav",w.paint(work,100,32)[0].plain())

    def test_live_model_refreshes_actual_state_after_toggle(self):
        m=ui.LiveModel()
        ready=backend.LiveReport(True,"ready",active_cue="none")
        playing=backend.LiveReport(True,"ready",active_cue="Some(4)")
        with patch.object(backend,"read_live",side_effect=[ready,playing]), \
             patch.object(backend,"send_live",return_value="OK playing cue 4") as send:
            m.execute("toggle 4")
        send.assert_called_once_with("toggle 4")
        self.assertEqual(w.cue_number(m),4)

    def test_protocol_toggle_allowlist_is_strict(self):
        self.assertEqual(protocol.operator_command("toggle 4"),("remote","toggle 4"))
        for invalid in ("toggle 10","toggle -1","toggle 4; quit","toggle 4\nquit"):
            with self.assertRaises(protocol.ConsoleError):
                protocol.operator_command(invalid)

    def test_library_scan_is_nonrecursive_excludes_symlinks_and_sorts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ("B.WAV","a.wav","ignore.txt"):
                (root/name).write_bytes(b"test")
            (root/"nested").mkdir()
            (root/"nested"/"deep.wav").write_bytes(b"test")
            (root/"link.wav").symlink_to(root/"a.wav")
            self.assertEqual([p.name for p in w.scan_wavs(str(root))],["a.wav","B.WAV"])
            with patch.object(w,"MAX_ENTRIES",1):
                with self.assertRaisesRegex(ValueError,"exceeds"):
                    w.scan_wavs(str(root))

    def test_demo_library_and_keymap_never_touch_user_files(self):
        m=ui.Model()
        work=w.Workspace(m)
        with patch.object(w,"scan_wavs",side_effect=AssertionError("demo scan")), \
             patch.object(keymap,"read_profile",side_effect=AssertionError("demo read")), \
             patch.object(session,"private_profile_cues",side_effect=AssertionError("demo profile")), \
             patch.object(session,"write_private_profile",side_effect=AssertionError("demo write")):
            self.assertTrue(work.command("library /home/user/music"))
            self.assertTrue(work.command("slot 4 2"))
            self.assertTrue(work.command("save library"))
            work.switch(2)
        self.assertEqual(work.running_cues[4].name,"Sample 2.wav")
        self.assertFalse(work.staged)

    def test_live_library_stages_then_explicitly_merges_next_session(self):
        m=ui.LiveModel()
        work=w.Workspace(m,("4=/test/current.wav",))
        work.files=(Path("/test/new.wav"),)
        with patch.object(session,"private_profile_cues",return_value=["4=/test/current.wav","5=/test/keep.wav"]), \
             patch.object(session,"write_private_profile") as save:
            work.command("slot 4 1")
            save.assert_not_called()
            self.assertEqual(work.running_cues[4],Path("/test/current.wav"))
            work.command("save library")
            save.assert_called_once_with(["4=/test/new.wav","5=/test/keep.wav"],replace=True)
        self.assertFalse(work.staged)
        self.assertEqual(work.running_cues[4],Path("/test/current.wav"))

    def test_device_identifiers_wrap_and_scroll(self):
        m=ui.LiveModel()
        m.report=backend.LiveReport(True,"ready",mic="alsa_input."+"x"*120,
                                    monitor="alsa_output.monitor",
                                    virtual_source="cuewire.microphone.1",
                                    mix_input="cuewire.mix.1")
        work=w.Workspace(m)
        work.switch(1)
        self.assertIn("x"*120," ".join(work.rows()))
        for _ in range(30):work.handle("DOWN")
        frame,geo=w.paint(work,68,23)
        self.assertTrue(geo.adequate)
        self.assertGreater(work.scroll,0)
        self.assertIn("UP/DOWN",frame.plain())

    def test_snapshot_mixer_stays_isolated_and_prints_tabbed_layout(self):
        out=io.StringIO()
        with patch.object(backend,"read_live",side_effect=AssertionError("no socket")), \
             redirect_stdout(out):
            self.assertEqual(ui.main(["--snapshot","--workspace","--workspace-tab","Mixer"]),0)
        shown=out.getvalue()
        self.assertIn("[Mixer]",shown)
        self.assertIn("NUMPAD / CUE STATE",shown)
        self.assertIn("cuewire>",shown)


if __name__=="__main__":
    unittest.main()
