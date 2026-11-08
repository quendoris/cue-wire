"""Synthetic independent regression tests for M4 level-002 A/B stage.

No microphones, PipeWire, network, user WAV or neural models are accessed.
"""
from __future__ import annotations

from array import array
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import voice_m4_refine as refine
from voice_m4_level import pcm_metrics, sha256, read_pcm16

RATE = 48000


def tone_fixture() -> array:
    x = array("h")
    # Actual quiet sections stay quiet, two longer voiced regions differ 12dB.
    for seconds, amp in ((1, 0), (2, 2100), (1, 0), (2, 8300), (1, 0)):
        for i in range(seconds * RATE):
            x.append(round(amp * math.sin(2 * math.pi * 190 * i / RATE)))
    x[4 * RATE + RATE // 3] = 24500  # rare transient, peak safety test
    return x


def write_file(path: Path, x: array) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(x.tobytes())


class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audit = Path(self.temp.name) / ".audit"
        self.capture = self.audit / "voice-captures/M4-R0-ALSA-007"
        self.clean = self.capture / "clean-003"
        self.level = self.capture / "level-001"
        self.clean.mkdir(parents=True)
        self.level.mkdir()
        self.raw = self.capture / "raw.wav"
        self.clean_audio = self.clean / "02-dpdfnet2-soft.wav"
        self.source = self.level / "03-voice-leveled.wav"
        self.out = self.capture / "level-002"
        self.samples = tone_fixture()
        for p in (self.raw, self.clean_audio, self.source):
            write_file(p, self.samples)
        (self.clean / "manifest.json").write_text('{"schema":"test-clean"}')
        (self.level / "manifest.json").write_text(json.dumps({
            "schema": "cuewire-M4-level-v1",
            "status": "rendered_awaiting_auditory_review",
            "output": {"file": self.source.name, "sha256": sha256(self.source)},
            "raw": {"sha256": sha256(self.raw)},
            "input": {"path": str(self.clean_audio.resolve()),
                      "sha256": sha256(self.clean_audio)},
            "clean_manifest_sha256": sha256(self.clean / "manifest.json"),
        }))
        self.auditor = mock.patch.object(refine, "AUDIT", self.audit.resolve())
        self.auditor.start()
        self.addCleanup(self.auditor.stop)

    def test_curve_continuity_and_limit(self):
        p = refine.PROFILES["dense"]
        threshold = -28.0
        self.assertEqual(refine.knee_reduction(-31, threshold, p["ratio"], 5, 5.5), 0)
        self.assertGreater(refine.knee_reduction(-28, threshold, p["ratio"], 5, 5.5), 0)
        values = [refine.knee_reduction(-35 + 0.1 * i, threshold, p["ratio"], 5, 5.5)
                  for i in range(170)]
        self.assertTrue(all(a <= b + 1e-6 for a, b in zip(values, values[1:])))
        self.assertLessEqual(max(values), 5.5)

    def test_provenance_and_no_overwrite(self):
        self.assertEqual(refine.expected_sources(self.source, self.out)[0], self.source)
        self.out.mkdir()
        with self.assertRaises(FileExistsError):
            refine.expected_sources(self.source, self.out)

    def test_tamper_of_raw_or_previous_master_is_rejected(self):
        (self.level / "manifest.json").write_text('{"schema":"cuewire-M4-level-v1"}')
        with self.assertRaises(ValueError):
            refine.expected_sources(self.source, self.out)

    def test_two_distinct_candidates_and_audit(self):
        reference_hashes = {p: sha256(p) for p in
                            (self.raw, self.clean_audio, self.source,
                             self.clean / "manifest.json", self.level / "manifest.json")}
        result = refine.render(self.source, self.out)
        self.assertEqual(result["status"], "rendered_awaiting_auditory_review")
        self.assertEqual(set(result["outputs"]), {"soft", "dense"})
        self.assertEqual(result["input"]["metrics"]["frames"], len(self.samples))
        for name in ("soft", "dense"):
            p = self.out / ("04-" + name + ".wav")
            data = read_pcm16(p)
            self.assertEqual(len(data), len(self.samples))
            self.assertEqual(pcm_metrics(data)["clipped_samples"], 0)
            self.assertLessEqual(pcm_metrics(data)["sample_peak_dbfs"], -1.999)
            self.assertEqual(data[:RATE//2], self.samples[:RATE//2])
            self.assertEqual(data[-RATE//2:], self.samples[-RATE//2:])
            self.assertEqual(sha256(p), result["outputs"][name]["sha256"])
            self.assertLess(result["outputs"][name]["active_voice_spread"]
                            ["speech_p90_minus_p25_db"],
                            result["before_active_voice_spread"]["speech_p90_minus_p25_db"])
        # The two outputs must actually compress louder phrases and
        # raise quiet speech compared with the already-good LEVEL-001.
        def section_rms_db(pcm, left_sec, right_sec):
            start, end = int(left_sec * RATE), int(right_sec * RATE)
            region = pcm[start:end]
            rms = math.sqrt(sum(int(v)*int(v) for v in region)/len(region))
            return 20 * math.log10(max(rms / 32768, 1e-12))
        input_quiet = section_rms_db(self.samples, 1.3, 2.7)
        input_loud = section_rms_db(self.samples, 4.3, 5.7)
        for name in ("soft", "dense"):
            output = read_pcm16(self.out / ("04-" + name + ".wav"))
            quiet = section_rms_db(output, 1.3, 2.7)
            loud = section_rms_db(output, 4.3, 5.7)
            self.assertGreater(quiet, input_quiet + 1.0)
            self.assertLess(loud - quiet, input_loud - input_quiet - 1.0)
        self.assertNotEqual((self.out/"04-soft.wav").read_bytes(),
                            (self.out/"04-dense.wav").read_bytes())
        self.assertTrue((self.out / "SHA256SUMS").is_file())
        self.assertTrue((self.out / "manifest.json").is_file())
        for p, digest in reference_hashes.items():
            self.assertEqual(sha256(p), digest)
        with self.assertRaises(FileExistsError):
            refine.render(self.source, self.out)

    def test_limiter_transient_and_exact_length(self):
        levels = refine.frame_rms_db(self.samples)
        gains, _, _ = refine.frame_gains(levels, refine.PROFILES["dense"])
        output, details = refine.apply_offline_peak_limiter(self.samples, gains, -3.0)
        self.assertEqual(len(output), len(self.samples))
        self.assertLessEqual(max(abs(v) for v in output), details["ceiling_sample_integer"])
        self.assertFalse(details["true_peak_certified"])
        self.assertGreaterEqual(details["max_limiter_reduction_db"], 0)

if __name__ == "__main__":
    unittest.main()
