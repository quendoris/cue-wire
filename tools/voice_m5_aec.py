#!/usr/bin/env python3
"""CueWire M5 offline AEC study using speaker-only calibration.

NOT production echo cancellation: fits a *frozen* linear acoustic
transfer function from an explicitly played digital reference (x)
to a physical ALSA microphone capture (m). The first 5 seconds of
playback must have no local speech. During later double-talk (local
speech + loudspeaker) the transfer function is NOT updated.

Reference and mic clocks are NOT synchronized. Fixed sample offset and
static acoustic path approximations may degrade over the 20-second pilot.
Fails closed on poor alignment or poor held-out calibration fit.
Never touches PipeWire/ALSA, devices, existing audio or backgrounds.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import wave

import numpy as np
from scipy import signal
from scipy.io import wavfile

RATE = 48000
MAX_SECONDS = 50
ROOT = Path(__file__).resolve().parents[1]
AUDIT = (ROOT / ".audit").resolve()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        info = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(),
                wav.getcomptype())
        if info != (RATE, 1, 2, "NONE"):
            raise ValueError(f"Expected mono PCM16 48k, got {info}: {path}")
        frames = wav.getnframes()
        if not RATE <= frames <= RATE * MAX_SECONDS:
            raise ValueError("WAV outside allowed 1..50 seconds")
        raw = wav.readframes(frames)
        if len(raw) != 2 * frames:
            raise ValueError("Truncated WAV")
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64)**2)))


def db(x: float) -> float:
    return float(20 * math.log10(max(float(x), 1e-12)))


def lag_estimate(reference: np.ndarray, mic: np.ndarray,
                 calibration_s: float = 5.0,
                 max_delay_s: float = 2.0) -> tuple[int, float]:
    """Find strongest coarse direct-reflection correlation, not hardware timestamp.

    A positive lag means the mic has delayed acoustic playback relative
    to the start of the known digital WAV; the mic recorder starts first.
    """
    # Correlate only early speaker-only audio; exclude the later local speech.
    ref = reference[:int(3.6 * RATE)]
    y = mic[:int((calibration_s + max_delay_s) * RATE)]
    ref = ref - np.mean(ref)
    y = y - np.mean(y)
    if rms(ref) < 0.001 or rms(y) < 0.00003:
        raise ValueError("Reference or microphone too quiet for reliable alignment")
    cross = signal.correlate(y, ref, mode="full", method="fft")
    lags = signal.correlation_lags(len(y), len(ref), mode="full")
    allowed = (lags >= 0) & (lags <= int(max_delay_s * RATE))
    candidate_lags = lags[allowed]
    candidates = cross[allowed]
    squared = np.concatenate(([0.0], np.cumsum(y**2)))
    ref_energy = float(np.dot(ref, ref))
    # Normalize by energy of mic window at each possible shift.
    ends = candidate_lags + len(ref)
    valid = ends <= len(y)
    candidate_lags = candidate_lags[valid]
    candidates = candidates[valid]
    ends = ends[valid]
    energies = squared[ends] - squared[candidate_lags]
    normalized = np.abs(candidates) / np.sqrt(np.maximum(energies * ref_energy, 1e-20))
    best = int(np.argmax(normalized))
    lag = int(candidate_lags[best])
    correlation = float(normalized[best])
    if correlation < 0.06:
        raise ValueError(f"Unreliable calibration correlation {correlation:.4f}; "
                         "check speaker output, mic device, and other sounds.")
    return lag, correlation


def fit_transfer(reference: np.ndarray, mic: np.ndarray,
                 lag: int, calibration_s: float = 5.0,
                 enforce_heldout: bool = True) -> tuple[np.ndarray, np.ndarray, dict]:
    """Identify acoustic FIR on first ~3.5 s and validate on *held-out* echo.

    Offset starts 10 ms before strongest echo peak to keep preceding RIR
    taps; the rest of the filter models later reflections.
    """
    offset = max(0, lag - int(0.010 * RATE))
    aligned = np.zeros(len(mic), dtype=np.float64)
    available = min(len(reference), max(0, len(mic) - offset))
    aligned[offset:offset + available] = reference[:available]
    # Training and validation end before the operator starts speaking.
    train_start = offset + int(0.25 * RATE)
    train_end = offset + int(3.30 * RATE)
    val_start = offset + int(3.45 * RATE)
    val_end = offset + int(4.40 * RATE)
    if val_end >= len(mic) or train_end >= len(mic):
        raise ValueError("Mic capture too short for 5-second silent calibration")

    x_train = aligned[train_start:train_end]
    m_train = mic[train_start:train_end]
    if rms(x_train) < 0.001 or rms(m_train) < 0.00003:
        raise ValueError("Calibration training section effectively silent")

    nfft = 8192
    f, pxx = signal.welch(x_train, fs=RATE, nperseg=nfft,
                          noverlap=nfft // 2, detrend="constant")
    _, pxy = signal.csd(x_train, m_train, fs=RATE, nperseg=nfft,
                         noverlap=nfft // 2, detrend="constant")
    # Wiener-regularized transfer estimate. Strong regularization prevents
    # wild gain in frequency bins absent from the musical reference.
    reg = 0.02 * float(np.mean(pxx))
    h_freq = pxy / (pxx + max(reg, 1e-16))
    impulse_period = np.fft.irfft(h_freq, nfft)
    ntaps = int(0.12 * RATE)  # 120 ms, inside nfft (170.7 ms)
    h = impulse_period[:ntaps].copy()
    # Smooth the last 8 ms of the truncated FIR to suppress ringing.
    fade = int(0.008 * RATE)
    h[-fade:] *= np.linspace(1.0, 0.0, fade)
    echo = signal.fftconvolve(aligned, h, mode="full")[:len(mic)]

    model = echo[train_start:train_end]
    denominator = float(np.dot(model, model))
    if denominator <= 1e-12:
        raise ValueError("Degenerate speaker path model")
    alpha = float(np.dot(model, m_train) / denominator)
    if not 0.05 <= alpha <= 3.0:
        raise ValueError(f"Unstable acoustic path scale {alpha:.4f}")
    echo *= alpha

    def erle(a: np.ndarray, predicted: np.ndarray) -> float:
        return 10 * math.log10(max(float(np.dot(a, a)), 1e-20) /
                              max(float(np.dot(a-predicted, a-predicted)), 1e-20))

    train_erle = erle(m_train, echo[train_start:train_end])
    val_y = mic[val_start:val_end]
    val_echo = echo[val_start:val_end]
    val_erle = erle(val_y, val_echo)
    if enforce_heldout and val_erle < 1.0:
        raise ValueError(f"Held-out speaker-only echo cancellation improved only "
                         f"{val_erle:.2f} dB (<1.0 dB). "
                         "Calibration rejected; no voice subtraction generated.")
    if enforce_heldout and val_erle > train_erle + 8.0:
        raise ValueError("Suspicious train/validation mismatch; check alignment")
    return aligned, echo, {
        "approx_acoustic_peak_lag_samples": lag,
        "approx_acoustic_peak_lag_ms": round(1000 * lag / RATE, 4),
        "reference_offset_frames": offset,
        "acoustic_fir_length_frames": ntaps,
        "acoustic_fir_length_ms": ntaps * 1000 / RATE,
        "regularization": reg,
        "gain_scale": alpha,
        "training_erle_db": round(train_erle, 4),
        "heldout_speaker_only_erle_db": round(val_erle, 4),
        "training_time_in_playback_s": [0.25, 3.30],
        "heldout_time_in_playback_s": [3.45, 4.40],
        "path_policy": "fixed_fir_after_first_5s_no_double_talk_adaptation"
    }


def metrics(samples: np.ndarray) -> dict:
    return {"rms_dbfs": round(db(rms(samples)), 5),
            "peak_dbfs": round(db(np.max(np.abs(samples))), 5),
            "samples": int(len(samples)),
            "above_fullscale": int(np.count_nonzero(np.abs(samples) >= 1.0))}


def analyze(input_dir: Path, output_dir: Path) -> dict:
    source = input_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in source.parents or source.name.startswith("."):
        raise ValueError("Capture directory must be private under .audit")
    record = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if record.get("schema") != "cuewire-M5-paired-physical-capture-v1":
        raise ValueError("Not a CueWire paired AEC capture")
    if record.get("status") != "captured_for_offline_aec":
        raise ValueError("Paired source was not successfully captured")
    x_path = source / "reference-sent.wav"
    y_path = source / "microphone-raw.wav"
    if digest(x_path) != record.get("reference", {}).get("sha256"):
        raise ValueError("Reference SHA256 mismatch")
    if digest(y_path) != record.get("microphone", {}).get("sha256"):
        raise ValueError("Microphone RAW SHA256 mismatch")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Output already exists, use a new analysis directory")
    parent = output_dir.expanduser().parent.resolve(strict=True)
    if parent != source.parent:
        raise ValueError("Analysis output must be a sibling of the capture directory")
    out = parent / output_dir.name

    x = read_wav(x_path)
    y = read_wav(y_path)
    if np.max(np.abs(y)) >= 32767 / 32768:
        raise ValueError("Microphone RAW has clipped samples; no safe AEC pilot")
    lag, corr = lag_estimate(x, y, record["speaker_only_calibration_s"])
    aligned, echo, fit = fit_transfer(x, y, lag, record["speaker_only_calibration_s"])
    residual = y - echo
    if not np.all(np.isfinite(residual)) or np.max(np.abs(residual)) >= 0.99:
        raise ValueError("AEC residual unsafe level/NaN; refusing PCM output")
    if np.max(np.abs(echo)) >= 0.99:
        raise ValueError("Estimated echo unsafe level; refusing PCM output")
    if digest(x_path) != record["reference"]["sha256"] or \
       digest(y_path) != record["microphone"]["sha256"]:
        raise ValueError("Source changed during analysis")
    out.mkdir(mode=0o700)
    # PCM32 FLOAT WAV: no clipping/quantization on intermediate echo; original WAV remains PCM16.
    residual_file = out / "aec-candidate-float32.wav"
    estimate_file = out / "speaker-echo-estimate-float32.wav"
    wavfile.write(str(residual_file), RATE, residual.astype(np.float32))
    wavfile.write(str(estimate_file), RATE, echo.astype(np.float32))
    data = {
        "schema": "cuewire-M5-frozen-linear-AEC-pilot-v1",
        "status": "estimated_not_listening_approved",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "reference_sha256": digest(x_path),
        "mic_raw_sha256": digest(y_path),
        "calibration_correlation": round(corr, 6),
        "frozen_filter": fit,
        "pre": metrics(y),
        "post": metrics(residual),
        "predicted_echo": metrics(echo),
        "outputs": {
            residual_file.name: digest(residual_file),
            estimate_file.name: digest(estimate_file)
        },
        "warnings": [
            "The first five seconds must contain only loudspeaker sound, no near-end speech.",
            "Digital reference is the PLAYED file, not the hardware sink monitor.",
            "Not clock-locked; lag/drift and room changes may degrade subtraction.",
            "Five seconds is a pilot calibration, not a general acoustic model.",
            "Frozen acoustic filter cannot track room motion/volume/output DSP changes.",
            "RMS gain/ERLE do NOT demonstrate preserved intelligibility during double-talk.",
            "No microphone cleanup, voice recognition, denoising or post-mastering applied.",
            "This WAV is research-only, not live CueWire routing, and never auto-played."
        ]
    }
    with (out / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    with (out / "SHA256SUMS").open("x", encoding="utf-8") as stream:
        for path in (residual_file, estimate_file, out / "manifest.json"):
            stream.write(digest(path) + "  " + path.name + "\n")
    print("PASS: time alignment correlation", round(corr, 4))
    print("Peak lag:", fit["approx_acoustic_peak_lag_ms"], "ms")
    print("Held-out calibration ERLE:", fit["heldout_speaker_only_erle_db"], "dB")
    print("Mic RAW rms/peak:", data["pre"]["rms_dbfs"], data["pre"]["peak_dbfs"])
    print("AEC candidate rms/peak:", data["post"]["rms_dbfs"], data["post"]["peak_dbfs"])
    print("Frozen FIR after calibration; no local voice samples used to train filter.")
    print("Review:", residual_file)
    return data


def diagnose(input_dir: Path, output_dir: Path) -> dict:
    """Research-only numerical report; no audio output and no relaxed AEC acceptance.

    The calibration hold-out may be used to diagnose model/timing errors,
    but diagnostic-only hindsight estimates MUST NOT be used to render
    the double-talk voice. A new independent validation would be required.
    """
    source = input_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in source.parents or not source.is_dir():
        raise ValueError("Capture directory must be private under .audit")
    manifest_path = source / "manifest.json"
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (record.get("schema") != "cuewire-M5-paired-physical-capture-v1" or
            record.get("status") != "captured_for_offline_aec"):
        raise ValueError("Capture was not successfully completed")
    x_path = source / "reference-sent.wav"
    y_path = source / "microphone-raw.wav"
    x_sha = digest(x_path)
    y_sha = digest(y_path)
    if (x_sha != record.get("reference", {}).get("sha256") or
            y_sha != record.get("microphone", {}).get("sha256")):
        raise ValueError("Captured WAV SHA256 mismatch; refuse diagnostics")

    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Diagnostics directory exists; choose another")
    parent = output_dir.expanduser().parent.resolve(strict=True)
    if parent != source.parent or audit not in parent.parents:
        raise ValueError("Output must be a NEW private sibling of the capture")
    outdir = parent / output_dir.name
    x = read_wav(x_path)
    y = read_wav(y_path)
    lag, corr = lag_estimate(x, y, record["speaker_only_calibration_s"])
    _, echo, fit = fit_transfer(
        x, y, lag, record["speaker_only_calibration_s"],
        enforce_heldout=False)
    offset = fit["reference_offset_frames"]
    start = offset + int(3.45 * RATE)
    end = offset + int(4.40 * RATE)
    observed = y[start:end]

    def erle(model: np.ndarray) -> float:
        return float(10 * math.log10(
            max(float(np.dot(observed, observed)), 1e-20) /
            max(float(np.dot(observed-model, observed-model)), 1e-20)))
    grid = []
    # Diagnostic-only temporal shift scan using the hold-out, never used
    # to adjust the official filter or to make a voice WAV.
    for shift_ms in (-10, -5, -2, -1, -0.5, 0, 0.5, 1, 2, 5, 10):
        shift = round(shift_ms * RATE / 1000)
        predicted = echo[start-shift:end-shift]
        if len(predicted) != len(observed):
            continue
        denominator = max(float(np.dot(predicted, predicted)), 1e-20)
        hindsight_scale = float(np.dot(observed, predicted) / denominator)
        grid.append({
            "shift_ms": shift_ms,
            "fixed_gain_erle_db": round(erle(predicted), 4),
            "holdout_oracle_optimal_gain": round(hindsight_scale, 4),
            "holdout_oracle_erle_db": round(erle(predicted * hindsight_scale), 4),
        })

    fit["passed_production_gate"] = bool(
        fit["heldout_speaker_only_erle_db"] >= 1.0 and
        fit["heldout_speaker_only_erle_db"] <= fit["training_erle_db"] + 8)
    result = {
        "schema": "cuewire-M5-AEC-diagnostics-v1",
        "status": "diagnostic_only_not_accepted_for_voice",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_capture_manifest_sha256": digest(manifest_path),
        "source_reference_sha256": x_sha,
        "source_mic_sha256": y_sha,
        "coarse_reference_mic_correlation": round(corr, 6),
        "calibration": fit,
        "holdout_microphone_rms_dbfs": round(db(rms(observed)), 5),
        "holdout_prediction_rms_dbfs": round(db(rms(echo[start:end])), 5),
        "holdout_shift_oracle_grid": grid,
        "warnings": [
            "All shifted/scaled hold-out values are hindsight diagnostics, NOT independent validation.",
            "Never use hold-out optimized shifts or gains for double-talk subtraction.",
            "No candidate WAV is generated when AEC acceptance fails.",
            "A poor result may reflect clock drift, nonlinearity, near-end noise or model mismatch.",
            "No change to audio devices, original files or PipeWire routes.",
        ],
    }
    if digest(x_path) != x_sha or digest(y_path) != y_sha:
        raise RuntimeError("Audio input changed during diagnostic analysis")
    outdir.mkdir(mode=0o700)
    with (outdir / "diagnostics.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    with (outdir / "SHA256SUMS").open("x", encoding="utf-8") as handle:
        handle.write(f'{digest(outdir / "diagnostics.json")}  diagnostics.json\n')
    print("M5 numerical diagnosis ONLY; no WAV generated.")
    print("Peak lag:", fit["approx_acoustic_peak_lag_ms"], "ms")
    print("Coarse correlation:", round(corr, 5))
    print("Training ERLE:", fit["training_erle_db"], "dB")
    print("Held-out ERLE:", fit["heldout_speaker_only_erle_db"], "dB")
    print("Official acceptance:", "PASS" if fit["passed_production_gate"] else "REJECT")
    print("Diagnostic-only holdout shift / gain scan:")
    for item in grid:
        print("  shift={:+5g}ms fixed={:+7.3f}dB oracle={:+7.3f}dB scale={:+.3f}".format(
            item["shift_ms"], item["fixed_gain_erle_db"],
            item["holdout_oracle_erle_db"],
            item["holdout_oracle_optimal_gain"]))
    print("Saved numerical audit:", outdir / "diagnostics.json")
    return result


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--diagnose", action="store_true",
                   help="Write numeric diagnostics ONLY (no residual WAV, no threshold bypass)")
    args = p.parse_args()
    try:
        if args.diagnose:
            diagnose(args.capture, args.out)
        else:
            analyze(args.capture, args.out)
    except (OSError, ValueError, RuntimeError, KeyError, wave.Error) as exc:
        p.exit(1, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
