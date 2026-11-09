#!/usr/bin/env python3
"""M5 physical 0.15 vs 0.30: reference-conditioned speaker level audit.

Read the TWO already captured speaker OFF/ON/OFF microphone WAVs and
ONE identical verified 20s digital reference from M5-006. Re-estimate
each run's process-to-mic alignment using only reference seconds 1..3.7,
then evaluate 4..9 and 10..16 without optimizing any AEC coefficients.

Magnitude-squared coherence * mic PSD is a finite-sample PROXY for
reference-linked linear acoustic power, NOT ground-truth echo SPL
or the fraction of near-end voice preserved. Report a speaker-OFF
unrelated-reference negative control. Do not retime frozen historic AEC.

Never open audio devices, play sound, create processed WAVs, change
system/audio settings or overwrite .audit records.
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

LOW_MANIFEST_SHA = "d38b12b6e5c4740deb9149dcfd43d0c49610ad4d9a098e96da425e4ae40786cf"
HIGH_MANIFEST_SHA = "3c0b6c2ba4468b84edd9fb550251277c62bd1a6ec57dc262f08818922ac9f9f4"
LOW_MIC_SHA = "f895567b0abe53612ee20385435d31577066f2f1589bad25b1af98b980adc30b"
HIGH_MIC_SHA = "5c9fa9b70b2c23e8c62dac5d1b17dc3e9106593af8f5e5754b05ee69013ccb50"
REFERENCE_SHA = "afd3b0ea1141147d6df284bfab62d2a9c9adc579053cfbea8c35559f2e94aa90"
SOURCE_MANIFEST_SHA = "b533714564a6ce9088c355338258ce8907a6ddaf26a76435c84999410114cb63"
EVAL = ((4.0, 9.0), (10.0, 16.0))
BANDS = ((70, 180), (180, 500), (500, 1500),
         (1500, 4000), (4000, 8000), (8000, 16000))
NFFT = 4096


def inspect_spectral_window(reference: np.ndarray, physical: np.ndarray) -> dict:
    if not (len(reference) == len(physical) and len(reference) >= 4 * RATE):
        raise ValueError("Need equal-length, at least 4-second music/mic window")
    f, coh = signal.coherence(reference, physical, fs=RATE,
                              nperseg=NFFT, noverlap=NFFT//2)
    _, mic_psd = signal.welch(physical, fs=RATE,
                              nperseg=NFFT, noverlap=NFFT//2)
    df = float(f[1]-f[0])
    if np.any(~np.isfinite(coh)) or np.any(~np.isfinite(mic_psd)):
        raise ValueError("Non-finite spectral data")
    out = []
    for low, high in BANDS:
        sel = (f >= low) & (f < high)
        mic_power = float(np.sum(mic_psd[sel]) * df)
        linked_power = float(np.sum(coh[sel]*mic_psd[sel]) * df)
        if mic_power <= 1e-20 or not np.isfinite(linked_power):
            raise ValueError("Insufficient/invalid band power")
        out.append({
            "band_hz": [low, high],
            "physical_mic_band_psd_power_dbfs": round(
                10*math.log10(mic_power), 5),
            "mic_psd_weighted_magnitude_squared_coherence": round(
                linked_power/mic_power, 6),
            "linear_reference_linked_power_proxy_dbfs": round(
                10*math.log10(max(linked_power, 1e-30)), 5),
            "linear_reference_linked_power_proxy_raw": linked_power,
        })
    return {
        "mic_rms_dbfs": round(db(rms(physical)), 5),
        "ref_rms_dbfs": round(db(rms(reference)), 5),
        "bands": out,
    }


def checked_take(directory: Path, expected_manifest: str, expected_mic: str,
                 expected_volume: float) -> tuple[dict, dict, np.ndarray]:
    directory = directory.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in directory.parents:
        raise ValueError("Capture must stay in private .audit")
    mf = directory/"manifest.json"
    mic_file = directory/"microphone-aba-raw.wav"
    h = {
        "manifest": digest(mf),
        "physical_mic_raw": digest(mic_file),
    }
    if h != {"manifest": expected_manifest, "physical_mic_raw": expected_mic}:
        raise ValueError("Physical capture SHA256 does not match historical verified manifest")
    m = json.loads(mf.read_text(encoding="utf-8"))
    if (m.get("schema") != "cuewire-M5-continuous-ABA-control-v1"
            or m.get("status") != "captured_aba_for_noise_diagnostics"
            or m.get("source_mic_sample_spec") != "s16le 1ch 48000Hz"
            or m.get("requested_speaker_off_before_s") != 8
            or m.get("requested_speaker_off_after_s") != 8
            or m.get("reference_duration_s") != 20.0
            or m.get("reference_wav_sha256") != REFERENCE_SHA
            or m.get("reference_source_manifest_sha256") != SOURCE_MANIFEST_SHA
            or m.get("microphone", {}).get("sha256") != expected_mic
            or abs(float(m.get("speaker_play_volume", -1))-expected_volume) > 1e-8):
        raise ValueError("A/B/A source schema, configuration or SHA256 mismatch")
    if (not str(m.get("source_mic_name", "")).startswith("alsa_input.") or
            not str(m.get("speaker_sink_name", "")).startswith("alsa_output.")):
        raise ValueError("Invalid physical mic/speaker source names")
    samples = read_wav(mic_file)
    if (len(samples) < 35 * RATE or len(samples) > 38 * RATE or
            float(np.max(np.abs(samples))) >= 32767 / 32768):
        raise ValueError("Microphone WAV length/clipping invalid")
    if digest(mic_file) != expected_mic or digest(mf) != expected_manifest:
        raise RuntimeError("A/B/A source files mutated while reading")
    return m, h, samples


def one_take(reference: np.ndarray, mic: np.ndarray, manifest: dict) -> dict:
    launch = float(manifest["playback_start_after_recorder_launch_s"])
    lag, timing = band_lag(reference, mic, launch, 1.0, 3.7)
    rows = []
    for start, end in EVAL:
        a, b = round(start*RATE), round(end*RATE)
        y = mic[lag+a:lag+b]
        x = reference[a:b]
        if len(y) != len(x):
            raise ValueError("Aligned evaluation window truncated")
        rows.append({
            "reference_time_s": [start, end],
            **inspect_spectral_window(x, y),
        })
    # Negative control: the exact same digital reference was NOT
    # playing in the 2-6s physical mic interval. Expect finite
    # estimation bias, not mathematically zero coherence.
    off = inspect_spectral_window(reference[3*RATE:7*RATE],
                                  mic[2*RATE:6*RATE])
    return {
        "volume_pw_play": manifest["speaker_play_volume"],
        "timing": timing,
        "speaker_on": rows,
        "speaker_off_unrelated_reference_negative_control": off,
        "speaker_off_rms_before_dbfs":
            manifest["phase_analysis"]["windows"]["before_speaker_on"]["segment_rms_dbfs"],
        "speaker_off_rms_after_dbfs":
            manifest["phase_analysis"]["windows"]["after_speaker_off"]["segment_rms_dbfs"],
        "speaker_on_original_report_rms_dbfs":
            manifest["phase_analysis"]["windows"]["speaker_on"]["segment_rms_dbfs"],
    }


def report_delta(first: dict, second: dict) -> list[dict]:
    deltas = []
    for low_win, high_win in zip(first["speaker_on"], second["speaker_on"]):
        if low_win["reference_time_s"] != high_win["reference_time_s"]:
            raise ValueError("Incompatible evaluation windows")
        band_deltas = []
        for low, high in zip(low_win["bands"], high_win["bands"]):
            if low["band_hz"] != high["band_hz"]:
                raise ValueError("Band ordering changed")
            band_deltas.append({
                "band_hz": low["band_hz"],
                "mic_band_power_change_030_minus_015_db": round(
                    high["physical_mic_band_psd_power_dbfs"] -
                    low["physical_mic_band_psd_power_dbfs"], 5),
                "mic_weighted_coherence_change_030_minus_015": round(
                    high["mic_psd_weighted_magnitude_squared_coherence"] -
                    low["mic_psd_weighted_magnitude_squared_coherence"], 6),
                "linked_power_proxy_change_030_minus_015_db": round(
                    high["linear_reference_linked_power_proxy_dbfs"] -
                    low["linear_reference_linked_power_proxy_dbfs"], 5),
            })
        deltas.append({
            "reference_time_s": low_win["reference_time_s"],
            "bands": band_deltas,
        })
    return deltas


def analyze(low_dir: Path, high_dir: Path, output_dir: Path) -> dict:
    low_dir = low_dir.expanduser().resolve(strict=True)
    high_dir = high_dir.expanduser().resolve(strict=True)
    if low_dir == high_dir:
        raise ValueError("Cannot compare the same physical take")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Numeric output already exists; cannot overwrite")
    parent = output_dir.expanduser().parent.resolve(strict=True)
    if parent != high_dir.parent:
        raise ValueError("Numeric output must be a fresh sibling of high-volume take")
    ml, hl, yl = checked_take(low_dir, LOW_MANIFEST_SHA, LOW_MIC_SHA, .15)
    mh, hh, yh = checked_take(high_dir, HIGH_MANIFEST_SHA, HIGH_MIC_SHA, .30)
    for name in ("source_mic_name", "speaker_sink_name", "reference_source_capture"):
        if ml[name] != mh[name]:
            raise ValueError("Physical source/sink/reference mismatch between takes")
    ref_dir = Path(ml["reference_source_capture"]).expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in ref_dir.parents:
        raise ValueError("Digital reference must remain local and private")
    ref_path = ref_dir/"reference-sent.wav"
    source_manifest_path = ref_dir/"manifest.json"
    if (digest(ref_path) != REFERENCE_SHA or
            digest(source_manifest_path) != SOURCE_MANIFEST_SHA):
        raise ValueError("Original digital reference/source manifest mutated")
    reference = read_wav(ref_path)
    if len(reference) != 20*RATE:
        raise ValueError("Unexpected digital reference frames")
    low = one_take(reference, yl, ml)
    high = one_take(reference, yh, mh)
    comparisons = report_delta(low, high)
    paths = {
        "level015_manifest": low_dir/"manifest.json",
        "level015_microphone": low_dir/"microphone-aba-raw.wav",
        "level030_manifest": high_dir/"manifest.json",
        "level030_microphone": high_dir/"microphone-aba-raw.wav",
        "digital_reference": ref_path,
        "digital_reference_manifest": source_manifest_path,
    }
    hashes = {
        "level015_manifest": hl["manifest"],
        "level015_microphone": hl["physical_mic_raw"],
        "level030_manifest": hh["manifest"],
        "level030_microphone": hh["physical_mic_raw"],
        "digital_reference": REFERENCE_SHA,
        "digital_reference_manifest": SOURCE_MANIFEST_SHA,
    }
    if any(digest(path) != hashes[name] for name, path in paths.items()):
        raise RuntimeError("Original private sources changed during analysis")
    out_data = {
        "schema": "cuewire-M5-physical-playback-level-reference-coherence-v1",
        "status": "numeric_diagnostic_only_no_echo_or_voice_certification",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashes,
        "fixed_playback_levels": [0.15, 0.30],
        "alignment_reference_time_s": [1.0, 3.7],
        "evaluation_reference_time_s": [list(x) for x in EVAL],
        "low_volume": low,
        "high_volume": high,
        "per_band_change": comparisons,
        "no_aec_filter_fitted": True,
        "voice_recorded_or_analyzed": False,
        "prospective_prior_gate_0_67881_db_remains_failed": True,
        "warnings": [
            "This proxy quantifies finite-sample linear digital-reference-linked spectral power, NOT pure physical echo SPL.",
            "Coherence PSD estimates have finite-sample positive bias even when source and mic are independent.",
            "The two separate mic takes were made sequentially; background and microphone AGC/DSP may differ.",
            "A fixed 0.15/0.30 per-stream gain change does not prove the historical system sink had ever been 100%.",
            "Digital playback file is pre-sink: mixer/hardware actual acoustic amplitude was not monitored.",
            "The alignment is diagnostic per take and never changes any previously failed AEC acceptance result.",
            "Only numeric JSON and SHA256SUMS are produced. Physical mic WAV remains private, immutable.",
        ],
    }
    out = parent / output_dir.name
    out.mkdir(mode=0o700)
    dest = out/"playback-coherence.json"
    with dest.open("x", encoding="utf-8") as fh:
        json.dump(out_data, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    with (out/"SHA256SUMS").open("x", encoding="utf-8") as fh:
        fh.write(digest(dest)+"  playback-coherence.json\n")
    print("M5 PLAYBACK LEVEL 0.15 VS 0.30 REFERENCE-COHERENCE DIAGNOSTIC")
    for row in comparisons:
        print("Reference interval:", row["reference_time_s"])
        for band in row["bands"]:
            print(" ", band["band_hz"], "linked proxy:",
                  band["linked_power_proxy_change_030_minus_015_db"], "dB",
                  "mic power:", band["mic_band_power_change_030_minus_015_db"], "dB")
    print("No AEC or processing, no audio generated; previous FAIL unchanged.")
    print("Report:", dest)
    return out_data


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--low-capture", type=Path, required=True)
    p.add_argument("--high-capture", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    try:
        analyze(args.low_capture, args.high_capture, args.out)
    except (ValueError, OSError, RuntimeError, KeyError) as e:
        p.exit(1, "ERROR: "+str(e)+"\n")


if __name__ == "__main__":
    main()
