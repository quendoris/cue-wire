#!/usr/bin/env python3
"""CueWire M5: 12s-calibration / double-talk frozen FIR NUMERICAL AUDIT.

Use only the immutable M5-doubletalk paired microphone/reference WAVs.
Alignment uses the start of playback; FIR/gain fit only in 3–9s.
Speaker-only validation is 10–11.5s, safely before 12s voice prompt.
The later 13–28s voice+music segment is numerical exploration ONLY.

No generated audio, no processed voice, no playback, no microphone opening,
no PipeWire settings or routing, no production AEC authorization.
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

from voice_m5_aec import AUDIT, RATE, db, digest, read_wav, lag_estimate, rms
from voice_m5_frozen_heldout import erle_db, MODEL_FIR_MS, MODEL_RIDGE, NFFT
from voice_m5_refcoherence import BANDS

TRAIN_S = (3.0, 9.0)
VALIDATE_S = (10.0, 11.5)
DOUBLE_TALK_S = (13.0, 28.0)
PRE_PEAK_S = .010
FADE_S = .008
MIN_ERLE_DB = 1.0


def cut(reference_time_s: tuple[float, float], offset: int,
        wave: np.ndarray) -> np.ndarray:
    start = offset + round(reference_time_s[0] * RATE)
    end = offset + round(reference_time_s[1] * RATE)
    if start < 0 or end > len(wave) or end <= start:
        raise ValueError("Audio does not cover requested reference-time interval")
    return wave[start:end]


def fit_calibration(reference: np.ndarray, mic: np.ndarray,
                    lag: int) -> tuple[np.ndarray, dict]:
    """Frozen train-only FIR. No heldout or voice samples enter training."""
    anchor = max(0, lag - round(PRE_PEAK_S * RATE))
    if anchor + len(reference) > len(mic):
        raise ValueError("Reference cannot fully align inside microphone WAV")
    aligned = np.zeros(len(mic), dtype=np.float64)
    aligned[anchor:anchor+len(reference)] = reference
    train_x = cut(TRAIN_S, lag, aligned)
    train_y = cut(TRAIN_S, lag, mic)
    if rms(train_x) < .001 or rms(train_y) < .00003:
        raise ValueError("No usable acoustic speaker-only training audio")
    _, pxx = signal.welch(
        train_x, fs=RATE, nperseg=NFFT, noverlap=NFFT//2,
        detrend="constant")
    _, pxy = signal.csd(
        train_x, train_y, fs=RATE, nperseg=NFFT,
        noverlap=NFFT//2, detrend="constant")
    ridge = max(1e-16, MODEL_RIDGE * float(np.mean(pxx)))
    taps = np.fft.irfft(pxy / (pxx + ridge), n=NFFT)[
        :round(MODEL_FIR_MS*RATE/1000)].copy()
    fade = round(FADE_S*RATE)
    taps[-fade:] *= np.linspace(1., 0., fade)
    prediction = signal.fftconvolve(
        aligned, taps, mode="full")[:len(mic)]
    train_prediction = cut(TRAIN_S, lag, prediction)
    denominator = float(np.dot(train_prediction, train_prediction))
    if denominator <= 1e-12:
        raise ValueError("Degenerate fitted echo prediction")
    gain = float(np.dot(train_y, train_prediction)/denominator)
    if not math.isfinite(gain) or not 0.05 <= gain <= 3.0:
        raise ValueError("Unstable training-only gain")
    prediction *= gain
    if not np.all(np.isfinite(prediction)):
        raise ValueError("Nonfinite model prediction")
    report = {
        "fir_ms": MODEL_FIR_MS,
        "fir_taps": len(taps),
        "ridge_factor": MODEL_RIDGE,
        "nfft": NFFT,
        "pre_peak_shift_ms": PRE_PEAK_S * 1000,
        "gain_from_training_only": round(gain, 8),
        "training_time_reference_s": list(TRAIN_S),
        "training_erle_db": round(
            erle_db(train_y, train_y-cut(TRAIN_S, lag, prediction)), 5),
        "filter_frozen_after_training": True,
    }
    return prediction, report


def band_score(reference: np.ndarray, original: np.ndarray,
               residual: np.ndarray) -> list[dict]:
    """Same ORIGINAL-microphone PSD weights before and after processing.

    1.5s validation uses nperseg=2048, unlike older 6s comparison;
    numeric values between those tests are not directly interchangeable.
    """
    if not (len(reference) == len(original) == len(residual)
            and len(reference) >= int(RATE*1.4)):
        raise ValueError("Reference/mic/residual lengths invalid")
    nperseg, noverlap = 2048, 1024
    freqs, before = signal.coherence(
        reference, original, fs=RATE, nperseg=nperseg,
        noverlap=noverlap)
    _, after = signal.coherence(
        reference, residual, fs=RATE, nperseg=nperseg,
        noverlap=noverlap)
    _, mic_psd = signal.welch(
        original, fs=RATE, nperseg=nperseg, noverlap=noverlap)
    output = []
    for low, high in BANDS:
        include = (freqs >= low) & (freqs < high)
        if not np.any(include):
            raise ValueError("Missing coherence frequencies")
        power = mic_psd[include]
        prior = float(np.dot(before[include], power) /
                      max(float(np.sum(power)), 1e-20))
        post = float(np.dot(after[include], power) /
                     max(float(np.sum(power)), 1e-20))
        sos = signal.butter(
            4, [low, high], btype="bandpass", fs=RATE,
            output="sos")
        original_band = signal.sosfiltfilt(sos, original)
        residual_band = signal.sosfiltfilt(sos, residual)
        output.append({
            "band_hz": [low, high],
            "erle_db": round(erle_db(original_band, residual_band), 5),
            "original_rms_dbfs": round(db(rms(original_band)), 5),
            "residual_rms_dbfs": round(db(rms(residual_band)), 5),
            "coherence_original_mic_psd_weights_before": round(prior, 6),
            "coherence_original_mic_psd_weights_after": round(post, 6),
            "coherence_before_minus_after": round(prior-post, 6),
        })
    return output


def compare(reference: np.ndarray, mic: np.ndarray, echo: np.ndarray,
            lag: int, times: tuple[float, float]) -> dict:
    ref = cut(times, 0, reference)
    observed = cut(times, lag, mic)
    modeled = cut(times, lag, echo)
    residual = observed - modeled
    if np.any(~np.isfinite(residual)):
        raise ValueError("Nonfinite modeled residual")
    return {
        "reference_time_s": list(times),
        "original_mic_rms_dbfs": round(db(rms(observed)), 5),
        "echo_prediction_rms_dbfs": round(db(rms(modeled)), 5),
        "hypothetical_residual_rms_dbfs": round(db(rms(residual)), 5),
        "erle_db": round(erle_db(observed, residual), 5),
        "original_peak_dbfs": round(
            db(float(np.max(np.abs(observed)))), 5),
        "hypothetical_residual_peak_dbfs": round(
            db(float(np.max(np.abs(residual)))), 5),
        "bands": band_score(ref, observed, residual),
    }


def study(capture_dir: Path, output_dir: Path) -> dict:
    source = capture_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if not source.is_dir() or audit not in source.parents:
        raise ValueError("Source must be a private local .audit directory")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Output directory exists; refuse overwrite")
    parent = output_dir.expanduser().parent.resolve(strict=True)
    if parent != source.parent:
        raise ValueError("New analysis must be a sibling of the capture")
    manifest_file = source/"manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if (manifest.get("schema") != "cuewire-M5-paired-physical-capture-v1"
            or manifest.get("status") != "captured_for_offline_aec"
            or manifest.get("speaker_only_calibration_s") != 12
            or manifest.get("requested_duration_s") != 32):
        raise ValueError("Not an accepted 32s M5 double-talk capture with 12s calibration")
    if (manifest.get("mic_capture", {}).get("backend") != "pipewire"
            or not manifest.get("mic_capture", {}).get(
                "mic_source_name", "").startswith("alsa_input.")
            or not manifest.get("speaker_sink_name", "").startswith("alsa_output.")):
        raise ValueError("Mic/speaker must be explicit physical endpoints")
    ref_path = source/"reference-sent.wav"
    mic_path = source/"microphone-raw.wav"
    hashes = {
        "capture_manifest": digest(manifest_file),
        "digital_reference": digest(ref_path),
        "physical_mic_raw": digest(mic_path),
    }
    if (hashes["digital_reference"] != manifest["reference"]["sha256"]
            or hashes["physical_mic_raw"] != manifest["microphone"]["sha256"]):
        raise ValueError("Source WAV SHA256 mismatch")
    reference = read_wav(ref_path)
    mic = read_wav(mic_path)
    if np.max(np.abs(mic)) >= 32767/32768:
        raise ValueError("Clipped raw microphone capture")
    lag, correlation = lag_estimate(
        reference, mic,
        calibration_s=12.0, max_delay_s=2.0)
    # Holdout ends 0.5 seconds BEFORE operator should begin reading.
    # This rule does not independently prove the operator stayed silent.
    if VALIDATE_S[1] >= manifest["speaker_only_calibration_s"]:
        raise RuntimeError("Holdout must end strictly before speech prompt")
    modeled, fit = fit_calibration(reference, mic, lag)
    speaker_only = compare(reference, mic, modeled, lag, VALIDATE_S)
    passed = speaker_only["erle_db"] >= MIN_ERLE_DB
    highband_warnings = [
        r["band_hz"] for r in speaker_only["bands"]
        if r["band_hz"][0] >= 4000 and r["erle_db"] < -0.05
    ]
    # Only after the training/validation gates, calculate descriptive
    # double-talk metrics in memory. Not a clean-voice quality metric.
    doubletalk = (compare(reference, mic, modeled, lag, DOUBLE_TALK_S)
                  if passed else None)
    if any(digest(path) != hashes[name] for name, path in [
            ("capture_manifest", manifest_file),
            ("digital_reference", ref_path),
            ("physical_mic_raw", mic_path)]):
        raise RuntimeError("Captured files changed during analysis")
    result = {
        "schema": "cuewire-M5-doubletalk-frozen-numeric-gate-v1",
        "status": "speaker_only_gate_passed_research_only" if passed
                  else "speaker_only_gate_rejected_no_voice_analysis",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashes,
        "alignment": {
            "lag_samples": lag,
            "lag_ms": round(lag*1000/RATE, 5),
            "reference_mic_normalized_correlation": round(correlation, 6),
            "uses_only_early_reference": True,
            "not_hardware_latency": True,
        },
        "frozen_model": fit,
        "speaker_only_validation": {
            **speaker_only,
            "minimum_wholeband_erle_db": MIN_ERLE_DB,
            "passes_minimum_wholeband_erle": passed,
            "upper_band_regressions_hz": highband_warnings,
        },
        "double_talk_numerical_exploration": doubletalk,
        "voice_preservation_validated": False,
        "approved_for_virtual_mic": False,
        "warnings": [
            "The manifest does not independently prove first 12s were voice-free.",
            "Only samples from reference 3–9s were used to fit the acoustic path and gain.",
            "Validation 10–11.5s is excluded from FIR fitting; however prior exploratory work influenced model design.",
            "Double-talk residual is ephemeral in memory; no candidate WAV is saved.",
            "Double-talk ERLE is not echo-only ERLE because local voice and background are mixed.",
            "Double-talk coherence change cannot prove that near-end voice timbre or clarity was preserved.",
            "Negative ERLE or increased coherence above 4kHz requires independent investigation.",
            "No hardware/device routes, streaming, background services or audio outputs are created.",
        ],
    }
    out = parent/output_dir.name
    out.mkdir(mode=0o700)
    report_path = out/"doubletalk-gate.json"
    with report_path.open("x", encoding="utf-8") as file:
        json.dump(result, file, indent=2, ensure_ascii=False,
                  sort_keys=True)
        file.write("\n")
    with (out/"SHA256SUMS").open("x", encoding="utf-8") as file:
        file.write(digest(report_path)+"  doubletalk-gate.json\n")
    print("M5 double-talk: PRIVATE numeric audit only; NO audio output")
    print("Lag:", result["alignment"]["lag_ms"], "ms")
    print("Training ERLE:", fit["training_erle_db"], "dB")
    print("Heldout 10–11.5s ERLE:", speaker_only["erle_db"],
          "dB; gate", "PASS" if passed else "REJECT")
    print("Highband regressions:", highband_warnings or "none")
    print("Double-talk metrics:", "research only" if doubletalk else "withheld")
    print("APPROVED FOR VOICE/STREAM: NO")
    print("Saved:", report_path)
    return result


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        study(args.capture, args.out)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        parser.exit(1, "ERROR: "+str(exc)+"\n")


if __name__ == "__main__":
    main()
