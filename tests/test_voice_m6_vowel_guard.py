"""Synthetic M6 vowel continuity: no private voice, codes or recordings."""
from __future__ import annotations
import unittest
import numpy as np
import voice_m6_vowel_guard as g


def synthetic():
    sr=g.RATE
    t=np.arange(sr*12)/sr
    rng=np.random.default_rng(3657)
    x=.00035*np.sin(2*np.pi*210*t)+rng.normal(0,.00017,len(t))
    # Keyboard click-like NOISE in isolated speech-free regions
    for sec in (3.8,4.5,5.2,5.8,7.0,8.5):
        n=np.arange(g.RATE//25)
        at=round(sec*sr)
        x[at:at+len(n)]+=.06*np.exp(-n/(sr*.012))*np.sin(2*np.pi*5300*n/sr)
    # Long vowel in R4 and R5; last click overlaps its DECAYING END.
    a,b=round(9.1*sr),round(10.3*sr)
    x[a:b]+=.035*np.sin(2*np.pi*180*np.arange(b-a)/sr)
    a,b=round(10.7*sr),round(11.45*sr)
    x[a:b]+=.055*np.sin(2*np.pi*190*np.arange(b-a)/sr)
    for sec in (11.35,11.44,11.5):
        n=np.arange(g.RATE//25)
        at=round(sec*sr)
        x[at:at+len(n)]+=.06*np.exp(-n/(sr*.012))*np.sin(2*np.pi*5300*n/sr)
    zero=1_000_000_000_000
    ranges=[('quiet_baseline',0,.4),('mouse_isolated',.4,3),('keyboard_isolated',3,6.5),('both_holdout',6.5,9),('voice_only_holdout',9,10.5),('voice_clicks_holdout',10.5,12)]
    phases=[{'phase':name,'start_monotonic_ns':zero+round(a*1e9),'end_monotonic_ns':zero+round(b*1e9)}for name,a,b in ranges]
    return x,{'session_start_monotonic_ns':zero,'phases':phases}


class VowelGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mic,cls.man=synthetic()
        cls.protect,cls.lock,cls.metadata=g.voicing_protection(cls.mic,cls.man)

    def test_active_vowel_and_last_phoneme_are_locked(self):
        self.assertTrue(np.all(self.lock[int(11.10*g.RATE):int(11.51*g.RATE)]))
        self.assertTrue(np.all(self.lock[int(11.51*g.RATE):int(11.60*g.RATE)]))
        self.assertTrue(np.all(self.protect[self.lock] == 1.0))

    def test_keyboard_only_not_mistaken_for_long_vowel(self):
        self.assertLess(float(np.mean(self.lock[3*g.RATE:9*g.RATE])),.02)
        self.assertEqual(float(np.mean(self.protect[7*g.RATE:8*g.RATE])),0.0)

    def test_harmonic_lock_keeps_pcm_bit_exact_even_during_click(self):
        raw=np.rint(self.mic*32768).astype('<i2')
        # A deliberately speech-damaging aggressive algorithm:
        # every sample is 35% quieter, including the ending of the vowel.
        aggressive=np.rint(.65*self.mic*32768).astype('<i2')
        wet=1-self.protect
        result=g.integer_dry_wet(raw,aggressive,wet)
        np.testing.assert_array_equal(result[self.lock],raw[self.lock])
        self.assertTrue(np.array_equal(result[11*g.RATE:round(11.5*g.RATE)],raw[11*g.RATE:round(11.5*g.RATE)]))

    def test_10_percent_hybrid_is_bounded_and_bypassed_edges(self):
        raw=np.array([300,-300,110,0],dtype='<i2')
        wet=np.array([1.,0.1,0.,0.5])
        aggressive=np.array([100,-100,-150,-50],dtype='<i2')
        out=g.integer_dry_wet(raw,aggressive,wet)
        np.testing.assert_array_equal(out,np.array([100,-280,110,-25],dtype='<i2'))

    def test_rejects_invalid_wet_and_pcm(self):
        raw=np.array([1,2],dtype='<i2')
        with self.assertRaisesRegex(ValueError,'PCM16'):
            g.integer_dry_wet(raw,raw,np.array([0.,1.1]))
        with self.assertRaisesRegex(ValueError,'PCM16'):
            g.integer_dry_wet(raw,np.array([0.,1.]),np.array([0.,.2]))

    def test_no_voice_yields_zero_protection_even_at_file_boundary(self):
        rng=np.random.default_rng(250)
        n=8*g.RATE
        x=rng.normal(0,.0004,n)
        t0=1_000_000_000_000
        bounds=[
            ('mouse_isolated',0,2),('keyboard_isolated',2,4),
            ('both_holdout',4,6),('voice_clicks_holdout',6,8)]
        manifest={'session_start_monotonic_ns':t0,
                  'phases':[{'phase':k,
                             'start_monotonic_ns':t0+round(a*1e9),
                             'end_monotonic_ns':t0+round(b*1e9)}
                            for k,a,b in bounds]}
        protected,locked,_=g.voicing_protection(x,manifest)
        self.assertFalse(np.any(locked))
        self.assertEqual(int(np.count_nonzero(protected)),0)

    def test_training_only_speech_free_and_metadata(self):
        self.assertEqual(self.metadata['speech_free_calibration_phases'],['mouse_isolated','keyboard_isolated'])
        self.assertEqual(self.metadata['post_hold_ms'],220)
        self.assertFalse(self.metadata['real_time_use_certified'])

if __name__=='__main__':unittest.main()
