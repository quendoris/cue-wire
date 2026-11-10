"""M6 Physical-005: synthetic regression of vowel PCM bypass with old M6-004.

All test WAV and event markers are fabricated. Nothing is captured,
played, transmitted or saved; no actual key/button identities are used.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import voice_m6_physical_vowel_ab as trial


class PhysicalVowelABTests(unittest.TestCase):
    def make_case(self):
        n=3*trial.RATE
        pcm=np.arange(n,dtype=np.int32)%16000
        raw=pcm.astype('<i2')
        mic=raw.astype(np.float64)/32768.
        click_mask=np.zeros(n,dtype=np.float32)
        first,second=40000,80000
        click_mask[first:first+500]=1.
        click_mask[second:second+500]=1.
        old_aggressive=raw.copy()
        old_guard=raw.copy()
        old_aggressive[click_mask>0]-=120
        old_guard[click_mask>0]-=50
        protection=np.zeros(n,dtype=np.float64)
        protected=np.zeros(n,dtype=bool)
        protected[second-1000:second+2000]=True
        protection[protected]=1.
        result={
            'B_004_aggressive':old_aggressive,
            'C_004_voice_guard':old_guard,
        }
        metadata={'vowel_core_is_accurately_frozen':True}
        return mic,raw,click_mask,protection,protected,result,metadata,first,second

    def test_new_b_keeps_detected_vowel_exact_but_clicks_outside_unchanged(self):
        mic,raw,mask,guard,locked,oldtracks,meta,first,second=self.make_case()
        with patch.object(trial.old,'make_tracks',
                          return_value=(oldtracks,raw,mask,{})), \
             patch.object(trial,'voicing_protection',
                          return_value=(guard,locked,meta)):
            tracks,original,newmask,protect,newlock,report=trial.make_vowel_tracks(
                mic,{},[],{},{})

        np.testing.assert_array_equal(
            tracks['B_005_vowel_lock'][locked],raw[locked])
        np.testing.assert_array_equal(
            tracks['B_005_vowel_lock'][first:first+500],
            oldtracks['B_004_aggressive'][first:first+500])
        np.testing.assert_array_equal(
            tracks['A_004_voice_guard'],oldtracks['C_004_voice_guard'])
        for candidate in tracks.values():
            np.testing.assert_array_equal(candidate[mask==0],raw[mask==0])
        self.assertTrue(report['protected_voice_core_has_exact_source_pcm16'])
        self.assertFalse(report['user_key_or_button_identity_accessed'])

    def test_c_limits_aggressive_correction_to_10_percent_in_vowel(self):
        mic,raw,mask,guard,locked,oldtracks,meta,first,second=self.make_case()
        with patch.object(trial.old,'make_tracks',
                          return_value=(oldtracks,raw,mask,{})), \
             patch.object(trial,'voicing_protection',
                          return_value=(guard,locked,meta)):
            tracks,*_=trial.make_vowel_tracks(mic,{},[],{}, {})
        region=slice(second,second+500)
        diff=tracks['C_005_vowel_soft'][region].astype(int)-raw[region].astype(int)
        self.assertTrue(np.all(diff==-12))
        self.assertTrue(np.array_equal(
            tracks['C_005_vowel_soft'][first:first+500],
            oldtracks['B_004_aggressive'][first:first+500]))

    def test_refuse_bad_protection_alignment(self):
        mic,raw,mask,guard,locked,oldtracks,meta,*_=self.make_case()
        with patch.object(trial.old,'make_tracks',
                          return_value=(oldtracks,raw,mask,{})), \
             patch.object(trial,'voicing_protection',
                          return_value=(guard[:-1],locked[:-1],meta)):
            with self.assertRaisesRegex(RuntimeError,'sample aligned'):
                trial.make_vowel_tracks(mic,{},[],{}, {})

    def test_protection_is_offline_and_not_validated_speech_truth(self):
        self.assertEqual(trial.PARTIAL_VOICED_ANTI_FRACTION,.1)
        self.assertIn('R5_voice_clicks_holdout',trial.SCENES)
        self.assertIn('R3_both_holdout',trial.SCENES)


if __name__=='__main__':
    unittest.main()
