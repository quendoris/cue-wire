#!/usr/bin/env python3
"""CueWire M5 offline FIR model ablation on immutable real capture.

Measure whether weak held-out echo suppression is caused by chosen FIR
length/regularization rather than repeating live microphone recordings.
Calibrate ONLY on speaker-only ~0.25..3.30s, evaluate held-out
~3.45..4.40s, and write numerical JSON only.

IMPORTANT: Results are exploratory. Selecting a "winner" using the same
hold-out invalidates that segment as independent validation for the
selected model. Do not use any tested setting to process voice until a
fresh independent captured speaker-only holdout is available.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import numpy as np
from scipy import signal

from voice_m5_aec import AUDIT, RATE, digest, lag_estimate, read_wav, rms, db

LENGTH_MS = (30, 60, 90, 120, 150)
RIDGE_FACTORS = (0.005, 0.02, 0.10)
BANDS = ((150, 500), (500, 1500), (1500, 4000), (4000, 8000))


def erle_db(actual: np.ndarray, estimate: np.ndarray) -> float:
    a = float(actual @ actual)
    residual = actual - estimate
    b = float(residual @ residual)
    return float(10.0 * math.log10(max(a, 1e-20) / max(b, 1e-20)))


def fit_one(aligned: np.ndarray, mic: np.ndarray, offset: int,
            taps_ms: int, ridge_factor: float) -> dict:
    """Wiener-regularized fixed FIR; gain fitted on training samples only."""
    if not (10 <= taps_ms <= 150 and 0.0 < ridge_factor <= 1):
        raise ValueError("Invalid FIR probe settings")
    train_start = offset + round(0.25 * RATE)
    train_end = offset + round(3.30 * RATE)
    held_start = offset + round(3.45 * RATE)
    held_end = offset + round(4.40 * RATE)
    if held_end > len(mic) or train_start < 0:
        raise ValueError("Missing full speaker-only calibration and holdout")
    x_train = aligned[train_start:train_end]
    mic_train = mic[train_start:train_end]
    if rms(x_train) < 0.001 or rms(mic_train) < .00003:
        raise ValueError("No effective reference/microphone calibration signal")
    nfft = 8192  # Common FFT resolution for ALL candidate models
    _, pxx = signal.welch(x_train, fs=RATE, nperseg=nfft,
                          noverlap=nfft // 2, detrend="constant")
    _, pxy = signal.csd(x_train, mic_train, fs=RATE, nperseg=nfft,
                         noverlap=nfft // 2, detrend="constant")
    ridge = max(1e-16, ridge_factor * float(np.mean(pxx)))
    taps = round(taps_ms * RATE / 1000)
    if taps > nfft - 512:
        raise ValueError("Filter taps too long relative to FFT period")
    impulse = np.fft.irfft(pxy / (pxx + ridge), nfft)[:taps].copy()
    fade = min(round(.008 * RATE), taps // 4)
    impulse[-fade:] *= np.linspace(1, 0, fade)
    prediction = signal.fftconvolve(aligned, impulse, mode="full")[:len(mic)]
    modeled_train = prediction[train_start:train_end]
    denominator = float(modeled_train @ modeled_train)
    if denominator <= 1e-12:
        raise ValueError("Degenerate predicted echo energy")
    gain = float((mic_train @ modeled_train) / denominator)
    if not math.isfinite(gain) or not 0.05 <= gain <= 3.0:
        raise ValueError(f"Training-only model gain out of range: {gain:.4g}")
    prediction *= gain
    actual_held = mic[held_start:held_end]
    pred_held = prediction[held_start:held_end]
    band_erle = {}
    for lo, hi in BANDS:
        sos = signal.butter(4, (lo, hi), btype="bandpass",
                            fs=RATE, output="sos")
        actual_band = signal.sosfiltfilt(sos, actual_held)
        pred_band = signal.sosfiltfilt(sos, pred_held)
        band_erle[f"{lo}-{hi}Hz"] = round(erle_db(actual_band, pred_band), 4)
    return {
        "fir_ms": taps_ms,
        "ridge_factor": ridge_factor,
        "training_fitted_gain": round(gain, 5),
        "training_erle_db": round(erle_db(mic_train, prediction[train_start:train_end]), 5),
        "heldout_erle_db": round(erle_db(actual_held, pred_held), 5),
        "heldout_echo_prediction_rms_dbfs": round(db(rms(pred_held)), 5),
        "heldout_mic_rms_dbfs": round(db(rms(actual_held)), 5),
        "heldout_erle_by_frequency_db": band_erle,
    }


def probe(capture_dir: Path, out_dir: Path) -> dict:
    capture_dir = capture_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if not capture_dir.is_dir() or audit not in capture_dir.parents:
        raise ValueError("Capture directory must be private under .audit")
    manifest_path = capture_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema") != "cuewire-M5-paired-physical-capture-v1" or
            manifest.get("status") != "captured_for_offline_aec"):
        raise ValueError("Capture is not approved for offline model diagnostics")
    ref_path = capture_dir / "reference-sent.wav"
    mic_path = capture_dir / "microphone-raw.wav"
    original_hashes = {
        "manifest": digest(manifest_path),
        "reference": digest(ref_path),
        "microphone": digest(mic_path),
    }
    if (original_hashes["reference"] != manifest.get("reference", {}).get("sha256")
            or original_hashes["microphone"] != manifest.get("microphone", {}).get("sha256")):
        raise ValueError("Reference or microphone SHA256 does not match manifest")
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("Output already exists; choose a fresh model-probe directory")
    dest_parent = out_dir.expanduser().parent.resolve(strict=True)
    if dest_parent != capture_dir.parent:
        raise ValueError("Output must be a sibling of the capture directory")
    output_dir = dest_parent / out_dir.name

    reference = read_wav(ref_path)
    mic = read_wav(mic_path)
    lag, correlation = lag_estimate(reference, mic, manifest["speaker_only_calibration_s"])
    offset = max(0, lag - round(.010 * RATE))
    # Read/match the first 4.6s only; local user speech later in the
    # recording cannot affect fitting or these diagnostic holdout scores.
    cal_len = min(len(mic), offset + round(4.60 * RATE))
    aligned = np.zeros(cal_len, dtype=np.float64)
    active = min(len(reference), cal_len - offset)
    aligned[offset:offset+active] = reference[:active]
    cal_mic = mic[:cal_len]
    candidates = []
    for taps in LENGTH_MS:
        for ridge in RIDGE_FACTORS:
            try:
                fit = fit_one(aligned, cal_mic, offset, taps, ridge)
            except ValueError as exc:
                candidates.append({
                    "fir_ms": taps, "ridge_factor": ridge,
                    "status": "refused",
                    "error": str(exc),
                })
            else:
                candidates.append({"status": "measured_not_validated", **fit})
    if not any(c["status"] == "measured_not_validated" for c in candidates):
        raise ValueError("All model candidates invalid; no diagnostic output produced")
    if (digest(ref_path) != original_hashes["reference"] or
            digest(mic_path) != original_hashes["microphone"] or
            digest(manifest_path) != original_hashes["manifest"]):
        raise RuntimeError("Captured sources changed during analysis")

    report = {
        "schema": "cuewire-M5-FIR-model-ablation-v1",
        "status": "exploratory_only_no_voice_output",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_hashes": original_hashes,
        "global_lag_ms": round(lag * 1000 / RATE, 5),
        "global_lag_normalized_correlation": round(correlation, 6),
        "reference_offset_samples": offset,
        "training_reference_time_s": [0.25, 3.30],
        "holdout_reference_time_s": [3.45, 4.40],
        "candidate_grid": {
            "filter_lengths_ms": list(LENGTH_MS),
            "regularization_factors": list(RIDGE_FACTORS),
            "common_spectral_fft_nperseg": 8192,
        },
        "candidates": candidates,
        "warnings": [
            "ALL candidates use the same hold-out. Never select a production model and then claim this hold-out independently validates that selection.",
            "A winning candidate requires independent fresh recording with speaker-only holdout and then near-end speech quality audit.",
            "Band-limited heldout ERLE and frequency coherence measure different things.",
            "No voice was processed. No WAV generated. No audio played or system audio defaults changed.",
        ],
    }
    output_dir.mkdir(mode=0o700)
    with (output_dir / "model-probe.json").open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")
    with (output_dir / "SHA256SUMS").open("x", encoding="utf-8") as f:
        f.write(digest(output_dir / "model-probe.json") + "  model-probe.json\n")
    print("M5 MODEL PROBE (diagnostic only; no voice WAV, no AEC threshold change)")
    print("Global lag:", report["global_lag_ms"], "ms")
    for c in candidates:
        if c["status"] != "measured_not_validated":
            print(f"{c['fir_ms']:>3}ms / ridge={c['ridge_factor']:.3f}: rejected")
        else:
            print(f"{c['fir_ms']:>3}ms / ridge={c['ridge_factor']:.3f}: "
                  f"train={c['training_erle_db']:+.3f}dB "
                  f"holdout={c['heldout_erle_db']:+.3f}dB")
    print("Audit:", output_dir / "model-probe.json")
    return report


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    try:
        probe(args.capture, args.out)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        p.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
