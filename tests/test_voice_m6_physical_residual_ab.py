"""Synthetic integration tests for Physical-007 frozen C006 comparator.

No real recordings, user event timestamps, keyboard codes or playback.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import voice_m6_physical_residual_ab as pipeline


class PhysicalResidualExportTests(unittest.TestCase):
    def make_case(self):
        n=3*pipeline.RATE
        t=np.arange(n)/pipeline.RATE
        mic=.024*np.sin(2*np.pi*185*t)
        raw=np.rint(mic*32768).astype("<i2")
        c006=raw.copy()
        # Simulate a previous trusted modification ONLY inside its event mask.
        mask=np.zeros(n,dtype=np.float32)
        start=round(1.1*pipeline.RATE)
        stop=start+round(.12*pipeline.RATE)
        mask[start:stop]=.9
        c006[start:stop]-=5
        phase=1_000_000_000_000
        events=[{
            "kind":"keyboard_press",
            "t_monotonic_ns":phase+round(1.124e9),
        }]
        offsets={
            "keyboard_press":24,"keyboard_release":10,
            "mouse_press":19,"mouse_release":17,
        }
        manifest={"session_start_monotonic_ns":phase}
        protection=np.ones(n)
        locked=np.ones(n,dtype=bool)
        returns=({
            "C_006_percussive_strong":c006,
        },raw,mask,protection,locked,{"synthetic":True})
        return mic,raw,mask,events,offsets,manifest,returns

    def test_favorite_C006_is_exact_frozen_A(self):
        mic,raw,mask,events,offsets,manifest,previous=self.make_case()
        with patch.object(pipeline.previous006,"build_options",
                          return_value=previous):
            options,source,gate,guard,locked,info=(
                pipeline.prepare_tracks(
                    mic,manifest,events,offsets,{}))
        np.testing.assert_array_equal(
            options[pipeline.A],previous[0][
                "C_006_percussive_strong"])
        self.assertEqual(set(options),
                         {pipeline.A,*pipeline.GAINS})
        self.assertTrue(info["preferred_006_C_is_frozen"])
        self.assertFalse(info[
            "individual_key_or_button_id_accessed"])
        for name in pipeline.GAINS:
            np.testing.assert_array_equal(
                options[name][gate==0],options[pipeline.A][gate==0])
            np.testing.assert_array_equal(
                options[name][mask==0],raw[mask==0])

    def test_mouse_only_additional_mask_is_zero(self):
        mic,raw,mask,events,offsets,manifest,previous=self.make_case()
        events[0]["kind"]="mouse_press"
        with patch.object(pipeline.previous006,"build_options",
                          return_value=previous):
            options,_,gate,*_=pipeline.prepare_tracks(
                mic,manifest,events,offsets,{})
        self.assertFalse(np.any(gate))
        for candidate in options.values():
            np.testing.assert_array_equal(
                candidate,options[pipeline.A])

    def test_fails_on_untrusted_old_reference_outside_mask(self):
        mic,raw,mask,events,offsets,manifest,previous=self.make_case()
        previous[0]["C_006_percussive_strong"][1]+=1
        with patch.object(pipeline.previous006,"build_options",
                          return_value=previous):
            with self.assertRaisesRegex(RuntimeError,
                                        "off-event PCM"):
                pipeline.prepare_tracks(
                    mic,manifest,events,offsets,{})


if __name__=="__main__":
    unittest.main()
