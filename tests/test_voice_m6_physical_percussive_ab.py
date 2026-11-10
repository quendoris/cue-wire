"""Synthetic-only harmonic/percussive M6 Physical-006 regression tests.

No real microphone WAV, actual event logs, keyboard codes, or live devices.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
from scipy import signal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import voice_m6_percussive_hpss as hp
import voice_m6_physical_percussive_ab as full


def synthetic_speech_click():
    rate=hp.RATE
    n=3*rate
    time=np.arange(n,dtype=np.float64)/rate
    voice=.04*np.sin(2*np.pi*180*time)
    voice+=.012*np.sin(2*np.pi*360*time)
    mixed=voice.copy()
    start=round(1.4*rate)
    samples=round(.08*rate)
    ramp=np.arange(samples)
    click=.14*np.exp(-ramp/(rate*.012))*np.sin(
        2*np.pi*4800*ramp/rate)
    mixed[start:start+samples]+=click
    mic=mixed.astype(np.float64)
    raw=np.rint(mic*32768.).astype('<i2')
    mask=np.zeros(n,dtype=np.float32)
    fade=round(.006*rate)
    width=round(.105*rate)
    event_start=start-fade
    taper=np.ones(width,dtype=np.float32)
    taper[:fade]=np.sin(np.linspace(0,np.pi/2,fade))**2
    taper[-fade:]=np.cos(np.linspace(0,np.pi/2,fade))**2
    mask[event_start:event_start+width]=taper
    protection=np.ones(n,dtype=np.float64)
    return mic,raw,mask,protection,start


class PercussivePilotTests(unittest.TestCase):
    def test_reference_detects_broadband_click_and_protects_low_harmonics(self):
        mic,raw,mask,protection,start=synthetic_speech_click()
        percussive,info=hp.harmonic_percussive_reference(mic)
        self.assertTrue(info["future_samples_required"])
        self.assertFalse(info["key_or_button_identity_required"])
        outputs=hp.add_event_limited_reference(
            raw,raw,percussive,mask,protection)
        for name in hp.GAINS:
            np.testing.assert_array_equal(
                outputs[name][mask==0],raw[mask==0])
        selected=(mask>.1)
        lower=hp.lowband_delta_db(
            raw,outputs["C_006_percussive_strong"],selected)
        self.assertLess(lower,-40.)
        sos=signal.butter(4,(1300,9000),btype="bandpass",
                          fs=hp.RATE,output="sos")
        a,b=start-240,start+3600
        def high(pcm):
            v=signal.sosfiltfilt(
                sos,pcm[a:b].astype(float)/32768.)
            return np.sqrt(np.mean(v*v))
        self.assertLess(
            high(outputs["C_006_percussive_strong"]),
            high(raw)*.85)

    def test_both_new_options_keep_event_free_pcm16_bit_exact(self):
        mic,raw,mask,protection,_=synthetic_speech_click()
        ref,_=hp.harmonic_percussive_reference(mic)
        out=hp.add_event_limited_reference(
            raw,raw,ref,mask,protection)
        for pcm in out.values():
            np.testing.assert_array_equal(pcm[mask==0],raw[mask==0])

    def test_no_voiced_signal_does_not_change_successful_005(self):
        mic,raw,mask,_,_=synthetic_speech_click()
        ref,_=hp.harmonic_percussive_reference(mic)
        dry=np.zeros(len(raw),dtype=float)
        out=hp.add_event_limited_reference(raw,raw,ref,mask,dry)
        for pcm in out.values():
            np.testing.assert_array_equal(pcm,raw)

    def test_bad_event_masks_or_wrong_pcm_dtype_are_rejected(self):
        mic,raw,mask,protection,_=synthetic_speech_click()
        sig=np.zeros_like(mic)
        with self.assertRaisesRegex(ValueError,"PCM16"):
            hp.add_event_limited_reference(
                raw.astype(float),raw,sig,mask,protection)
        with self.assertRaisesRegex(ValueError,"PCM16"):
            hp.add_event_limited_reference(
                raw,raw,sig,mask*2.,protection)
        corrupted=raw.copy()
        corrupted[0]+=1
        with self.assertRaisesRegex(ValueError,"outside events"):
            hp.add_event_limited_reference(
                raw,corrupted,sig,mask,protection)

    def test_stronger_candidate_never_auto_amplifies_or_clips(self):
        mic,raw,mask,protection,_=synthetic_speech_click()
        huge=np.full(len(mic),2.,dtype=float)
        with self.assertRaisesRegex(ValueError,"clip"):
            hp.add_event_limited_reference(
                raw,raw,huge,mask,protection)

    def test_integration_preserves_exact_favorite_005_variant(self):
        mic,raw,mask,protection,_=synthetic_speech_click()
        safe=raw.copy()
        safe[mask>0]=np.rint(
            safe[mask>0].astype(float)*.97).astype('<i2')
        locked=protection.astype(bool)
        old={"C_005_vowel_soft":safe}
        with patch.object(full.vowel,"make_vowel_tracks",
                          return_value=(old,raw,mask,protection,locked,{})):
            tracks,r,m,p,l,metadata=full.build_options(
                mic,{},[],{}, {})
        np.testing.assert_array_equal(
            tracks["A_005_vowel_soft"],safe)
        self.assertEqual(set(tracks),{
            "A_005_vowel_soft",
            "B_006_percussive",
            "C_006_percussive_strong"})
        for candidate in tracks.values():
            np.testing.assert_array_equal(
                candidate[mask==0],raw[mask==0])
        self.assertFalse(metadata[
            "external_key_button_code_read"])


if __name__=="__main__":
    unittest.main()
