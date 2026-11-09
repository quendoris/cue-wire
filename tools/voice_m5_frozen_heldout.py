#!/usr/bin/env python3
"""CueWire M5: one frozen FIR, train-once / held-out echo-subtraction audit.

The only audio inputs are the immutable, SHA256-verified local M5-006
digital reference and the physical M5-ABA-001 microphone recording.
All coefficients (including gain) are fitted on reference time 3–9s;
alignment uses reference time 1–3.7s; validation uses 10–16s ONLY.

Never writes any processed audio. No AEC voice preview, PipeWire actions,
device access, background services, implicit routes or threshold changes.
Numbers are research diagnostics; neither noise removal nor preservation
of a near-end speaker can be certified from this voice-free A/B/A take.
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

from voice_m5_aec import AUDIT, RATE, db, digest, read_wav, rms
from voice_m5_refcoherence import band_lag, BANDS

TRAIN_REFERENCE_S = (3.0, 9.0)
HELDOUT_REFERENCE_S = (10.0, 16.0)
ALIGNMENT_REFERENCE_S = (1.0, 3.7)
MODEL_FIR_MS = 120
MODEL_RIDGE = 0.02
NFFT = 8192
PRE_PEAK_MS = 10
FIR_TAIL_FADE_MS = 8
GAIN_RANGE = (0.05, 3.0)


def indices(window: tuple[float, float], lag: int,
            samples: int) -> tuple[int, int]:
    a = lag + round(window[0] * RATE)
    b = lag + round(window[1] * RATE)
    if not (0 <= a < b <= samples):
        raise ValueError("Selected reference-relative window is outside mic capture")
    return a, b


def erle_db(microphone: np.ndarray, residual: np.ndarray) -> float:
    if len(microphone) != len(residual) or not len(microphone):
        raise ValueError("Cannot compare mismatched ERLE arrays")
    original = float(np.dot(microphone, microphone))
    remainder = float(np.dot(residual, residual))
    return 10 * math.log10(max(original, 1e-20) / max(remainder, 1e-20))


def fit_frozen(reference: np.ndarray, mic: np.ndarray,
               lag: int) -> tuple[np.ndarray, dict]:
    """Train strictly 3–9s; do not use held-out samples for any parameter."""
    if len(reference) < 17*RATE or len(mic) < 34*RATE:
        raise ValueError("Expected complete ~20s reference and ~36s ABA mic")
    start, end = indices(TRAIN_REFERENCE_S, lag, len(mic))
    indices(HELDOUT_REFERENCE_S, lag, len(mic))
    offset = max(0, lag - round(PRE_PEAK_MS * RATE / 1000))
    if offset + len(reference) > len(mic):
        raise ValueError("Capture too short to align entire digital reference")

    aligned = np.zeros(len(mic), dtype=np.float64)
    aligned[offset:offset + len(reference)] = reference
    training_reference = aligned[start:end]
    training_microphone = mic[start:end]
    if rms(training_reference) < .001 or rms(training_microphone) < .00003:
        raise ValueError("Calibrating on effectively silent samples is unsafe")
    _, pxx = signal.welch(training_reference, fs=RATE,
                          nperseg=NFFT, noverlap=NFFT//2,
                          detrend="constant")
    _, pxy = signal.csd(training_reference, training_microphone,
                        fs=RATE, nperseg=NFFT,
                        noverlap=NFFT//2, detrend="constant")
    ridge = max(1e-16, MODEL_RIDGE * float(np.mean(pxx)))
    impulse_period = np.fft.irfft(pxy / (pxx + ridge), n=NFFT)
    ntaps = round(MODEL_FIR_MS * RATE/1000)
    taps = impulse_period[:ntaps].copy()
    fade = round(FIR_TAIL_FADE_MS * RATE/1000)
    taps[-fade:] *= np.linspace(1., 0., fade)
    predicted = signal.fftconvolve(aligned, taps, mode="full")[:len(mic)]
    p = predicted[start:end]
    denominator = float(np.dot(p, p))
    if denominator <= 1e-12:
        raise ValueError("Degenerate echo model: training prediction is too small")
    gain = float(np.dot(training_microphone, p) / denominator)
    if not np.isfinite(gain) or not GAIN_RANGE[0] <= gain <= GAIN_RANGE[1]:
        raise ValueError(f"Training-only acoustic gain invalid: {gain!r}")
    predicted *= gain
    if not np.all(np.isfinite(predicted)):
        raise ValueError("Non-finite microphone echo prediction")
    result = {
        "fir_length_ms": MODEL_FIR_MS,
        "fir_taps": ntaps,
        "ridge_factor": MODEL_RIDGE,
        "nfft": NFFT,
        "regularization_absolute": float(ridge),
        "gain_from_training_only": round(gain, 7),
        "impulse_l2_norm": round(float(np.linalg.norm(taps*gain)), 8),
        "pre_peak_reference_shift_ms": PRE_PEAK_MS,
        "training_reference_time_s": list(TRAIN_REFERENCE_S),
        "training_erle_db": round(
            erle_db(training_microphone,
                    training_microphone - predicted[start:end]), 5),
        "frozen_after_training": True,
    }
    return predicted, result


def compare_coherence_same_weights(reference: np.ndarray,
                                   original: np.ndarray,
                                   residual: np.ndarray) -> list[dict]:
    """BEFORE mic PSD is held as frequency weighting for BEFORE and AFTER.

    This prevents apparent changes caused only by changing normalization
    weights after subtraction. Reference PSD weights are also unchanged.
    """
    if not (len(reference) == len(original) == len(residual) and
            len(reference) >= 3 * RATE):
        raise ValueError("Coherence requires matching >=3 second arrays")
    nperseg = 4096
    noverlap = nperseg // 2
    f, original_coherence = signal.coherence(
        reference, original, fs=RATE, nperseg=nperseg,
        noverlap=noverlap)
    f_after, residual_coherence = signal.coherence(
        reference, residual, fs=RATE, nperseg=nperseg,
        noverlap=noverlap)
    _, original_psd = signal.welch(original, fs=RATE,
                                   nperseg=nperseg, noverlap=noverlap)
    _, reference_psd = signal.welch(reference, fs=RATE,
                                    nperseg=nperseg, noverlap=noverlap)
    if not (np.array_equal(f, f_after) and
            np.all(np.isfinite(original_coherence)) and
            np.all(np.isfinite(residual_coherence))):
        raise ValueError("Invalid coherence FFT or nonfinite spectral estimates")
    out = []
    for low, high in BANDS:
        mask = (f >= low) & (f < high)
        if not np.any(mask):
            raise ValueError("Empty frequency band")
        weight = original_psd[mask]
        ref_weight = reference_psd[mask]
        before = float(np.dot(original_coherence[mask], weight) /
                       max(float(np.sum(weight)), 1e-20))
        after = float(np.dot(residual_coherence[mask], weight) /
                      max(float(np.sum(weight)), 1e-20))
        before_ref = float(np.dot(original_coherence[mask], ref_weight) /
                           max(float(np.sum(ref_weight)), 1e-20))
        after_ref = float(np.dot(residual_coherence[mask], ref_weight) /
                          max(float(np.sum(ref_weight)), 1e-20))
        sos = signal.butter(4, [low, high], btype="bandpass",
                            fs=RATE, output="sos")
        original_band = signal.sosfiltfilt(sos, original)
        residual_band = signal.sosfiltfilt(sos, residual)
        out.append({
            "band_hz": [low, high],
            "original_mic_rms_dbfs": round(db(rms(original_band)), 5),
            "residual_mic_rms_dbfs": round(db(rms(residual_band)), 5),
            "heldout_erle_db": round(erle_db(original_band, residual_band), 5),
            "coherence_mic_original_psd_weights_before": round(before, 6),
            "coherence_mic_original_psd_weights_after": round(after, 6),
            "coherence_delta_before_minus_after": round(before-after, 6),
            "coherence_reference_psd_weights_before": round(before_ref, 6),
            "coherence_reference_psd_weights_after": round(after_ref, 6),
            "coherence_median_before": round(
                float(np.median(original_coherence[mask])), 6),
            "coherence_median_after": round(
                float(np.median(residual_coherence[mask])), 6),
        })
    return out


def verify_inputs(aba_dir: Path, output_dir: Path
                  ) -> tuple[Path, Path, dict, dict, dict]:
    source = aba_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in source.parents or not source.is_dir():
        raise ValueError("A/B/A capture must be private under repo .audit")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("New report folder required: no overwriting")
    output_parent = output_dir.expanduser().parent.resolve(strict=True)
    if output_parent != source.parent:
        raise ValueError("New report must be a sibling of M5 A/B/A capture")
    aba_path = source / "manifest.json"
    aba = json.loads(aba_path.read_text(encoding="utf-8"))
    if (aba.get("schema") != "cuewire-M5-continuous-ABA-control-v1" or
            aba.get("status") != "captured_aba_for_noise_diagnostics"):
        raise ValueError("No validated continuous A/B/A source manifest")
    reference_dir = Path(aba["reference_source_capture"]).expanduser().resolve(
        strict=True)
    if audit not in reference_dir.parents or reference_dir == source:
        raise ValueError("Reference capture is not a distinct private M5 source")
    reference_manifest_path = reference_dir / "manifest.json"
    rm = json.loads(reference_manifest_path.read_text(encoding="utf-8"))
    if (rm.get("schema") != "cuewire-M5-paired-physical-capture-v1" or
            rm.get("status") != "captured_for_offline_aec"):
        raise ValueError("Invalid reference capture")
    ref_path = reference_dir / "reference-sent.wav"
    mic_path = source / "microphone-aba-raw.wav"
    hashes = {
        "aba_manifest": digest(aba_path),
        "reference_manifest": digest(reference_manifest_path),
        "digital_reference": digest(ref_path),
        "physical_microphone": digest(mic_path),
    }
    if hashes["reference_manifest"] != aba["reference_source_manifest_sha256"]:
        raise ValueError("Reference source manifest SHA256 mismatch")
    if (hashes["digital_reference"] != aba["reference_wav_sha256"] or
            hashes["digital_reference"] != rm["reference"]["sha256"] or
            hashes["physical_microphone"] != aba["microphone"]["sha256"]):
        raise ValueError("Original audio SHA256 mismatch")
    if (aba["source_mic_name"] != rm["mic_capture"].get("mic_source_name") or
            aba["speaker_sink_name"] != rm["speaker_sink_name"] or
            abs(float(aba["speaker_play_volume"]) -
                float(rm["volume_pw_play"])) > 1e-4):
        raise ValueError("Reference/M5 A/B/A mic, speaker or volume mismatch")
    return ref_path, mic_path, hashes, aba, {
        "aba_manifest": aba_path,
        "reference_manifest": reference_manifest_path,
    }


def analyze(aba_dir: Path, output_dir: Path) -> dict:
    ref_path, mic_path, hashes, aba, manifest_paths = verify_inputs(
        aba_dir, output_dir)
    reference = read_wav(ref_path)
    mic = read_wav(mic_path)
    if np.max(np.abs(mic)) >= 32767/32768:
        raise ValueError("Clipped microphone samples; refuse numeric AEC probe")
    if len(reference) < 17*RATE or len(mic) < 34*RATE:
        raise ValueError("Insufficient complete A/B/A calibration intervals")
    lag, timing = band_lag(
        reference, mic, float(aba["playback_start_after_recorder_launch_s"]),
        *ALIGNMENT_REFERENCE_S)
    predicted, model = fit_frozen(reference, mic, lag)
    train_a, train_b = indices(TRAIN_REFERENCE_S, lag, len(mic))
    val_a, val_b = indices(HELDOUT_REFERENCE_S, lag, len(mic))
    # The gap is deliberate; neither gain nor spectral model sees val.
    if train_b > val_a:
        raise RuntimeError("Training/heldout window overlap detected")
    mic_train = mic[train_a:train_b]
    mic_val = mic[val_a:val_b]
    echo_val = predicted[val_a:val_b]
    residual_train = mic_train - predicted[train_a:train_b]
    residual_val = mic_val - echo_val
    if not (np.all(np.isfinite(residual_val)) and
            np.all(np.isfinite(residual_train))):
        raise ValueError("Nonfinite calculated residual")
    r_start, r_end = (round(t*RATE) for t in HELDOUT_REFERENCE_S)
    on_reference = reference[r_start:r_end]
    train_ref_start, train_ref_end = (round(t*RATE) for t in TRAIN_REFERENCE_S)
    band_stats = compare_coherence_same_weights(
        on_reference, mic_val, residual_val)
    train_stats = compare_coherence_same_weights(
        reference[train_ref_start:train_ref_end],
        mic_train, residual_train)
    original_peak = float(np.max(np.abs(mic_val)))
    echo_peak = float(np.max(np.abs(echo_val)))
    residual_peak = float(np.max(np.abs(residual_val)))
    if not all(math.isfinite(p) for p in
               (original_peak, echo_peak, residual_peak)):
        raise ValueError("Nonfinite audio extrema")
    report = {
        "schema": "cuewire-M5-ABA-frozen-AEC-heldout-v1",
        "status": "research_only_no_voice_no_audio_output",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashes,
        "alignment": timing,
        "model": model,
        "frozen_model_policy": {
            "alignment_reference_time_s": list(ALIGNMENT_REFERENCE_S),
            "train_reference_time_s": list(TRAIN_REFERENCE_S),
            "heldout_reference_time_s": list(HELDOUT_REFERENCE_S),
            "holdout_used_for_alignment": False,
            "holdout_used_for_fitting": False,
            "model_selected_using_holdout": False,
            "no_double_talk_fitting": True,
        },
        "training": {
            "rms_before_dbfs": round(db(rms(mic_train)), 5),
            "rms_after_dbfs": round(db(rms(residual_train)), 5),
            "erle_db": round(erle_db(mic_train, residual_train), 5),
            "band_metrics": train_stats,
            "in_sample_optimistic": True,
        },
        "heldout": {
            "rms_before_dbfs": round(db(rms(mic_val)), 5),
            "rms_echo_prediction_dbfs": round(db(rms(echo_val)), 5),
            "rms_after_dbfs": round(db(rms(residual_val)), 5),
            "erle_db": round(erle_db(mic_val, residual_val), 5),
            "max_abs_before": round(original_peak, 7),
            "max_abs_echo_prediction": round(echo_peak, 7),
            "max_abs_residual": round(residual_peak, 7),
            "band_metrics": band_stats,
            "meets_prior_minimum_total_erle": bool(
                erle_db(mic_val, residual_val) >= 1.0),
        },
        "speaker_off_control": {
            "microphone_before_rms_dbfs": round(db(rms(mic[
                round(2*RATE):round(6*RATE)])), 5),
            "microphone_after_rms_dbfs": round(db(rms(mic[
                round(30.245*RATE):round(34.245*RATE)])), 5),
            "echo_prediction_off_max_abs": round(
                max(float(np.max(np.abs(predicted[2*RATE:6*RATE]))),
                    float(np.max(np.abs(predicted[30*RATE:34*RATE])))), 9),
            "note": "OFF prediction is zero by construction because digital reference is zero; not proof of unchanged near-end voice",
        },
        "warnings": [
            "All model coefficients and gain are determined by the first ON interval only.",
            "The alignment probe overlaps the beginning of the training window but never the later heldout window.",
            "Neither M5-006 nor M5-ABA-001 contains independent clean near-end voice truth during this A/B/A test.",
            "Coherence is not equivalent to a measured percent of voice or speaker leakage removed.",
            "OFF baseline can contain firmware DSP, environmental/electrical noise or unrelated sounds.",
            "Sample clocks are not hardware synchronized and the lag is fixed across the 20-second playback.",
            "Passing a minimal numerical ERLE gate does NOT authorize processing the user's voice or routing a live virtual microphone.",
            "No WAV, FIR impulse file, hardware commands or modified source data are generated.",
        ],
    }
    if any(digest(path) != hashes[name] for name, path in (
            ("aba_manifest", manifest_paths["aba_manifest"]),
            ("reference_manifest", manifest_paths["reference_manifest"]),
            ("digital_reference", ref_path),
            ("physical_microphone", mic_path))):
        raise RuntimeError("Source files changed during numerical analysis")
    out = output_dir.expanduser().parent.resolve(strict=True) / output_dir.name
    out.mkdir(mode=0o700)
    output = out / "frozen-aec-heldout.json"
    with output.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True, ensure_ascii=False)
        file.write("\n")
    with (out / "SHA256SUMS").open("x", encoding="utf-8") as file:
        file.write(digest(output)+"  frozen-aec-heldout.json\n")
    print("M5 FROZEN AEC HELDOUT: numerical research only; no voice/audio output")
    print("Matched lag (mic from reference origin):",
          timing["lag_from_reference_origin_ms"], "ms")
    print("Training ERLE:", report["training"]["erle_db"], "dB (in sample)")
    print("Heldout ERLE:", report["heldout"]["erle_db"], "dB")
    print("Heldout RMS before/after:",
          report["heldout"]["rms_before_dbfs"],
          report["heldout"]["rms_after_dbfs"], "dBFS")
    for metric in report["heldout"]["band_metrics"]:
        print(" ", metric["band_hz"],
              "ERLE:", metric["heldout_erle_db"],
              "coherence (before -> after):",
              metric["coherence_mic_original_psd_weights_before"],
              "->",
              metric["coherence_mic_original_psd_weights_after"])
    print("Speaker-off prediction max:",
          report["speaker_off_control"]["echo_prediction_off_max_abs"])
    print("Report:", output)
    return report


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path,
                        help="Existing private M5-ABA-001 folder")
    parser.add_argument("--out", required=True, type=Path,
                        help="Fresh .audit/voice-aec/M5-frozen-aec-heldout-001")
    args = parser.parse_args()
    try:
        analyze(args.capture, args.out)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        parser.exit(1, "ERROR: "+str(exc)+"\n")


if __name__ == "__main__":
    main()
