#!/usr/bin/env python3
"""Objective, reference-based scoring for CueWire voice benchmark.

Metrics are meaningful only if clean.wav and noisy.wav contain matching
time-aligned source content. Models may change phase/timing; reported SI-SDR
is an imperfect waveform metric, not a measure of intelligibility.
"""
import argparse
import csv
import json
import math
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.signal import correlate, correlation_lags


RATE = 48000


def load(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) != (1, RATE, 2):
            raise ValueError(f"{path}: required mono, PCM16, 48000 Hz")
        raw = wav.readframes(wav.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0


def rms(x):
    return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0


def db(x):
    return float(20.0 * math.log10(max(float(x), 1e-12)))


def alignment(ref, candidate, max_shift=RATE):
    """Locate global lag using downsampled FFT correlation, refine at full rate.

    Positive lag means candidate has samples BEFORE the reference
    (candidate is delayed). That lag is removed from candidate.
    """
    if min(len(ref), len(candidate)) < 1024:
        raise ValueError("recordings too short for reliable alignment")
    step = 8
    x = ref[::step] - np.mean(ref[::step])
    y = candidate[::step] - np.mean(candidate[::step])
    if rms(x) < 1e-8 or rms(y) < 1e-8:
        return 0, 0.0, 0.0
    corr = correlate(y, x, mode="full", method="fft")
    lags = correlation_lags(len(y), len(x), mode="full")
    keep = np.abs(lags) <= max_shift // step
    rough = int(lags[keep][np.argmax(np.abs(corr[keep]))] * step)
    best = (0.0, rough)
    for lag in range(max(-max_shift, rough - 20), min(max_shift, rough + 20) + 1):
        if lag >= 0:
            a = ref[:min(len(ref), len(candidate) - lag):2]
            b = candidate[lag:lag + len(a) * 2:2]
        else:
            a = ref[-lag:min(len(ref), len(candidate) - lag):2]
            b = candidate[:len(a) * 2:2]
        n = min(len(a), len(b))
        if n < 1024:
            continue
        a = a[:n]
        b = b[:n]
        aa = float(np.dot(a, a))
        bb = float(np.dot(b, b))
        if aa < 1e-15 or bb < 1e-15:
            continue
        c = abs(float(np.dot(a, b))) / math.sqrt(aa * bb)
        if c > best[0]:
            best = (c, lag)
    lag = best[1]
    if lag >= 0:
        a = ref[:min(len(ref), len(candidate) - lag)]
        b = candidate[lag:lag + len(a)]
    else:
        a = ref[-lag:min(len(ref), len(candidate) - lag)]
        b = candidate[:len(a)]
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    aa = a - np.mean(a)
    bb = b - np.mean(b)
    corr_abs = float(abs(np.dot(aa, bb)) / max(np.linalg.norm(aa) * np.linalg.norm(bb), 1e-12))
    return lag, corr_abs, n / max(len(ref), 1)


def score(ref, candidate):
    if not np.all(np.isfinite(candidate)):
        raise ValueError("Non-finite WAV samples")
    lag, corr, fraction = alignment(ref, candidate)
    if lag >= 0:
        a = ref[:min(len(ref), len(candidate) - lag)]
        b = candidate[lag:lag + len(a)]
    else:
        a = ref[-lag:min(len(ref), len(candidate) - lag)]
        b = candidate[:len(a)]
    n = min(len(a), len(b))
    a = a[:n] - np.mean(a[:n])
    b = b[:n] - np.mean(b[:n])
    energy = float(np.dot(a, a))
    if energy < 1e-14:
        si_sdr = None
    else:
        alpha = float(np.dot(a, b)) / energy
        target = alpha * a
        residue = b - target
        si_sdr = 10.0 * math.log10(
            max(float(np.dot(target, target)), 1e-18) /
            max(float(np.dot(residue, residue)), 1e-18)
        )
    return {
        "lag_samples": lag, "aligned_correlation_abs": round(corr, 6),
        "reference_coverage": round(fraction, 6),
        "si_sdr_db": round(si_sdr, 4) if si_sdr is not None else None,
        "rms_dbfs": round(db(rms(candidate)), 4),
        "sample_peak_dbfs": round(db(np.max(np.abs(candidate))), 4),
        "duration_s": round(len(candidate)/RATE, 6),
        "clipped_samples": int(np.count_nonzero(np.abs(candidate) >= 32767 / 32768))
    }


def measure_speech_file(clean_path, noisy_path):
    clean = load(clean_path)
    noisy = load(noisy_path)
    pair = score(clean, noisy)
    valid = pair["aligned_correlation_abs"] >= 0.12 and pair["reference_coverage"] >= 0.90
    pair["reference_pair_verified_heuristically"] = bool(valid)
    pair["verification_note"] = (
        "Heuristic only; listen/inspect to confirm same utterance. "
        "No automatic transcript identity verification."
    )
    return clean, noisy, pair


def collect(root):
    clean_path = root / "inputs" / "clean.wav"
    noisy_path = root / "inputs" / "noisy.wav"
    clean, noisy, initial = measure_speech_file(clean_path, noisy_path)
    records = []
    source_sdr = initial["si_sdr_db"]
    for file in sorted((root / "results").glob("*.wav")):
        metrics = score(clean, load(file))
        delta = None
        if metrics["si_sdr_db"] is not None and source_sdr is not None:
            delta = round(metrics["si_sdr_db"] - source_sdr, 4)
        metrics["delta_si_sdr_db"] = delta
        metrics["name"] = file.stem
        metrics["eligible_for_reference_ranking"] = (
            initial["reference_pair_verified_heuristically"]
            and metrics["reference_coverage"] >= 0.90
        )
        records.append(metrics)
    result = {
        "schema": "cuewire-voice-bench-v1",
        "input_pair": initial,
        "results": records,
        "ranking_warning": "SI-SDR compares waveforms, not intelligibility, natural timbre, speaker identity or perceived quality.",
    }
    (root / "scores.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    fields = ["name", "si_sdr_db", "delta_si_sdr_db", "aligned_correlation_abs",
              "reference_coverage", "lag_samples", "rms_dbfs", "sample_peak_dbfs",
              "clipped_samples", "duration_s", "eligible_for_reference_ranking"]
    with (root / "scores.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for item in records:
            writer.writerow({key: item.get(key) for key in fields})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        _, _, result = measure_speech_file(
            args.run_dir/"inputs"/"clean.wav", args.run_dir/"inputs"/"noisy.wav")
    else:
        result = collect(args.run_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.preflight and not result["reference_pair_verified_heuristically"]:
        sys.exit(3)


if __name__ == "__main__":
    main()
