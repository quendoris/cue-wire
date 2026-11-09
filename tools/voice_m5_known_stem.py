#!/usr/bin/env python3
"""CueWire M5: synthetic doubletalk with a KNOWN private clean voice stem.

Numerical ONLY. Mix one verified M4 voice-only RAW take digitally into
a *physical speaker-only* M5-ABA heldout mic interval. A frozen FIR is
trained on the earlier speaker-only M5 calibration. Compare the mixture
and frozen/echo-only guarded residuals against exact known speech stem.

This confirms software signal math, not actual simultaneous doubletalk
voice preservation, because physical microphone AGC and nonlinearity
cannot be recreated by digital stem injection.

NO audio output, WAV export, playback, system settings, background
processes or virtual mic routing. Original M4/M5 files immutable.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import wave

import numpy as np
from scipy import signal

from voice_m5_aec import AUDIT, RATE, db, digest, read_wav, rms
from voice_m5_frozen_heldout import (
    HELDOUT_REFERENCE_S, TRAIN_REFERENCE_S,
    fit_frozen, indices, erle_db, verify_inputs,
)
from voice_m5_guarded_fir import guarded_echo
from voice_m5_refcoherence import band_lag

VOICE_TARGET_RMS_DBFS = -39.0
VOICE_MIN_SECONDS = 3.0
VOICE_MAX_SECONDS = 6.0
VOICE_OFFSET_S = 6.0  # Older M4 ALSA recording protocol starts with 5s silence.
BANDS = ((70, 180), (180, 500), (500, 1500),
         (1500, 4000), (4000, 8000), (8000, 16000))


def clean_stem_score(stem: np.ndarray,
                     baseline_mixed: np.ndarray,
                     full_residual: np.ndarray,
                     guard_residual: np.ndarray) -> dict:
    """Absolute digital reference-stem SNR; no scale/lag optimization."""
    arrays = (stem, baseline_mixed, full_residual, guard_residual)
    if any(a.ndim != 1 or len(a) != len(stem) for a in arrays):
        raise ValueError("All score inputs must be same-length mono PCM")
    if len(stem) < round(VOICE_MIN_SECONDS*RATE):
        raise ValueError("Known clean voice stem is shorter than 3s")
    if not all(np.all(np.isfinite(a)) for a in arrays):
        raise ValueError("Nonfinite simulated audio")
    stem_power = rms(stem)**2
    if stem_power < 1e-12:
        raise ValueError("Known clean stem effectively silent")

    def score(observed: np.ndarray) -> dict:
        interference = observed - stem
        noise_power = rms(interference)**2
        return {
            "known_voice_stem_rms_dbfs": round(db(math.sqrt(stem_power)), 5),
            "interference_rms_dbfs": round(db(math.sqrt(noise_power)), 5),
            "exact_stem_snr_db": round(
                10*math.log10(max(stem_power, 1e-24) /
                              max(noise_power, 1e-24)), 5),
            "difference_from_exact_clean_stem_rms_dbfs":
                round(db(rms(interference)), 5),
        }

    scored = {
        "original_mixture": score(baseline_mixed),
        "full_fir_subtraction": score(full_residual),
        "guarded_fir_subtraction": score(guard_residual),
    }
    baseline = scored["original_mixture"]["exact_stem_snr_db"]
    for name in ("full_fir_subtraction", "guarded_fir_subtraction"):
        scored[name]["exact_stem_snr_improvement_db"] = round(
            scored[name]["exact_stem_snr_db"]-baseline, 5)
    # To avoid spurious claims, label the metric as reference-stem
    # SNR, not PESQ/STOI/SI-SDR or a real voice-preservation score.
    return scored


def error_by_band(stem: np.ndarray, cases: dict[str, np.ndarray]) -> list[dict]:
    out = []
    for lo, hi in BANDS:
        sos = signal.butter(
            4, [lo, hi], btype="bandpass", fs=RATE, output="sos")
        speech_band = signal.sosfiltfilt(sos, stem)
        speech_power = rms(speech_band)**2
        row = {"band_hz": [lo, hi],
               "clean_voice_band_rms_dbfs": round(
                    db(math.sqrt(speech_power)), 5)}
        for name, waveform in cases.items():
            error = signal.sosfiltfilt(sos, waveform-stem)
            power = rms(error)**2
            row[name+"_interference_rms_dbfs"] = round(
                db(math.sqrt(power)), 5)
            row[name+"_exact_voice_snr_db"] = round(
                10*math.log10(
                    max(speech_power, 1e-24)/max(power, 1e-24)), 5)
        out.append(row)
    return out


def validate_m4_voice_manifest(manifest: dict) -> tuple[str, str, str]:
    """Recognize the two real CueWire M4 recorder schemas; never relax SHA256.

    The original M4/R0/ALSA capture used raw_wav/raw_sha256; the later
    PipeWire RAW capture uses wav/wav_sha256. Unknown or rejected schemas
    MUST NOT be treated as equivalent based solely on filename.
    """
    if manifest.get("status") != "recorded_unprocessed_raw":
        raise ValueError("M4 voice RAW capture is not successfully recorded")
    schema = manifest.get("schema")
    if schema == "cuewire-M4-raw-alsa-v1":
        if (manifest.get("source") != "physical_alsa_hw" or
                not re.fullmatch(r"hw:\d{1,3},\d{1,3}",
                                 str(manifest.get("device", ""))) or
                not manifest.get("card_id")):
            raise ValueError("M4 ALSA manifest lacks verified physical hardware provenance")
        if manifest.get("raw_wav") != "raw.wav":
            raise ValueError("M4 ALSA raw_wav must be exactly raw.wav")
        processing = manifest.get("processing")
        if not isinstance(processing, dict) or any(
                processing.get(name) is not False for name in
                ("aec", "denoise", "eq", "compression", "virtual_mic",
                 "normalization", "monitoring")):
            raise ValueError("M4 ALSA capture must have unprocessed recording metadata")
        sha = manifest.get("raw_sha256")
        kind = "physical_alsa_hw"
    elif schema == "cuewire-M4-raw-capture-v2":
        if (manifest.get("wav") != "raw.wav" or
                not manifest.get("mic_node_name", "").startswith("alsa_input.")):
            raise ValueError("M4 PipeWire capture must explicitly name a physical RAW mic")
        processing = manifest.get("explicit_processing")
        if not isinstance(processing, dict) or any(
                processing.get(name) is not False for name in
                ("aec", "denoise", "eq", "compression",
                 "manual_gain_normalization", "playback", "virtual_microphone")):
            raise ValueError("M4 PipeWire capture must have unprocessed recording metadata")
        sha = manifest.get("wav_sha256")
        kind = "physical_pipewire"
    else:
        raise ValueError(f"Unknown M4 capture schema: {schema!r}")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("M4 voice RAW manifest has invalid SHA256")
    meta = manifest.get("measurements")
    if isinstance(meta, dict):
        if (meta.get("clipped_samples") not in (None, 0) or
                meta.get("rate") not in (None, RATE) or
                meta.get("channels") not in (None, 1) or
                meta.get("sample_width_bytes") not in (None, 2)):
            raise ValueError("M4 capture measurements incompatible/clipped")
    return sha, schema, kind


def read_voice_excerpt(path: Path, start_seconds: float,
                       max_seconds: float = VOICE_MAX_SECONDS) -> np.ndarray:
    """Read only a bounded speech window, even for long 150s ALSA RAW takes.

    M5's general read_wav is intentionally capped at 50s; ALSA M4
    recorder can legitimately produce up to 300s, so avoid decoding
    the whole file or rejecting a good long-voice recording.
    """
    if (not math.isfinite(start_seconds) or start_seconds < 0 or
            start_seconds > 300 - VOICE_MIN_SECONDS):
        raise ValueError("Invalid M4 speech excerpt start")
    with wave.open(str(path), "rb") as input_wav:
        info = (input_wav.getframerate(), input_wav.getnchannels(),
                input_wav.getsampwidth(), input_wav.getcomptype())
        if info != (RATE, 1, 2, "NONE"):
            raise ValueError(f"Expected physical mono PCM16 48k for M4 RAW, got {info}")
        total = input_wav.getnframes()
        if not RATE <= total <= 300 * RATE:
            raise ValueError("M4 RAW duration outside 1..300 seconds")
        start = round(start_seconds * RATE)
        frames = min(round(max_seconds*RATE), total-start)
        if frames < round(VOICE_MIN_SECONDS*RATE):
            raise ValueError("M4 voice RAW too short after initial calibration silence")
        input_wav.setpos(start)
        raw = input_wav.readframes(frames)
        if len(raw) != frames*2:
            raise ValueError("M4 RAW truncated during selected speech excerpt")
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0


def analyze(aba_dir: Path, voice_dir: Path, output_dir: Path,
            voice_start_s: float = VOICE_OFFSET_S) -> dict:
    # Reuse the audited M5-ABA-001 source and immutable M5-006
    # reference link verification. No audio device is ever opened.
    ref_path, speaker_mic_path, hashes, aba, manifest_paths = verify_inputs(
        aba_dir, output_dir)
    voice_dir = voice_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in voice_dir.parents or not voice_dir.is_dir():
        raise ValueError("Known voice input must be private under .audit")
    voice_manifest_path = voice_dir/"manifest.json"
    voice_manifest = json.loads(
        voice_manifest_path.read_text(encoding="utf-8"))
    expected_voice_sha, voice_schema, voice_backend = (
        validate_m4_voice_manifest(voice_manifest))
    voice_path = voice_dir/"raw.wav"
    hashes["voice_manifest"] = digest(voice_manifest_path)
    hashes["known_voice_raw"] = digest(voice_path)
    if hashes["known_voice_raw"] != expected_voice_sha:
        raise ValueError("Known voice RAW SHA256 does not match M4 manifest")

    reference = read_wav(ref_path)
    physical_speaker_mic = read_wav(speaker_mic_path)
    voice = read_voice_excerpt(voice_path, voice_start_s)
    if np.max(np.abs(voice)) >= 32767/32768:
        raise ValueError("Clipped clean speech control; reject synthetic mix")
    lag, alignment = band_lag(
        reference, physical_speaker_mic,
        float(aba["playback_start_after_recorder_launch_s"]))
    predicted, frozen = fit_frozen(reference, physical_speaker_mic, lag)
    guarded, guard_spec = guarded_echo(predicted)
    val_start, val_end = indices(
        HELDOUT_REFERENCE_S, lag, len(physical_speaker_mic))
    speaker_only = physical_speaker_mic[val_start:val_end]
    full_speaker_residual = speaker_only-predicted[val_start:val_end]
    guard_speaker_residual = speaker_only-guarded[val_start:val_end]
    speaker_only_erle = erle_db(speaker_only, full_speaker_residual)
    if speaker_only_erle < 1.0:
        raise ValueError(
            f"Original frozen speaker-only holdout below 1 dB: "
            f"{speaker_only_erle:.5f} dB. No voice injection permitted.")

    voice_begin = 0  # read_voice_excerpt has already skipped initial silence
    voice_samples = min(len(voice), len(speaker_only))
    if voice_samples < round(VOICE_MIN_SECONDS*RATE):
        raise ValueError("M4 voice RAW insufficient for >=3s clean stem")
    original_voice = voice[voice_begin:voice_begin+voice_samples]
    input_level = db(rms(original_voice))
    if input_level < -65.0:
        raise ValueError("M4 source is too quiet; not a reliable clean stem")
    scale = 10**((VOICE_TARGET_RMS_DBFS-input_level)/20)
    stem = original_voice*scale

    background = speaker_only[:voice_samples]
    full_echo = predicted[val_start:val_start+voice_samples]
    limited_echo = guarded[val_start:val_start+voice_samples]
    original_mixture = background+stem
    full_output = original_mixture-full_echo
    guard_output = original_mixture-limited_echo
    if max(float(np.max(np.abs(a))) for a in
           (original_mixture, full_output, guard_output)) > .98:
        raise ValueError("Synthetic digital mixture unsafe amplitude")

    # By construction our subtraction is only based on reference;
    # confirm injecting clean speech cannot change the computed echo
    # or modify the clean speech contribution arithmetically.
    full_identity_error = float(np.max(np.abs(
        (full_output-(background-full_echo))-stem)))
    guard_identity_error = float(np.max(np.abs(
        (guard_output-(background-limited_echo))-stem)))
    if max(full_identity_error, guard_identity_error) > 1e-12:
        raise RuntimeError("Injected voice was numerically modified")

    cases = {
        "original": original_mixture,
        "full_fir": full_output,
        "echo_guard": guard_output,
    }
    results = clean_stem_score(
        stem, original_mixture, full_output, guard_output)
    rows = error_by_band(stem, cases)
    safety = {
        "max_stem_algebraic_identity_error": round(
            max(full_identity_error, guard_identity_error), 15),
        "no_voice_used_in_acoustic_model_fit": True,
        "physical_microphone_agc_effect_simulated": False,
        "synthetic_sum_only_no_physical_doubletalk": True,
        "clean_speech_root_unchanged": True,
    }
    paths = {
        **manifest_paths,
        "digital_reference": ref_path,
        "physical_microphone": speaker_mic_path,
        "voice_manifest": voice_manifest_path,
        "known_voice_raw": voice_path,
    }
    keys = {
        "aba_manifest": "aba_manifest",
        "reference_manifest": "reference_manifest",
        "digital_reference": "digital_reference",
        "physical_microphone": "physical_microphone",
        "voice_manifest": "voice_manifest",
        "known_voice_raw": "known_voice_raw",
    }
    if any(digest(paths[p]) != hashes[h] for p, h in keys.items()):
        raise RuntimeError("Immutable audio source mutated during analysis")

    report = {
        "schema": "cuewire-M5-ABA-known-voice-digital-injection-v1",
        "status": "research_only_known_stem_synthetic_doubletalk",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashes,
        "source_notes": [
            "Physical speaker-only M5-ABA mic track from actual prior room recording.",
            "M4 voice-only RAW recorded at a different time, injected digitally into M5 speaker track.",
            "This is NOT a real simultaneously recorded speaker+voice microphone take.",
        ],
        "alignment": alignment,
        "frozen_fir": frozen,
        "guard": guard_spec,
        "speaker_only_heldout_erle_db": round(speaker_only_erle, 5),
        "heldout_reference_time_s": list(HELDOUT_REFERENCE_S),
        "injected_voice_target_rms_dbfs": VOICE_TARGET_RMS_DBFS,
        "voice_original_rms_dbfs": round(input_level, 5),
        "voice_source_capture_schema": voice_schema,
        "voice_source_capture_backend": voice_backend,
        "voice_excerpt_start_s": round(voice_start_s, 5),
        "voice_excerpt_sampling_policy":
            "fixed user-selectable offset; original M4 ALSA protocol starts with 5s silence",
        "voice_rescaling_gain": round(scale, 8),
        "injected_voice_seconds": round(voice_samples/RATE, 5),
        "known_voice_stem_metrics": results,
        "known_voice_stem_per_band_metrics": rows,
        "algebraic_safety_checks": safety,
        "voice_preservation_validated_under_real_acoustics": False,
        "approved_for_virtual_mic": False,
        "warnings": [
            "This exact-stem trial checks digital subtraction identity, not acoustic voice preservation.",
            "No microphone AGC/nonlinear interaction with real simultaneous speaking is represented.",
            "The ABA holdout was reviewed previously and is NOT an independent guard validation.",
            "Improved known-stem SNR measures reduced combined interference relative to a digitally injected stem.",
            "An earlier observed guard highband regression influenced the guard design; no new blind holdout.",
            "The 513-tap centered echo guard is offline, non-causal, not approved for low-latency live processing.",
            "No audio is generated or uploaded, no microphone routed, and no existing recording changed.",
        ],
    }
    out = output_dir.expanduser().parent.resolve(strict=True)/output_dir.name
    out.mkdir(mode=0o700)
    report_path = out/"known-stem.json"
    with report_path.open("x", encoding="utf-8") as f:
        json.dump(report, f, sort_keys=True, indent=2, ensure_ascii=False)
        f.write("\n")
    with (out/"SHA256SUMS").open("x", encoding="utf-8") as f:
        f.write(digest(report_path)+"  known-stem.json\n")
    print("M5 KNOWN CLEAN STEM - SYNTHETIC DOUBLE TALK (NUMERIC ONLY)")
    print("Speaker-only original model heldout ERLE:",
          report["speaker_only_heldout_erle_db"], "dB")
    print("Clean stem:", report["injected_voice_seconds"],
          "seconds, target", VOICE_TARGET_RMS_DBFS, "dBFS")
    for name, vals in results.items():
        print(name, "exact-stem SNR:", vals["exact_stem_snr_db"],
              "dB", "improvement:",
              vals.get("exact_stem_snr_improvement_db", 0), "dB")
    print("Unmodified digital injected speech identity:",
          safety["max_stem_algebraic_identity_error"])
    print("No audio generated. Real voice preservation NOT validated.")
    print("Report:", report_path)
    return report


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", required=True, type=Path,
                   help="Existing M5-ABA-001 speaker-only physical mic capture")
    p.add_argument("--voice", required=True, type=Path,
                   help="Existing M4 RAW voice-only folder with manifest.json")
    p.add_argument("--out", required=True, type=Path,
                   help="NEW private sibling of M5-ABA capture")
    p.add_argument("--voice-start-s", type=float, default=VOICE_OFFSET_S,
                   help="Speech-only excerpt start in M4 RAW; default 6s skips initial 5s silence")
    args = p.parse_args()
    try:
        analyze(args.capture, args.voice, args.out, args.voice_start_s)
    except (ValueError, OSError, RuntimeError, KeyError) as e:
        p.exit(1, "ERROR: "+str(e)+"\n")


if __name__ == "__main__":
    main()
