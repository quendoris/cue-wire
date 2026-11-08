#!/usr/bin/env python3
"""CueWire M5: immutable, one-session ABA spectral floor audit.

Analyze ONLY existing A/B/A physical microphone PCM and its manifest.
Report a source-hash-protected numerical JSON without audio playback,
speaker access, AEC changes, device configuration or new WAV files.

Band power ratios do not label causal signal origin. No source reference
WAV is used here, so "music-correlated echo" cannot be certified.
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

BANDS = ((20, 70), (70, 180), (180, 500), (500, 1500),
         (1500, 4000), (4000, 8000), (8000, 16000))
PHASES = ("before_speaker_on", "speaker_on", "after_speaker_off")


def frequency_peaks(segment: np.ndarray) -> list[dict]:
    """Find spectral line near 50Hz and near 64Hz; no causal attribution."""
    f, power = signal.welch(segment, fs=RATE, nperseg=RATE,
                            noverlap=RATE // 2)
    result = []
    for lo, hi in ((45, 55), (60, 68)):
        m = (f >= lo) & (f <= hi)
        if not np.any(m):
            continue
        j = int(np.argmax(power[m]))
        freq = float(f[m][j])
        density = float(power[m][j])
        result.append({
            "searched_hz": [lo, hi],
            "peak_frequency_hz": round(freq, 3),
            "peak_psd_dbfs2_per_hz": round(
                10 * math.log10(max(density, 1e-30)), 5),
        })
    return result


def inspect_samples(waveform: np.ndarray, phase_windows: dict) -> dict:
    segments = {}
    for phase in PHASES:
        bounds = phase_windows[phase]["approx_wav_position_s"]
        if len(bounds) != 2:
            raise ValueError("Invalid A/B/A phase window")
        start = round(float(bounds[0]) * RATE)
        end = round(float(bounds[1]) * RATE)
        if start < 0 or end > len(waveform) or end - start < RATE * 3:
            raise ValueError("A/B/A phase window too short/outside WAV")
        segments[phase] = waveform[start:end]

    levels = {}
    for phase in PHASES:
        samples = segments[phase]
        levels[phase] = {
            "rms_dbfs": round(db(rms(samples)), 5),
            "peak_dbfs": round(db(float(np.max(np.abs(samples)))), 5),
            "dominant_low_frequency_lines": frequency_peaks(samples),
        }

    band_rows = []
    for low, high in BANDS:
        sos = signal.butter(
            4, (low, high), fs=RATE,
            btype="bandpass", output="sos")
        band_power = {}
        for phase in PHASES:
            # Offline zero-phase filtering, same bandpass each phase.
            band = signal.sosfiltfilt(sos, segments[phase])
            band_power[phase] = rms(band) ** 2
        off_power = (band_power["before_speaker_on"] +
                     band_power["after_speaker_off"]) / 2
        on_power = band_power["speaker_on"]
        band_rows.append({
            "band_hz": [low, high],
            "before_rms_dbfs": round(db(math.sqrt(band_power["before_speaker_on"])), 5),
            "on_rms_dbfs": round(db(math.sqrt(on_power)), 5),
            "after_rms_dbfs": round(db(math.sqrt(band_power["after_speaker_off"])), 5),
            "on_minus_mean_off_db": round(
                10 * math.log10(max(on_power, 1e-24) /
                                max(off_power, 1e-24)), 5),
        })

    off_total_power = (
        rms(segments["before_speaker_on"]) ** 2 +
        rms(segments["after_speaker_off"]) ** 2) / 2
    on_total_power = rms(segments["speaker_on"]) ** 2
    for band, row in zip(BANDS, band_rows):
        band_off_rms = 10 ** (row["before_rms_dbfs"] / 20)
        band_off2_rms = 10 ** (row["after_rms_dbfs"] / 20)
        row["fraction_of_off_total_power"] = round(
            (band_off_rms**2 + band_off2_rms**2) /
            (2 * max(off_total_power, 1e-24)), 6)

    # Independent peak audit. Bound the report to top 8 quarter-second
    # windows, so periods of short transients cannot disappear in
    # the all-recording RMS statistic. No waveform is emitted.
    quarter = RATE // 4
    candidates = []
    for at in range(0, len(waveform) - quarter + 1, quarter):
        peak = float(np.max(np.abs(waveform[at:at+quarter])))
        candidates.append((peak, at))
    highest = sorted(candidates, key=lambda pair: pair[0], reverse=True)[:8]

    return {
        "phase_levels": levels,
        "bands": band_rows,
        "on_minus_off_db": round(
            10 * math.log10(max(on_total_power, 1e-24) /
                            max(off_total_power, 1e-24)), 5),
        "conditional_off_power_fraction_if_stationary": round(
            min(1., off_total_power / max(on_total_power, 1e-24)), 6),
        "top_quarter_second_transients": [{
            "start_s": round(pos / RATE, 4),
            "peak_dbfs": round(db(peak), 4)
        } for peak, pos in highest],
    }


def analyze(capture_dir: Path, out_dir: Path) -> dict:
    source = capture_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in source.parents:
        raise ValueError("Source must be a private .audit capture")
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("Output exists; choose a fresh numeric-audit directory")
    parent = out_dir.expanduser().parent.resolve(strict=True)
    if parent != source.parent:
        raise ValueError("Output must be a sibling of the original capture")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema") != "cuewire-M5-continuous-ABA-control-v1"
            or manifest.get("status") != "captured_aba_for_noise_diagnostics"):
        raise ValueError("Not a successful CueWire physical A/B/A capture")
    wav_path = source / "microphone-aba-raw.wav"
    source_hashes = {
        "manifest": digest(manifest_path),
        "microphone_aba_raw": digest(wav_path),
    }
    if source_hashes["microphone_aba_raw"] != manifest["microphone"]["sha256"]:
        raise ValueError("Microphone raw WAV SHA256 mismatch")
    data = read_wav(wav_path)
    observed = inspect_samples(data, manifest["phase_analysis"]["windows"])
    if (digest(wav_path) != source_hashes["microphone_aba_raw"] or
            digest(manifest_path) != source_hashes["manifest"]):
        raise RuntimeError("Source changed during numeric audit")
    report = {
        "schema": "cuewire-M5-ABA-spectral-off-on-off-v1",
        "status": "diagnostic_only_not_aec_accepted",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": source_hashes,
        "measurements": observed,
        "warnings": [
            "Window boundaries use approximate clocks and skip the transitions.",
            "The apparent low-frequency lines may be electronic, acoustic, or DSP artifacts; source is unknown.",
            "OFF/ON/OFF does not prove the identity of acoustic speaker leakage.",
            "Power fractions and cancellation bounds assume stationary independent OFF noise.",
            "No verification of preservation of near-end speech has been made.",
            "No audio is played or written; the original WAV and AEC model are immutable.",
        ],
    }
    out = parent / out_dir.name
    out.mkdir(mode=0o700)
    with (out / "aba-spectral.json").open("x", encoding="utf-8") as f:
        json.dump(report, f, sort_keys=True, ensure_ascii=False, indent=2)
        f.write("\n")
    with (out / "SHA256SUMS").open("x", encoding="utf-8") as f:
        f.write(digest(out / "aba-spectral.json") + "  aba-spectral.json\n")
    print("M5 ABA SPECTRUM: immutable numeric audit; NO audio generated")
    for row in observed["bands"]:
        print(f"{row['band_hz'][0]:>5}-{row['band_hz'][1]:<5} Hz "
              f"speaker_on-off={row['on_minus_mean_off_db']:+.3f} dB "
              f"background_fraction={row['fraction_of_off_total_power']:.3f}")
    print("Whole-band ON - OFF:", observed["on_minus_off_db"], "dB")
    print("Audit:", out / "aba-spectral.json")
    return report


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        analyze(args.capture, args.out)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        parser.exit(1, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
