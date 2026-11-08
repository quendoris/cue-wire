"""Synthetic smoke tests: do not claim neural enhancement quality."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import wave
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'tools/voice_m4_clean.py'

class CleanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'tools').mkdir()
        shutil.copyfile(SOURCE, root / 'tools/voice_m4_clean.py')
        spec = importlib.util.spec_from_file_location('m4_clean', root / 'tools/voice_m4_clean.py')
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.raw = root / '.audit/voice-captures/M4-R0-ALSA-007/raw.wav'
        self.raw.parent.mkdir(parents=True)
        with wave.open(str(self.raw), 'wb') as w:
            w.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            w.writeframes((b'\x00\x10' * 48000))
        self.out = self.raw.parent / 'clean-001'
        self.manifest = self.raw.parent / 'manifest.json'
        self.manifest.write_text(json.dumps({'status': 'recorded_unprocessed_raw',
                                              'raw_sha256': self.m.digest(self.raw)}))
        self.root = root

    def test_preflight_and_no_overwrite(self):
        self.assertEqual(self.m.metrics(self.raw)['frames'], 48000)
        self.assertEqual(self.m.private_paths(self.raw, self.out)[1], self.out)
        self.out.mkdir()
        with self.assertRaises(FileExistsError):
            self.m.private_paths(self.raw, self.out)

    def test_capture_hash_guard(self):
        self.manifest.write_text(json.dumps({'status': 'recorded_unprocessed_raw',
                                              'raw_sha256': 'bad'}))
        with self.assertRaisesRegex(RuntimeError, 'SHA256'):
            self.m.process(argparse.Namespace(source=self.raw, out=self.out, timeout_sec=60))
        self.assertFalse(self.out.exists())

    def test_rust_D_compensated_delay_with_silent_tail(self):
        """Synthetic streaming delay: the -D output is shorter than model input."""
        extra = self.m.pad_deepfilter_input(self.raw, self.root / '.audit/padded.wav',
                                            original_frames=48000)
        with wave.open(str(self.root / '.audit/padded.wav'), 'rb') as w:
            src_pcm = w.readframes(w.getnframes())
        self.assertEqual(len(src_pcm), extra * 2)
        self.assertEqual(src_pcm[48000 * 2:], bytes([0]) * (extra - 48000) * 2)
        # The synthesised processor has 1440 samples (30ms) of latency.
        # Rust -D discards the first 1440 frames of its *delayed* output.
        delay = 1440
        simulated = (bytes([0]) * (delay * 2) + src_pcm)[:extra * 2]
        compensated = simulated[delay * 2:]
        full = self.root / '.audit/deepfilter-full.wav'
        with wave.open(str(full), 'wb') as w:
            w.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            w.writeframes(compensated)
        final = self.root / '.audit/cropped.wav'
        report = self.m.crop_deepfilter_compensated(
            full, final, original_frames=48000, padded_frames=extra)
        self.assertEqual(report['inferred_delay_frames'], delay)
        self.assertEqual(report['kept_frames'], 48000)
        with wave.open(str(final), 'rb') as w:
            output_pcm = w.readframes(w.getnframes())
        with wave.open(str(self.raw), 'rb') as w:
            raw_pcm = w.readframes(w.getnframes())
        self.assertEqual(output_pcm, raw_pcm)

    def test_reject_unexplained_long_delay(self):
        padded = self.m.pad_deepfilter_input(self.raw, self.root / '.audit/padded.wav',
                                             original_frames=48000)
        output = self.root / '.audit/bad.wav'
        with wave.open(str(output), 'wb') as w:
            w.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            w.writeframes(bytes([0]) * ((padded - 6000) * 2))
        with self.assertRaisesRegex(RuntimeError, 'Unexpected DeepFilter delay'):
            self.m.crop_deepfilter_compensated(
                output, self.root / '.audit/should-not-exist.wav',
                original_frames=48000, padded_frames=padded)

    def test_mocked_two_model_chain(self):
        fake = self.root / '.audit/fake-deepfilter'
        fake.write_text('#!/usr/bin/env python3\nimport shutil,sys,pathlib\n'
                        'shutil.copyfile(sys.argv[-1],pathlib.Path(sys.argv[sys.argv.index("-o")+1])/"result.wav")\n')
        fake.chmod(0o700)
        (self.root / 'tools/voice_bench_sherpa.py').write_text(
            'import sys,shutil\nshutil.copyfile(sys.argv[1],sys.argv[2])\n')
        py = self.root / '.audit/voice-tools/sherpa/bin'
        py.mkdir(parents=True)
        (py / 'python').symlink_to(sys.executable)
        models = self.root / '.audit/voice-tools/sherpa-models'
        models.mkdir()
        (models / 'dpdfnet2_48khz_hr.onnx').write_bytes(b'M' * 100001)
        with mock.patch.dict(os.environ, {'CUEWIRE_BENCH_DEEPFILTER': str(fake)}), \
             mock.patch.object(self.m, 'DPDFNET2_SHA256',
                               self.m.digest(models / 'dpdfnet2_48khz_hr.onnx')):
            result = self.m.process(argparse.Namespace(source=self.raw, out=self.out, timeout_sec=60))
        self.assertEqual(result['status'], 'rendered_awaiting_auditory_review')
        self.assertEqual(result['source_sha256_after'], self.m.digest(self.raw))
        self.assertTrue((self.out / '02-dpdfnet2-soft.wav').is_file())
        self.assertTrue((self.out / 'SHA256SUMS').is_file())

if __name__ == '__main__':
    unittest.main()
