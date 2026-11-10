"""Private CueWire blind test regression: no real recordings, no playback."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import voice_m5_blind_round as blind


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fixture(root: Path):
    source = root / ".audit" / "voice-aec" / "M5-chaos-listening-001"
    source.mkdir(parents=True)
    entries = []
    for segment in ("01_mouse_and_music", "02_mouse_keyboard_music",
                    "03_real_voice_no_music", "04_real_full_chaos"):
        dest = source / segment
        dest.mkdir()
        frames = 1440000 if segment.startswith("04_") else 288000
        for variant, filename in blind.TRACKS.items():
            value = (1 if variant == "raw" else 2 if variant == "speex" else 3)
            if segment == "03_real_voice_no_music" and variant == "guarded":
                value = 1
            pcm = value.to_bytes(2, "little", signed=True) * frames
            wav = dest / filename
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(blind.RATE)
                w.writeframes(pcm)
            entries.append({
                "relative_path": f"{segment}/{filename}", "sha256": sha(wav),
                "channels": 1, "sample_width_bytes": 2,
                "sample_rate": blind.RATE, "frames": frames,
            })
    report = {
        "schema": "cuewire-M5-private-real-doubletalk-listening-AB-v1",
        "status": "manually_authorized_private_WAVs_not_auto_played",
        "global_playback_gain": {"gain_linear": 1.0},
        "clean_voice_pcm16_preservation": {
            "sample_exact_PCM16_after_shared_gain": True,
        },
        "files": entries,
    }
    report_file = source / "listening-report.json"
    report_file.write_text(json.dumps(report), encoding="utf-8")
    manifest = [f'{row["sha256"]}  {row["relative_path"]}' for row in entries]
    manifest.append(f"{sha(report_file)}  listening-report.json")
    (source / "SHA256SUMS").write_text(
        "\n".join(manifest) + "\n", encoding="utf-8")
    return source


class PrivateBlindTests(unittest.TestCase):
    def test_zip_blind_correct_data_and_secret_key_not_in_zip(self):
        with tempfile.TemporaryDirectory() as d:
            source = make_fixture(Path(d))
            out = source.parent / "M5-chaos-blind-github-001"
            result = blind.build(source, out)
            self.assertEqual(result["wav_count"], 9)
            mapping = json.loads((out / blind.KEY_NAME).read_text())
            canonical = blind.canonical_mapping(mapping["mapping"])
            self.assertEqual(
                hashlib.sha256(bytes.fromhex(mapping["salt_hex"]) +
                               canonical.encode()).hexdigest(),
                mapping["sha256_salt_plus_canonical"])
            with zipfile.ZipFile(result["zip"]) as archive:
                names = set(archive.namelist())
                self.assertEqual(len(names), 12)
                self.assertNotIn(blind.KEY_NAME, names)
                for round_id, (segment, total) in blind.ROUNDS.items():
                    for letter in "ABC":
                        candidate = archive.read(f"{round_id}/{letter}.wav")
                        self.assertEqual(
                            candidate, (out / round_id /
                                        (letter + ".wav")).read_bytes())
                        role = mapping["mapping"][round_id][letter]
                        src = source / segment / blind.TRACKS[role]
                        with wave.open(str(src), "rb") as w:
                            w.setpos(blind.CROP_FRAMES)
                            expected = w.readframes(
                                total - 2 * blind.CROP_FRAMES)
                        with wave.open(str(out / round_id /
                                               (letter + ".wav")), "rb") as w:
                            self.assertEqual(w.getnframes(),
                                             total - 2 * blind.CROP_FRAMES)
                            self.assertEqual(w.readframes(w.getnframes()),
                                             expected)
            with self.assertRaisesRegex(ValueError, "NEW private sibling"):
                blind.build(source, out)

    def test_changed_audio_fails_closed_before_creating_output(self):
        with tempfile.TemporaryDirectory() as d:
            source = make_fixture(Path(d))
            wrong = source / "04_real_full_chaos" / blind.TRACKS["raw"]
            with wrong.open("ab") as f:
                f.write(b"tampered")
            out = source.parent / "M5-chaos-blind-github-001"
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                blind.build(source, out)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
