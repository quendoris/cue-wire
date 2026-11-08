#!/usr/bin/env python3
"""CueWire M5: reference-conditioned coherence of the REAL speaker-only A/B/A take.

Align immutable pre-playback digital reference to the continuously captured
physical microphone using the already stable 1500–4000 Hz band; then
measure spectral coherence in separate ON intervals, compared with
an OFF-phase unrelated reference as a negative control.

No AEC coefficients are fitted. No audio is modified, played or generated.
This is not an ERLE measurement or evidence of preserved near-end voice.
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

from voice_m5_aec import AUDIT, RATE, digest, read_wav, rms, db

BANDS = ((70, 180), (180, 500), (500, 1500), (1500, 4000),
         (4000, 8000), (8000, 16000))
REFERENCE_WINDOWS = ((3.0, 9.0), (10.0, 16.0))


def band_lag(reference: np.ndarray, mic: np.ndarray, launch_s: float,
             probe_start_s: float = 1.0, probe_end_s: float = 3.7
             ) -> tuple[int, dict]:
    """Estimate WAV-sample offset from reference origin to microphone.

    Search relative to logged playback launch, with a 0.3s early to 0.9s
    late allowance for software and acoustic latency. This is NOT a
    hardware timing guarantee; audio streams are not clock synchronized.
    """
    if not 0 < launch_s < 60:
        raise ValueError("Invalid playback launch timestamp")
    sos = signal.butter(4, (1500, 4000), btype="bandpass",
                        output="sos", fs=RATE)
    x = signal.sosfiltfilt(sos, reference)
    y = signal.sosfiltfilt(sos, mic)
    a = round(probe_start_s * RATE)
    b = round(probe_end_s * RATE)
    if b > len(x):
        raise ValueError("Reference too short for timing probe")
    search_start = max(0, round((launch_s - .3) * RATE))
    search_end = round((launch_s + .9) * RATE)
    if search_end + b > len(y):
        raise ValueError("Microphone capture too short for timing search")
    xx = x[a:b] - float(np.mean(x[a:b]))
    yy = y[search_start + a:search_end + b]
    # Valid correlation has exactly search_end-search_start+1 candidates.
    dots = signal.correlate(yy, xx, mode="valid", method="fft")
    energy = np.r_[0., np.cumsum(yy * yy)]
    sums = np.r_[0., np.cumsum(yy)]
    n = len(xx)
    sum_y = sums[n:] - sums[:-n]
    sum_y2 = energy[n:] - energy[:-n]
    centered_energy = np.maximum(sum_y2 - sum_y**2 / n, 1e-20)
    score = np.abs(dots) / np.sqrt(
        np.maximum(centered_energy * float(xx @ xx), 1e-20))
    winner = int(np.argmax(score))
    lag = search_start + winner
    confidence = float(score[winner])
    if not np.isfinite(confidence) or confidence < .06:
        raise ValueError(
            f"Weak/ambiguous reference timing correlation: {confidence:.5f}")
    if lag + round(16.0 * RATE) >= len(y):
        raise ValueError("Aligned microphone does not include full evaluation windows")
    return lag, {
        "lag_from_reference_origin_ms": round(1000*lag / RATE, 5),
        "lag_after_launch_ms": round(1000*(lag / RATE - launch_s), 5),
        "peak_highmid_normalized_correlation": round(confidence, 6),
        "alignment_band_hz": [1500, 4000],
        "reference_probe_s": [probe_start_s, probe_end_s],
        "launch_approx_s": launch_s,
        "search_after_launch_s": [-.3, .9],
    }


def coherence_by_bands(x: np.ndarray, y: np.ndarray) -> list[dict]:
    if len(x) != len(y) or len(x) < 3*RATE:
        raise ValueError("Need aligned >=3s equal-length reference and microphone")
    nfft = 4096
    f, coh = signal.coherence(x, y, fs=RATE,
                               nperseg=nfft, noverlap=nfft//2)
    _, mic_psd = signal.welch(y, fs=RATE,
                              nperseg=nfft, noverlap=nfft//2)
    _, ref_psd = signal.welch(x, fs=RATE,
                              nperseg=nfft, noverlap=nfft//2)
    results = []
    for lo, hi in BANDS:
        mask = (f>=lo) & (f<hi)
        if not np.any(mask):
            raise ValueError("Empty coherence band")
        weighted_mic = float((coh[mask] * mic_psd[mask]).sum() /
                             max(float(mic_psd[mask].sum()), 1e-20))
        weighted_ref = float((coh[mask] * ref_psd[mask]).sum() /
                             max(float(ref_psd[mask].sum()), 1e-20))
        results.append({
            "band_hz": [lo, hi],
            "median_magnitude_squared_coherence":
                round(float(np.median(coh[mask])), 6),
            "mic_power_weighted_coherence": round(weighted_mic, 6),
            "reference_power_weighted_coherence": round(weighted_ref, 6),
            "reference_band_psd_total": float(ref_psd[mask].sum()),
            "microphone_band_psd_total": float(mic_psd[mask].sum()),
        })
    return results


def analyze(aba_dir: Path, output_dir: Path) -> dict:
    aba_dir = aba_dir.expanduser().resolve(strict=True)
    audit = AUDIT.resolve(strict=True)
    if audit not in aba_dir.parents or not aba_dir.is_dir():
        raise ValueError("ABA must be an existing private .audit directory")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Numeric report directory exists, refuse overwrite")
    parent = output_dir.expanduser().parent.resolve(strict=True)
    if parent != aba_dir.parent:
        raise ValueError("Report must be a NEW sibling of A/B/A capture")
    aba_manifest_path = aba_dir / "manifest.json"
    aba = json.loads(aba_manifest_path.read_text(encoding="utf-8"))
    if (aba.get("schema") != "cuewire-M5-continuous-ABA-control-v1" or
            aba.get("status") != "captured_aba_for_noise_diagnostics"):
        raise ValueError("Source is not a validated M5 A/B/A capture")
    ref_dir = Path(aba["reference_source_capture"]).expanduser().resolve(strict=True)
    if audit not in ref_dir.parents or ref_dir == aba_dir:
        raise ValueError("Reference source must be a distinct private M5 capture")
    ref_manifest_path = ref_dir / "manifest.json"
    ref_manifest = json.loads(ref_manifest_path.read_text(encoding="utf-8"))
    if (ref_manifest.get("schema") != "cuewire-M5-paired-physical-capture-v1"
            or ref_manifest.get("status") != "captured_for_offline_aec"):
        raise ValueError("Linked reference was not a valid paired capture")
    if digest(ref_manifest_path) != aba["reference_source_manifest_sha256"]:
        raise ValueError("M5 reference source manifest SHA256 mismatch")
    x_path = ref_dir / "reference-sent.wav"
    y_path = aba_dir / "microphone-aba-raw.wav"
    hashes = {"aba_manifest": digest(aba_manifest_path),
              "reference_manifest": digest(ref_manifest_path),
              "digital_reference": digest(x_path),
              "physical_microphone": digest(y_path)}
    if (hashes["digital_reference"] != aba["reference_wav_sha256"] or
            hashes["digital_reference"] != ref_manifest["reference"]["sha256"] or
            hashes["physical_microphone"] != aba["microphone"]["sha256"]):
        raise ValueError("Original source WAV SHA256 mismatch")
    if aba["speaker_sink_name"] != ref_manifest["speaker_sink_name"]:
        raise ValueError("Speaker sink changed between references and ABA")
    if aba["source_mic_name"] != ref_manifest["mic_capture"].get("mic_source_name"):
        raise ValueError("Microphone source changed between references and ABA")
    if abs(float(aba["speaker_play_volume"]) -
           float(ref_manifest["volume_pw_play"])) > .0001:
        raise ValueError("Speaker volume changed between references and ABA")
    x = read_wav(x_path)
    y = read_wav(y_path)
    launch = float(aba["playback_start_after_recorder_launch_s"])
    lag, timing = band_lag(x, y, launch)
    result_windows = []
    for lo, hi in REFERENCE_WINDOWS:
        a, b = round(lo*RATE), round(hi*RATE)
        ref_window = x[a:b]
        mic_window = y[lag+a:lag+b]
        if len(ref_window) != len(mic_window):
            raise ValueError("Truncated microphone coherence evaluation window")
        result_windows.append({
            "reference_time_s": [lo, hi],
            "mic_rms_dbfs": round(db(rms(mic_window)), 5),
            "band_coherence": coherence_by_bands(ref_window, mic_window),
        })
    # Negative control: mic physical SPEAKER-OFF samples 2..6 seconds,
    # compared with an unrelated 4 seconds of reference music.
    off_mic = y[round(2*RATE):round(6*RATE)]
    unrelated_ref = x[round(3*RATE):round(7*RATE)]
    background_control = {
        "description": "speaker_off_microphone_vs_reference_never_played_at_that_time",
        "microphone_wav_s": [2.0, 6.0],
        "reference_s": [3.0, 7.0],
        "band_coherence": coherence_by_bands(unrelated_ref, off_mic)
    }
    if any(digest(p)!=hashes[k] for k,p in [
            ("aba_manifest",aba_manifest_path),
            ("reference_manifest",ref_manifest_path),
            ("digital_reference",x_path),("physical_microphone",y_path)]):
        raise RuntimeError("Input changed during numerical study")
    report = {
        "schema": "cuewire-M5-ABA-reference-coherence-v1",
        "status": "diagnostic_only_not_aec_approved",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashes,
        "timing": timing,
        "speaker_on_independent_windows": result_windows,
        "speaker_off_negative_control": background_control,
        "warnings": [
            "Coherence measures normalized linear spectral relationship, NOT echo cancellation ERLE.",
            "A spectral coherence value is not a proven fraction of removable acoustic sound.",
            "No AEC model was fitted and no audio output was generated.",
            "The alignment lag is software/digital-plus-acoustic, not a DAC-to-ADC hardware timestamp.",
            "Different OFF and ON samples and a finite number of Welch segments introduce estimator noise.",
            "The ABA take contains no near-end voice; speech preservation is untested.",
            "No prior failed ERLE acceptance gate was changed or bypassed."
        ]
    }
    out = parent / output_dir.name
    out.mkdir(mode=0o700)
    report_file = out / "reference-coherence.json"
    with report_file.open("x",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2,sort_keys=True)
        f.write("\n")
    with (out/"SHA256SUMS").open("x",encoding="utf-8") as f:
        f.write(digest(report_file)+"  reference-coherence.json\n")
    print("M5 A/B/A REFERENCE COHERENCE: diagnostic only (NO audio generated)")
    print("Matched acoustic/software lag:", timing["lag_from_reference_origin_ms"], "ms")
    print("Lag relative to playback launch:", timing["lag_after_launch_ms"], "ms")
    print("Matching correlation:", timing["peak_highmid_normalized_correlation"])
    for window in result_windows:
        print("Reference time", window["reference_time_s"])
        for row in window["band_coherence"]:
            print(" ", row["band_hz"], "mic-power-weighted coherence:",
                  row["mic_power_weighted_coherence"])
    print("Speaker-OFF negative control (reference never played then):")
    for row in background_control["band_coherence"]:
        print(" ",row["band_hz"], row["mic_power_weighted_coherence"])
    print("Report:", report_file)
    return report


def main() -> None:
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args()
    try:
        analyze(args.capture,args.out)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        p.exit(1,"ERROR: "+str(exc)+"\n")


if __name__=="__main__":
    main()
