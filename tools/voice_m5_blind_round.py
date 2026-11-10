#!/usr/bin/env python3
"""Build a private, blinded CueWire M5 listening comparison from verified WAVs.

The repository contains ONLY this generator. Actual voice and copyrighted music
stay in the operator's ignored .audit directory. No playback, capture, network,
system audio changes, daemon, or autostart. A private key is stored OUTSIDE the
ZIP; reveal it only after recording blind judgements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import wave
import zipfile

RATE = 48000
CROP_FRAMES = RATE // 10
TRACKS = {
    "raw": "01_unprocessed_physical_microphone.wav",
    "speex": "02_native_speex_aec.wav",
    "guarded": "03_guarded_dual_speex_delta.wav",
}
ROUNDS = {
    "R1": ("03_real_voice_no_music", 288000),
    "R2": ("02_mouse_keyboard_music", 288000),
    "R3": ("04_real_full_chaos", 1440000),
}
ZIP_NAME = "cuewire-m5-blind-round1.zip"
KEY_NAME = "PRIVATE-KEY-DO-NOT-OPEN.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_source(root: Path) -> dict[str, dict]:
    """Verify exact source files, their SHA256 manifest and PCM16 metadata."""
    if (root.name != "M5-chaos-listening-001"
            or root.parent.name != "voice-aec"
            or root.parent.parent.name != ".audit"):
        raise ValueError("Source must be the private M5 chaos listening evidence")
    report_path = root / "listening-report.json"
    sums_path = root / "SHA256SUMS"
    for path in (report_path, sums_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or linked source evidence: " + path.name)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("schema") != "cuewire-M5-private-real-doubletalk-listening-AB-v1"
            or report.get("status") != "manually_authorized_private_WAVs_not_auto_played"
            or report.get("global_playback_gain", {}).get("gain_linear") != 1.0
            or report.get("clean_voice_pcm16_preservation", {}).get(
                "sample_exact_PCM16_after_shared_gain") is not True):
        raise ValueError("Unverified input report, gain or clean voice")
    rows = report.get("files", [])
    expected = {
        f"{segment}/{track}"
        for segment in (
            "01_mouse_and_music", "02_mouse_keyboard_music",
            "03_real_voice_no_music", "04_real_full_chaos"
        )
        for track in TRACKS.values()
    }
    if len(rows) != 12 or {r.get("relative_path") for r in rows} != expected:
        raise ValueError("Expected exactly 12 known physical listening files")
    index = {r["relative_path"]: r for r in rows}
    expected_hashes = {name: row["sha256"] for name, row in index.items()}
    expected_hashes["listening-report.json"] = sha256(report_path)
    manifest_hashes = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, sep, name = line.partition("  ")
        if not sep or name in manifest_hashes:
            raise ValueError("Malformed or duplicate SHA256SUMS entry")
        manifest_hashes[name] = digest
    if manifest_hashes != expected_hashes:
        raise ValueError("Listening SHA256SUMS does not match report")
    for name, row in index.items():
        path = root / name
        if (path.is_symlink() or not path.is_file()
                or path.resolve(strict=True).parent != (root / Path(name).parent)):
            raise ValueError("Refuse unsafe WAV source path")
        if sha256(path) != row["sha256"]:
            raise ValueError("Source WAV SHA256 mismatch: " + name)
        with wave.open(str(path), "rb") as w:
            info = (w.getnchannels(), w.getsampwidth(), w.getframerate(),
                    w.getnframes(), w.getcomptype())
        expected_frames = 1440000 if name.startswith("04_") else 288000
        if (info != (1, 2, RATE, expected_frames, "NONE")
                or row["channels"] != 1 or row["sample_width_bytes"] != 2
                or row["sample_rate"] != RATE or row["frames"] != expected_frames):
            raise ValueError("Source WAV format/length mismatch: " + name)
    raw = index["03_real_voice_no_music/" + TRACKS["raw"]]["sha256"]
    guarded = index["03_real_voice_no_music/" + TRACKS["guarded"]]["sha256"]
    if raw != guarded:
        raise ValueError("Expected bit-exact clean-voice control WAV")
    return index


def read_cropped_frames(path: Path, expected_frames: int) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate(),
                w.getnframes(), w.getcomptype()) != (
                1, 2, RATE, expected_frames, "NONE"):
            raise ValueError("Changed WAV after source validation")
        w.setpos(CROP_FRAMES)
        frames = w.readframes(expected_frames - 2 * CROP_FRAMES)
    if len(frames) != 2 * (expected_frames - 2 * CROP_FRAMES):
        raise RuntimeError("Truncated WAV during private preview extraction")
    return frames


def write_cropped_wav(path: Path, frames: bytes) -> None:
    with path.open("xb") as target:
        with wave.open(target, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(frames)


def canonical_mapping(mapping: dict) -> str:
    return json.dumps(mapping, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"))


def build(source: Path, out: Path) -> dict:
    source = source.expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Source is not a directory")
    out = out.expanduser().absolute()
    if (out.exists() or out.is_symlink()
            or out.parent.resolve(strict=True) != source.parent
            or out.name == source.name):
        raise ValueError("Output must be a NEW private sibling of listening source")
    verify_source(source)
    choices = list(TRACKS)
    random = secrets.SystemRandom()
    mapping = {}
    for round_id in ROUNDS:
        shuffled = choices.copy()
        random.shuffle(shuffled)
        mapping[round_id] = dict(zip("ABC", shuffled))
    salt = secrets.token_hex(32)
    canonical = canonical_mapping(mapping)
    commitment = hashlib.sha256(
        bytes.fromhex(salt) + canonical.encode("utf-8")).hexdigest()
    output = {
        "format": "cuewire-M5-blind-private-key-v1",
        "mapping": mapping,
        "salt_hex": salt,
        "canonical_mapping_json": canonical,
        "sha256_salt_plus_canonical": commitment,
        "note": "PRIVATE: reveal only after writing listening judgements",
    }

    out.mkdir(mode=0o700)
    hash_lines = []
    for round_id, (segment, frames) in ROUNDS.items():
        folder = out / round_id
        folder.mkdir(mode=0o700)
        for letter, variant in mapping[round_id].items():
            src = source / segment / TRACKS[variant]
            raw_bytes = read_cropped_frames(src, frames)
            dst = folder / (letter + ".wav")
            write_cropped_wav(dst, raw_bytes)
            hash_lines.append(f"{sha256(dst)}  {round_id}/{letter}.wav")
    readme = (
        "CueWire M5 — PRIVATE BLIND LISTENING TEST, ROUND 1\n\n"
        "Three independently shuffled candidates A, B, C per round.\n"
        "R1: clean voice (5.8s); R2: mouse, keyboard and music (5.8s);\n"
        "R3: real overlapping speech, music and clicks (29.8s).\n"
        "Every track: 48kHz mono PCM16, unchanged common gain 0 dB.\n"
        "Only 100ms trimmed at start/end, NO DSP or normalization.\n"
        "Listen at one fixed headset volume (pw-play R1/A.wav etc.).\n"
        "Record rankings, descriptions and confidence before key reveal.\n"
        "A/B/C labels are independently randomized across rounds.\n"
        "The source is PRIVATE human speech and may include copyrighted music.\n"
        "Never upload this ZIP or PRIVATE-KEY-DO-NOT-OPEN.json to public GitHub.\n"
        "The secret salt and mapping are deliberately absent from this ZIP.\n"
    )
    (out / "README.txt").write_text(readme, encoding="utf-8")
    (out / "COMMITMENT.txt").write_text(
        "SHA256(salt_bytes || canonical_mapping_json_utf8) = "
        + commitment + "\n", encoding="utf-8")
    (out / "SHA256SUMS").write_text("\n".join(hash_lines) + "\n",
                                     encoding="utf-8")
    with (out / KEY_NAME).open("x", encoding="utf-8") as keyfile:
        json.dump(output, keyfile, indent=2, sort_keys=True)
        keyfile.write("\n")
    zip_path = out / ZIP_NAME
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        for item in ("README.txt", "COMMITMENT.txt", "SHA256SUMS"):
            archive.write(out / item, item)
        for round_id in ROUNDS:
            for letter in "ABC":
                archive.write(out / round_id / (letter + ".wav"),
                              f"{round_id}/{letter}.wav")
    # Only the ZIP is handed to a listener. The private mapping never enters it.
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        if (len(names) != 12 or KEY_NAME in names
                or any("PRIVATE" in name for name in names)):
            raise RuntimeError("Private key leaked into listening ZIP")
    return {
        "zip": str(zip_path),
        "zip_sha256": sha256(zip_path),
        "private_key": str(out / KEY_NAME),
        "commitment": commitment,
        "wav_count": 9,
    }


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True,
                   help="Private .audit/voice-aec/M5-chaos-listening-001")
    p.add_argument("--out", type=Path, required=True,
                   help="NEW sibling under the same private .audit/voice-aec")
    args = p.parse_args()
    try:
        print(json.dumps(build(args.source, args.out), indent=2))
    except (OSError, ValueError, RuntimeError, KeyError,
            TypeError, wave.Error, zipfile.BadZipFile) as e:
        p.exit(1, "ERROR: " + str(e) + "\n")


if __name__ == "__main__":
    main()
