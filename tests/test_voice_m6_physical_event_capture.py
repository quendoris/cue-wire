"""M6 anonymous physical event capture privacy and lifecycle unit tests.

No access to /dev/input, PipeWire, sound devices, microphone or keyboard.
Synthetic input objects have deliberately poisoned keycodes.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import voice_m6_physical_event_capture as capture


class FakeCodes:
    EV_KEY=1
    EV_SYN=0
    SYN_DROPPED=3


class FakeEvent:
    def __init__(self, kind:int=1, value:int=1, sec:int=100,
                 usec:int=75321):
        self.type=kind
        self.value=value
        self.sec=sec
        self.usec=usec

    @property
    def code(self):
        raise AssertionError(
            "Event keycode MUST NOT be touched during class-only capture")


class PhysicalPrivacyTests(unittest.TestCase):
    def test_anonymous_marker_ignores_identity_code(self):
        evdev=SimpleNamespace(ecodes=FakeCodes())
        evt=FakeEvent(value=1)
        mouse=capture.classify_event("mouse",evt,evdev)
        keyboard=capture.classify_event("keyboard",evt,evdev)
        self.assertEqual(mouse,("mouse_press",100075321000))
        self.assertEqual(keyboard,("keyboard_press",100075321000))
        self.assertEqual(
            capture.classify_event("keyboard",FakeEvent(value=0),evdev),
            ("keyboard_release",100075321000))
        self.assertIsNone(
            capture.classify_event("keyboard",FakeEvent(value=2),evdev))
        self.assertIsNone(
            capture.classify_event("mouse",FakeEvent(kind=2),evdev))
        encoded=capture.event_line(*mouse)
        self.assertEqual(
            json.loads(encoded),
            {"kind":"mouse_press","t_monotonic_ns":100075321000})
        self.assertEqual(
            set(json.loads(encoded)),{"kind","t_monotonic_ns"})
        for private_field in ("code","key","button","name","device",
                              "value","text","position","x","y","scan"):
            self.assertNotIn('"'+private_field+'"',encoded)

    def test_refuse_unrecognized_event_classes_and_bad_inputs(self):
        with self.assertRaisesRegex(ValueError,"Unknown authorized"):
            capture.classify_event("whatever",FakeEvent(),
                                   SimpleNamespace(ecodes=FakeCodes()))
        with self.assertRaises(ValueError):
            capture.event_line("keyboard_a",100)
        with self.assertRaises(ValueError):
            capture.event_line("mouse_press",0)
        with self.assertRaises(ValueError):
            capture.validate_device_path("/dev/input/by-id/something")
        with self.assertRaises(ValueError):
            capture.validate_device_path("/dev/input/event0/../event1")
        with self.assertRaises(ValueError):
            capture.validate_device_path("/tmp/input")

    def test_phase_policy_and_anonymity_are_hardcoded(self):
        names=[row[0] for row in capture.PHASES]
        self.assertEqual(names,[
            "quiet_baseline","mouse_isolated","keyboard_isolated",
            "both_holdout","voice_only_holdout","voice_clicks_holdout"])
        self.assertTrue(
            all(row[1]>0 for row in capture.PHASES))
        self.assertGreater(sum(row[1] for row in capture.PHASES),35.)
        self.assertEqual(capture.MAX_EVENT_ROWS,10000)

    def test_clock_ioctl_requires_kernel_monotonic(self):
        fake=SimpleNamespace(fd=37,read=lambda:[])
        with patch.object(capture.fcntl,"ioctl") as called:
            capture.set_kernel_monotonic(fake)
            self.assertEqual(called.call_args.args[1],
                             capture.EVIOCSCLOCKID)
            self.assertEqual(called.call_args.args[2],
                             capture.struct.pack(
                                 "i", capture.KERNEL_CLOCK_ID))

    def test_empty_evdev_queue_exits_immediately_not_infinite_loop(self):
        calls=[]
        fake=SimpleNamespace(
            fd=39,read=lambda: (calls.append(1) or []))
        with patch.object(capture.fcntl,"ioctl"):
            capture.set_kernel_monotonic(fake)
        self.assertEqual(len(calls),1)

    def test_evdev_generator_drains_old_events_and_then_stops(self):
        calls=[]
        def read():
            calls.append(1)
            if len(calls)==1:
                return iter((object(),object()))
            if len(calls)==2:
                return iter((object(),))
            return iter(())
        fake=SimpleNamespace(fd=39,read=read)
        with patch.object(capture.fcntl,"ioctl"):
            capture.set_kernel_monotonic(fake)
        self.assertEqual(len(calls),3)

    def test_nonquiescing_evdev_queue_fails_closed_instead_of_hanging(self):
        calls=[]
        fake=SimpleNamespace(
            fd=39,read=lambda: (calls.append(1) or [object()]))
        with patch.object(capture.fcntl,"ioctl"):
            with self.assertRaisesRegex(RuntimeError,"did not quiesce"):
                capture.set_kernel_monotonic(fake)
        self.assertEqual(len(calls),64)

    def test_evdev_nonblocking_empty_queue_exception_exits(self):
        def read():
            raise BlockingIOError(11, "Resource temporarily unavailable")
        fake=SimpleNamespace(fd=39,read=read)
        with patch.object(capture.fcntl,"ioctl"):
            capture.set_kernel_monotonic(fake)

    def test_terminal_calibration_hides_input_and_restores_tty(self):
        attrs = [0, 0, 0,
                 capture.termios.ECHO | capture.termios.ICANON |
                 capture.termios.ISIG, 0, 0, []]
        fake_stdin = SimpleNamespace(isatty=lambda:True, fileno=lambda:17)
        with patch.object(capture.sys, "stdin", fake_stdin), \
                patch.object(capture.termios, "tcgetattr",
                             return_value=attrs), \
                patch.object(capture.termios, "tcsetattr") as setattrs, \
                patch.object(capture.termios, "tcflush") as flush:
            with capture.noecho_capture_terminal():
                self.assertEqual(setattrs.call_count, 1)
                active = setattrs.call_args.args[2]
                self.assertFalse(active[3] & capture.termios.ECHO)
                self.assertFalse(active[3] & capture.termios.ICANON)
                self.assertTrue(active[3] & capture.termios.ISIG)
            self.assertEqual(setattrs.call_count, 2)
            self.assertEqual(setattrs.call_args.args[2], attrs)
            self.assertEqual(flush.call_count, 2)

    def test_terminal_restores_on_ctrl_c(self):
        attrs = [0, 0, 0, capture.termios.ECHO |
                 capture.termios.ICANON | capture.termios.ISIG,
                 0, 0, []]
        fake_stdin = SimpleNamespace(isatty=lambda:True, fileno=lambda:17)
        with patch.object(capture.sys,"stdin",fake_stdin), \
                patch.object(capture.termios,"tcgetattr",
                             return_value=attrs), \
                patch.object(capture.termios,"tcsetattr") as setattrs, \
                patch.object(capture.termios,"tcflush"):
            with self.assertRaises(KeyboardInterrupt):
                with capture.noecho_capture_terminal():
                    raise KeyboardInterrupt()
            self.assertEqual(setattrs.call_args.args[2], attrs)

    def test_wav_format_guard_on_synthetic_fixture(self):
        import wave
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"mic.wav"
            with wave.open(str(path),"wb") as w:
                w.setframerate(48000)
                w.setnchannels(1)
                w.setsampwidth(2)
                w.writeframes(b"\x00\x00"*31*48000)
            info=capture.check_wav(path)
            self.assertEqual(info["frames"],31*48000)
            self.assertEqual(info["sample_rate"],48000)


if __name__=="__main__":
    unittest.main()
