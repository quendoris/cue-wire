"""Synthetic-only M6 Physical-004 aggressive anti-click regression coverage.

No physical mic, input device, PipeWire, key code or playback is accessed.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np
from scipy import signal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import voice_m6_physical_aggressive_ab as m6


def isolated_synthetic():
    n=12*m6.RATE
    t=np.arange(n)/m6.RATE
    # Speech-free calibration carries a very weak lowband room hum.
    x=.001*np.sin(2*np.pi*210*t)
    x[round(9*m6.RATE):round(11*m6.RATE)]+=.025*np.sin(
        2*np.pi*190*np.arange(2*m6.RATE)/m6.RATE)
    t0=1_000_000_000_000
    phases=[]
    for name,a,b in (
        ("quiet_baseline",0,.5),
        ("mouse_isolated",.5,3),
        ("keyboard_isolated",3,7),
        ("both_holdout",7,9),
        ("voice_only_holdout",9,11),
        ("voice_clicks_holdout",11,12),
    ):
        phases.append({
            "phase":name,
            "start_monotonic_ns":t0+round(a*1e9),
            "end_monotonic_ns":t0+round(b*1e9),
        })
    manifest={"session_start_monotonic_ns":t0,"phases":phases}
    offsets={"mouse_press":19,"mouse_release":17,
             "keyboard_press":24,"keyboard_release":10}
    events=[]
    kinds=[
        ("mouse_press",[1.,1.5,2.],.016),
        ("mouse_release",[1.15,1.65,2.15],.012),
        ("keyboard_press",[3.6,4.4,5.2,6.],.042),
        ("keyboard_release",[3.8,4.6,5.4,6.2],.038),
        ("keyboard_press",[7.2,7.7,8.2,8.7],.042),
    ]
    for kind,seconds,amp in kinds:
        for second in seconds:
            start=round(second*m6.RATE)
            length=m6.ms(110)
            pulse=amp*np.exp(
                -np.arange(length)/(m6.RATE*.024))*np.sin(
                2*np.pi*5100*np.arange(length)/m6.RATE)
            x[start:start+length]+=pulse
            events.append({
                "kind":kind,
                "t_monotonic_ns":t0+round(
                    (second+offsets[kind]/1000.)*1e9),
            })
    events.sort(key=lambda row:row["t_monotonic_ns"])
    return x,manifest,events,offsets


class AggressivePhysicalAntiWaveTests(unittest.TestCase):
    def test_mask_is_anchored_and_extends_beyond_old_eight_ms(self):
        envelope=np.full(5*m6.RATE,.00007,dtype=float)
        peak=m6.RATE
        tail=np.arange(m6.ms(100))
        envelope[peak:peak+len(tail)]+=.025*np.exp(
            -tail/(m6.RATE*.022))
        t0=1_000_000_000_000
        events=[{"kind":"keyboard_press",
                 "t_monotonic_ns":t0+1_024_000_000}]
        mask,record=m6.aggressive_mask(
            {"session_start_monotonic_ns":t0},
            events,
            {"mouse_press":19,"mouse_release":17,
             "keyboard_press":24,"keyboard_release":10},
            envelope,{"mouse":41,"keyboard":59})
        self.assertEqual(record["detected_by_class"]["keyboard_press"],1)
        self.assertGreater(np.count_nonzero(mask),m6.ms(30))
        self.assertGreater(mask[peak+m6.ms(20)],.3)
        self.assertEqual(float(mask[peak-m6.ms(20)]),0.)
        self.assertEqual(float(mask[peak+m6.ms(180)]),0.)
        self.assertFalse(record["key_or_button_identity_collected"])

    def test_does_not_read_identity_and_rejects_extra_input_fields(self):
        envelope=np.full(3*m6.RATE,.0001)
        event={"kind":"keyboard_press",
               "t_monotonic_ns":1_000_000_000_000+2_024_000_000,
               "key_code":"PRIVATE"}
        offsets={k:0 for k in m6.KINDS}
        with self.assertRaisesRegex(ValueError,"event fields"):
            m6.aggressive_mask(
                {"session_start_monotonic_ns":1_000_000_000_000},
                [event],offsets,envelope,
                {"mouse":41,"keyboard":59})
        with self.assertRaisesRegex(ValueError,"training"):
            m6.aggressive_mask(
                {"session_start_monotonic_ns":1_000_000_000_000},
                [],offsets,envelope,
                {"keyboard":59})

    def test_full_synthetic_antiwave_is_stronger_and_speech_off_event_exact(self):
        mic,manifest,events,offsets=isolated_synthetic()
        tracks,original,mask,info=m6.make_tracks(
            mic,manifest,events,offsets,
            {"mouse":41,"keyboard":59})
        self.assertTrue(info["new_event_mask_has_greater_coverage"])
        self.assertGreater(np.count_nonzero(mask),0)
        for name in ("B_004_aggressive","C_004_voice_guard"):
            np.testing.assert_array_equal(
                original[mask==0],tracks[name][mask==0])
        # Compare event-rich keyboard-only interval rather than the
        # unchanged voice-only phase; no clean-speech truth implied.
        a,b=3*m6.RATE,7*m6.RATE
        sos=signal.butter(4,m6.SCORE_BAND,
                          btype="bandpass",fs=m6.RATE,output="sos")
        def bandrms(pcm):
            z=signal.sosfiltfilt(
                sos,pcm[a:b].astype(float)/32768.)
            return np.sqrt(np.mean(z*z))
        self.assertLess(
            bandrms(tracks["B_004_aggressive"]),
            bandrms(tracks["A_003_adaptive"]))
        # No detected event in the synthetic voice-only segment.
        va,vb=9*m6.RATE,11*m6.RATE
        for name,pcm in tracks.items():
            np.testing.assert_array_equal(
                pcm[va:vb],original[va:vb])

    def test_refuse_empty_or_bad_calibration_or_out_of_range_pcm(self):
        mic,manifest,events,offsets=isolated_synthetic()
        _,envelope=m6.previous.envelope(mic,m6.SCORE_BAND)
        with self.assertRaisesRegex(ValueError,"No measurable"):
            m6.aggressive_mask(
                manifest,[],offsets,envelope,
                {"mouse":41,"keyboard":59})
        with self.assertRaisesRegex(ValueError,"unsafe/clipped"):
            m6.to_pcm16(np.array([.99]))
        wrong={**offsets,"keyboard_press":200}
        with self.assertRaises(ValueError):
            m6.aggressive_mask(
                manifest,events,wrong,envelope,
                {"mouse":41,"keyboard":59})

    def test_non_event_silence_is_never_replaced(self):
        mic,manifest,events,offsets=isolated_synthetic()
        tracks,original,mask,_=m6.make_tracks(
            mic,manifest,events,offsets,
            {"mouse":41,"keyboard":59})
        self.assertEqual(np.count_nonzero(mask[:m6.RATE//2]),0)
        for role,track in tracks.items():
            np.testing.assert_array_equal(
                original[:m6.RATE//2],track[:m6.RATE//2])


if __name__=="__main__":
    unittest.main()
