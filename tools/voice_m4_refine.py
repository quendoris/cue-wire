#!/usr/bin/env python3
"""CueWire M4 LEVEL-002: two independent voice-dynamics A/B renders.

Takes the accepted level-001/03-voice-leveled.wav and produces soft/dense
variants. Both are derived directly from the same immutable source; neither
variant is passed into the other. No models, EQ, device changes, playback,
network, services or modifications to the established RAW/CLEAN/LEVEL-001.
The offline lookahead peak limiter is sample-peak only, NOT a true-peak meter.
"""
from __future__ import annotations

import argparse
from array import array
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

from voice_m4_level import (
    AUDIT, HOP, RATE, dbfs, frame_rms_db, pcm_metrics, percentile,
    read_pcm16, sha256, speech_mask, write_pcm16_exclusive
)

PROFILES = {
    "soft": {
        "ratio": 1.6, "knee_db": 5.0, "max_reduction_db": 3.5,
        "speech_makeup_db": 2.0, "attack_ms": 28.0, "release_ms": 200.0,
    },
    "dense": {
        "ratio": 2.0, "knee_db": 5.0, "max_reduction_db": 5.5,
        "speech_makeup_db": 3.0, "attack_ms": 18.0, "release_ms": 160.0,
    },
}


def expected_sources(source: Path, outdir: Path) -> tuple[Path, Path, Path, Path, dict]:
    """Fail closed on missing/changed provenance or overwrite attempts."""
    src = source.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in src.parents or src.name != "03-voice-leveled.wav":
        raise ValueError("Expected private M4 LEVEL-001 03-voice-leveled.wav")
    if src.parent.name != "level-001":
        raise ValueError("Only frozen M4 level-001 is accepted in this protocol")
    capture = src.parent.parent
    if outdir.exists() or outdir.is_symlink():
        raise FileExistsError("Destination already exists: use a fresh level-002/NNN")
    parent = outdir.expanduser().parent.resolve(strict=True)
    if parent != capture:
        raise ValueError("Output must be a NEW sibling directory of level-001")

    level_manifest_path = src.parent / "manifest.json"
    level_manifest = json.loads(level_manifest_path.read_text(encoding="utf-8"))
    if level_manifest.get("schema") != "cuewire-M4-level-v1":
        raise ValueError("LEVEL-001 manifest schema mismatch")
    if level_manifest.get("status") != "rendered_awaiting_auditory_review":
        raise ValueError("LEVEL-001 was not technically completed")
    if level_manifest.get("output", {}).get("file") != src.name:
        raise ValueError("LEVEL-001 output filename differs from manifest")
    if level_manifest.get("output", {}).get("sha256") != sha256(src):
        raise ValueError("LEVEL-001 audio SHA256 mismatch")

    raw = capture / "raw.wav"
    clean = capture / "clean-003/02-dpdfnet2-soft.wav"
    clean_manifest_path = clean.parent / "manifest.json"
    if (not raw.is_file() or not clean.is_file() or
            not clean_manifest_path.is_file()):
        raise FileNotFoundError("Frozen RAW/CLEAN upstream artifacts are missing")
    if sha256(raw) != level_manifest.get("raw", {}).get("sha256"):
        raise ValueError("Frozen RAW SHA256 mismatch")
    if sha256(clean) != level_manifest.get("input", {}).get("sha256"):
        raise ValueError("Frozen CLEAN SHA256 mismatch")
    if sha256(clean_manifest_path) != level_manifest.get("clean_manifest_sha256"):
        raise ValueError("Frozen CLEAN manifest SHA256 mismatch")
    if level_manifest.get("input", {}).get("path") != str(clean.resolve()):
        raise ValueError("LEVEL-001 source path differs from verified CLEAN")
    return src, parent / outdir.name, raw, clean, level_manifest


def knee_reduction(level: float, threshold: float, ratio: float,
                   knee: float, maximum: float) -> float:
    """Continuous, differentiable soft-knee downward compression curve."""
    x = level - threshold
    if x <= -knee / 2:
        return 0.0
    if x >= knee / 2:
        above = x
    else:
        above = (x + knee / 2) ** 2 / (2 * knee)
    return min(maximum, max(0.0, above * (1.0 - 1.0 / ratio)))


def release_smooth(target: list[float], attack_ms: float,
                   release_ms: float) -> list[float]:
    """Smoothed downward-only reduction in dB, 20 ms control steps."""
    attack = 1.0 - math.exp(-20 / attack_ms)
    release = 1.0 - math.exp(-20 / release_ms)
    state = 0.0
    out = []
    for value in target:
        state += (attack if value > state else release) * (value - state)
        out.append(state)
    return out


def frame_gains(levels: list[float], profile: dict
               ) -> tuple[list[float], dict, list[bool]]:
    detected, expanded, threshold_activity = speech_mask(levels)
    speech = [v for v, yes in zip(levels, detected) if yes]
    # A broad threshold across the active speech distribution compresses
    # loud phrases while leaving quieter speech largely uncompressed.
    threshold = percentile(speech, 0.60)
    targets = [
        knee_reduction(level, threshold, profile["ratio"],
                       profile["knee_db"], profile["max_reduction_db"])
        if enabled else 0.0
        for level, enabled in zip(levels, expanded)
    ]
    reductions = release_smooth(targets, profile["attack_ms"],
                                profile["release_ms"])
    # No general gain on long quiet pauses. 120 ms pre-roll supplied by
    # speech_mask protects starts of quiet spoken words.
    makeup_control = [profile["speech_makeup_db"] if yes else 0.0
                      for yes in expanded]
    makeup = []
    current = 0.0
    fade_in = 1.0 - math.exp(-20 / 60.0)
    fade_out = 1.0 - math.exp(-20 / 250.0)
    for desired in makeup_control:
        current += (fade_in if desired > current else fade_out) * (desired - current)
        makeup.append(current)
    gains = [up - reduction for up, reduction in zip(makeup, reductions)]
    return gains, {
        "rms_detector_window_ms": 20,
        "activity_threshold_dbfs": round(threshold_activity, 5),
        "speech_detection": "RMS heuristic, NOT phonetic VAD",
        "active_frames": sum(detected),
        "total_frames": len(levels),
        "active_fraction": round(sum(detected) / len(levels), 6),
        "compression_threshold_dbfs": round(threshold, 5),
        "ratio": profile["ratio"],
        "knee_db": profile["knee_db"],
        "max_compression_db": profile["max_reduction_db"],
        "measured_max_compression_db": round(max(reductions), 5),
        "speech_makeup_db": profile["speech_makeup_db"],
        "measured_max_control_gain_db": round(max(gains), 5),
        "measured_min_control_gain_db": round(min(gains), 5),
        "attack_ms": profile["attack_ms"],
        "release_ms": profile["release_ms"],
        "makeup_rise_ms": 60,
        "makeup_fall_ms": 250,
        "notes": "Compression follows RMS frame envelopes, not word boundaries",
    }, detected


def apply_offline_peak_limiter(pcm: array, gains_db: list[float],
                               peak_ceiling_dbfs: float = -2.0,
                               lookahead_ms: float = 8.0,
                               release_ms: float = 130.0
                               ) -> tuple[array, dict]:
    """Offline reverse lookahead + forward release; no hard clipping or global trim."""
    if not (-6 <= peak_ceiling_dbfs <= -1.5):
        raise ValueError("Invalid sample peak ceiling")
    if not (3 <= lookahead_ms <= 20 and 40 <= release_ms <= 500):
        raise ValueError("Invalid limiter timing")
    n = len(pcm)
    if len(gains_db) != (n + HOP - 1) // HOP:
        raise ValueError("20ms gain schedule length mismatch")
    ceiling_int = min(32766, math.floor(32768 * 10**(peak_ceiling_dbfs / 20)) - 8)
    safe_int = ceiling_int - 8
    if safe_int <= 0:
        raise ValueError("Invalid peak ceiling integer")
    gains = [10 ** (v / 20) for v in gains_db]
    pre = array("f")
    for frame, start in enumerate(range(0, n, HOP)):
        end = min(start + HOP, n)
        a = gains[frame]
        b = gains[min(frame + 1, len(gains)-1)]
        delta = (b - a) / (end-start)
        for i in range(start, end):
            pre.append(pcm[i] * (a + delta * (i-start)))
    # Attack is computed from future samples, so the limiter reacts before
    # a transient without delaying/shortening the offline output.
    attack = math.exp(-1.0 / (RATE * lookahead_ms / 1000))
    release = math.exp(-1.0 / (RATE * release_ms / 1000))
    demands = array("f", [0.0]) * n
    next_demand = 0.0
    for i in range(n - 1, -1, -1):
        magnitude = abs(pre[i])
        required = 0.0 if magnitude <= safe_int else 1.0 - safe_int / magnitude
        next_demand = max(required, next_demand * attack)
        demands[i] = next_demand

    out = array("h")
    envelope = 0.0
    max_limit_db = 0.0
    attenuation_frames = 0
    for i, v in enumerate(pre):
        envelope = max(demands[i], envelope * release)
        if envelope > 1e-5:
            attenuation_frames += 1
            max_limit_db = max(max_limit_db, -dbfs(1 - envelope))
        sample = round(v * (1 - envelope))
        if abs(sample) > ceiling_int:
            raise RuntimeError("Limiter would exceed ceiling: refusing hard clipping")
        out.append(sample)
    return out, {
        "method": "two-pass reverse lookahead and forward release",
        "ceiling_dbfs": peak_ceiling_dbfs,
        "ceiling_sample_integer": ceiling_int,
        "lookahead_ms": lookahead_ms,
        "release_ms": release_ms,
        "max_limiter_reduction_db": round(max_limit_db, 5),
        "limiter_active_fraction": round(attenuation_frames/n, 6),
        "output_exactly_input_frames": len(out) == n,
        "true_peak_certified": False,
    }


def active_spread(pcm: array, reference_detected: list[bool]) -> dict:
    values = frame_rms_db(pcm)
    if len(values) != len(reference_detected):
        raise RuntimeError("Voice activity comparison lost frame alignment")
    active = [value for value, flag in zip(values, reference_detected) if flag]
    return {
        "speech_p25_dbfs": round(percentile(active, 0.25), 4),
        "speech_median_dbfs": round(percentile(active, 0.50), 4),
        "speech_p90_dbfs": round(percentile(active, 0.90), 4),
        "speech_p90_minus_p25_db": round(
            percentile(active, 0.90) - percentile(active, 0.25), 4),
    }


def render(source: Path, outdir: Path, peak_ceiling_dbfs: float = -2.0) -> dict:
    source, folder, raw, clean, level_manifest = expected_sources(source, outdir)
    initial_hash = sha256(source)
    raw_hash = sha256(raw)
    clean_hash = sha256(clean)
    level_manifest_path = source.parent / "manifest.json"
    level_manifest_hash = sha256(level_manifest_path)
    samples = read_pcm16(source)
    before = pcm_metrics(samples)
    if before["clipped_samples"]:
        raise ValueError("Accepted LEVEL-001 is clipped, refusing refinement")
    levels = frame_rms_db(samples)
    outputs = {}
    folder.mkdir(mode=0o700)
    try:
        for name, profile in PROFILES.items():
            gains, info, activity = frame_gains(levels, profile)
            processed, limiter = apply_offline_peak_limiter(
                samples, gains, peak_ceiling_dbfs)
            after = pcm_metrics(processed)
            if (after["frames"] != before["frames"] or
                    after["clipped_samples"] or
                    after["sample_peak_dbfs"] > peak_ceiling_dbfs + 0.001):
                raise RuntimeError("Level render invalid frame count / clipping / peak")
            candidate = folder / ("04-" + name + ".wav")
            write_pcm16_exclusive(candidate, processed)
            if pcm_metrics(read_pcm16(candidate)) != after:
                raise RuntimeError("Output did not survive PCM round-trip")
            outputs[name] = {
                "file": candidate.name,
                "sha256": sha256(candidate),
                "metrics": after,
                "compressor": info,
                "limiter": limiter,
                "active_voice_spread": active_spread(processed, activity)
            }
            print(name + ": RMS", after["rms_dbfs"],
                  "peak", after["sample_peak_dbfs"],
                  "limiter max GR", limiter["max_limiter_reduction_db"], "dB",
                  flush=True)
        if (sha256(source) != initial_hash or sha256(raw) != raw_hash or
                sha256(clean) != clean_hash or
                sha256(level_manifest_path) != level_manifest_hash):
            raise RuntimeError("An upstream frozen artifact changed while rendering")
        report = {
            "schema": "cuewire-M4-level-refinement-v1",
            "status": "rendered_awaiting_auditory_review",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "input": {"file": str(source), "sha256": initial_hash,
                      "metrics": before},
            "upstream": {"raw_sha256": raw_hash, "clean_sha256": clean_hash,
                         "level_001_manifest_sha256": level_manifest_hash,
                         "clean_003_manifest_sha256": level_manifest["clean_manifest_sha256"]},
            "profiles": PROFILES,
            "before_active_voice_spread": active_spread(samples, activity),
            "outputs": outputs,
            "notes": [
                "Both candidates independently derive from frozen LEVEL-001.",
                "RMS and speech spread are not LUFS / perceptual quality scores.",
                "No EQ or denoising. Limiter bounds sample peaks, not true peaks.",
                "No speech detection model. Low background speech can confuse RMS masks.",
                "Offline only: no playback, PipeWire changes or automatic upload."
            ]
        }
        with (folder / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        with (folder / "SHA256SUMS").open("x", encoding="utf-8") as stream:
            for path in sorted(folder.glob("*")):
                if path.is_file() and path.name != "SHA256SUMS":
                    stream.write(sha256(path) + "  " + path.name + "\n")
        print("PASS: separate SOFT and DENSE, RAW/CLEAN/LEVEL-001 unchanged.")
        print("Results:", folder)
        return report
    except BaseException as exc:
        failed = {
            "schema": "cuewire-M4-level-refinement-v1",
            "status": "failed_or_interrupted",
            "input_sha256_at_start": initial_hash,
            "error": type(exc).__name__ + ": " + str(exc),
            "outputs_completed_before_failure": outputs,
        }
        with (folder / "failed-manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(failed, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        raise


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in", dest="source", required=True, type=Path)
    p.add_argument("--out", dest="outdir", required=True, type=Path)
    p.add_argument("--sample-peak-ceiling-dbfs", default=-2.0, type=float)
    args = p.parse_args()
    if not math.isfinite(args.sample_peak_ceiling_dbfs):
        p.error("Peak ceiling must be finite")
    try:
        render(args.source, args.outdir, args.sample_peak_ceiling_dbfs)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        p.exit(1, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
