#!/usr/bin/env python3
"""M5 actual old/new physical calibration comparison — NUMERICAL ONLY.

Compare only early speaker-only music reference time 0.8–2.3,
3.0–4.5 and 6.0–7.5s on the actual doubletalk-001 and -002
captures. Use the previously *committed* lag in each take.
DO NOT fit/re-time any AEC model, access 10–11.5s holdout,
or inspect the later human-voice segment.

The two manifests, both physical WAVs and the identical reference
are SHA256-pinned to the historically audited captures. NO WAV/audio
output, playback, microphone device access, settings or routing.
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

WINDOWS = ((0.8, 2.3), (3.0, 4.5), (6.0, 7.5))
BANDS = ((20, 70), (70, 180), (180, 500), (500, 1500),
         (1500, 4000), (4000, 8000), (8000, 16000))

# SHA256s from the actual historical M5 manifests and analysis reports.
OLD_MANIFEST_SHA = "6e3a33caf73620e718d2e378a8daec96800e24712477fde932cffecaaaa7bb83"
NEW_MANIFEST_SHA = "7664960264e0117a110e7b62f6a2d137b52578012569ee50fcc26fe34ea92c77"
OLD_MIC_SHA = "f6ce01ba66b9e68121c535ff1ddd735001e4381f9028e9fcb7032da420ed24c8"
NEW_MIC_SHA = "5a35b112abb8de9f7701ea990c87ce2961f1dc449fdf9fff2589ac13c78e8811"
REFERENCE_SHA = "7661d6412ec346a79db5781ed2bef20fa55a66578a00e5852c5b775451c8b685"

# Frozen *original* alignments as accepted by the already archived reports.
# Their role here is sample indexing ONLY; not re-optimization.
OLD_LAG_FRAMES = 4690     # 97.70833 ms
NEW_LAG_FRAMES = 13502   # 281.29167 ms
NFFT = 4096


def checked_take(directory: Path, manifest_sha: str,
                 mic_sha: str) -> tuple[dict, dict, np.ndarray, np.ndarray]:
    audit = AUDIT.resolve(strict=True)
    directory = directory.expanduser().resolve(strict=True)
    if audit not in directory.parents:
        raise ValueError("Capture must be under local private .audit")
    manifest_path = directory / "manifest.json"
    ref_path = directory / "reference-sent.wav"
    mic_path = directory / "microphone-raw.wav"
    hashes = {
        "manifest": digest(manifest_path),
        "reference": digest(ref_path),
        "physical_microphone": digest(mic_path),
    }
    if (hashes["manifest"] != manifest_sha or
            hashes["physical_microphone"] != mic_sha or
            hashes["reference"] != REFERENCE_SHA):
        raise ValueError("Physical capture hash mismatch with audited evidence")
    meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (meta.get("schema") != "cuewire-M5-paired-physical-capture-v1"
            or meta.get("status") != "captured_for_offline_aec"
            or meta.get("requested_duration_s") != 32
            or meta.get("speaker_only_calibration_s") != 12):
        raise ValueError("Not the agreed 12-second speaker-only calibration take")
    if (meta.get("reference", {}).get("sha256") != REFERENCE_SHA or
            meta.get("reference", {}).get("frames") != 32 * RATE or
            meta.get("microphone", {}).get("sha256") != mic_sha):
        raise ValueError("Capture manifest self-reference hashes/length mismatch")
    if (meta.get("mic_capture", {}).get("backend") != "pipewire" or
            meta.get("mic_capture", {}).get("mic_source_sample_spec") !=
                "s16le 1ch 48000Hz" or
            not str(meta.get("mic_capture", {}).get("mic_source_name", "")).startswith("alsa_input.") or
            not str(meta.get("speaker_sink_name", "")).startswith("alsa_output.")):
        raise ValueError("Explicit physical mic/sink and expected format required")
    x, y = read_wav(ref_path), read_wav(mic_path)
    if (len(x) != 32 * RATE or len(y) < 32 * RATE or
            np.max(np.abs(y)) >= 32767 / 32768):
        raise ValueError("Invalid reference or clipped/truncated microphone PCM")
    if (digest(manifest_path) != hashes["manifest"] or
            digest(ref_path) != hashes["reference"] or
            digest(mic_path) != hashes["physical_microphone"]):
        raise RuntimeError("Evidence mutated during input validation")
    return meta, hashes, x, y


def window_statistics(reference: np.ndarray, microphone: np.ndarray,
                      committed_lag: int,
                      start: float, end: float) -> dict:
    """No timing search and no AEC model. One predeclared reference window."""
    a, b = round(start * RATE), round(end * RATE)
    if a < 0 or b > len(reference) or committed_lag < 0 or b + committed_lag > len(microphone):
        raise ValueError("Fixed early interval outside available WAV")
    x = reference[a:b]
    y = microphone[committed_lag+a:committed_lag+b]
    if len(x) < RATE or len(x) != len(y):
        raise ValueError("Need matched early interval >=1s")
    dx, dy = x - float(np.mean(x)), y - float(np.mean(y))
    denom = float(np.linalg.norm(dx) * np.linalg.norm(dy))
    signed_corr = float(np.dot(dx, dy)/max(denom, 1e-20))
    f, coherence = signal.coherence(
        x, y, fs=RATE, nperseg=NFFT, noverlap=NFFT//2)
    _, mic_psd = signal.welch(
        y, fs=RATE, nperseg=NFFT, noverlap=NFFT//2)
    bands = []
    for low, high in BANDS:
        mask = (f >= low) & (f < high)
        if not np.any(mask):
            raise RuntimeError("Empty band in spectral analysis")
        sos = signal.butter(4, [low, high], btype="bandpass",
                            fs=RATE, output="sos")
        x_band = signal.sosfiltfilt(sos, x)
        y_band = signal.sosfiltfilt(sos, y)
        weight = mic_psd[mask]
        weighted = float(np.dot(coherence[mask], weight) /
                         max(float(np.sum(weight)), 1e-20))
        bands.append({
            "hz": [low, high],
            "ref_rms_dbfs": round(db(rms(x_band)), 5),
            "mic_rms_dbfs": round(db(rms(y_band)), 5),
            "mic_power_weighted_coherence": round(weighted, 6),
            "mic_to_digital_reference_rms_difference_db": round(
                db(rms(y_band))-db(rms(x_band)), 5),
        })
    return {
        "reference_seconds": [start, end],
        "frozen_original_lag_ms": round(1000*committed_lag/RATE, 5),
        "reference_total_rms_dbfs": round(db(rms(x)), 5),
        "microphone_total_rms_dbfs": round(db(rms(y)), 5),
        "microphone_peak_dbfs": round(db(float(np.max(np.abs(y)))), 5),
        "signed_zero_shift_correlation_at_frozen_lag": round(signed_corr, 6),
        "bands": bands,
    }


def compare(old_dir: Path, new_dir: Path, out_dir: Path) -> dict:
    audit = AUDIT.resolve(strict=True)
    old_dir = old_dir.expanduser().resolve(strict=True)
    new_dir = new_dir.expanduser().resolve(strict=True)
    if old_dir == new_dir or audit not in old_dir.parents or audit not in new_dir.parents:
        raise ValueError("Require two distinct private physical M5 takes")
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("Audit output exists; never overwrite")
    parent = out_dir.expanduser().parent.resolve(strict=True)
    if parent != new_dir.parent:
        raise ValueError("Comparison output must be fresh sibling of new take")
    old_meta, old_hash, x1, y1 = checked_take(old_dir, OLD_MANIFEST_SHA, OLD_MIC_SHA)
    new_meta, new_hash, x2, y2 = checked_take(new_dir, NEW_MANIFEST_SHA, NEW_MIC_SHA)
    matching = {
        "mic_name": old_meta["mic_capture"]["mic_source_name"] == new_meta["mic_capture"]["mic_source_name"],
        "mic_pipewire_id": old_meta["mic_capture"]["mic_source_id_at_start"] == new_meta["mic_capture"]["mic_source_id_at_start"],
        "speaker_name": old_meta["speaker_sink_name"] == new_meta["speaker_sink_name"],
        "speaker_pipewire_id": old_meta["speaker_sink_id_at_start"] == new_meta["speaker_sink_id_at_start"],
        "volume": old_meta["volume_pw_play"] == new_meta["volume_pw_play"] == 0.30,
        "digital_reference_pcm_equal": bool(np.array_equal(x1, x2)),
    }
    if not all(matching.values()):
        raise ValueError("Hardware/reference/volume mismatch; cannot do matched-take comparison")
    rows = []
    for start, end in WINDOWS:
        old = window_statistics(x1, y1, OLD_LAG_FRAMES, start, end)
        new = window_statistics(x2, y2, NEW_LAG_FRAMES, start, end)
        band_delta = []
        for left, right in zip(old["bands"], new["bands"]):
            if left["hz"] != right["hz"]:
                raise AssertionError("Band-order mismatch")
            band_delta.append({
                "hz": left["hz"],
                "new_minus_old_mic_rms_db": round(
                    right["mic_rms_dbfs"]-left["mic_rms_dbfs"], 5),
                "new_minus_old_mic_weighted_coherence": round(
                    right["mic_power_weighted_coherence"] -
                    left["mic_power_weighted_coherence"], 6),
            })
        rows.append({
            "reference_seconds": [start, end],
            "earlier_pass_timing": old,
            "later_failed_timing": new,
            "comparison": {
                "new_minus_old_total_mic_rms_db": round(
                    new["microphone_total_rms_dbfs"]-old["microphone_total_rms_dbfs"], 5),
                "band_deltas": band_delta,
            },
        })
    input_paths = {
        "old_manifest": old_dir/"manifest.json",
        "old_reference": old_dir/"reference-sent.wav",
        "old_mic": old_dir/"microphone-raw.wav",
        "new_manifest": new_dir/"manifest.json",
        "new_reference": new_dir/"reference-sent.wav",
        "new_mic": new_dir/"microphone-raw.wav",
    }
    input_hashes = {
        "old_manifest": old_hash["manifest"],
        "old_reference": old_hash["reference"],
        "old_mic": old_hash["physical_microphone"],
        "new_manifest": new_hash["manifest"],
        "new_reference": new_hash["reference"],
        "new_mic": new_hash["physical_microphone"],
    }
    if any(digest(p) != input_hashes[k] for k, p in input_paths.items()):
        raise RuntimeError("Input file was modified while computing comparison")
    report = {
        "schema": "cuewire-M5-early-cross-take-reference-noise-v1",
        "status": "postmortem_only_prospective_gate_still_failed",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": input_hashes,
        "identical_conditions_observed_in_manifests": matching,
        "predeclared_reference_windows_s": [list(w) for w in WINDOWS],
        "frozen_old_lag_frames": OLD_LAG_FRAMES,
        "frozen_new_lag_frames": NEW_LAG_FRAMES,
        "comparisons": rows,
        "all_original_heldout_results_unchanged": True,
        "speech_doubletalk_not_analyzed": True,
        "aec_fitting_or_retiming_performed": False,
        "warnings": [
            "Only early speaker-only audio is read for metrics; assumed operator silent during first 12s.",
            "Same physical endpoint names, IDs and command-line volume do not prove unchanged acoustic gain, hardware position or DSP/AGC.",
            "The reference is identical digital PCM but no identical physical speaker output was independently measured.",
            "Mic/reference band levels mix reference-linked acoustic echo and independent ambient/electronic/mic noise.",
            "Magnitude-squared spectral coherence is not an exact percentage of removable music energy.",
            "Absolute original lag includes unsynchronized process startup; these are fixed historical lags, not retuned ones.",
            "Previously failed prospective guarded ERLE=0.67881dB remains FAIL.",
            "No hidden trial selection, no audio generated, no real-time device access or audio routing changes.",
        ],
    }
    out = parent/out_dir.name
    out.mkdir(mode=0o700)
    path = out/"cross-take-calibration.json"
    with path.open("x", encoding="utf-8") as fp:
        json.dump(report, fp, indent=2, sort_keys=True, ensure_ascii=False)
        fp.write("\n")
    with (out/"SHA256SUMS").open("x", encoding="utf-8") as fp:
        fp.write(digest(path)+"  cross-take-calibration.json\n")
    print("M5 EARLY CROSS-TAKE: 3 preregistered reference-only windows")
    print("Both manifests and four WAVs hash-verified; no audio generated.")
    for row in rows:
        print("Reference seconds", row["reference_seconds"])
        print("  OLD mic RMS", row["earlier_pass_timing"]["microphone_total_rms_dbfs"],
              "NEW mic RMS", row["later_failed_timing"]["microphone_total_rms_dbfs"])
        for b in row["comparison"]["band_deltas"]:
            print("  ", b["hz"], "new-old mic RMS",
                  b["new_minus_old_mic_rms_db"],
                  "dB; weighted reference coherence change",
                  b["new_minus_old_mic_weighted_coherence"])
    print("Original prospective gate remains FAIL.")
    print("Report:", path)
    return report


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-capture", type=Path, required=True)
    p.add_argument("--new-capture", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    try:
        compare(args.old_capture, args.new_capture, args.out)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        p.exit(1, "ERROR: "+str(exc)+"\n")


if __name__ == "__main__":
    main()
