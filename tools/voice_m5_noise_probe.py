#!/usr/bin/env python3
"""CueWire M5: compare quiet-room and speaker-only mic power WITHOUT playback.

Use SHA256-verified private WAV files; calculate short-window RMS distributions.
This is NOT a contemporaneous noise/speaker A-B experiment and cannot certify
a physical echo floor or effective ERLE bound.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import numpy as np

from voice_m5_aec import AUDIT, RATE, digest, lag_estimate, read_wav, rms, db

CHUNK_SECONDS = 0.5


def powers(signal: np.ndarray, start: int, end: int) -> list[float]:
    samples_per_chunk = round(CHUNK_SECONDS * RATE)
    if start < 0 or end > len(signal) or end - start < samples_per_chunk:
        raise ValueError("Insufficient PCM data for half-second windows")
    return [float(np.mean(signal[i:i+samples_per_chunk] ** 2))
            for i in range(start, end - samples_per_chunk + 1,
                           samples_per_chunk)]


def description(values: list[float]) -> dict:
    if not values:
        raise ValueError("No valid RMS windows")
    levels = np.array(values, dtype=np.float64)
    return {
        "windows": len(levels),
        "window_duration_s": CHUNK_SECONDS,
        "p10_rms_dbfs": round(db(math.sqrt(float(np.percentile(levels, 10)))), 5),
        "p50_rms_dbfs": round(db(math.sqrt(float(np.percentile(levels, 50)))), 5),
        "p90_rms_dbfs": round(db(math.sqrt(float(np.percentile(levels, 90)))), 5),
        "minimum_rms_dbfs": round(db(math.sqrt(float(np.min(levels)))), 5),
        "maximum_rms_dbfs": round(db(math.sqrt(float(np.max(levels)))), 5),
        "mean_power_rms_dbfs": round(db(math.sqrt(float(np.mean(levels)))), 5),
        "window_rms_dbfs": [round(db(math.sqrt(float(p))), 5) for p in values],
    }


def analyze(baseline_dir: Path, capture_dir: Path, output_dir: Path) -> dict:
    baseline_dir = baseline_dir.expanduser().resolve(strict=True)
    capture_dir = capture_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in baseline_dir.parents or audit not in capture_dir.parents:
        raise ValueError("Both captures must be private under local .audit")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Output already exists, choose a fresh directory")
    parent = output_dir.expanduser().parent.resolve(strict=True)
    if parent != capture_dir.parent:
        raise ValueError("Output must be a sibling of paired capture")

    baseline_manifest = baseline_dir / "manifest.json"
    capture_manifest = capture_dir / "manifest.json"
    before = json.loads(baseline_manifest.read_text(encoding="utf-8"))
    after = json.loads(capture_manifest.read_text(encoding="utf-8"))
    if (before.get("schema") != "cuewire-M4-raw-capture-v2" or
            before.get("status") != "recorded_unprocessed_raw"):
        raise ValueError("Baseline was not a successfully recorded RAW capture")
    if (after.get("schema") != "cuewire-M5-paired-physical-capture-v1" or
            after.get("status") != "captured_for_offline_aec"):
        raise ValueError("Speaker capture was not a successful M5 pair")
    if before.get("mic_node_name") != after.get("mic_capture", {}).get("mic_source_name"):
        raise ValueError("Different microphone node names; cannot compare")
    baseline_path = baseline_dir / before.get("wav", "raw.wav")
    mic_path = capture_dir / "microphone-raw.wav"
    reference_path = capture_dir / "reference-sent.wav"
    h = {
        "baseline_manifest": digest(baseline_manifest),
        "capture_manifest": digest(capture_manifest),
        "baseline_raw": digest(baseline_path),
        "capture_mic": digest(mic_path),
        "capture_reference": digest(reference_path),
    }
    if (h["baseline_raw"] != before.get("wav_sha256") or
            h["capture_mic"] != after.get("microphone", {}).get("sha256") or
            h["capture_reference"] != after.get("reference", {}).get("sha256")):
        raise ValueError("Input WAV SHA256 does not match capture manifest")
    baseline = read_wav(baseline_path)
    mic = read_wav(mic_path)
    reference = read_wav(reference_path)
    lag, corr = lag_estimate(
        reference, mic, after["speaker_only_calibration_s"])
    # M5 fit calibrated 10 ms before strongest direct/reflected peak.
    offset = max(0, lag - round(.010 * RATE))
    baseline_powers = powers(baseline, round(.5 * RATE),
                             len(baseline) - round(.5 * RATE))
    # Strictly speaker-only, same section as the original AEC calibration
    # train and validation. This is NOT speech, which starts after 5 sec.
    speaker_start = offset + round(.25 * RATE)
    speaker_end = offset + round(4.40 * RATE)
    speaker_powers = powers(mic, speaker_start, speaker_end)
    baseline_desc = description(baseline_powers)
    speaker_desc = description(speaker_powers)
    whole_baseline_level = db(rms(baseline))
    on_level = db(rms(mic[speaker_start:speaker_end]))
    observed_diff = on_level - whole_baseline_level
    # Only meaningful if noise in the two different takes remained
    # stationary, uncorrelated, and with the same input gain.
    noise_power_fraction = min(1., 10 ** (-observed_diff / 10))
    result = {
        "schema": "cuewire-M5-noise-floor-window-comparison-v1",
        "status": "diagnostic_only_no_aec_model_selected",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input_sha256": h,
        "global_lag_ms": round(lag * 1000 / RATE, 5),
        "lag_correlation": round(corr, 6),
        "speaker_only_reference_window_s": [0.25, 4.40],
        "baseline_entire_wav_rms_dbfs": round(whole_baseline_level, 5),
        "speaker_only_mic_window_rms_dbfs": round(on_level, 5),
        "difference_rms_db": round(observed_diff, 5),
        "conditional_background_power_fraction": round(noise_power_fraction, 6),
        "baseline_time_windows": baseline_desc,
        "speaker_on_time_windows": speaker_desc,
        "limitations": [
            "Baseline and speaker-on measurements were recorded at different times.",
            "A difference in dB does not prove signal source proportions.",
            "Source name equality does not prove constant hardware gain, position, or DSP state.",
            "Percentiles describe half-second PCM powers, not frequency-specific echo.",
            "Conditional background fraction assumes stationary, independent noise.",
            "No microphone audio is played, altered, routed, or saved in this report.",
        ]
    }
    if any(digest(path) != h[label] for label, path in [
            ("baseline_manifest", baseline_manifest),
            ("capture_manifest", capture_manifest),
            ("baseline_raw", baseline_path),
            ("capture_mic", mic_path),
            ("capture_reference", reference_path)]):
        raise RuntimeError("Source changed during analysis")
    output = parent / output_dir.name
    output.mkdir(mode=0o700)
    report_path = output / "noise-probe.json"
    with report_path.open("x", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    with (output / "SHA256SUMS").open("x", encoding="utf-8") as f:
        f.write(digest(report_path) + "  noise-probe.json\n")
    print("M5 NOISE FLOOR DIAGNOSTICS: no sound and no WAV generated")
    print("Baseline whole-record RMS:", round(whole_baseline_level, 3), "dBFS")
    print("Speaker-only M5 interval:", round(on_level, 3), "dBFS")
    print("Observed difference:", round(observed_diff, 3), "dB")
    print("Half-second baseline p10/p50/p90 (dBFS):",
          baseline_desc["p10_rms_dbfs"], baseline_desc["p50_rms_dbfs"],
          baseline_desc["p90_rms_dbfs"])
    print("Half-second speaker-on p10/p50/p90 (dBFS):",
          speaker_desc["p10_rms_dbfs"], speaker_desc["p50_rms_dbfs"],
          speaker_desc["p90_rms_dbfs"])
    print("Report:", report_path)
    return result


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--capture", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    try:
        analyze(args.baseline, args.capture, args.out)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        p.exit(1, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
