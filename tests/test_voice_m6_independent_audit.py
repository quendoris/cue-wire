"""Independent M6 audit gate tests: synthetic PCM only; no device access."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import voice_m6_independent_audit as qa

RATE = 48000
N = RATE * 3


def wav(path: Path, data: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.astype("<i2").tobytes())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fixture(root: Path) -> tuple[Path, Path, Path]:
    audit = root / ".audit"
    fixture = audit / "voice-aec" / "M6-synthetic-test"
    result = audit / "voice-aec" / "M6-result-test"
    fixture.mkdir(parents=True)
    result.mkdir()
    t = np.arange(N)
    voice = np.rint(850 * np.sin(2*np.pi*185*t/RATE)).astype(np.int32)
    music = np.rint(1300 * np.sin(2*np.pi*997*t/RATE)).astype(np.int32)
    click = np.zeros(N, dtype=np.int32)
    click[int(.55*RATE):int(.55*RATE)+10] = 1600
    click[int(1.4*RATE):int(1.4*RATE)+10] = 1600
    residue = (t % 100 == 0).astype(np.int32)
    raw = voice + music + click + residue
    output = {
        "01_physical_raw.wav": raw,
        "02_inverted_music_estimate.wav": -music,
        "03_inverted_click_estimate.wav": -click,
        "04_mic_minus_music.wav": voice+click+residue,
        "05_combined_compensation.wav": voice+residue,
    }
    fixture_files = {
        "cal-mic.wav": np.zeros(N,dtype=np.int32),
        "cal-reference.wav": np.zeros(N,dtype=np.int32),
        "eval-mic.wav": raw,
        "eval-reference.wav": music,
        qa.TRUTH_NAME: voice,
    }
    for name, data in fixture_files.items():
        wav(fixture/name, data)
    for name in ("cal-events.jsonl", "eval-events.jsonl"):
        (fixture/name).write_text(
            '{"kind":"mouse_press","sample":40000}\n', encoding="utf-8")
    fixture_hashes = {name: sha(fixture/name) for name in (
        *fixture_files, "cal-events.jsonl", "eval-events.jsonl")}
    meta = {
        "schema": "cuewire-M6-synthetic-acoustic-reference-fixture-v1",
        "all_sources_synthetic": True,
        "human_speech_or_real_input_recorded": False,
        "files_sha256": fixture_hashes,
    }
    (fixture/"fixture.json").write_text(
        json.dumps(meta), encoding="utf-8")
    fixture_hashes["fixture.json"] = sha(fixture/"fixture.json")
    (fixture/"SHA256SUMS").write_text(
        "".join(f"{value}  {key}\n"
                for key,value in fixture_hashes.items()), encoding="utf-8")

    for name, samples in output.items():
        wav(result/name,samples)
    output_sha = {name: sha(result/name) for name in output}
    report = {
        "schema": "cuewire-M6-event-anchored-offline-ANC-v1",
        "state": "experimental_offline_not_live",
        "nearend_speech_quality_proved": False,
        "evaluation": {"no_nearend_learning_during_doubletalk": True},
        "output_sha256": output_sha,
        "evaluation_audio": {
            "physical_mic_sha256": fixture_hashes["eval-mic.wav"],
            "playback_reference_sha256":
                fixture_hashes["eval-reference.wav"],
            "event_markers_sha256": fixture_hashes["eval-events.jsonl"],
        },
    }
    (result/"m6-report.json").write_text(
        json.dumps(report), encoding="utf-8")
    output_sha["m6-report.json"] = sha(result/"m6-report.json")
    (result/"SHA256SUMS").write_text(
        "".join(f"{value}  {key}\n" for key,value in output_sha.items()),
        encoding="utf-8")
    return fixture, result, audit


class IndependentAuditTests(unittest.TestCase):
    def test_known_truth_and_sample_exact_parallel_sums(self):
        with tempfile.TemporaryDirectory() as work:
            fixture, result, root = make_fixture(Path(work))
            audit = qa.audit(fixture, result, root)
            self.assertTrue(audit["accepted"])
            self.assertTrue(audit["PCM16_exact_parallel_sums"])
            self.assertTrue(audit["original_mic_PCM16_exact"])
            self.assertGreater(
                audit["interference_rms_reduction_db"], 50)
            self.assertLess(
                audit["combined_error_rms_PCM16_LSB"], .2)

    def test_tampered_wav_rejected_even_if_sha_list_is_updated(self):
        with tempfile.TemporaryDirectory() as work:
            fixture, result, root = make_fixture(Path(work))
            name = "05_combined_compensation.wav"
            samples = qa.read_pcm(result/name)
            samples[5000] += 500
            wav(result/name, samples)
            report_path = result/"m6-report.json"
            report = json.loads(report_path.read_text())
            report["output_sha256"][name] = sha(result/name)
            report_path.write_text(json.dumps(report), encoding="utf-8")
            digests = {
                entry: sha(result/entry)
                for entry in (*qa.WAV_NAMES,"m6-report.json")}
            (result/"SHA256SUMS").write_text(
                "".join(f"{v}  {k}\n" for k,v in digests.items()),
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sample-exact"):
                qa.audit(fixture, result, root)


if __name__ == "__main__":
    unittest.main()
