#!/usr/bin/env python3
"""CueWire M5: absolute echo-linked residual vs physical speaker-OFF floor.

Research-only analysis of the two already SHA256-pinned, physical
OFF/ON/OFF M5 volume-control takes (0.15 and 0.30).
No voice, playback, audio output, device access or new recordings.

Prespecified ALIGN: digital reference 1.0..3.7s, 1500..4000Hz.
TRAIN: digital reference 3..9s, one 120ms/0.02 frozen FIR.
EVALUATE: digital reference 10..16s, unchanged fullband and
3800Hz echo-only guard. OFF: physical microphone 2..7s and 30..35s.

"Reference-linked power" is a Welch finite-sample linear coherence
PROXY, not an acoustic sound-pressure measurement. Its nonzero
speaker-OFF estimate quantifies estimator/ambient biases.
No new production AEC gate is created or retrospectively rescued.
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
from voice_m5_refcoherence import band_lag
from voice_m5_frozen_heldout import fit_frozen, indices, erle_db
from voice_m5_guarded_fir import guarded_echo
from voice_m5_playback_coherence import (
    checked_take, LOW_MANIFEST_SHA, HIGH_MANIFEST_SHA,
    LOW_MIC_SHA, HIGH_MIC_SHA, REFERENCE_SHA, SOURCE_MANIFEST_SHA,
)

ALIGN_S = (1.0, 3.7)
TRAIN_S = (3.0, 9.0)
EVAL_S = (10.0, 16.0)
OFF_PRE_S = (2.0, 7.0)
OFF_POST_S = (30.0, 35.0)
BANDS = ((70, 180), (180, 500), (500, 1500),
         (1500, 4000), (4000, 8000), (8000, 16000))
NFFT = 4096


def linear_spectral_proxy(reference: np.ndarray, mic: np.ndarray,
                          residual: np.ndarray | None = None) -> dict:
    """Welch PSD/coherent proxy for equal-length signals.

    No filtering of actual mic; no dependence on fitted AEC filter.
    The linked-power proxy integrates coherence*mic-PSD. Remaining
    non-zero proxy after ideal cancellation is expected from finite
    sample coherence bias and unrelated background.
    """
    if (reference.ndim != 1 or mic.ndim != 1 or
            len(reference) != len(mic) or len(mic) < 4*RATE or
            (residual is not None and len(residual) != len(mic)) or
            not np.all(np.isfinite(reference)) or
            not np.all(np.isfinite(mic)) or
            (residual is not None and not np.all(np.isfinite(residual)))):
        raise ValueError("Need finite equal-length mono signals >=4 seconds")
    f, pxx = signal.welch(reference, fs=RATE, nperseg=NFFT,
                           noverlap=NFFT//2)
    _, pyy = signal.welch(mic, fs=RATE, nperseg=NFFT,
                          noverlap=NFFT//2)
    _, pxy = signal.csd(reference, mic, fs=RATE, nperseg=NFFT,
                         noverlap=NFFT//2)
    if residual is not None:
        _, prr = signal.welch(residual, fs=RATE, nperseg=NFFT,
                              noverlap=NFFT//2)
        _, pxr = signal.csd(reference, residual, fs=RATE,
                            nperseg=NFFT, noverlap=NFFT//2)
    df = float(f[1]-f[0])
    bands = []
    for low, high in BANDS:
        ix = (f >= low) & (f < high)
        if not np.any(ix):
            raise ValueError("No spectral bins within a requested band")
        # Coherence-weighted mic power. We divide by Pxx only in
        # bins of nonzero reference power. In truly quiet reference
        # bins the proxy must not be interpreted as echo.
        xr = pxx[ix]
        yr = pyy[ix]
        cs = pxy[ix]
        linked = float(np.sum(np.abs(cs)**2 /
                             np.maximum(xr, 1e-25))*df)
        power = float(np.sum(yr)*df)
        ref_power = float(np.sum(xr)*df)
        if not (np.isfinite(linked) and np.isfinite(power)
                and power > 1e-24 and ref_power > 1e-24):
            raise ValueError("Invalid reference/mic PSD band powers")
        row = {
            "band_hz": [low, high],
            "mic_band_power_dbfs": round(10*math.log10(power), 5),
            "digital_reference_band_power_dbfs":
                round(10*math.log10(ref_power), 5),
            "reference_linked_power_proxy_dbfs":
                round(10*math.log10(max(linked, 1e-30)), 5),
            "reference_linked_power_proxy_linear": linked,
            "mic_weighted_coherence": round(linked/power, 6),
        }
        if residual is not None:
            rr = float(np.sum(prr[ix])*df)
            rem = float(np.sum(np.abs(pxr[ix])**2 /
                               np.maximum(xr, 1e-25))*df)
            row.update({
                "residual_band_power_dbfs":
                    round(10*math.log10(max(rr, 1e-30)), 5),
                "residual_linked_power_proxy_dbfs":
                    round(10*math.log10(max(rem, 1e-30)), 5),
                "residual_linked_power_proxy_linear": rem,
                "linked_proxy_before_minus_after_db": round(
                    10*math.log10(max(linked, 1e-30) /
                                  max(rem, 1e-30)), 5),
                "residual_mic_weighted_coherence":
                    round(rem/max(rr, 1e-24), 6),
                "band_erle_db": round(
                    10*math.log10(power/max(rr, 1e-24)), 5),
            })
        bands.append(row)
    return {
        "mic_total_rms_dbfs": round(db(rms(mic)), 5),
        "residual_total_rms_dbfs": (
            round(db(rms(residual)), 5) if residual is not None else None),
        "bands": bands,
    }


def background_analysis(reference: np.ndarray,
                        mic: np.ndarray) -> dict:
    """Two TRUE speaker-OFF periods; reject interpolation as truth."""
    if len(reference) != 20*RATE or len(mic) < 35*RATE:
        raise ValueError("Insufficient OFF reference/microphone data")
    # Reference window is unrelated to physical mic OFF windows.
    unrelated = reference[10*RATE:15*RATE]
    pre = mic[2*RATE:7*RATE]
    post = mic[30*RATE:35*RATE]
    before = linear_spectral_proxy(unrelated, pre)
    after = linear_spectral_proxy(unrelated, post)
    per_band = []
    for p, q in zip(before["bands"], after["bands"]):
        if p["band_hz"] != q["band_hz"]:
            raise RuntimeError("OFF band mismatch")
        floor_power = (10**(p["mic_band_power_dbfs"]/10) +
                       10**(q["mic_band_power_dbfs"]/10)) / 2
        null_linked = (p["reference_linked_power_proxy_linear"] +
                       q["reference_linked_power_proxy_linear"]) / 2
        per_band.append({
            "band_hz": p["band_hz"],
            "floor_before_dbfs": p["mic_band_power_dbfs"],
            "floor_after_dbfs": q["mic_band_power_dbfs"],
            "floor_after_minus_before_db": round(
                q["mic_band_power_dbfs"] -
                p["mic_band_power_dbfs"], 5),
            "floor_mean_power_dbfs": round(
                10*math.log10(max(floor_power, 1e-30)), 5),
            "unrelated_reference_null_proxy_before_dbfs":
                p["reference_linked_power_proxy_dbfs"],
            "unrelated_reference_null_proxy_after_dbfs":
                q["reference_linked_power_proxy_dbfs"],
            "unrelated_reference_null_mean_power_dbfs": round(
                10*math.log10(max(null_linked, 1e-30)), 5),
        })
    return {
        "mic_off_before_wav_time_s": list(OFF_PRE_S),
        "mic_off_after_wav_time_s": list(OFF_POST_S),
        "unrelated_reference_s": [10.0, 15.0],
        "whole_off_before_rms_dbfs": before["mic_total_rms_dbfs"],
        "whole_off_after_rms_dbfs": after["mic_total_rms_dbfs"],
        "band_noise_and_null": per_band,
    }


def speaker_only_takes(reference: np.ndarray,
                       mic: np.ndarray, manifest: dict) -> dict:
    lag, timing = band_lag(
        reference, mic,
        float(manifest["playback_start_after_recorder_launch_s"]),
        *ALIGN_S)
    # Exactly one frozen FIR, fitted ONLY on 3-9s. No selection on
    # the 10-16s validation. Guard is predeclared and never refitted.
    predicted, model = fit_frozen(reference, mic, lag)
    guarded, guard = guarded_echo(predicted)
    val_a, val_b = indices(EVAL_S, lag, len(mic))
    x = reference[round(EVAL_S[0]*RATE):round(EVAL_S[1]*RATE)]
    y = mic[val_a:val_b]
    if len(x) != len(y):
        raise ValueError("Echo calibration holdout truncated")
    full = y - predicted[val_a:val_b]
    safe = y - guarded[val_a:val_b]
    if not np.all(np.isfinite(full)) or not np.all(np.isfinite(safe)):
        raise ValueError("Nonfinite AEC predictions")
    off = background_analysis(reference, mic)
    full_specs = linear_spectral_proxy(x, y, full)
    guard_specs = linear_spectral_proxy(x, y, safe)
    floor_map = {tuple(b["band_hz"]):b
                 for b in off["band_noise_and_null"]}
    for variant in (full_specs, guard_specs):
        for band in variant["bands"]:
            floor = floor_map[tuple(band["band_hz"])]
            band["residual_power_above_mean_off_floor_db"] = round(
                band["residual_band_power_dbfs"] -
                floor["floor_mean_power_dbfs"], 5)
            band["residual_linked_proxy_above_unrelated_off_null_db"] = round(
                band["residual_linked_power_proxy_dbfs"] -
                floor["unrelated_reference_null_mean_power_dbfs"], 5)
        variant["full_band_erle_db"] = round(
            erle_db(y, full if variant is full_specs else safe), 5)
    return {
        "volume": manifest["speaker_play_volume"],
        "alignment": timing,
        "frozen_model": model,
        "guard_config": guard,
        "holdout_reference_time_s": list(EVAL_S),
        "background": off,
        "whole_mic_on_rms_dbfs": round(db(rms(y)), 5),
        "unfiltered_fullband": full_specs,
        "echo_only_3800hz_guard": guard_specs,
        "no_production_gate_applied": True,
    }


def combined_band_proxy(take: dict, variant: str,
                        low_hz: int = 500, high_hz: int = 4000
                        ) -> dict:
    selected = [b for b in take[variant]["bands"]
                if b["band_hz"][0] >= low_hz and
                b["band_hz"][1] <= high_hz]
    if not selected:
        raise ValueError("No speech-relevant proxy bands")
    before = sum(b["reference_linked_power_proxy_linear"] for b in selected)
    after = sum(b["residual_linked_power_proxy_linear"] for b in selected)
    gain = 10*math.log10(max(before, 1e-30)/max(after, 1e-30))
    return {
        "band_hz": [low_hz, high_hz],
        "linked_proxy_before_dbfs": round(10*math.log10(max(before, 1e-30)), 5),
        "linked_proxy_after_dbfs": round(10*math.log10(max(after, 1e-30)), 5),
        "linked_proxy_suppression_db": round(gain, 5),
        "raw_proxy_power_before": before,
        "raw_proxy_power_after": after,
    }


def analyze(low_dir: Path, high_dir: Path, output_dir: Path) -> dict:
    if low_dir == high_dir:
        raise ValueError("Two different physical captures required")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Cannot overwrite an existing numeric audit")
    low_dir = low_dir.expanduser().resolve(strict=True)
    high_dir = high_dir.expanduser().resolve(strict=True)
    dest_parent = output_dir.expanduser().parent.resolve(strict=True)
    if dest_parent != high_dir.parent:
        raise ValueError("New output must be a sibling of the high-level capture")
    ml, hl, yl = checked_take(
        low_dir, LOW_MANIFEST_SHA, LOW_MIC_SHA, .15)
    mh, hh, yh = checked_take(
        high_dir, HIGH_MANIFEST_SHA, HIGH_MIC_SHA, .30)
    for name in ("source_mic_name", "speaker_sink_name",
                 "reference_source_capture"):
        if ml[name] != mh[name]:
            raise ValueError("Not the same physical mic, sink and reference")
    ref_dir = Path(ml["reference_source_capture"]).expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in ref_dir.parents:
        raise ValueError("Digital reference must be in private .audit")
    ref_path, ref_mf = ref_dir/"reference-sent.wav", ref_dir/"manifest.json"
    if (digest(ref_path) != REFERENCE_SHA or
            digest(ref_mf) != SOURCE_MANIFEST_SHA):
        raise ValueError("Original digital reference SHA256 mismatch")
    x = read_wav(ref_path)
    if len(x) != 20*RATE:
        raise ValueError("Expected exactly 20 seconds of reference")
    low = speaker_only_takes(x, yl, ml)
    high = speaker_only_takes(x, yh, mh)
    for take in (low, high):
        take["speech_band_proxies"] = {
            "full_fir": combined_band_proxy(take, "unfiltered_fullband"),
            "echo_only_guard": combined_band_proxy(
                take, "echo_only_3800hz_guard"),
        }
    paths = [
        (low_dir/"manifest.json", hl["manifest"]),
        (low_dir/"microphone-aba-raw.wav", hl["physical_mic_raw"]),
        (high_dir/"manifest.json", hh["manifest"]),
        (high_dir/"microphone-aba-raw.wav", hh["physical_mic_raw"]),
        (ref_path, REFERENCE_SHA), (ref_mf, SOURCE_MANIFEST_SHA),
    ]
    if any(digest(p) != checksum for p, checksum in paths):
        raise RuntimeError("Audited RAW sources changed during analysis")
    output = {
        "schema": "cuewire-M5-echo-linked-absolute-floor-numeric-v1",
        "status": "diagnostic_only_no_voice_preservation_or_production_pass",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": {
            "level015_manifest": hl["manifest"],
            "level015_mic_raw": hl["physical_mic_raw"],
            "level030_manifest": hh["manifest"],
            "level030_mic_raw": hh["physical_mic_raw"],
            "digital_reference": REFERENCE_SHA,
            "digital_reference_manifest": SOURCE_MANIFEST_SHA,
        },
        "training_reference_s": list(TRAIN_S),
        "heldout_reference_s": list(EVAL_S),
        "low_volume": low,
        "high_volume": high,
        "original_prospective_0_67881_db_gate_still_failed": True,
        "recorded_real_near_end_speech": False,
        "voice_preservation_tested": False,
        "approved_for_virtual_microphone": False,
        "no_audio_files_emitted": True,
        "warnings": [
            "Reference-linked power is an uncalibrated finite-sample linear coherence proxy, not exact echo SPL.",
            "After subtraction the proxy need not reach zero even for ideal echo removal due to coherence bias.",
            "OFF reference/mic coherence is a separate negative control, not a universal bias correction or statistical confidence bound.",
            "OFF before/after may differ due to AGC/DSP, background transients and room noise; their mean is not guaranteed to equal ON independent background.",
            "This uses previously inspected M5 volume-control captures and a known guard; it is not an independent blind validation.",
            "FIR and echo-only guard parameters are fixed, but the alignment uses early reference data; no holdout data tune the filter.",
            "The 513-tap guard is offline noncausal; no real-time latency or user voice preservation was assessed.",
            "An old 0.67881dB prospective FAIL cannot be converted into a PASS by this diagnostic.",
            "All original audio and manifests remain SHA256-pinned, unchanged and private.",
        ],
    }
    target = dest_parent / output_dir.name
    target.mkdir(mode=0o700)
    dest = target/"echo-floor.json"
    with dest.open("x", encoding="utf-8") as fd:
        json.dump(output, fd, indent=2, ensure_ascii=False, sort_keys=True)
        fd.write("\n")
    with (target/"SHA256SUMS").open("x", encoding="utf-8") as fd:
        fd.write(digest(dest)+"  echo-floor.json\n")
    print("M5 echo-linked absolute floor audit: NUMERIC ONLY; no audio generated")
    for key, take in (("0.15",low), ("0.30",high)):
        print("Volume",key,"train ERLE", take["frozen_model"]["training_erle_db"])
        for name, metrics in take["speech_band_proxies"].items():
            print(" ",name,"500-4000Hz linked proxy before/after",
                  metrics["linked_proxy_before_dbfs"],
                  metrics["linked_proxy_after_dbfs"],
                  "suppression dB",metrics["linked_proxy_suppression_db"])
        print(" Whole-band speaker-only heldout ERLE:",
              take["unfiltered_fullband"]["full_band_erle_db"],"full,",
              take["echo_only_3800hz_guard"]["full_band_erle_db"],"guard")
    print("Original 0.67881dB FAIL unchanged; voice preservation UNKNOWN.")
    print("Report:",dest)
    return output


def main() -> None:
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--low-capture",type=Path,required=True)
    p.add_argument("--high-capture",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    try:
        analyze(args.low_capture,args.high_capture,args.out)
    except (ValueError,OSError,RuntimeError,KeyError) as e:
        p.exit(1,"ERROR: "+str(e)+"\n")


if __name__=="__main__":
    main()
