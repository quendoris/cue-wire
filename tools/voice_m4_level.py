#!/usr/bin/env python3
"""CueWire M4: offline speech-aware gain leveling AFTER frozen CLEAN-003.

Input: validated 02-dpdfnet2-soft.wav only. This is not a denoiser, ASR,
LUFS meter, true-peak limiter, or live microphone DSP. It never changes
PipeWire, ALSA, defaults, config, original WAVs or any old run directory.

Pure Python standard library; 48 kHz mono PCM16, 1..300 s.
The level estimator works on 20 ms RMS windows, identifies likely speech,
uses gently smoothed speech-only makeup and preserves long quiet pauses.
A conservative global sample-peak guard attenuates the finished signal
rather than clipping transients. This does not guarantee a true-peak cap.
"""
from __future__ import annotations

import argparse
from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import wave

RATE = 48000
HOP = 960  # 20 ms
MAX_SECONDS = 300
ROOT = Path(__file__).resolve().parents[1]
AUDIT = (ROOT / ".audit").resolve()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def dbfs(linear: float) -> float:
    return 20.0 * math.log10(max(float(linear), 1e-12))


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("No measured frames")
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lo = int(index)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def read_pcm16(path: Path) -> array:
    with wave.open(str(path), "rb") as wav:
        fmt = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(),
               wav.getcomptype())
        if fmt != (RATE, 1, 2, "NONE"):
            raise ValueError(f"Expected mono 48kHz PCM16, got {fmt}")
        frames = wav.getnframes()
        if frames < RATE or frames > RATE * MAX_SECONDS:
            raise ValueError("WAV duration must be 1..300 s")
        data = wav.readframes(frames)
        if len(data) != 2 * frames:
            raise ValueError("Truncated WAV")
    pcm = array("h")
    pcm.frombytes(data)
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm


def write_pcm16_exclusive(path: Path, pcm: array) -> None:
    with path.open("xb") as output:
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            if sys.byteorder != "little":
                pcm = array("h", pcm)
                pcm.byteswap()
            wav.writeframes(pcm.tobytes())


def pcm_metrics(pcm: array) -> dict:
    count = len(pcm)
    if not count:
        raise ValueError("Empty PCM")
    square = sum(int(v) * int(v) for v in pcm)
    peak = max(abs(v) for v in pcm)
    first = pcm[:min(count, RATE * 5)]
    first_rms = math.sqrt(sum(int(v)*int(v) for v in first) / len(first)) / 32768
    return {
        "frames": count,
        "duration_s": round(count / RATE, 6),
        "rms_dbfs": round(dbfs(math.sqrt(square / count) / 32768), 5),
        "sample_peak_dbfs": round(dbfs(peak / 32768), 5),
        "first_5s_rms_dbfs": round(dbfs(first_rms), 5),
        "clipped_samples": sum(abs(v) >= 32767 for v in pcm),
    }


def frame_rms_db(pcm: array) -> list[float]:
    levels = []
    for start in range(0, len(pcm), HOP):
        end = min(start + HOP, len(pcm))
        square = sum(int(v) * int(v) for v in pcm[start:end])
        levels.append(dbfs(math.sqrt(square / (end-start)) / 32768))
    return levels


def speech_mask(levels: list[float]) -> tuple[list[bool], list[bool], float]:
    """Conservative RMS activity heuristic, NOT a speech recognizer / VAD."""
    quiet = percentile(levels, 0.20)
    speech = percentile(levels, 0.85)
    threshold = max(-55.0, quiet + 12.0, speech - 19.0)
    threshold = min(-24.0, threshold)
    detected = [v >= threshold for v in levels]
    if sum(detected) < max(10, int(len(levels) * 0.04)):
        raise ValueError("Too few credible speech frames; refusing to boost noise")
    # Join <=180 ms gaps to preserve quiet consonants between syllables.
    merged = detected[:]
    indices = [i for i, yes in enumerate(detected) if yes]
    for left, right in zip(indices, indices[1:]):
        if right - left <= 10:
            for j in range(left, right):
                merged[j] = True
    # Pre-roll 120 ms and release protection 280 ms.
    expanded = [False] * len(levels)
    for i, yes in enumerate(merged):
        if yes:
            for j in range(max(0, i-6), min(len(levels), i+15)):
                expanded[j] = True
    return detected, expanded, threshold


def gain_schedule(levels: list[float], target_dbfs: float,
                  max_makeup_db: float) -> tuple[list[float], dict, list[bool]]:
    detected, active, threshold = speech_mask(levels)
    actual_speech = [level for level, yes in zip(levels, detected) if yes]
    reference = statistics.median(actual_speech)
    # Whole phrase is moved toward target; range limited for safety.
    makeup = min(max_makeup_db, max(-6.0, target_dbfs - reference))
    # Local gentle compression/expansion reduces speech level differences
    # without trying to bring whisper-level consonants to full vowel level.
    targets = []
    for level, yes in zip(levels, active):
        local = max(-3.0, min(2.0, (reference - level) * 0.23))
        targets.append(makeup + local if yes else 0.0)
    result_db = []
    current = 0.0
    up = 1.0 - math.exp(-0.020 / 0.080)     # 80 ms rise
    down = 1.0 - math.exp(-0.020 / 0.300)   # 300 ms fall
    for desired in targets:
        current += (up if desired > current else down) * (desired - current)
        result_db.append(current)
    details = {
        "frame_ms": 20,
        "activity_threshold_dbfs": round(threshold, 4),
        "speech_detected_frames": sum(detected),
        "frames_total": len(levels),
        "speech_detected_fraction": round(sum(detected) / len(levels), 5),
        "detected_speech_median_rms_dbfs": round(reference, 4),
        "target_speech_median_rms_dbfs": target_dbfs,
        "base_makeup_db": round(makeup, 4),
        "local_adjustment_db_range": [-3.0, 2.0],
        "rise_ms": 80,
        "fall_ms": 300,
        "activity_preroll_ms": 120,
        "activity_hangover_ms": 280,
        "level_detector": "frame RMS heuristic; not certified VAD",
    }
    return result_db, details, detected


def apply_gain(pcm: array, gains_db: list[float], ceiling_dbfs: float
              ) -> tuple[array, dict]:
    if not (-6.0 <= ceiling_dbfs <= -1.5):
        raise ValueError("Ceiling range -6..-1.5 dBFS")
    gain = [10 ** (g / 20.0) for g in gains_db]
    if len(gain) != (len(pcm) + HOP - 1) // HOP:
        raise ValueError("Gain schedule length mismatch")

    # Pass 1: determine how much *global* trim is needed for all samples.
    # That leaves transients intact, unlike a hard clipper.
    provisional_peak = 0.0
    for frame, start in enumerate(range(0, len(pcm), HOP)):
        end = min(start + HOP, len(pcm))
        a = gain[frame]
        b = gain[min(frame+1, len(gain)-1)]
        delta = (b-a) / (end-start)
        for n, value in enumerate(pcm[start:end]):
            provisional_peak = max(provisional_peak, abs(value) * (a + n*delta))
    ceiling_integer = min(32766, math.floor(32768 * 10**(ceiling_dbfs / 20)) - 4)
    if provisional_peak <= 0:
        raise ValueError("Silence: cannot level")
    trim = min(1.0, ceiling_integer / provisional_peak)

    result = array("h")
    for frame, start in enumerate(range(0, len(pcm), HOP)):
        end = min(start + HOP, len(pcm))
        a = gain[frame] * trim
        b = gain[min(frame+1, len(gain)-1)] * trim
        delta = (b-a) / (end-start)
        for n, value in enumerate(pcm[start:end]):
            output = round(value * (a + n*delta))
            if abs(output) > ceiling_integer:
                raise RuntimeError("Internal sample-peak guard violation")
            result.append(output)
    return result, {
        "ceiling_dbfs": ceiling_dbfs,
        "ceiling_integer": ceiling_integer,
        "global_safety_trim_db": round(dbfs(trim), 5),
        "max_scheduled_gain_db": round(max(gains_db), 4),
        "min_scheduled_gain_db": round(min(gains_db), 4),
        "method": "global peak-safe trim after speech-aware frame gain",
        "warning": "Sample-peak only. NOT certified intersample true-peak limiter."
    }


def source_paths(input_name: Path, output_dir: Path) -> tuple[Path, Path, Path, dict]:
    source = input_name.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in source.parents or source.name != "02-dpdfnet2-soft.wav":
        raise ValueError("Input must be the private DPDFNet2 Soft output")
    clean_dir = source.parent
    provenance_file = clean_dir / "manifest.json"
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    if provenance.get("status") != "rendered_awaiting_auditory_review":
        raise ValueError("Clean run not complete; refusing to level")
    expected = provenance.get("outputs", {}).get("dpdfnet2_soft", {}).get("sha256")
    if not expected or sha256(source) != expected:
        raise ValueError("CLEAN source SHA256 differs from recorded provenance")
    raw = clean_dir.parent / "raw.wav"
    raw_hash = provenance.get("raw", {}).get("sha256")
    if not raw_hash or not raw.is_file() or sha256(raw) != raw_hash:
        raise ValueError("Original RAW SHA256 verification failed")

    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Output directory exists; use a NEW level-NNN directory")
    parent = output_dir.expanduser().parent.resolve(strict=True)
    if parent != clean_dir.parent:
        raise ValueError("Output must be a new sibling folder of clean-003")
    if audit not in parent.parents:
        raise ValueError("Output must remain in private .audit")
    return source, parent / output_dir.name, raw, provenance


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="source", required=True, type=Path)
    parser.add_argument("--out", dest="output", required=True, type=Path)
    parser.add_argument("--target-voice-rms-dbfs", type=float, default=-24.0)
    parser.add_argument("--max-makeup-db", type=float, default=16.0)
    parser.add_argument("--sample-peak-ceiling-dbfs", type=float, default=-2.0)
    args = parser.parse_args()
    nums = [args.target_voice_rms_dbfs, args.max_makeup_db,
            args.sample_peak_ceiling_dbfs]
    if any(not math.isfinite(x) for x in nums):
        parser.error("Numeric parameters must be finite")
    if not (-30 <= args.target_voice_rms_dbfs <= -20
            and 0 <= args.max_makeup_db <= 18
            and -6 <= args.sample_peak_ceiling_dbfs <= -1.5):
        parser.error("Invalid target/makeup/ceiling range")

    try:
        source, folder, raw, clean_manifest = source_paths(args.source, args.output)
        clean_hash = sha256(source)
        raw_hash = sha256(raw)
        pcm = read_pcm16(source)
        original = pcm_metrics(pcm)
        if original["clipped_samples"]:
            raise ValueError("Input already contains clipped samples")
        levels = frame_rms_db(pcm)
        gains, gain_info, detected = gain_schedule(
            levels, args.target_voice_rms_dbfs, args.max_makeup_db)
        processed, safety = apply_gain(pcm, gains, args.sample_peak_ceiling_dbfs)
        after = pcm_metrics(processed)
        if after["frames"] != original["frames"] or after["clipped_samples"]:
            raise RuntimeError("Output frame/clipping verification failed")
        if after["sample_peak_dbfs"] > args.sample_peak_ceiling_dbfs + 0.001:
            raise RuntimeError("Peak ceiling verification failed")
        if sha256(source) != clean_hash or sha256(raw) != raw_hash:
            raise RuntimeError("Original/source changed during processing; output refused")

        folder.mkdir(mode=0o700)
        result_path = folder / "03-voice-leveled.wav"
        write_pcm16_exclusive(result_path, processed)
        if pcm_metrics(read_pcm16(result_path)) != after:
            raise RuntimeError("Post-write sample integrity check failed")
        if sha256(source) != clean_hash or sha256(raw) != raw_hash:
            raise RuntimeError("Original or CLEAN source changed after write")

        manifest = {
            "schema": "cuewire-M4-level-v1",
            "status": "rendered_awaiting_auditory_review",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "approved_clean_chain": ["deepfilter", "dpdfnet2_soft"],
            "input": {"path": str(source), "sha256": clean_hash,
                      "metrics": original},
            "raw": {"path": str(raw), "sha256": raw_hash},
            "clean_manifest_sha256": sha256(source.parent / "manifest.json"),
            "parameters": {
                "target_voice_rms_dbfs": args.target_voice_rms_dbfs,
                "max_makeup_db": args.max_makeup_db,
                "sample_peak_ceiling_dbfs": args.sample_peak_ceiling_dbfs
            },
            "activity": gain_info,
            "peak_safety": safety,
            "output": {"file": result_path.name, "sha256": sha256(result_path),
                       "metrics": after},
            "notes": [
                "Active-speech RMS is a heuristic, not integrated LUFS.",
                "Speech activity inferred from RMS; no word-level semantic VAD.",
                "Global trim may prevent the requested speech target being reached.",
                "No EQ, no denoising, no replay, no device/config changes, no uploads.",
                "First five seconds are a time window, not guaranteed silent."
            ]
        }
        with (folder / "manifest.json").open("x", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
        with (folder / "SHA256SUMS").open("x", encoding="utf-8") as f:
            for name in ("03-voice-leveled.wav", "manifest.json"):
                f.write(f"{sha256(folder/name)}  {name}\n")
        print("PASS: M4 speech level rendered. RAW and CLEAN SHA256 unchanged.")
        print("Activity threshold (RMS):", gain_info["activity_threshold_dbfs"], "dBFS")
        print("Speech median before:", gain_info["detected_speech_median_rms_dbfs"], "dBFS")
        print("Base makeup:", gain_info["base_makeup_db"], "dB; peak trim:",
              safety["global_safety_trim_db"], "dB")
        print("Before RMS:", original["rms_dbfs"], "peak:", original["sample_peak_dbfs"])
        print("After  RMS:", after["rms_dbfs"], "peak:", after["sample_peak_dbfs"])
        print("Output:", result_path)
        print("Manifest:", folder / "manifest.json")
        print("No audio played, uploaded or system settings changed.")
    except (OSError, ValueError, RuntimeError, wave.Error) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
