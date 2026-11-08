"""Offline CueWire M4 leveling regression; synthetic PCM, no audio hardware/models."""
from __future__ import annotations

from array import array
import importlib.util
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
import wave
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "voice_m4_level.py"
RATE = 48000


def write_wav(path: Path, samples: array) -> None:
    with path.open("xb") as output:
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(samples.tobytes())


def fixture_pcm() -> array:
    pcm = array("h")
    # 1 s silent, 2 s quiet speech, 2 s loud speech, 1 s silent.
    for seconds, amplitude in ((1, 0), (2, 1500), (2, 6500), (1, 0)):
        for i in range(seconds * RATE):
            pcm.append(round(amplitude * math.sin(2 * math.pi * 230 * i / RATE)))
    return pcm


def region_rms(pcm: array, start: float, end: float) -> float:
    xs = pcm[int(start * RATE):int(end * RATE)]
    return math.sqrt(sum(int(x)*int(x) for x in xs)/len(xs))


class LevelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/"tools").mkdir()
        shutil.copyfile(SCRIPT, self.root/"tools/voice_m4_level.py")
        spec = importlib.util.spec_from_file_location("m4_level_test", self.root/"tools/voice_m4_level.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.audit = self.root/".audit"
        self.clean = self.audit/"voice-captures/M4-R0-ALSA-007/clean-003"
        self.clean.mkdir(parents=True)
        self.raw = self.clean.parent/"raw.wav"
        self.source = self.clean/"02-dpdfnet2-soft.wav"
        self.pcm = fixture_pcm()
        write_wav(self.raw, self.pcm)
        write_wav(self.source, self.pcm)
        self.manifest = self.clean/"manifest.json"
        self.manifest.write_text(json.dumps({
            "status": "rendered_awaiting_auditory_review",
            "raw": {"sha256": self.mod.sha256(self.raw)},
            "outputs": {"dpdfnet2_soft": {"sha256": self.mod.sha256(self.source)}}
        }))
        self.patcher = mock.patch.object(self.mod, "AUDIT", self.audit.resolve())
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_provenance_verifies_both_input_hashes(self):
        source, out, raw, data = self.mod.source_paths(
            self.source, self.clean.parent/"level-001")
        self.assertEqual(source, self.source.resolve())
        self.assertEqual(raw, self.raw.resolve())
        self.assertEqual(out.name, "level-001")
        self.assertEqual(data["status"], "rendered_awaiting_auditory_review")
        self.manifest.write_text(json.dumps({
            "status": "rendered_awaiting_auditory_review",
            "raw": {"sha256": "wrong"},
            "outputs": {"dpdfnet2_soft": {"sha256": self.mod.sha256(self.source)}}
        }))
        with self.assertRaisesRegex(ValueError, "RAW SHA256"):
            self.mod.source_paths(self.source, self.clean.parent/"level-001")

    def test_existing_destination_refused(self):
        out = self.clean.parent/"level-001"
        out.mkdir()
        with self.assertRaises(FileExistsError):
            self.mod.source_paths(self.source, out)

    def test_speech_gain_smooth_and_pause_stays_quiet(self):
        levels = self.mod.frame_rms_db(self.pcm)
        gains, info, detected = self.mod.gain_schedule(levels, -24.0, 16.0)
        self.assertGreater(info["speech_detected_frames"], 30)
        self.assertEqual(len(gains), len(levels))
        result, safe = self.mod.apply_gain(self.pcm, gains, -2.0)
        self.assertEqual(len(result), len(self.pcm))
        self.assertLessEqual(max(abs(x) for x in result), safe["ceiling_integer"])
        self.assertEqual(self.mod.pcm_metrics(result)["clipped_samples"], 0)
        self.assertEqual(region_rms(result, 0, 0.5), 0)
        self.assertEqual(region_rms(result, 5.5, 6), 0)
        before = self.mod.dbfs(region_rms(self.pcm, 3, 4)/region_rms(self.pcm, 1.3, 2.3))
        after = self.mod.dbfs(region_rms(result, 3, 4)/region_rms(result, 1.3, 2.3))
        self.assertLess(after, before - 1.0)
        self.assertNotEqual(result.tobytes(), self.pcm.tobytes())

    def test_sample_peak_guard_with_transient(self):
        pcm = array("h", self.pcm)
        pcm[RATE * 3] = 30000
        levels = self.mod.frame_rms_db(pcm)
        gains, info, detected = self.mod.gain_schedule(levels, -20.0, 18.0)
        processed, safety = self.mod.apply_gain(pcm, gains, -3.0)
        self.assertLessEqual(max(abs(x) for x in processed), safety["ceiling_integer"])
        self.assertEqual(self.mod.pcm_metrics(processed)["clipped_samples"], 0)

    def test_silent_input_rejected(self):
        with self.assertRaisesRegex(ValueError, "Too few credible speech"):
            self.mod.gain_schedule([-240.0] * 500, -24.0, 16.0)

    def test_generated_output_keeps_frame_count(self):
        levels = self.mod.frame_rms_db(self.pcm)
        gains, _, _ = self.mod.gain_schedule(levels, -24.0, 16.0)
        processed, _ = self.mod.apply_gain(self.pcm, gains, -2.0)
        output = self.clean.parent/"test-output.wav"
        write_wav(output, processed)
        self.assertEqual(self.mod.read_pcm16(output).tobytes(), processed.tobytes())
        self.assertEqual(self.mod.read_pcm16(self.source).tobytes(), self.pcm.tobytes())


if __name__ == "__main__":
    unittest.main()
