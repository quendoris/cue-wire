"""M6 physical replay DSP math tests (no private recordings or devices)."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import voice_m6_physical_replay as m6
from voice_m6_event_anc import predict_music


def synthetic_acoustic_case():
    n = 44*m6.RATE
    rng = np.random.default_rng(20261110)
    reference = np.zeros(n, dtype=np.float64)
    reference[11*m6.RATE:35*m6.RATE] = rng.normal(
        0., .05, 24*m6.RATE)
    impulse = np.zeros(600)
    impulse[0] = .41
    impulse[5] = -.14
    impulse[215] = .09
    impulse[480] = .075
    echo = predict_music(reference, impulse)
    # The speaker gain changes partway through the speech-free calibration.
    gain = np.ones(n)
    gain[20*m6.RATE:] = 1.20
    mic = echo*gain
    for t in (13.2, 14.7, 16.4, 21.1, 22.9, 24.2, 29.2, 31.3):
        n0 = round(t*m6.RATE)
        mic[n0:n0+100] += rng.normal(0., .15, 100)
    # Clean voice-only reference stage: the farend has already stopped.
    tt = np.arange(37*m6.RATE, 43*m6.RATE) / m6.RATE
    mic[37*m6.RATE:43*m6.RATE] = (
        .03*np.sin(2*np.pi*210*tt)
        + .006*np.sin(2*np.pi*830*tt))
    return mic, reference, echo, gain


class M6PhysicalReplayTests(unittest.TestCase):
    def test_frozen_fir_training_isolated_from_heldout_and_clean(self):
        mic, ref, truth, gains = synthetic_acoustic_case()
        train = m6.interval(m6.TRAIN,len(mic))
        fir, meta = m6.fit_frozen_physical(mic,ref,train)
        self.assertEqual(len(fir),5760)
        self.assertTrue(meta["frozen_during_real_doubletalk"])
        pred = predict_music(ref,fir)
        a,b = m6.interval(m6.TRAIN,len(mic))
        e0 = np.sqrt(np.mean(mic[a:b]**2))
        e1 = np.sqrt(np.mean((mic[a:b]-pred[a:b])**2))
        self.assertLess(e1,e0*.30)
        # Nothing about training depends on the voice-only holdout.
        modified = mic.copy()
        modified[37*m6.RATE:43*m6.RATE] *= .1
        fir2,_ = m6.fit_frozen_physical(modified,ref,train)
        np.testing.assert_array_equal(fir, fir2)
        clean_start,clean_end = m6.interval(m6.CLEAN,len(mic))
        np.testing.assert_array_equal(
            m6.exact_pcm16(mic[clean_start:clean_end]),
            m6.exact_pcm16((mic-pred)[clean_start:clean_end]))

    def test_causal_amplitude_updates_only_before_heldout(self):
        mic, ref, truth, gains = synthetic_acoustic_case()
        fir,_ = m6.fit_frozen_physical(
            mic,ref,m6.interval(m6.TRAIN,len(mic)))
        p = predict_music(ref,fir)
        curve,info = m6.gain_update_physical(
            mic,p,m6.interval(m6.ADAPT,len(mic)))
        self.assertTrue(info["gain_update_uses_only_no_voice_phase"])
        self.assertFalse(info["heldout_28_34_used_for_fit"])
        self.assertGreater(info["gain_updates_accepted"],5)
        # The dynamic level rises in the no-voice stage, THEN freezes.
        self.assertGreater(curve[28*m6.RATE],1.05)
        self.assertAlmostEqual(curve[28*m6.RATE],curve[42*m6.RATE])
        a,b = m6.interval(m6.HELDOUT,len(mic))
        frozen_error = np.sqrt(np.mean((mic[a:b]-p[a:b])**2))
        adaptive_error = np.sqrt(np.mean(
            (mic[a:b]-p[a:b]*curve[a:b])**2))
        self.assertLess(adaptive_error, frozen_error)
        # Editing ANY future doubletalk cannot influence earlier gains.
        modified = mic.copy()
        modified[28*m6.RATE:] += np.random.default_rng(42).normal(
            0.,.1,len(mic)-28*m6.RATE)
        curve2,_ = m6.gain_update_physical(
            modified,p,m6.interval(m6.ADAPT,len(mic)))
        np.testing.assert_array_equal(curve,curve2)

    def test_annotated_speaker_only_gate_rejects_voice_and_boundaries(self):
        phase={
            "start_since_recorder_launch_s":10.,
            "end_since_recorder_launch_s":19.,
            "clean_voice_reference":False,
        }
        self.assertTrue(m6.in_phase(phase,m6.TRAIN))
        self.assertFalse(m6.in_phase(phase,(9.,18.)))
        self.assertFalse(m6.in_phase({
            **phase,"clean_voice_reference":True},m6.TRAIN))
        self.assertFalse(m6.in_phase(phase,(12.,19.)))

    def test_explicit_input_failure_guards(self):
        with self.assertRaisesRegex(ValueError,"Invalid"):
            m6.gain_update_physical(np.ones(10),np.ones(9),(0,9))
        with self.assertRaisesRegex(ValueError,"Only frozen"):
            m6.fit_frozen_physical(
                np.ones(6*m6.RATE),np.ones(6*m6.RATE),
                (0,m6.RATE),taps=4)
        with self.assertRaisesRegex(ValueError,"outside"):
            m6.interval((99.,100.),44*m6.RATE)


if __name__=="__main__":
    unittest.main()
