#!/usr/bin/env python3
"""M5: inspect private physical speaker-OFF microphone noise stationarity.

Use only existing SHA256-pinned M5 A/B/A level-control WAVs
0.15 and 0.30. Analyze mic WAV seconds 2..7 and 30..35,
each in ten 0.5-second windows. Report band power distribution
and concentrated outliers; NO new AEC, no alignment, no holdout,
no music playback, no file exports beyond numeric JSON.

A spike is not automatically microphone AGC, input hardware noise
or an environmental sound. This is diagnostic, not acceptance.
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
from voice_m5_playback_coherence import (
    checked_take, LOW_MANIFEST_SHA, HIGH_MANIFEST_SHA, LOW_MIC_SHA,
    HIGH_MIC_SHA, REFERENCE_SHA, SOURCE_MANIFEST_SHA,
)

OFF_PHASES = (("before", 2.0, 7.0), ("after", 30.0, 35.0))
WINDOW_S = .5
BANDS = ((70,180), (180,500), (500,1500), (1500,4000),
         (4000,8000), (8000,16000))


def split_level(x: np.ndarray, start_sec: float,
                end_sec: float) -> dict:
    """Split a single speaker-OFF WAV segment; no extrapolation into ON."""
    a,b=round(start_sec*RATE), round(end_sec*RATE)
    step=round(WINDOW_S*RATE)
    if a<0 or b>len(x) or (b-a)!=10*step:
        raise ValueError("Expected exactly 5s OFF signal with ten 0.5s windows")
    segment=x[a:b]
    if not np.all(np.isfinite(segment)):
        raise ValueError("Nonfinite physical microphone samples")
    out=[]
    for lo,hi in BANDS:
        sos=signal.butter(4,[lo,hi],btype="bandpass",fs=RATE,output="sos")
        band=signal.sosfiltfilt(sos,segment)
        energy=np.mean(band.reshape(10,step)**2,axis=1)
        if np.any(~np.isfinite(energy)) or np.any(energy<0):
            raise ValueError("Invalid band power")
        levels=10*np.log10(np.maximum(energy,1e-30))
        median=float(np.median(levels))
        max_i=int(np.argmax(energy))
        total=float(np.sum(energy))
        maxshare=float(energy[max_i]/max(total,1e-30))
        out.append({
            "band_hz":[lo,hi],
            "half_second_power_dbfs":[round(float(v),5) for v in levels],
            "median_half_second_power_dbfs":round(median,5),
            "p10_half_second_power_dbfs":round(float(np.percentile(levels,10)),5),
            "p90_half_second_power_dbfs":round(float(np.percentile(levels,90)),5),
            "max_half_second_power_dbfs":round(float(np.max(levels)),5),
            "mean_power_dbfs":round(float(10*math.log10(max(total/10,1e-30))),5),
            "maximum_window_midpoint_mic_wav_s":round(
                start_sec+(max_i+.5)*WINDOW_S,5),
            "maximum_window_fraction_of_phase_power":round(maxshare,6),
            "count_windows_over_median_plus_6_db":int(
                np.count_nonzero(levels>median+6)),
        })
    return {
        "mic_wav_interval_s":[start_sec,end_sec],
        "window_s":WINDOW_S,
        "num_windows":10,
        "unfiltered_phase_mic_rms_dbfs":round(db(rms(segment)),5),
        "band_windows":out,
    }


def analyze(low_dir: Path, high_dir: Path, dest_dir: Path)->dict:
    if low_dir==high_dir:
        raise ValueError("Require distinct existing physical control takes")
    if dest_dir.exists() or dest_dir.is_symlink():
        raise FileExistsError("Output exists, refusing overwrite")
    low_dir=low_dir.expanduser().resolve(strict=True)
    high_dir=high_dir.expanduser().resolve(strict=True)
    audit=AUDIT.resolve(strict=True)
    if audit not in low_dir.parents or audit not in high_dir.parents:
        raise ValueError("Source must be within private .audit")
    parent=dest_dir.expanduser().parent.resolve(strict=True)
    if parent!=high_dir.parent:
        raise ValueError("Output must be a fresh sibling of high-volume capture")
    lm,lh,ly=checked_take(low_dir,LOW_MANIFEST_SHA,LOW_MIC_SHA,.15)
    hm,hh,hy=checked_take(high_dir,HIGH_MANIFEST_SHA,HIGH_MIC_SHA,.30)
    for key in ("source_mic_name","speaker_sink_name","reference_source_capture"):
        if lm.get(key)!=hm.get(key):
            raise ValueError("Physical source and referenced music mismatch")
    source_dir=Path(lm["reference_source_capture"]).expanduser().resolve(strict=True)
    if audit not in source_dir.parents:
        raise ValueError("Linked reference must be private")
    ref_path=source_dir/"reference-sent.wav"
    ref_manifest=source_dir/"manifest.json"
    if digest(ref_path)!=REFERENCE_SHA or digest(ref_manifest)!=SOURCE_MANIFEST_SHA:
        raise ValueError("Referenced original music SHA256 mismatch")
    results={}
    for key,mic,m in (("low_volume",ly,lm),("high_volume",hy,hm)):
        phases={}
        for label,a,b in OFF_PHASES:
            phases[label]=split_level(mic,a,b)
        pre={tuple(row["band_hz"]):row for row in phases["before"]["band_windows"]}
        post={tuple(row["band_hz"]):row for row in phases["after"]["band_windows"]}
        comparisons=[]
        for band in BANDS:
            p,q=pre[band],post[band]
            comparisons.append({
                "band_hz":list(band),
                "after_minus_before_phase_mean_power_db":round(
                    q["mean_power_dbfs"]-p["mean_power_dbfs"],5),
                "after_minus_before_window_median_db":round(
                    q["median_half_second_power_dbfs"]-
                    p["median_half_second_power_dbfs"],5),
                "before_max_window_share_of_phase_power":
                    p["maximum_window_fraction_of_phase_power"],
                "after_max_window_share_of_phase_power":
                    q["maximum_window_fraction_of_phase_power"],
                "before_num_6db_outliers":
                    p["count_windows_over_median_plus_6_db"],
                "after_num_6db_outliers":
                    q["count_windows_over_median_plus_6_db"],
            })
        results[key]={
            "per_stream_volume":m["speaker_play_volume"],
            "physical_off":phases,
            "before_after_band_comparison":comparisons,
        }
    inputs={
        "level015_manifest":(low_dir/"manifest.json",lh["manifest"]),
        "level015_mic":(low_dir/"microphone-aba-raw.wav",lh["physical_mic_raw"]),
        "level030_manifest":(high_dir/"manifest.json",hh["manifest"]),
        "level030_mic":(high_dir/"microphone-aba-raw.wav",hh["physical_mic_raw"]),
        "digital_reference":(ref_path,REFERENCE_SHA),
        "digital_reference_manifest":(ref_manifest,SOURCE_MANIFEST_SHA),
    }
    if any(digest(file)!=sha for file,sha in inputs.values()):
        raise RuntimeError("Audited input changed during OFF-noise analysis")
    report={
        "schema":"cuewire-M5-OFF-floor-window-stability-v1",
        "status":"numeric_only_off_window_diagnostics_no_aec_pass",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "source_sha256":{k:sha for k,(_,sha) in inputs.items()},
        "speaker_off_phases_mic_wav_s":[[a,b] for _,a,b in OFF_PHASES],
        "window_seconds":WINDOW_S,
        "bands_hz":[list(x) for x in BANDS],
        **results,
        "aec_fir_fitted_or_modified":False,
        "reference_holdout_or_doubletalk_read_for_measurements":False,
        "voice_preservation_validated":False,
        "approved_for_virtual_microphone":False,
        "historical_0_67881_db_prospective_gate_remains_failed":True,
        "warnings":[
            "Band-limited RMS computed by zero-phase offline filters inside each 5s OFF phase; not same Welch PSD estimator as echo-floor report.",
            "Only 10 half-second windows per phase: descriptive sample statistics, not an acoustic population confidence interval.",
            "A concentrated high-frequency window may be a transient, audio-device artifact, ambient sound, or microphone processing; physical cause unknown.",
            "A shift of phase medians suggests extended change, but cannot establish gain/AGC or mic hardware behavior without more controls.",
            "No 10-16s speaker-on holdout, no speech and no AEC parameters are used to compute these OFF-only windows.",
            "Only JSON and SHA256SUMS written; old captures remain byte-identical and private.",
        ],
    }
    dest=parent/dest_dir.name
    dest.mkdir(mode=0o700)
    output=dest/"off-floor-windows.json"
    with output.open("x",encoding="utf-8") as f:
        json.dump(report,f,sort_keys=True,ensure_ascii=False,indent=2)
        f.write("\n")
    with (dest/"SHA256SUMS").open("x",encoding="utf-8") as f:
        f.write(digest(output)+"  off-floor-windows.json\n")
    print("M5 OFF noise short-window diagnostic, no AEC, NO audio output")
    for label in ("low_volume","high_volume"):
        print("Level",report[label]["per_stream_volume"])
        for row in report[label]["before_after_band_comparison"]:
            print(" ",row["band_hz"],
                  "mean change",row["after_minus_before_phase_mean_power_db"],
                  "median change",row["after_minus_before_window_median_db"],
                  "max-window power shares",
                  row["before_max_window_share_of_phase_power"],
                  row["after_max_window_share_of_phase_power"])
    print("Historical failed 0.67881dB gate unchanged.")
    print("Report:",output)
    return report


def main()->None:
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--low-capture",required=True,type=Path)
    p.add_argument("--high-capture",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args()
    try:
        analyze(args.low_capture,args.high_capture,args.out)
    except (OSError,ValueError,RuntimeError,KeyError) as e:
        p.exit(1,"ERROR: "+str(e)+"\n")


if __name__=="__main__":
    main()
