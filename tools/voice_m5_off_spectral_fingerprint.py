#!/usr/bin/env python3
"""CueWire M5 physical OFF high-frequency bursts: spectral fingerprint.

Reuses exactly SHA256-pinned M5 0.15/0.30 OFF/ON/OFF microphone WAVs.
For each of 40 predeclared 0.5s OFF windows (2..7 and 30..35s, both
physical runs) measure 8..16kHz Welch PSD and its four 2kHz subbands.
Flag windows >6dB over their same-phase median and characterize
whether their spectra are narrowband or broadly distributed.

This is a POST-HOC DIAGNOSTIC following observed bursts, NOT blind
AEC validation or acoustic source identification. No new audio,
recordings, devices, playback, WAV, defaults, DSP or microphone routes.
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

from voice_m5_aec import AUDIT, RATE, digest
from voice_m5_playback_coherence import (
    checked_take, LOW_MANIFEST_SHA, HIGH_MANIFEST_SHA, LOW_MIC_SHA,
    HIGH_MIC_SHA, REFERENCE_SHA, SOURCE_MANIFEST_SHA,
)

OFF_PHASES = (("before", 2.0, 7.0), ("after", 30.0, 35.0))
WINDOW_S = 0.5
BAND_HZ = (8000, 16000)
SUBBANDS_HZ = ((8000, 10000), (10000, 12000),
               (12000, 14000), (14000, 16000))
WELCH_NPERSEG = 4096
THRESHOLD_DB_ABOVE_MEDIAN = 6.0


def window_fingerprint(samples: np.ndarray) -> dict:
    """One 0.5s physical mono signal; normalize only to form spectral shape."""
    if (samples.ndim != 1 or len(samples) != round(WINDOW_S * RATE)
            or not np.all(np.isfinite(samples))):
        raise ValueError("Expected exactly one finite 0.5s raw physical MIC window")
    freqs, psd = signal.welch(
        samples, fs=RATE, nperseg=WELCH_NPERSEG,
        noverlap=WELCH_NPERSEG // 2, detrend="constant")
    keep = (freqs >= BAND_HZ[0]) & (freqs < BAND_HZ[1])
    power = psd[keep]
    if len(power) < 20 or not np.all(np.isfinite(power)):
        raise ValueError("Invalid high-frequency Welch spectrum")
    freq = freqs[keep]
    df = float(freqs[1] - freqs[0])
    band_power = float(np.sum(power) * df)
    if band_power <= 1e-25:
        raise ValueError("High-frequency physical mic power too low")
    spectral_shape = np.maximum(power, 0.) / max(float(np.sum(power)), 1e-30)
    flatness = float(np.exp(np.mean(np.log(np.maximum(power, 1e-30)))) /
                     max(float(np.mean(power)), 1e-30))
    entropy = float(-np.sum(spectral_shape *
                           np.log(np.maximum(spectral_shape, 1e-30))) /
                    math.log(len(spectral_shape)))
    top_count = max(1, math.ceil(len(spectral_shape) * .01))
    concentration = float(np.sum(np.sort(spectral_shape)[-top_count:]))
    top_freq = float(freq[int(np.argmax(power))])
    bands = []
    for low, high in SUBBANDS_HZ:
        k = (freq >= low) & (freq < high)
        fraction = float(np.sum(spectral_shape[k]))
        bands.append({
            "band_hz": [low, high],
            "fraction_of_8_16khz_spectral_power": round(fraction, 6),
            "band_power_dbfs": round(
                10 * math.log10(max(float(np.sum(power[k]) * df), 1e-30)), 5),
        })
    return {
        "full_highband_power_dbfs": round(10 * math.log10(band_power), 5),
        "highest_welch_psd_frequency_hz": round(top_freq, 4),
        "spectral_flatness_0_to_1": round(flatness, 6),
        "normalized_spectral_entropy_0_to_1": round(entropy, 6),
        "top_one_percent_frequency_bins_power_fraction":
            round(concentration, 6),
        "subbands": bands,
    }


def one_phase(mic: np.ndarray, label: str,
              phase_start: float, phase_end: float) -> dict:
    step = round(WINDOW_S * RATE)
    start, end = round(phase_start * RATE), round(phase_end * RATE)
    if (start < 0 or end > len(mic) or end-start != step*10):
        raise ValueError("OFF phase must span ten valid 0.5s windows")
    metrics = []
    for i in range(10):
        data = mic[start+i*step:start+(i+1)*step]
        item = window_fingerprint(data)
        item["mic_wav_time_s"] = [
            round(phase_start+i*WINDOW_S, 5),
            round(phase_start+(i+1)*WINDOW_S, 5)
        ]
        metrics.append(item)
    levels = np.array([item["full_highband_power_dbfs"] for item in metrics])
    median = float(np.median(levels))
    anomalies = [
        {**item,
         "level_above_phase_median_db": round(item["full_highband_power_dbfs"]-median, 5)}
        for item in metrics if item["full_highband_power_dbfs"] > median+THRESHOLD_DB_ABOVE_MEDIAN
    ]
    # This is only a descriptive summary of spectral shape. A narrow
    # frequency peak is NOT proof of electrical interference, and
    # broadband structure is NOT proof of a real room sound.
    return {
        "label": label,
        "mic_wav_interval_s": [phase_start, phase_end],
        "num_windows": 10,
        "median_8_16khz_welch_power_dbfs": round(median, 5),
        "threshold_db_over_phase_median": THRESHOLD_DB_ABOVE_MEDIAN,
        "num_windows_above_threshold": len(anomalies),
        "windows": metrics,
        "elevated_windows": anomalies,
    }


def analyze(low_dir: Path, high_dir: Path, output_dir: Path) -> dict:
    low_dir = low_dir.expanduser().resolve(strict=True)
    high_dir = high_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if (low_dir == high_dir or audit not in low_dir.parents
            or audit not in high_dir.parents):
        raise ValueError("Need two distinct private SHA256-pinned OFF/ON/OFF captures")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Output directory exists; refuse to overwrite")
    parent = output_dir.expanduser().parent.resolve(strict=True)
    if parent != high_dir.parent:
        raise ValueError("New numeric output must be a sibling of the source captures")
    m_low, h_low, x_low = checked_take(
        low_dir, LOW_MANIFEST_SHA, LOW_MIC_SHA, .15)
    m_high, h_high, x_high = checked_take(
        high_dir, HIGH_MANIFEST_SHA, HIGH_MIC_SHA, .30)
    for key in ("source_mic_name", "speaker_sink_name", "reference_source_capture"):
        if m_low.get(key) != m_high.get(key):
            raise ValueError("Physical mic/sink/reference mismatch")
    source_dir = Path(m_low["reference_source_capture"]).expanduser().resolve(strict=True)
    if audit not in source_dir.parents:
        raise ValueError("Linked music reference must stay inside private .audit")
    reference = source_dir/"reference-sent.wav"
    source_manifest = source_dir/"manifest.json"
    if digest(reference) != REFERENCE_SHA or digest(source_manifest) != SOURCE_MANIFEST_SHA:
        raise ValueError("Immutable source reference SHA256 mismatch")
    result = {}
    for name, manifest, mic in (
            ("volume_015", m_low, x_low), ("volume_030", m_high, x_high)):
        phases = {}
        for label, start, end in OFF_PHASES:
            phases[label] = one_phase(mic, label, start, end)
        result[name] = {
            "per_stream_volume": manifest["speaker_play_volume"],
            "speaker_off": phases
        }
    input_hashes = {
        "level015_manifest": h_low["manifest"],
        "level015_mic": h_low["physical_mic_raw"],
        "level030_manifest": h_high["manifest"],
        "level030_mic": h_high["physical_mic_raw"],
        "reference": REFERENCE_SHA,
        "reference_manifest": SOURCE_MANIFEST_SHA,
    }
    input_paths = {
        "level015_manifest": low_dir/"manifest.json",
        "level015_mic": low_dir/"microphone-aba-raw.wav",
        "level030_manifest": high_dir/"manifest.json",
        "level030_mic": high_dir/"microphone-aba-raw.wav",
        "reference": reference,
        "reference_manifest": source_manifest,
    }
    if any(digest(input_paths[name]) != sha for name, sha in input_hashes.items()):
        raise RuntimeError("A private source file changed during the spectral audit")
    report = {
        "schema": "cuewire-M5-OFF-highband-spectral-fingerprint-v1",
        "status": "diagnostic_after_observed_highband_bursts_not_independent",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": input_hashes,
        "only_speaker_off_mic_intervals_s": [list(p[1:]) for p in OFF_PHASES],
        "window_s": WINDOW_S,
        "welch_nperseg": WELCH_NPERSEG,
        "welch_overlap": WELCH_NPERSEG//2,
        "frequency_band_hz": list(BAND_HZ),
        "frequency_subbands_hz": [list(x) for x in SUBBANDS_HZ],
        "selection_median_plus_db": THRESHOLD_DB_ABOVE_MEDIAN,
        **result,
        "microphone_voice_analyzed": False,
        "aec_model_fitted_or_changed": False,
        "virtual_mic_approved": False,
        "historical_0_67881_db_gate_remains_failed": True,
        "warnings": [
            "Selected >6dB anomalies are post-hoc relative to the same phase and not an independent hypothesis test.",
            "Welch PSD in half-second windows is not the zero-phase Butterworth bandpower estimator from the earlier OFF-window report.",
            "Spectral flatness/entropy/peak concentration describe digital PSD shape, NOT microphone artifact cause.",
            "Fixed 8-16kHz bands are not an estimate of actual clean human speech or perceptual audio quality.",
            "All source recordings were byte hash-verified against their local manifests and remained private.",
            "No playback, new recording, system device changes, AEC retiming, audio output, or WAV export was performed.",
        ],
    }
    out = parent / output_dir.name
    out.mkdir(mode=0o700)
    path = out/"off-spectral-fingerprint.json"
    with path.open("x", encoding="utf-8") as f:
        json.dump(report, f, sort_keys=True, ensure_ascii=False, indent=2)
        f.write("\n")
    with (out/"SHA256SUMS").open("x", encoding="utf-8") as f:
        f.write(digest(path)+"  off-spectral-fingerprint.json\n")
    print("M5 highband spectral fingerprint - OFF-only, numeric, no mic or audio IO")
    for name in ("volume_015", "volume_030"):
        for phase in ("before", "after"):
            data = report[name]["speaker_off"][phase]
            print(name, phase, "elevated windows",
                  [r["mic_wav_time_s"] for r in data["elevated_windows"]])
            for row in data["elevated_windows"]:
                print("  time", row["mic_wav_time_s"],
                      "PSD peak", row["highest_welch_psd_frequency_hz"], "Hz",
                      "flatness", row["spectral_flatness_0_to_1"],
                      "top1pct", row["top_one_percent_frequency_bins_power_fraction"])
    print("No voice processed. Original 0.67881dB FAIL unchanged.")
    print("Report:", path)
    return report


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--low-capture", type=Path, required=True)
    p.add_argument("--high-capture", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    try:
        analyze(args.low_capture, args.high_capture, args.out)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        p.exit(1, "ERROR: "+str(exc)+"\n")


if __name__ == "__main__":
    main()
