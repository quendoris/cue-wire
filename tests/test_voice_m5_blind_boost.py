"""Regression tests for M5 private blind listening boost (synthetic PCM only)."""
from __future__ import annotations

from array import array
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import voice_m5_blind_boost as boost


def make_blind_fixture(root: Path) -> Path:
    folder = root / ".audit" / "voice-aec" / "M5-chaos-blind-github-001"
    folder.mkdir(parents=True)
    checksums = []
    for round_id, frame_count in boost.FRAMES.items():
        (folder / round_id).mkdir()
        for letter in "ABC":
            level = {"A": 800, "B": 1200, "C": 1600}[letter]
            # In R1, the two clean control candidates are bit-identical.
            if round_id == "R1" and letter == "C":
                level = 800
            wav = folder / round_id / (letter + ".wav")
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(boost.RATE)
                w.writeframes(
                    level.to_bytes(2, "little", signed=True) * frame_count)
            checksums.append(
                f"{boost.sha256(wav)}  {round_id}/{letter}.wav")
    (folder / "SHA256SUMS").write_text(
        "\n".join(checksums) + "\n", encoding="utf-8")
    (folder / "README.txt").write_text("private test", encoding="utf-8")
    (folder / "COMMITMENT.txt").write_text(
        "SHA256 secret commitment placeholder", encoding="utf-8")
    # The booster MUST not open/parse the key, even if invalid JSON.
    (folder / boost.PRIVATE_KEY).write_bytes(b"DO-NOT-READ-SECRET")
    names = boost.WAVS | {
        "SHA256SUMS", "README.txt", "COMMITMENT.txt"}
    with zipfile.ZipFile(folder / boost.SOURCE_ZIP, "w",
                         compression=zipfile.ZIP_STORED) as z:
        for name in sorted(names):
            z.write(folder / name, name)
    return folder


class BlindBoostTests(unittest.TestCase):
    def test_gain_is_shared_per_round_and_original_permutation_preserved(self):
        with tempfile.TemporaryDirectory() as work:
            src = make_blind_fixture(Path(work))
            secret_digest = boost.sha256(src / boost.PRIVATE_KEY)
            original_zip_digest = boost.sha256(src / boost.SOURCE_ZIP)
            original_hashes = {
                name: boost.sha256(src / name) for name in boost.WAVS
            }
            out = src.parent / "M5-chaos-blind-louder-001"
            result = boost.build(src, out)
            self.assertEqual(result["audio_files"], 9)
            self.assertEqual(result["clipped_samples"], 0)
            self.assertEqual(secret_digest, boost.sha256(src / boost.PRIVATE_KEY))
            self.assertEqual(
                original_zip_digest, boost.sha256(src / boost.SOURCE_ZIP))
            self.assertEqual(
                original_hashes,
                {name: boost.sha256(src / name) for name in boost.WAVS})
            report = json.loads((out / "BOOST-REPORT.json").read_text())
            self.assertTrue(report["mapping_preserved"])
            self.assertFalse(report["secret_mapping_read"])
            for round_id, frame_count in boost.FRAMES.items():
                gain = report["rounds"][round_id]["common_gain_linear"]
                self.assertGreater(gain, 1.0)
                self.assertEqual(
                    report["rounds"][round_id]["clipped_samples"], 0)
                observed_peak = 0
                for letter in "ABC":
                    original = boost.read_pcm16(
                        src / round_id / (letter + ".wav"), frame_count)
                    new = boost.read_pcm16(
                        out / round_id / (letter + ".wav"), frame_count)
                    self.assertEqual(len(original), len(new))
                    self.assertTrue(all(
                        b == round(a * gain)
                        for a, b in zip(original, new)))
                    observed_peak = max(
                        observed_peak, max(abs(int(v)) for v in new))
                self.assertLessEqual(observed_peak, boost.PEAK_PCM)
            # If R1 A and C were identical before gain, remain identical.
            self.assertEqual(
                boost.sha256(out / "R1/A.wav"),
                boost.sha256(out / "R1/C.wav"))
            with zipfile.ZipFile(result["zip"]) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    boost.WAVS | {
                        "SHA256SUMS", "README.txt", "COMMITMENT.txt",
                        "BOOST-REPORT.json"})
                self.assertNotIn(boost.PRIVATE_KEY, archive.namelist())
                for name in archive.namelist():
                    self.assertEqual(
                        archive.read(name), (out / name).read_bytes())
            with self.assertRaisesRegex(ValueError, "NEW private sibling"):
                boost.build(src, out)

    def test_hash_tamper_rejected_before_creating_any_files(self):
        with tempfile.TemporaryDirectory() as work:
            src = make_blind_fixture(Path(work))
            (src / "R2/B.wav").write_bytes(b"bad WAV")
            out = src.parent / "M5-chaos-blind-louder-001"
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                boost.build(src, out)
            self.assertFalse(out.exists())

    def test_gain_never_clips_even_at_negative_pcm_extreme(self):
        sample = array("h", [-32768, 32767, 0, -1, 1])
        with self.assertRaisesRegex(ValueError, "Clipping"):
            boost.amplify(sample, 1.001)
        self.assertEqual(list(boost.amplify(
            array("h", [-16000, 16000]), 1.25)), [-20000, 20000])


if __name__ == "__main__":
    unittest.main()
