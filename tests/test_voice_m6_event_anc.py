"""M6 regression: synthetic known voice, real FIR echo math, timed clicks.

No microphone access, global key logging, playback or personal audio in tests.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import voice_m6_event_anc as m6


def synth():
    n = 3 * m6.RATE
    rng = np.random.default_rng(20261110)
    t = np.arange(n) / m6.RATE
    h = np.array([.48, -.21, .12, -.035], dtype=np.float64)

    cal_reference = np.zeros(n, dtype=np.float64)
    a, b = round(.25 * m6.RATE), round(1.2 * m6.RATE)
    cal_reference[a:b] = rng.normal(0., .044, b - a)
    cal_mic = m6.predict_music(cal_reference, h)
    music_window = [(round(.3 * m6.RATE), round(1.1 * m6.RATE))]

    def tmpl(freq):
        out = np.zeros(m6.PRE + m6.POST)
        idx = np.arange(700)
        out[m6.PRE + 30:m6.PRE + 730] = (
            .055 * np.exp(-idx / 170) * np.cos(
                2 * np.pi * freq * idx / m6.RATE))
        return out

    mouse = tmpl(1700)
    keys = tmpl(2900)

    cal_events = []
    for kind, times, waveform in (
        ("mouse_press", [1.5, 1.8, 2.1], mouse),
        ("keyboard_press", [1.65, 1.95, 2.25], keys),
    ):
        for seconds in times:
            sample = round(seconds * m6.RATE)
            cal_mic[sample - m6.PRE:sample + m6.POST] += waveform
            cal_events.append({"kind": kind, "sample": sample})
    cal_events.sort(key=lambda e: e["sample"])

    eval_reference = np.zeros(n, dtype=np.float64)
    c, d = round(.55 * m6.RATE), round(2.85 * m6.RATE)
    eval_reference[c:d] = rng.normal(0., .045, d - c)
    voice = np.zeros(n, dtype=np.float64)
    va, vb = round(1.1 * m6.RATE), round(2.75 * m6.RATE)
    speech_t = t[va:vb]
    envelope = np.sin(np.linspace(0, np.pi, vb - va)) ** 2
    voice[va:vb] = (
        .028 * (np.sin(2 * np.pi * 180 * speech_t)
                + .25 * np.sin(2 * np.pi * 720 * speech_t))
        * envelope
    )
    eval_mic = m6.predict_music(eval_reference, h) + voice
    eval_events = [
        {"kind": "mouse_press", "sample": round(1.38 * m6.RATE)},
        {"kind": "keyboard_press", "sample": round(1.92 * m6.RATE)},
        {"kind": "mouse_press", "sample": round(2.30 * m6.RATE)},
    ]
    for ev in eval_events:
        waveform = mouse if ev["kind"] == "mouse_press" else keys
        sample = ev["sample"]
        eval_mic[sample - m6.PRE:sample + m6.POST] += waveform
    return (cal_mic, cal_reference, cal_events, music_window,
            eval_mic, eval_reference, eval_events, voice, h)


class M6PredictiveTests(unittest.TestCase):
    def test_speaker_and_event_estimates_recover_original_known_voice(self):
        (cm, cr, ce, windows, mic, ref, ev, voice, truth) = synth()
        m6.events_outside_windows(ce, windows)
        h, report = m6.train_music_fir(
            cm, cr, windows, taps=32, stride=8, ridge=1e-6)
        self.assertTrue(report["causal_filter"])
        self.assertLess(np.max(np.abs(h[:len(truth)]-truth)), .005)
        templates, info = m6.train_click_templates(cm, cr, ce)
        self.assertEqual(set(templates), {"mouse_press", "keyboard_press"})
        self.assertEqual(info["mouse_press"]["isolated_event_count"], 3)
        self.assertEqual(info["keyboard_press"]["isolated_event_count"], 3)
        tracks, metadata = m6.compensate(mic, ref, ev, h, templates)
        self.assertTrue(metadata["no_nearend_learning_during_doubletalk"])
        self.assertEqual(metadata["music_gain"]["update_count"], 0)
        self.assertEqual(metadata["clicks"]["predicted_event_count"],
                         {"mouse_press": 2, "keyboard_press": 1})
        residual_before = mic - voice
        residual_after = tracks["combined"] - voice
        before_rms = np.sqrt(np.mean(residual_before ** 2))
        after_rms = np.sqrt(np.mean(residual_after ** 2))
        self.assertLess(after_rms, .05 * before_rms)
        self.assertLess(
            np.max(np.abs(tracks["combined"][:int(.4*m6.RATE)]
                          - mic[:int(.4*m6.RATE)])), 1e-12)
        np.testing.assert_allclose(
            tracks["combined"],
            tracks["raw"] + tracks["anti_music"] + tracks["anti_clicks"],
            atol=2e-16, rtol=0)
        self.assertLess(
            np.sqrt(np.mean((tracks["combined"] - voice)**2)), .002)

    def test_explicit_acoustic_delay_restores_aligned_reference(self):
        rng = np.random.default_rng(12007)
        n = 3*m6.RATE
        reference = np.zeros(n)
        first = round(.2 * m6.RATE)
        last = round(2.3 * m6.RATE)
        reference[first:last] = rng.normal(
            0., .04, last - first)
        delayed = m6.delay_reference(reference, 480)
        mic = .55*delayed
        a, b = int(.5*m6.RATE), int(1.5*m6.RATE)
        h, _ = m6.train_music_fir(
            mic, delayed, [(a,b)], taps=24, ridge=1e-6)
        estimate = m6.predict_music(delayed, h)
        self.assertLess(
            np.sqrt(np.mean((estimate-mic)**2)), 0.00005)
        self.assertLess(abs(h[0]-.55), .003)
        np.testing.assert_array_equal(
            delayed[:480], np.zeros(480))
        with self.assertRaisesRegex(ValueError, "measured nonnegative"):
            m6.delay_reference(reference, -1)

    def test_gain_is_updated_after_certified_speech_free_window_only(self):
        n = 3 * m6.RATE
        predict = np.zeros(n)
        predict[:2 * m6.RATE] = .04
        mic = .6 * predict
        safe = [(0, m6.RATE)]
        gains, info = m6.causal_gain_curve(
            mic, predict, safe, events=[])
        self.assertGreater(info["update_count"], 0)
        self.assertAlmostEqual(gains[0], 1.)
        self.assertLess(gains[m6.RATE], 1.)
        self.assertAlmostEqual(gains[2*m6.RATE], gains[-1])
        with self.assertRaisesRegex(ValueError, "Events overlap"):
            m6.causal_gain_curve(mic, predict, safe, [
                {"kind": "mouse_press", "sample": m6.RATE // 2}
            ])

    def test_misregistered_or_privacy_leaking_event_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            path.write_text(
                '{"kind":"keyboard_press","sample":2000}\n'
                '{"kind":"mouse_press","sample":3100}\n')
            entries = m6.read_events(path, 48000)
            self.assertEqual(len(entries), 2)
            path.write_text(
                '{"kind":"keyboard_press","sample":3000,'
                '"key":"PASSWORD"}\n')
            with self.assertRaisesRegex(ValueError, "Invalid/private"):
                m6.read_events(path, 48000)
            path.write_text(
                '{"kind":"keyboard_press","monotonic_ns":9999}\n')
            with self.assertRaisesRegex(ValueError, "unsynchronized"):
                m6.read_events(path, 48000)

    def test_click_training_refuses_music_overlap_and_dense_events(self):
        cm, cr, ce, windows, *_ = synth()
        bad = [{"kind": "mouse_press", "sample": round(.5*m6.RATE)}]*3
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            m6.train_click_templates(cm, cr, bad)
        music = [
            {"kind": "mouse_press", "sample": round(.55*m6.RATE)},
            {"kind": "mouse_press", "sample": round(.75*m6.RATE)},
            {"kind": "mouse_press", "sample": round(.95*m6.RATE)},
        ]
        with self.assertRaisesRegex(ValueError, "Music was ON"):
            m6.train_click_templates(cm, cr, music)

    def test_peak_guard_raises_rather_than_hiding_failure_in_limiter(self):
        n = 2*m6.RATE
        mic = np.zeros(n)
        mic[2*m6.PRE:2*m6.PRE+20] = -.95
        ref = np.zeros(n)
        ref[2*m6.PRE:2*m6.PRE+20] = +.2
        with self.assertRaisesRegex(ValueError, "clip"):
            m6.compensate(mic, ref, [], np.array([1.]), {})

    def test_pcm16_write_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "safe.wav"
            samples = np.array([0., .5, -.5, .25], dtype=np.float64)
            m6.pcm16_write(path, samples)
            np.testing.assert_allclose(
                m6.pcm16_read(path), samples, atol=1/32768)
            with self.assertRaises(FileExistsError):
                m6.pcm16_write(path, samples)


if __name__ == "__main__":
    unittest.main()
