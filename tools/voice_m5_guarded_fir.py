#!/usr/bin/env python3
"""CueWire M5: diagnostic-only frequency-guarded echo subtraction (no WAV).

Compare ONE existing 120-ms/0.02 frozen model with ONE prespecified
zero-phase, offline filtered echo estimate on the SAME 32-second capture.
The guarded trial is NOT an independent validation: the 4–16 kHz
regressions were already seen on this take. Only a fresh heldout capture
can certify whether the guard generalizes.

The original microphone is NEVER filtered; only the echo PREDICTION
gets a band-limited guard. No audio is played, emitted, or written.
No system audio settings, sinks, mic, M4 chain or AEC gate are modified.
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

from voice_m5_aec import AUDIT, RATE, digest, read_wav, lag_estimate
from voice_m5_doubletalk_gate import (
    TRAIN_S, VALIDATE_S, DOUBLE_TALK_S, cut,
    fit_calibration, compare, MIN_ERLE_DB,
)

FIR_GUARD_TAPS = 513
GUARD_CUTOFF_HZ = 3800
GUARD_WINDOW = "hann"


def guarded_echo(predicted: np.ndarray) -> tuple[np.ndarray, dict]:
    """Symmetric FIR zero-phase filtering of predicted echo ONLY.

    Research use: non-causal centered convolution is NOT an
    implementable low-latency real-time filter without buffering.
    """
    if len(predicted) < RATE * 10 or not np.all(np.isfinite(predicted)):
        raise ValueError("Invalid echo estimate")
    taps = signal.firwin(
        FIR_GUARD_TAPS, cutoff=GUARD_CUTOFF_HZ,
        window=GUARD_WINDOW, fs=RATE, pass_zero="lowpass")
    frequencies, gain = signal.freqz(taps, worN=8192, fs=RATE)
    low = np.abs(gain[(frequencies >= 100) & (frequencies <= 2500)])
    high = np.abs(gain[(frequencies >= 5000) & (frequencies <= 16000)])
    if np.min(low) < 0.97 or np.max(high) > 0.0032:
        raise RuntimeError("Predeclared cutoff filter failed response audit")
    guarded = signal.fftconvolve(
        predicted, taps, mode="same")
    if len(guarded) != len(predicted) or not np.all(np.isfinite(guarded)):
        raise RuntimeError("Guarded echo output length/nonfinite values")
    return guarded, {
        "only_echo_estimate_is_filtered": True,
        "original_physical_mic_filtering": False,
        "linear_phase_symmetric_fir_length": FIR_GUARD_TAPS,
        "cutoff_hz": GUARD_CUTOFF_HZ,
        "window": GUARD_WINDOW,
        "implementation": "offline_centered_zero_phase_513tap_fir",
        "min_gain_100_to_2500_hz": round(float(np.min(low)), 7),
        "max_gain_5000_to_16000_hz": round(float(np.max(high)), 8),
        "noncausal_not_realtime": True,
        "selected_after_review_of_high_frequency_regressions": True,
    }


def band_comparison(full: dict, guard: dict) -> list[dict]:
    if full["reference_time_s"] != guard["reference_time_s"]:
        raise ValueError("Different measurement time slices")
    rows = []
    for a, b in zip(full["bands"], guard["bands"]):
        if a["band_hz"] != b["band_hz"]:
            raise RuntimeError("Band ordering mismatch")
        rows.append({
            "band_hz": a["band_hz"],
            "full_fir_erle_db": a["erle_db"],
            "guarded_fir_erle_db": b["erle_db"],
            "erle_change_guard_minus_full_db":
                round(b["erle_db"]-a["erle_db"], 5),
            "full_coherence_before":
                a["coherence_original_mic_psd_weights_before"],
            "full_coherence_after":
                a["coherence_original_mic_psd_weights_after"],
            "guarded_coherence_after":
                b["coherence_original_mic_psd_weights_after"],
            "coherence_after_guard_minus_full":
                round(b["coherence_original_mic_psd_weights_after"] -
                      a["coherence_original_mic_psd_weights_after"], 6),
        })
    return rows


def analyze(source_dir: Path, report_dir: Path) -> dict:
    source = source_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if not source.is_dir() or audit not in source.parents:
        raise ValueError("Only a verified private .audit capture is permitted")
    if report_dir.exists() or report_dir.is_symlink():
        raise FileExistsError("Report directory already exists")
    dest_parent = report_dir.expanduser().parent.resolve(strict=True)
    if dest_parent != source.parent:
        raise ValueError("Output must be a new sibling of the source")
    manifest_path = source/"manifest.json"
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (record.get("schema") != "cuewire-M5-paired-physical-capture-v1"
            or record.get("status") != "captured_for_offline_aec"
            or record.get("speaker_only_calibration_s") != 12
            or record.get("requested_duration_s") != 32):
        raise ValueError("Capture must be a successful 12s/32s double-talk take")
    ref_path = source/"reference-sent.wav"
    mic_path = source/"microphone-raw.wav"
    hashes = {
        "capture_manifest": digest(manifest_path),
        "digital_reference": digest(ref_path),
        "physical_mic_raw": digest(mic_path),
    }
    if (hashes["digital_reference"] != record["reference"]["sha256"]
            or hashes["physical_mic_raw"] != record["microphone"]["sha256"]):
        raise ValueError("Source audio SHA256 mismatch")
    ref, mic = read_wav(ref_path), read_wav(mic_path)
    if np.max(np.abs(mic)) >= 32767/32768:
        raise ValueError("Clipped microphone audio")
    lag, corr = lag_estimate(
        ref, mic, calibration_s=12, max_delay_s=2)
    original_echo, model = fit_calibration(ref, mic, lag)
    guard_echo, response = guarded_echo(original_echo)
    # One fixed model: the holdout and speech samples are never
    # consulted by fit_calibration() or guarded_echo() parameters.
    full_validation = compare(ref, mic, original_echo, lag, VALIDATE_S)
    guarded_validation = compare(ref, mic, guard_echo, lag, VALIDATE_S)
    minimum_gate_pass = full_validation["erle_db"] >= MIN_ERLE_DB
    # Explicitly avoid double-talk numerical work if the original
    # frozen model fails its unchanged minimal speaker-only gate.
    if minimum_gate_pass:
        full_voice = compare(ref, mic, original_echo, lag, DOUBLE_TALK_S)
        guarded_voice = compare(ref, mic, guard_echo, lag, DOUBLE_TALK_S)
    else:
        full_voice = guarded_voice = None

    # Speaker-only holdout here is *not blind* for the newly chosen
    # frequency guard, and must never be labelled a production pass.
    report = {
        "schema": "cuewire-M5-doubletalk-guarded-fir-ablation-v1",
        "status": "exploratory_reused_holdout_not_production_approved",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input_sha256": hashes,
        "model_training": model,
        "estimated_alignment_lag_ms": round(1000*lag/RATE, 5),
        "estimated_alignment_correlation": round(corr, 6),
        "frozen_original_speaker_only_gate_passes": minimum_gate_pass,
        "guard": response,
        "speaker_only": {
            "reference_time_s": list(VALIDATE_S),
            "full_fir_erle_db": full_validation["erle_db"],
            "guarded_fir_erle_db": guarded_validation["erle_db"],
            "bands": band_comparison(full_validation, guarded_validation),
        },
        "double_talk": (
            {
                "reference_time_s": list(DOUBLE_TALK_S),
                "full_fir_mixed_signal_energy_change_db":
                    full_voice["erle_db"],
                "guarded_fir_mixed_signal_energy_change_db":
                    guarded_voice["erle_db"],
                "bands": band_comparison(full_voice, guarded_voice),
            } if minimum_gate_pass else None
        ),
        "clean_voice_ground_truth_available": False,
        "near_end_voice_preservation_approved": False,
        "virtual_microphone_approved": False,
        "warnings": [
            "The original fullband model stays frozen; the guard modifies only estimated echo, never the microphone directly.",
            "This guard was chosen AFTER earlier highband regressions were observed on this exact recording; all results are exploratory and NOT independent.",
            "The entire 10-11.5s holdout is reused and cannot independently validate the guarded model.",
            "Total and bandwise mixed-speech ERLE describe a mixture; neither is a clean-voice distortion metric.",
            "The zero-phase FIR is noncausal/offline and is not yet a viable low-latency streaming design.",
            "No proof of microphone firmware AGC, unmodeled distortions, or true physical speaker echo suppression is established.",
            "No WAV, audio preview, speaker playback, virtual mic, or changes to PipeWire/defaults are generated.",
        ]
    }
    if any(digest(p) != hashes[k] for k, p in [
            ("capture_manifest", manifest_path),
            ("digital_reference", ref_path),
            ("physical_mic_raw", mic_path)]):
        raise RuntimeError("Audio input changed during processing")
    out = dest_parent/report_dir.name
    out.mkdir(mode=0o700)
    dest = out/"guarded-fir.json"
    with dest.open("x", encoding="utf-8") as f:
        json.dump(report, f, sort_keys=True, indent=2, ensure_ascii=False)
        f.write("\n")
    with (out/"SHA256SUMS").open("x", encoding="utf-8") as f:
        f.write(digest(dest)+"  guarded-fir.json\n")
    print("M5 GUARDED AEC research, reused holdout (NOT a production PASS)")
    print("Original training ERLE:", model["training_erle_db"], "dB")
    print("Speaker-only original/guarded ERLE:",
          full_validation["erle_db"], guarded_validation["erle_db"])
    if minimum_gate_pass:
        print("Speech+music mixture original/guarded energy change:",
              full_voice["erle_db"], guarded_voice["erle_db"])
        for row in report["double_talk"]["bands"]:
            print("Band", row["band_hz"], "ERLE original/guarded",
                  row["full_fir_erle_db"],
                  row["guarded_fir_erle_db"])
    else:
        print("Original speaker-only gate rejected; voice interval not analyzed")
    print("No audio written, voice preservation unknown.")
    print("Report:", dest)
    return report


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    try:
        analyze(args.capture, args.out)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        p.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
