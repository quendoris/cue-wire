"""Synthetic tests for M6 event timestamp bridge; no evdev device opened."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import voice_m6_event_clock as clock
import voice_m6_event_anc as m6


class M6EventClockTests(unittest.TestCase):
    def test_monotonic_ns_to_pcm_samples_with_explicit_lag(self):
        sample_zero = 9_000_000_000_000
        events = "".join([
            clock.serialize_class_event(
                "mouse_press", sample_zero + 100_000_000),
            clock.serialize_class_event(
                "keyboard_press", sample_zero + 250_000_000),
            clock.serialize_class_event(
                "mouse_release", sample_zero + 290_000_000),
        ])
        result = clock.convert_events(
            events, sample_zero, acoustic_offset_samples=96,
            num_frames=m6.RATE)
        self.assertEqual([r["sample"] for r in result],
                         [4896, 12096, 14016])
        self.assertEqual(set(result[0]), {"kind", "sample"})
        self.assertNotIn("t_monotonic_ns",
                         json.dumps(result))
        self.assertNotIn("code", json.dumps(result))
        self.assertNotIn("text", json.dumps(result))

    def test_decreasing_timestamps_or_key_contents_fail_closed(self):
        t0 = 12_000_000_000
        bad = (clock.serialize_class_event("keyboard_press", t0+100_000_000)
               + clock.serialize_class_event("keyboard_press", t0+10_000_000))
        with self.assertRaisesRegex(ValueError, "unsorted"):
            clock.convert_events(bad, t0, 0, m6.RATE)
        with self.assertRaisesRegex(ValueError, "Invalid coarse"):
            clock.serialize_class_event("keyboard_press", -1)
        bad = json.dumps({
            "kind": "keyboard_press", "t_monotonic_ns":t0+100_000_000,
            "keycode": "KEY_A"})
        with self.assertRaisesRegex(ValueError, "private"):
            clock.convert_events(bad, t0, 0, m6.RATE)

    def test_unknown_audio_anchor_or_out_of_bounds_rejected(self):
        row = clock.serialize_class_event("mouse_press", 1_500_000_000)
        with self.assertRaisesRegex(ValueError, "Invalid PCM timing"):
            clock.convert_events(row, -1, 0, m6.RATE)
        with self.assertRaisesRegex(ValueError, "outside"):
            clock.convert_events(row, 1_000_000_000, 0, m6.RATE)


if __name__ == "__main__":
    unittest.main()
