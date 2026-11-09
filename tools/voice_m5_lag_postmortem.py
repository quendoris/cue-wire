#!/usr/bin/env python3
"""CueWire M5 postmortem: independent early-window lag evidence, NOT re-fitting.

For a failed prospective speaker-only gate, measure how stable the SAME
digital reference/microphone relation is across three predeclared EARLY
speaker-only windows, at fullband and two frequency-limited bands.

DO NOT use newly found peaks to recalculate AEC, alter/accept the
heldout gate, or infer hardware latency from a software startup delay.
No new audio recording, no playback, no WAV or virtual microphone,
no fitting, no audio-system configuration, no use of double-talk voice.
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

EARLY_WINDOWS = ((0.8, 2.3), (3.0, 4.5), (6.0, 7.5))
PROBE_BANDS = (
    ("fullband", None),
    ("mid_500_1500", (500, 1500)),
    ("highmid_1500_4000", (1500, 4000)),
)
SEARCH_DELAY_S = 2.0
PEAK_SEPARATION_MS = 3.0
REFERENCE_OLDER_MIC_SHA256 = (
    "f6ce01ba66b9e68121c535ff1ddd735001e4381f9028e9fcb7032da420ed24c8"
)


def correlation_probe(reference: np.ndarray, microphone: np.ndarray,
                      reference_window_s: tuple[float, float],
                      committed_lag_frames: int,
                      max_delay_frames: int = round(SEARCH_DELAY_S * RATE)
                      ) -> dict:
    """Report correlation; never use peaks to select AEC parameters.

    Correlation is normalized per candidate-window, not across the
    entire mic recording. Return top three separated |r| peaks,
    including signed r, plus Pearson r at the original committed lag.
    """
    a = round(reference_window_s[0] * RATE)
    b = round(reference_window_s[1] * RATE)
    if a < 0 or b <= a or b > len(reference) or b+max_delay_frames > len(microphone):
        raise ValueError("Early correlation window outside available PCM")
    if not 0 <= committed_lag_frames <= max_delay_frames:
        raise ValueError("Frozen report delay exceeds diagnostic search window")
    x = reference[a:b]
    y = microphone[a:b+max_delay_frames]
    xc = x - float(np.mean(x))
    ex = float(np.dot(xc, xc))
    if ex <= 1e-12:
        raise ValueError("Reference interval has no usable signal")
    dots = signal.correlate(y, xc, mode="valid", method="fft")
    cy = np.r_[0., np.cumsum(y)]
    cy2 = np.r_[0., np.cumsum(y * y)]
    n = len(xc)
    means = (cy[n:] - cy[:-n]) / n
    ey = np.maximum((cy2[n:] - cy2[:-n]) - n * means**2, 1e-20)
    signed_r = np.clip(dots / np.sqrt(np.maximum(ex*ey, 1e-20)), -1., 1.)
    if not np.all(np.isfinite(signed_r)):
        raise ValueError("Invalid normalized acoustic correlations")

    abs_r = np.abs(signed_r)
    pool = abs_r.copy()
    peaks = []
    separation = round(PEAK_SEPARATION_MS * RATE)
    for _ in range(3):
        i = int(np.argmax(pool))
        if pool[i] < 1e-5:
            break
        peaks.append({
            "lag_ms": round(1000*i/RATE, 5),
            "normalized_correlation": round(float(signed_r[i]), 6),
            "absolute_normalized_correlation": round(float(abs_r[i]), 6),
        })
        pool[max(0, i-separation):min(len(pool), i+separation+1)] = -1.
    global_lag = int(committed_lag_frames)
    frozen_r = float(signed_r[global_lag])
    return {
        "reference_time_s": list(reference_window_s),
        "reference_rms_dbfs": round(db(rms(x)), 5),
        "mic_rms_at_committed_lag_dbfs": round(
            db(rms(y[global_lag:global_lag+n])), 5),
        "reported_original_lag_ms": round(1000*global_lag/RATE, 5),
        "correlation_at_original_lag": round(frozen_r, 6),
        "absolute_correlation_at_original_lag": round(abs(frozen_r), 6),
        "diagnostic_top_separated_peaks": peaks,
        "first_minus_second_peak_abs_correlation": round(
            peaks[0]["absolute_normalized_correlation"] -
            peaks[1]["absolute_normalized_correlation"], 6)
            if len(peaks) > 1 else None,
        "diagnostic_not_a_retiming": True,
    }


def analyze(capture_dir: Path, original_report_path: Path,
            output_dir: Path) -> dict:
    audit = AUDIT.resolve(strict=True)
    source = capture_dir.expanduser().resolve(strict=True)
    old_report = original_report_path.expanduser().resolve(strict=True)
    if (not source.is_dir() or audit not in source.parents or
            audit not in old_report.parents or not old_report.is_file()):
        raise ValueError("Input must be existing private .audit capture/report")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Diagnostic output folder already exists")
    parent = output_dir.expanduser().parent.resolve(strict=True)
    if parent != source.parent:
        raise ValueError("Output must be fresh sibling of source capture")

    report_sha = digest(old_report)
    manifest_path = source/"manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(old_report.read_text(encoding="utf-8"))
    if (manifest.get("schema") != "cuewire-M5-paired-physical-capture-v1"
            or manifest.get("status") != "captured_for_offline_aec"
            or manifest.get("requested_duration_s") != 32
            or manifest.get("speaker_only_calibration_s") != 12):
        raise ValueError("Need successful 32s M5 capture with 12s silent-speaker calibration")
    if (report.get("schema") != "cuewire-M5-doubletalk-guarded-fir-ablation-v1"
            or report.get("frozen_original_speaker_only_gate_passes") is not False
            or report.get("double_talk") is not None):
        raise ValueError("Postmortem is only for original 1dB speaker-only gate FAIL")
    if (not str(manifest.get("mic_capture", {}).get("mic_source_name", "")).startswith("alsa_input.")
            or not str(manifest.get("speaker_sink_name", "")).startswith("alsa_output.")):
        raise ValueError("Physical mic and speaker identity required")
    if manifest.get("reference", {}).get("frames") != 32*RATE:
        raise ValueError("Reference is not exact 32s PCM")
    ref_path, mic_path = source/"reference-sent.wav", source/"microphone-raw.wav"
    h = {
        "capture_manifest": digest(manifest_path),
        "digital_reference": digest(ref_path),
        "physical_mic_raw": digest(mic_path),
    }
    if (h != report["input_sha256"] or
            h["digital_reference"] != manifest["reference"]["sha256"] or
            h["physical_mic_raw"] != manifest["microphone"]["sha256"]):
        raise ValueError("Postmortem source hashes differ from original frozen report")
    if h["physical_mic_raw"] == REFERENCE_OLDER_MIC_SHA256:
        raise ValueError("Original and prospective MIC hash match: not new take")
    x, y = read_wav(ref_path), read_wav(mic_path)
    reported_lag = float(report["estimated_alignment_lag_ms"])
    if not math.isfinite(reported_lag) or reported_lag < 0:
        raise ValueError("Invalid original lag")
    frozen_lag = round(reported_lag * RATE / 1000)
    if (len(x) != 32*RATE or len(y) < 32*RATE or
            np.max(np.abs(y)) >= 32767/32768):
        raise ValueError("RAW PCM duration/clipping invalid")

    bands = []
    for name, limits in PROBE_BANDS:
        if limits is None:
            bx, by = x, y
        else:
            sos = signal.butter(4, limits, fs=RATE, output="sos",
                                btype="bandpass")
            bx = signal.sosfiltfilt(sos, x)
            by = signal.sosfiltfilt(sos, y)
        probes = [
            correlation_probe(bx, by, window, frozen_lag)
            for window in EARLY_WINDOWS
        ]
        bands.append({"band": name, "range_hz": list(limits) if limits else None,
                      "windows": probes})

    hashes_after = {
        "capture_manifest": digest(manifest_path),
        "digital_reference": digest(ref_path),
        "physical_mic_raw": digest(mic_path),
    }
    if hashes_after != h or digest(old_report) != report_sha:
        raise RuntimeError("Original evidence mutated during diagnostic")
    out_doc = {
        "schema": "cuewire-M5-failed-prospective-lag-postmortem-v1",
        "status": "research_postmortem_no_model_or_gate_changes",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input_sha256": {**h, "original_guarded_report": report_sha},
        "original_frozen_alignment_ms": round(reported_lag, 5),
        "original_reported_speaker_only_erle_db":
            report["speaker_only"]["guarded_fir_erle_db"],
        "original_full_model_gate": False,
        "early_reference_time_windows_s": [list(w) for w in EARLY_WINDOWS],
        "max_candidate_lag_ms": SEARCH_DELAY_S*1000,
        "bands": bands,
        "no_aec_fit_or_retiming": True,
        "double_talk_analyzed": False,
        "warnings": [
            "Diagnostic correlation peaks must NOT be substituted into an already failed heldout AEC trial.",
            "Speaker start/software buffering is included in the inferred lag; no DAC/ADC hardware timing claim.",
            "Music periodicity and short windows can generate multiple high correlation peaks.",
            "Only early speaker-only reference samples before 7.5s plus possible microphone lag up to 2s were used.",
            "Original 10-11.5s validation interval and 13-28s speech are NEVER used for new fitting or diagnostic correlations.",
            "A low normalized correlation can reflect background floor, changed playback chain, time variation or wrong alignment; this diagnostic alone does not prove a cause.",
            "No new audio, routing, model changes, AEC gate changes or virtual microphone was created.",
        ]
    }
    out = parent / output_dir.name
    out.mkdir(mode=0o700)
    outfile = out/"lag-postmortem.json"
    with outfile.open("x", encoding="utf-8") as fd:
        json.dump(out_doc, fd, sort_keys=True, indent=2, ensure_ascii=False)
        fd.write("\n")
    with (out/"SHA256SUMS").open("x", encoding="utf-8") as fd:
        fd.write(digest(outfile)+"  lag-postmortem.json\n")
    print("M5 PROSPECTIVE GATE FAILED: historical 0.67881 dB NOT changed.")
    for b in bands:
        print(b["band"], "committed-lag |corr|:",
              [x["absolute_correlation_at_original_lag"] for x in b["windows"]])
        print("    diagnostic top lag candidates (ms):",
              [x["diagnostic_top_separated_peaks"][0]["lag_ms"]
               if x["diagnostic_top_separated_peaks"] else None
               for x in b["windows"]])
    print("No microphone audio was processed or written.")
    print("Report:", outfile)
    return out_doc


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path,
                   help="Original immutable, failed guarded-fir.json")
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    try:
        analyze(args.capture, args.report, args.out)
    except (OSError, RuntimeError, ValueError, KeyError) as e:
        p.exit(1, "ERROR: "+str(e)+"\n")


if __name__ == "__main__":
    main()
