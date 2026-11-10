"""Synthetic-only M6 physical click A/B unit tests; no real recording or keycodes."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

import numpy as np
from scipy import signal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import voice_m6_physical_click_ab as pilot


def make_impulse_case():
    rate=pilot.RATE
    n=rate*3
    stamp0=1_000_000_000_000
    t=np.arange(n)/rate
    lowvoice=.02*np.sin(2*np.pi*170*t)
    mic=lowvoice.copy()
    event_second=1.20
    offset_ms=19
    click_center=round((event_second-offset_ms/1000.)*rate)
    kk=np.arange(-240,481)
    pulse=.13*np.exp(-((kk/95.)**2))*np.sin(
        2*np.pi*4700*kk/rate)
    mic[click_center-240:click_center+481]+=pulse
    manifest={"session_start_monotonic_ns":stamp0}
    events=[{"kind":"mouse_press",
             "t_monotonic_ns":stamp0+round(event_second*1e9)}]
    offsets={
        "mouse_press":19,"mouse_release":17,
        "keyboard_press":24,"keyboard_release":10}
    return mic,manifest,events,offsets,click_center


class PhysicalClickPilotTests(unittest.TestCase):
    def test_click_trigger_is_short_and_preserves_outside_samples(self):
        mic,manifest,events,offsets,center=make_impulse_case()
        mask,band,details=pilot.sample_mask(
            mic,manifest,events,offsets)
        self.assertEqual(
            details["accepted_events_by_class"]["mouse_press"],1)
        self.assertTrue(details["event_identity_accessed"] is False)
        self.assertTrue(details["future_samples_required"])
        self.assertGreater(mask[center],0)
        self.assertLess(int(np.count_nonzero(mask)),600)
        original=pilot.to_pcm16(mic)
        conservative=pilot.to_pcm16(
            mic-pilot.GAINS["B_conservative"]*mask*band)
        strong=pilot.to_pcm16(
            mic-pilot.GAINS["C_stronger"]*mask*band)
        np.testing.assert_array_equal(
            original[mask==0],strong[mask==0])
        np.testing.assert_array_equal(
            original[mask==0],conservative[mask==0])
        sos=signal.butter(
            4,[pilot.LOW_HZ,pilot.HIGH_HZ],
            fs=pilot.RATE,btype="bandpass",output="sos")
        def high_rms(s):
            z=signal.sosfiltfilt(
                sos,s.astype("float64")/32768.)
            a,b=center-480,center+480
            return np.sqrt(np.mean(z[a:b]**2))
        self.assertLess(high_rms(strong),
                        high_rms(original)*.9)
        self.assertLess(high_rms(conservative),
                        high_rms(original))

    def test_final_phase_175ms_shortfall_is_bounded_not_fabricated(self):
        # Physical M6 WAV duration 44.330667 s; last logged phase ends
        # 44.50545 s. The last scene may only contain REAL samples.
        begin=round(35.50448*pilot.RATE)
        requested=round(44.50545*pilot.RATE)
        total=round(44.330667*pilot.RATE)
        phase={"start_monotonic_ns":1000000000000+round(
                   35.50448*1e9),
               "end_monotonic_ns":1000000000000+round(
                   44.50545*1e9)}
        a,b=pilot.phase_bounds(phase,1000000000000,total)
        self.assertEqual(a,begin)
        self.assertEqual(b,total)
        self.assertGreater(requested,b)
        phase["end_monotonic_ns"]+=round(.5*1e9)
        with self.assertRaisesRegex(ValueError,"does not fit"):
            pilot.phase_bounds(phase,1000000000000,total)

    def test_silent_event_and_wrong_offsets_are_rejected(self):
        mic,manifest,events,offsets,_=make_impulse_case()
        x=np.zeros_like(mic)
        mask,_,report=pilot.sample_mask(
            x,manifest,events,offsets)
        self.assertEqual(report[
            "accepted_events_by_class"]["mouse_press"],0)
        self.assertFalse(np.any(mask))
        invalid={**offsets,"mouse_press":300}
        with self.assertRaisesRegex(ValueError,"acoustic offsets"):
            pilot.sample_mask(mic,manifest,events,invalid)

    def test_clipping_refusal_and_wav_exclusive_creation(self):
        with self.assertRaisesRegex(ValueError,"clipped"):
            pilot.to_pcm16(np.array([.99]))
        pcm=pilot.to_pcm16(np.array([0.,.125,-.125]))
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/"test.wav"
            pilot.wav_write(file,pcm)
            with wave.open(str(file),"rb") as w:
                self.assertEqual(w.getnchannels(),1)
                self.assertEqual(w.getframerate(),48000)
                self.assertEqual(w.readframes(3),pcm.tobytes())
            with self.assertRaises(FileExistsError):
                pilot.wav_write(file,pcm)

    def test_speech_without_timed_events_is_byte_exact(self):
        mic,manifest,events,offsets,_=make_impulse_case()
        mask,band,report=pilot.sample_mask(
            mic,manifest,[],offsets)
        original=pilot.to_pcm16(mic)
        changed=pilot.to_pcm16(mic-.75*mask*band)
        np.testing.assert_array_equal(original,changed)
        self.assertEqual(report["mask_changed_samples"],0)


if __name__=="__main__":
    unittest.main()
