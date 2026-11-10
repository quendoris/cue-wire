"""Synthetic-only regression for M6 Physical-007 acoustic residual model.

No real voice, microphone device, key identity, event listening, or playback.
All waveforms and anonymous events below are invented for these tests.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np
from scipy import signal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import voice_m6_residual_comb as model


def synthetic_case():
    n=3*model.RATE
    t=np.arange(n)/model.RATE
    voice=(.024*np.sin(2*np.pi*185*t)
           +.008*np.sin(2*np.pi*370*t)
           +.004*np.sin(2*np.pi*925*t))
    mic=voice.copy()
    onset=round(1.2*model.RATE)
    pulse_len=round(.075*model.RATE)
    k=np.arange(pulse_len)
    click=.14*np.exp(-k/(model.RATE*.013))*np.sin(
        2*np.pi*4900*k/model.RATE)
    mic[onset:onset+pulse_len]+=click
    raw=np.rint(mic*32768).astype("<i2")
    mask=np.zeros(n,dtype=np.float32)
    mask[onset-round(.015*model.RATE):
         onset+round(.120*model.RATE)]=.9
    protection=np.ones(n,dtype=np.float64)
    zero=1_000_000_000_000
    event={
        "kind":"keyboard_press",
        "t_monotonic_ns":zero+round((1.2+.024)*1e9),
    }
    offsets={
        "keyboard_press":24, "keyboard_release":10,
        "mouse_press":19, "mouse_release":17,
    }
    return mic,raw,mask,protection,zero,event,offsets,onset


class ResidualKeyboardTests(unittest.TestCase):
    def test_pitch_guard_recognizes_stable_vowel(self):
        mic,_,_,_,_,_,_,_=synthetic_case()
        pitch,confident,metadata=model.pitch_protection(mic)
        self.assertGreater(float(np.mean(confident)),.65)
        self.assertTrue(metadata["not_validated_speech_activity_detection"])
        self.assertTrue(np.any((pitch>90)&(pitch<280)))

    def test_keyboard_gate_ignores_mouse_and_keeps_time_privacy(self):
        mic,_,mask,_,t0,event,offsets,onset=synthetic_case()
        mouse=dict(event,kind="mouse_press")
        only_mouse=model.keyboard_event_gate(
            mask,[mouse],offsets,t0)
        self.assertEqual(np.count_nonzero(only_mouse),0)
        keyboard=model.keyboard_event_gate(
            mask,[event],offsets,t0)
        self.assertGreater(float(keyboard[onset]),0.)
        self.assertTrue(np.all(keyboard[mask==0]==0))
        self.assertEqual(float(keyboard[0]),0.)
        illegal={**event,"keycode":"FORBIDDEN"}
        with self.assertRaisesRegex(ValueError,"Privacy-leaking"):
            model.keyboard_event_gate(mask,[illegal],offsets,t0)

    def test_residual_stft_has_correct_bounds_and_is_noncausal(self):
        mic,raw,mask,protection,t0,event,offsets,onset=synthetic_case()
        attack,tail,report=model.spectral_references(
            mic,mic,protection)
        self.assertEqual(attack.shape,mic.shape)
        self.assertEqual(tail.shape,mic.shape)
        self.assertTrue(report["future_samples_required"])
        self.assertFalse(report[
            "individual_keyboard_key_or_button_identity_read"])
        self.assertTrue(np.all(np.isfinite(attack)))
        self.assertTrue(np.all(np.isfinite(tail)))

    def test_processing_preserves_all_non_keyboard_samples(self):
        mic,raw,mask,protection,t0,event,offsets,onset=synthetic_case()
        attack,tail,_=model.spectral_references(
            mic,mic,protection)
        gate=model.keyboard_event_gate(
            mask,[event],offsets,t0)
        candidates=model.add_keyboard_only_corrections(
            raw,gate,attack,tail)
        self.assertEqual(set(candidates),set(model.GAINS))
        for track in candidates.values():
            np.testing.assert_array_equal(
                track[gate==0],raw[gate==0])
        def energy(pcm):
            band=signal.butter(
                4,(1300,9000),btype="bandpass",
                fs=model.RATE,output="sos")
            z=signal.sosfiltfilt(
                band,pcm[onset-480:onset+3800].astype(float)/32768.)
            return np.sqrt(np.mean(z*z))
        self.assertLess(
            energy(candidates["B_007_attack"]),energy(raw))
        self.assertLess(
            energy(candidates["C_007_attack_and_tail"]),
            energy(raw))

    def test_lowband_stays_near_original_in_voiced_click(self):
        mic,raw,mask,protection,t0,event,offsets,onset=synthetic_case()
        attack,tail,_=model.spectral_references(
            mic,mic,protection)
        gate=model.keyboard_event_gate(mask,[event],offsets,t0)
        out=model.add_keyboard_only_corrections(
            raw,gate,attack,tail)
        selected=gate>.5
        for name,pcm in out.items():
            error=model.protected_lowband_error_db(
                raw,pcm,selected)
            self.assertLess(error,-40.,name)

    def test_rejects_bad_pcm_masks_and_clipping(self):
        mic,raw,mask,_,t0,event,offsets,_=synthetic_case()
        zeros=np.zeros(len(raw))
        with self.assertRaisesRegex(ValueError,"Bad signed PCM16"):
            model.add_keyboard_only_corrections(
                raw.astype(float),mask,zeros,zeros)
        with self.assertRaisesRegex(ValueError,"Bad signed PCM16"):
            model.add_keyboard_only_corrections(
                raw,mask*2,zeros,zeros)
        huge=np.full(len(raw),2.0)
        with self.assertRaisesRegex(ValueError,"clip"):
            model.add_keyboard_only_corrections(
                raw,mask,huge,huge)

    def test_absent_keyboard_event_is_sample_exact(self):
        mic,raw,mask,protection,t0,event,offsets,_=synthetic_case()
        empty=model.keyboard_event_gate(mask,[],offsets,t0)
        zero=np.zeros(len(raw))
        out=model.add_keyboard_only_corrections(
            raw,empty,zero,zero)
        for pcm in out.values():
            np.testing.assert_array_equal(pcm,raw)


if __name__=="__main__":
    unittest.main()
