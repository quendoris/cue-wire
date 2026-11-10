"""M6 Physical-003 synthetic regressions: never open real microphone or key input."""
from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
import voice_m6_physical_tail_ab as m6


def synthetic_reverb():
    """20s with isolated physically plausible impulses and no voice in training."""
    rng = np.random.default_rng(11062026)
    n = 20*m6.RATE
    t = np.arange(n)/m6.RATE
    voice = .001*np.sin(2*np.pi*220*t)
    mic = voice.copy()
    t0 = 1_000_000_000_000
    event_rows = []
    offsets = {
        "mouse_press": 19, "mouse_release": 17,
        "keyboard_press": 24, "keyboard_release": 10,
    }
    schedule = {
        "mouse_press": np.arange(1.0, 3.8, .24),
        "mouse_release": np.arange(1.12, 3.9, .24),
        "keyboard_press": np.arange(6., 9., .24),
        "keyboard_release": np.arange(6.12, 9.12, .24),
    }
    for kind, positions in schedule.items():
        for sec in positions:
            at = round(sec*m6.RATE)
            length = m6.ms(80 if kind.startswith("keyboard") else 40)
            j = np.arange(length)
            tau = (.020 if kind.startswith("keyboard") else .008)
            wave = .06*np.exp(-j/(tau*m6.RATE))*np.sin(
                2*np.pi*5000*j/m6.RATE)
            mic[at:at+length] += wave
            # measured acoustic position corresponds to kernel timestamp
            # after known class offset.
            timestamp_sec = sec + offsets[kind]/1000.
            event_rows.append({
                "kind": kind,
                "t_monotonic_ns": t0+round(timestamp_sec*1e9),
            })
    event_rows.sort(key=lambda r: r["t_monotonic_ns"])
    phases = []
    for name, a, b in (
        ("quiet_baseline", 0, .5),
        ("mouse_isolated", .5, 4.5),
        ("keyboard_isolated", 5.5, 9.5),
        ("both_holdout", 10, 13),
        ("voice_only_holdout", 13, 16),
        ("voice_clicks_holdout", 16, 19)):
        phases.append({
            "phase": name,
            "start_monotonic_ns": t0+round(a*1e9),
            "end_monotonic_ns": t0+round(b*1e9),
        })
    manifest = {"session_start_monotonic_ns": t0, "phases": phases}
    return mic, manifest, event_rows, offsets


class PhysicalTailTests(unittest.TestCase):
    def test_long_decay_not_forced_into_old_eight_ms(self):
        noise = np.full(5*m6.RATE, .0002, dtype=float)
        peak = 2*m6.RATE
        j = np.arange(m6.ms(110))
        noise[peak:peak+len(j)] += .03*np.exp(
            -j/(m6.RATE*.018))
        duration = m6.tail_samples(peak, .0002, .03, noise, 100)
        self.assertGreater(duration, m6.ms(25))
        self.assertLess(duration, m6.ms(100))

    def test_isolated_calibration_then_event_mask_extends_tail(self):
        mic, manifest, events, offsets = synthetic_reverb()
        high, strength = m6.envelope(mic, m6.BAND)
        caps, info = m6.calibrate_tails(
            manifest, events, offsets, strength)
        self.assertGreaterEqual(info["mouse"]["isolated_events"], 8)
        self.assertGreaterEqual(info["keyboard"]["isolated_events"], 8)
        self.assertGreater(caps["mouse"], 8)
        self.assertGreaterEqual(caps["keyboard"], 50)
        self.assertTrue(info["keyboard"]["no_speech_used_for_fit"])
        mask, details = m6.dynamic_mask(
            manifest, events, offsets, strength, caps)
        self.assertGreater(details["modified_samples"], 0)
        self.assertTrue(details["event_fields_used"] == [
            "kind", "t_monotonic_ns"])
        self.assertFalse(details["key_or_button_identity_read"])
        raw = m6.to_pcm16(mic)
        out = m6.to_pcm16(mic-.75*mask*high)
        np.testing.assert_array_equal(
            raw[mask == 0], out[mask == 0])
        # A genuine extended event requires >8ms of support in the mask.
        self.assertGreater(details["modified_samples"],
                           8*m6.ms(8))

    def test_sustained_low_frequency_voice_activates_guard(self):
        mic, manifest, events, offsets = synthetic_reverb()
        start, end = 14*m6.RATE, 16*m6.RATE
        mic[start:end] += .055*np.sin(
            2*np.pi*185*np.arange(end-start)/m6.RATE)
        guard, info = m6.voice_guard(mic, manifest)
        self.assertTrue(info["training_only_speech_free"])
        self.assertLess(float(np.mean(guard[start:end])), .9)
        self.assertGreater(float(np.mean(guard[m6.RATE:3*m6.RATE])), .9)

    def test_no_events_never_changes_physical_pcm(self):
        n=3*m6.RATE
        mic=.03*np.sin(2*np.pi*320*np.arange(n)/m6.RATE)
        hi,_=m6.envelope(mic,m6.BAND)
        raw=m6.to_pcm16(mic)
        mask=np.zeros(n,dtype=np.float32)
        candidate=m6.to_pcm16(mic-.75*mask*hi)
        np.testing.assert_array_equal(raw,candidate)

    def test_privacy_contract_rejects_extended_input_fields(self):
        rows=[{"kind":"keyboard_press",
               "t_monotonic_ns":1_000_000_000_000,
               "keycode":"not permitted"}]
        off={key:0 for key in m6.KINDS}
        with self.assertRaisesRegex(ValueError,"identifying event"):
            m6.event_centers(rows,off,0,5*m6.RATE)
        off.pop("keyboard_release")
        with self.assertRaisesRegex(ValueError,"offset"):
            m6.event_centers([],off,0,5*m6.RATE)

    def test_physical_peak_guard_refuses_clipping(self):
        with self.assertRaisesRegex(ValueError,"unsafe/clipped"):
            m6.to_pcm16(np.array([.99]))

    def test_old_final_phase_bounds_only_trim_250ms(self):
        from voice_m6_physical_click_ab import phase_bounds
        t0=1_000_000_000_000
        p={"start_monotonic_ns":t0+10_000_000_000,
           "end_monotonic_ns":t0+13_200_000_000}
        start,end=phase_bounds(p,t0,13*m6.RATE)
        self.assertEqual(start,10*m6.RATE)
        self.assertEqual(end,13*m6.RATE)
        p["end_monotonic_ns"]+=500_000_000
        with self.assertRaisesRegex(ValueError,"does not fit"):
            phase_bounds(p,t0,13*m6.RATE)


if __name__=="__main__":
    unittest.main()
