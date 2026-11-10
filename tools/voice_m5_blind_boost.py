#!/usr/bin/env python3
"""Make a louder, PCM16-safe local copy of an EXISTING CueWire M5 blind round.

A/B/C identity is NEVER changed. Only identical linear gain per round is
applied. Existing WAVs, secret mapping and system audio state are untouched.
The only output is a NEW private .audit sibling directory; no playback.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import wave
import zipfile

RATE = 48000
TARGET_PEAK_DBFS = -3.0  # leaves intersample / DAC safety headroom
PEAK_PCM = math.floor(32767 * 10 ** (TARGET_PEAK_DBFS / 20))
FRAMES = {"R1": 278400, "R2": 278400, "R3": 1430400}
WAVS = {f"{round_id}/{letter}.wav" for round_id in FRAMES for letter in "ABC"}
ZIP_NAME = "cuewire-m5-blind-louder.zip"
SOURCE_ZIP = "cuewire-m5-blind-round1.zip"
PRIVATE_KEY = "PRIVATE-KEY-DO-NOT-OPEN.json"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_original(source: Path) -> dict[str, str]:
    if (source.parent.name != "voice-aec"
            or source.parent.parent.name != ".audit"
            or not source.name.startswith("M5-chaos-blind-")):
        raise ValueError("Expected existing private CueWire blind M5 directory")
    for name in ("SHA256SUMS", "README.txt", "COMMITMENT.txt",
                 SOURCE_ZIP, PRIVATE_KEY):
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or linked private source file: " + name)
    lines = (source / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    entries = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (R[123]/[ABC]\.wav)", line)
        if not match or match.group(2) in entries:
            raise ValueError("Unexpected or duplicate original SHA256SUMS entry")
        entries[match.group(2)] = match.group(1)
    if set(entries) != WAVS:
        raise ValueError("Expected exactly 9 original WAV checksums")
    source_zip = source / SOURCE_ZIP
    with zipfile.ZipFile(source_zip) as archive:
        expected = WAVS | {"SHA256SUMS", "README.txt", "COMMITMENT.txt"}
        if set(archive.namelist()) != expected:
            raise ValueError("Unexpected original blind ZIP contents")
        for name in expected - WAVS:
            if archive.read(name) != (source / name).read_bytes():
                raise ValueError("Original ZIP metadata differs from source")
        for name in WAVS:
            path = source / name
            if not path.is_file() or path.is_symlink():
                raise ValueError("Missing or linked original WAV: " + name)
            if path.resolve(strict=True).parent != (source / name.split("/")[0]):
                raise ValueError("Unsafe original WAV path: " + name)
            if sha256(path) != entries[name]:
                raise ValueError("Original WAV SHA256 mismatch: " + name)
            if digest_bytes(archive.read(name)) != entries[name]:
                raise ValueError("Original ZIP WAV mismatch: " + name)
    return entries


def read_pcm16(path: Path, expected_frames: int) -> array:
    with wave.open(str(path), "rb") as w:
        fmt = (w.getnchannels(), w.getsampwidth(),
               w.getframerate(), w.getnframes(), w.getcomptype())
        if fmt != (1, 2, RATE, expected_frames, "NONE"):
            raise ValueError("Invalid PCM16 source WAV format: " + str(path))
        contents = w.readframes(expected_frames)
    if len(contents) != expected_frames * 2:
        raise ValueError("Truncated original WAV: " + str(path))
    samples = array("h")
    samples.frombytes(contents)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def write_pcm16(path: Path, samples: array) -> None:
    data = array("h", samples)
    if sys.byteorder != "little":
        data.byteswap()
    with path.open("xb") as stream:
        with wave.open(stream, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(data.tobytes())


def amplify(original: array, gain: float) -> array:
    # No compressor, limiter, soft clip, resampling or dither.
    boosted = array("h")
    for sample in original:
        value = round(int(sample) * gain)
        if value < -32768 or value > 32767:
            raise ValueError("Clipping would occur; refusing to write audio")
        boosted.append(value)
    return boosted


def build(source: Path, out: Path) -> dict:
    os.umask(0o077)
    source = source.expanduser().resolve(strict=True)
    out = out.expanduser().absolute()
    if (not source.is_dir() or out.exists() or out.is_symlink()
            or out.parent.resolve(strict=True) != source.parent
            or out.name == source.name):
        raise ValueError("Output must be a NEW private sibling of original blind test")
    verify_original(source)
    # Commit to only reading the public commitment; NEVER read the key.
    report = {
        "schema": "cuewire-M5-blind-common-per-round-gain-v1",
        "input_archive_sha256": sha256(source / SOURCE_ZIP),
        "mapping_preserved": True,
        "secret_mapping_read": False,
        "target_peak_dbfs": TARGET_PEAK_DBFS,
        "limiter_compressor_noise_suppression_added": False,
        "rounds": {},
    }
    # Pre-scan all three candidates BEFORE creating output, so every
    # candidate within a round is multiplied by one identical gain.
    peaks = {}
    for round_id, frames in FRAMES.items():
        peaks[round_id] = max(
            max(abs(int(v)) for v in read_pcm16(
                source / round_id / f"{letter}.wav", frames))
            for letter in "ABC")
        if peaks[round_id] == 0 or peaks[round_id] > PEAK_PCM:
            raise ValueError("Cannot safely peak-boost " + round_id)
    out.mkdir(mode=0o700)
    checksums = []
    for round_id, frames in FRAMES.items():
        (out / round_id).mkdir(mode=0o700)
        common_gain = PEAK_PCM / peaks[round_id]
        result_peak = 0
        for letter in "ABC":
            original = read_pcm16(source / round_id / f"{letter}.wav", frames)
            boosted = amplify(original, common_gain)
            result_peak = max(result_peak, max(abs(int(v)) for v in boosted))
            path = out / round_id / f"{letter}.wav"
            write_pcm16(path, boosted)
            checksums.append(f"{sha256(path)}  {round_id}/{letter}.wav")
        if result_peak > PEAK_PCM:
            raise RuntimeError("Ceiling violated after integer quantization")
        report["rounds"][round_id] = {
            "common_gain_db": round(20 * math.log10(common_gain), 5),
            "common_gain_linear": common_gain,
            "original_max_peak_pcm16": peaks[round_id],
            "boosted_max_peak_pcm16": result_peak,
            "boosted_max_peak_dbfs":
                round(20 * math.log10(result_peak / 32768), 5),
            "gain_identical_for_A_B_C": True,
            "clipped_samples": 0,
            "sample_count_each": frames,
        }
    (out / "BOOST-REPORT.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    shutil.copyfile(source / "COMMITMENT.txt", out / "COMMITMENT.txt")
    (out / "README.txt").write_text(
        "CueWire M5 / LOUDER VERSION of EXISTING blind test\n\n"
        "A, B, C labels are UNCHANGED from the previous run.\n"
        "R1 and R2 are 5.8 sec; R3 is 29.8 sec.\n"
        "Each round uses a SINGLE common linear amplitude gain for ALL A/B/C.\n"
        "Three rounds may have DIFFERENT gains. No compression, limiter,\n"
        "resampling, or separate normalization for each candidate.\n"
        "Maximum sample peak is -3 dBFS, leaving output headroom.\n"
        "Sample values are requantized to signed PCM16 after gain.\n"
        "Original files and original blind mapping key are untouched.\n"
        "The original private key stays in the ORIGINAL test directory;\n"
        "DO NOT reveal until AFTER completing all listening judgements.\n"
        "The ZIP deliberately contains no mapping key or original voice.\n"
        "Listen locally: pw-play R1/A.wav (repeat B/C, then R2/R3).\n"
        "Only the audio's gain changed; it is NOT a new random blind test.\n",
        encoding="utf-8")
    for filename in ("BOOST-REPORT.json", "COMMITMENT.txt", "README.txt"):
        checksums.append(f"{sha256(out / filename)}  {filename}")
    (out / "SHA256SUMS").write_text("\n".join(checksums) + "\n",
                                     encoding="utf-8")
    expected_zip = WAVS | {
        "BOOST-REPORT.json", "COMMITMENT.txt", "README.txt", "SHA256SUMS"}
    archive_path = out / ZIP_NAME
    with zipfile.ZipFile(archive_path, "x",
                         compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        for name in sorted(expected_zip):
            archive.write(out / name, name)
    with zipfile.ZipFile(archive_path) as archive:
        if set(archive.namelist()) != expected_zip:
            raise RuntimeError("Unsafe boosted ZIP contents")
    return {
        "output_dir": str(out),
        "zip": str(archive_path),
        "zip_sha256": sha256(archive_path),
        "original_mapping_key_unchanged": str(source / PRIVATE_KEY),
        "per_round_gain_db": {
            r: report["rounds"][r]["common_gain_db"] for r in FRAMES},
        "audio_files": 9,
        "clipped_samples": 0,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True,
                   help="Existing local M5-chaos-blind-github-001")
    p.add_argument("--out", type=Path, required=True,
                   help="NEW private sibling directory")
    args = p.parse_args()
    try:
        print(json.dumps(build(args.source, args.out), indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError,
            wave.Error, zipfile.BadZipFile) as exc:
        p.exit(1, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
