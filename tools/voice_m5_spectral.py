#!/usr/bin/env python3
"""M5 spectral AEC diagnosis: offline numerical report, no voice processing.

Uses only the speaker-only first ~4.4 seconds of the immutable reference
and microphone capture, estimates broadband coherence and local lag drift.
No inferred calibration is accepted as proof of voice quality. No audio,
playback, PipeWire actions, devices, or existing files are modified.
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

from voice_m5_aec import AUDIT, RATE, db, digest, lag_estimate, read_wav, rms

BANDS = ((150, 500), (500, 1500), (1500, 4000), (4000, 8000))
SPAN_SEC = (0.30, 4.35)
LOCAL_WINDOWS = ((0.30, 1.20), (1.35, 2.25), (2.40, 3.30), (3.40, 4.30))


def correlation_windows(x: np.ndarray, y: np.ndarray, peak_lag: int) -> list[dict]:
    """Try +/- 10 ms near peak lag, independently within four speaker-only spans."""
    margin = int(RATE * 0.010)
    records = []
    for start_s, end_s in LOCAL_WINDOWS:
        a = round(start_s * RATE)
        b = round(end_s * RATE)
        speaker = x[a:b].copy()
        lo = a + peak_lag - margin
        hi = b + peak_lag + margin
        if lo < 0 or hi > len(y):
            raise ValueError("Insufficient microphone samples for local lag estimate")
        mic = y[lo:hi].copy()
        speaker -= np.mean(speaker)
        mic -= np.mean(mic)
        if rms(speaker) < 1e-5 or rms(mic) < 1e-6:
            raise ValueError("Speaker-only window too quiet to measure local lag")
        sums = np.r_[0.0, np.cumsum(mic * mic)]
        energy = sums[len(speaker):] - sums[:-len(speaker)]
        correlation = signal.correlate(mic, speaker, mode="valid", method="fft")
        score = np.abs(correlation) / np.sqrt(
            np.maximum(energy * float(speaker @ speaker), 1e-20))
        k = int(np.argmax(score))
        local_lag = peak_lag - margin + k
        # Identify alternative correlation peaks without fitting or
        # promoting any of them into the acoustic cancellation filter.
        # Independent candidates must be >=0.8 ms apart; adding the
        # global-lag candidate explicitly helps distinguish ambiguous
        # music/reverberation peaks from a genuine clock trend.
        spacing = max(1, round(0.0008 * RATE))
        peak_indices, _ = signal.find_peaks(score, distance=spacing)
        peak_indices = np.unique(np.r_[peak_indices, 0, len(score)-1, k, margin])
        ranked = sorted((int(i) for i in peak_indices),
                        key=lambda i: float(score[i]), reverse=True)
        kept = []
        for idx in ranked:
            if all(abs(idx - previous) >= spacing for previous in kept):
                kept.append(idx)
            if len(kept) >= 5:
                break
        options = [{
            "relative_shift_from_global_ms": round(
                (idx - margin) * 1000 / RATE, 5),
            "candidate_lag_ms": round(
                (peak_lag - margin + idx) * 1000 / RATE, 5),
            "normalized_correlation": round(float(score[idx]), 6)
        } for idx in kept]
        dominance = (float(score[kept[0]]) / max(float(score[kept[1]]), 1e-12)
                     if len(kept) >= 2 else None)
        records.append({
            "reference_interval_s": [start_s, end_s],
            "estimated_local_lag_ms": round(local_lag * 1000 / RATE, 5),
            "difference_from_initial_lag_ms": round(
                (local_lag - peak_lag) * 1000 / RATE, 5),
            "normalized_correlation": round(float(score[k]), 6),
            "correlation_at_global_lag": round(float(score[margin]), 6),
            "top_alternative_peaks": options,
            "best_to_second_separated_peak_ratio": (
                round(dominance, 5) if dominance is not None else None),
            "candidate_minimum_spacing_ms": round(spacing * 1000 / RATE, 5),
            "searched_relative_offset_ms": 10
        })
    return records


def band_lag_windows(x: np.ndarray, y: np.ndarray,
                     lag: int) -> list[dict]:
    """Compare ambiguous correlations after zero-phase *offline* band filtering.

    This is a diagnostics-only experiment. Filter both x and y identically
    to avoid introducing a relative phase delay. All measurements use only
    the original speaker-only calibration windows. Do not use a selected
    band-dependent lag to generate a voice-subtraction candidate.
    """
    diagnostic_bands = ((70, 180), (180, 500), (500, 1500),
                        (1500, 4000), (4000, 8000))
    results = []
    for low, high in diagnostic_bands:
        sos = signal.butter(4, [low, high], btype="bandpass",
                            fs=RATE, output="sos")
        reference = signal.sosfiltfilt(sos, x)
        microphone = signal.sosfiltfilt(sos, y)
        a = round(0.3 * RATE)
        b = round(4.3 * RATE)
        ref_rms = rms(reference[a:b])
        mic_rms = rms(microphone[a+lag:b+lag])
        item = {
            "band_hz": [low, high],
            "reference_band_rms_dbfs": round(db(ref_rms), 5),
            "microphone_band_rms_dbfs": round(db(mic_rms), 5),
            "filter": "fourth_order_butterworth_zero_phase_sosfiltfilt",
            "interpretation": "exploratory only, not an AEC timing estimate"
        }
        # Low-level band-only correlation peaks can be dominated by
        # numerical noise or unexcited frequencies.
        if ref_rms < 10**(-75/20) or mic_rms < 10**(-90/20):
            item["status"] = "insufficient_band_energy"
        else:
            try:
                local = correlation_windows(reference, microphone, lag)
            except ValueError as exc:
                item["status"] = "unreliable_window"
                item["reason"] = str(exc)
            else:
                item["status"] = "measured_not_validated"
                item["local_windows"] = [{
                    "reference_interval_s": entry["reference_interval_s"],
                    "best_lag_ms": entry["estimated_local_lag_ms"],
                    "best_peak_score": entry["normalized_correlation"],
                    "global_lag_score": entry["correlation_at_global_lag"],
                    "best_to_second_peak_ratio":
                        entry["best_to_second_separated_peak_ratio"],
                } for entry in local]
        results.append(item)
    return results


def spectrum_summary(x: np.ndarray, y: np.ndarray, lag: int) -> dict:
    """Magnitude-squared coherence over only first 4.35 s, already lag-aligned."""
    a = round(SPAN_SEC[0] * RATE)
    b = round(SPAN_SEC[1] * RATE)
    if b + lag > len(y) or b > len(x):
        raise ValueError("Calibration signal too short")
    ref = x[a:b]
    mic = y[a + lag:b + lag]
    nperseg = 4096
    overlap = 2048
    freqs, coherence = signal.coherence(
        ref, mic, fs=RATE, nperseg=nperseg, noverlap=overlap)
    _, reference_spectrum = signal.welch(
        ref, fs=RATE, nperseg=nperseg, noverlap=overlap)
    regions = []
    for low, high in BANDS:
        mask = (freqs >= low) & (freqs < high)
        vals = coherence[mask]
        weight = reference_spectrum[mask]
        if not len(vals):
            continue
        weighted = float(np.dot(vals, weight) / max(float(np.sum(weight)), 1e-30))
        regions.append({
            "band_hz": [low, high],
            "median_magnitude_squared_coherence": round(float(np.median(vals)), 6),
            "reference_energy_weighted_coherence": round(weighted, 6),
            "mic_rms_dbfs_whole_window": round(db(rms(mic)), 5),
            "reference_rms_dbfs_whole_window": round(db(rms(ref)), 5)
        })
    return {
        "window_s_reference_time": list(SPAN_SEC),
        "window_length_s": round((b-a) / RATE, 4),
        "nperseg": nperseg,
        "noverlap": overlap,
        "bands": regions
    }


def analyze(capture_dir: Path, target_dir: Path) -> dict:
    capture_dir = capture_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in capture_dir.parents or not capture_dir.is_dir():
        raise ValueError("Capture must be under local private .audit")
    if target_dir.exists() or target_dir.is_symlink():
        raise FileExistsError("Diagnostics output already exists")
    parent = target_dir.expanduser().parent.resolve(strict=True)
    if parent != capture_dir.parent:
        raise ValueError("Diagnostics must be a new sibling of capture")
    manifest_path = capture_dir / "manifest.json"
    capture = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (capture.get("schema") != "cuewire-M5-paired-physical-capture-v1"
            or capture.get("status") != "captured_for_offline_aec"):
        raise ValueError("Capture not successfully completed")
    reference_path = capture_dir / "reference-sent.wav"
    mic_path = capture_dir / "microphone-raw.wav"
    source_hashes = {
        "capture_manifest": digest(manifest_path),
        "reference": digest(reference_path),
        "microphone": digest(mic_path),
    }
    if source_hashes["reference"] != capture.get("reference", {}).get("sha256"):
        raise ValueError("Reference SHA256 mismatch")
    if source_hashes["microphone"] != capture.get("microphone", {}).get("sha256"):
        raise ValueError("Microphone SHA256 mismatch")
    x = read_wav(reference_path)
    y = read_wav(mic_path)
    lag, corr = lag_estimate(x, y, capture["speaker_only_calibration_s"])
    local = correlation_windows(x, y, lag)
    coherence = spectrum_summary(x, y, lag)
    band_lag = band_lag_windows(x, y, lag)
    if (source_hashes["reference"] != digest(reference_path) or
            source_hashes["microphone"] != digest(mic_path) or
            source_hashes["capture_manifest"] != digest(manifest_path)):
        raise RuntimeError("Source changed during analysis")
    output = {
        "schema": "cuewire-M5-spectral-diagnostics-v3",
        "status": "diagnostic_only_not_aec_accepted",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "sources": source_hashes,
        "global_peak_lag_ms": round(lag * 1000 / RATE, 5),
        "global_peak_correlation": round(corr, 6),
        "local_lag_windows": local,
        "spectrum": coherence,
        "frequency_selected_lag_diagnostics": band_lag,
        "caveats": [
            "Magnitude-squared coherence is not ERLE and does not measure voice preservation.",
            "Coherence can be biased in short records, and low energy bands are unreliable.",
            "Local lag estimates on musical audio may jump from periodicity, reflections or noise.",
            "Alternative peaks are exploratory and are not independent model-validation evidence.",
            "Band-limited lag estimates may also pick harmonics and do not measure AEC quality.",
            "Only the speaker-only calibration segment is used, never the user's voice.",
            "No WAVs created, no model threshold lowered, no source files changed."
        ]
    }
    out = parent / target_dir.name
    out.mkdir(mode=0o700)
    with (out / "spectral.json").open("x", encoding="utf-8") as f:
        json.dump(output, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    with (out / "SHA256SUMS").open("x", encoding="utf-8") as f:
        f.write(digest(out / "spectral.json") + "  spectral.json\n")
    print("M5 SPECTRAL DIAGNOSTICS ONLY: no WAV, no AEC gate bypass.")
    print("Global lag", output["global_peak_lag_ms"], "ms;",
          "correlation", output["global_peak_correlation"])
    for entry in local:
        print("Lag window", entry["reference_interval_s"],
              "lag_ms", entry["estimated_local_lag_ms"],
              "correlation", entry["normalized_correlation"],
              "global_candidate", entry["correlation_at_global_lag"],
              "peak_ratio", entry["best_to_second_separated_peak_ratio"])
    for entry in coherence["bands"]:
        print("Band", entry["band_hz"], "coherence median",
              entry["median_magnitude_squared_coherence"],
              "reference-weighted", entry["reference_energy_weighted_coherence"])
    for band in band_lag:
        if band["status"] == "measured_not_validated":
            print("Band-lag", band["band_hz"], "ms",
                  [w["best_lag_ms"] for w in band["local_windows"]],
                  "global_scores",
                  [w["global_lag_score"] for w in band["local_windows"]])
        else:
            print("Band-lag", band["band_hz"], band["status"])
    print("Report:", out / "spectral.json")
    return output


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    try:
        analyze(args.capture, args.out)
    except (OSError, KeyError, RuntimeError, ValueError) as exc:
        p.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
