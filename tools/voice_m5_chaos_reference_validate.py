#!/usr/bin/env python3
"""M5 Chaos: cross-validate prior 2 far-end music lag estimates on later windows.

No new recording. Reads exact SHA256-pinned Chaos-001 microphone +
original stereo song + uploaded prior alignment numeric JSON.
Both playback launches are treated separately. For each source
music section, compute:
  - correlation at the previously FOUND immutable lag, no fitting
  - closest +/-120ms-local correlation search (descriptive drift only)
for left/right/mono-average independently. The song reference is the
unchanged source WAV, not a guessed far-end or generated vocal.
Repetition, unmodeled adaptive mic gain, speech and clicks can
weaken correlation; no single posthoc pass/fail gate for this trial.
All output numeric-only; no audio device, playback, routing or WAV write.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import numpy as np
from scipy import signal as sp

from voice_m5_aec import AUDIT, RATE, digest
from voice_m5_chaos_reference_probe import (
    ACTUAL_MANIFEST_SHA256, ACTUAL_MIC_SHA256,
    ACTUAL_SONG_SHA256, SOURCE_SCHEMA, read_raw_wave,
)

PRIOR_ALIGNMENT_SHA256="ea1855721441b4a859a4409af707f0ff554953184b5bd634519a9349cc67b243"
BAND=(1500.,4000.)
WINDOWS_FIRST=((1.,3.7),(5.,7.7),(10.,12.7),(18.,20.7))
WINDOWS_SECOND=((1.,3.7),(5.,7.7),(10.,12.7),(18.,20.7),(27.,29.7),(38.,40.7))
MAX_LOCAL_SHIFT_S=.12


def lag_score(
    ref: np.ndarray, mic: np.ndarray,
    begin_s:float, end_s:float, lag:int,
    nearby_s:float=MAX_LOCAL_SHIFT_S
)->dict:
    """Stationary normalized xcorr, at FIXED prior lag and in local ±120ms."""
    if (ref.ndim!=1 or mic.ndim!=1 or
        not (0 <= begin_s < end_s) or not math.isfinite(nearby_s)
        or not (0 < nearby_s <= .20) or
        not all(np.all(np.isfinite(arr)) for arr in (ref,mic))):
        raise ValueError("Invalid finite prefiltered recording or window")
    a,b=round(begin_s*RATE),round(end_s*RATE)
    if b-a<round(1*RATE) or b>len(ref):
        raise ValueError("Source music section too short")
    margin=round(nearby_s*RATE)
    lo,hi=lag-margin,lag+margin
    if lo+a<0 or hi+b>len(mic):
        raise ValueError("Mic source cannot cover prior lag ±local search")
    x=np.ascontiguousarray(ref[a:b],dtype=np.float64)
    x-=np.mean(x)
    ex=float(np.dot(x,x))
    if ex<1e-9:
        raise ValueError("Silent/uninformative musical reference window")
    y=np.ascontiguousarray(mic[lo+a:hi+b],dtype=np.float64)
    dots=sp.correlate(y,x,mode="valid",method="fft")
    cum=np.r_[0.,np.cumsum(y)]
    cum2=np.r_[0.,np.cumsum(y*y)]
    n=len(x)
    sy=cum[n:]-cum[:-n]
    ey=np.maximum(cum2[n:]-cum2[:-n]-sy*sy/n,1e-18)
    score=dots/np.sqrt(ex*ey)
    if len(score)!=(hi-lo+1) or not np.all(np.isfinite(score)):
        raise RuntimeError("Correlator produced invalid drift search")
    idx=int(np.argmax(np.abs(score)))
    return {
        "window_source_time_s":[begin_s,end_s],
        "reference_energy":round(ex,5),
        "prior_lag_samples":lag,
        "fixed_lag_signed_normalized_correlation":round(float(score[margin]),6),
        "fixed_lag_abs_normalized_correlation":round(float(abs(score[margin])),6),
        "best_local_lag_samples":lo+idx,
        "best_local_minus_prior_ms":round(1000*(idx-margin)/RATE,5),
        "best_local_abs_normalized_correlation":round(float(abs(score[idx])),6),
        "best_local_correlation_sign":1 if score[idx]>=0 else -1,
        "local_search_halfwidth_ms":1000*nearby_s,
        "within_search_edge_samples":min(idx,len(score)-1-idx),
    }


def evaluate(music:np.ndarray, mic:np.ndarray,
             prior_lag:int, windows:tuple[tuple[float,float],...],
             prefiltered:bool=False)->dict:
    """Calculate held-out fixed-score and descriptive local drift per channel."""
    if (music.ndim!=2 or music.shape[1]!=2 or
        mic.ndim!=1 or not isinstance(prior_lag,int)):
        raise ValueError("Stereo far-end, mono mic, sample lag required")
    if not prefiltered:
        sos=sp.butter(4,BAND,btype="bandpass",output="sos",fs=RATE)
        far={
            "left":sp.sosfiltfilt(sos,music[:,0]),
            "right":sp.sosfiltfilt(sos,music[:,1]),
            "stereo_arithmetic_mean":sp.sosfiltfilt(
                sos,.5*(music[:,0]+music[:,1])),
        }
        rec=sp.sosfiltfilt(sos,mic)
    else:
        far={"left":music[:,0],"right":music[:,1],
             "stereo_arithmetic_mean":.5*(music[:,0]+music[:,1])}
        rec=mic
    rows=[]
    for begin,end in windows:
        record={"source_song_window_s":[begin,end],"channels":{}}
        for name,signal in far.items():
            record["channels"][name]=lag_score(
                signal,rec,begin,end,prior_lag)
        rows.append(record)
    # No source-relative latency or drift assumed from process launch.
    return {
        "prior_lag_samples":prior_lag,
        "prior_lag_time_s":round(prior_lag/RATE,7),
        "filter_band_hz":list(BAND),
        "windows":rows,
        "fixed_lag_not_reoptimized":True,
        "local_lag_search_is_descriptive_not_retraining":True,
    }


def analyze(capture:Path, prior:Path, out:Path)->dict:
    capture=capture.expanduser().resolve(strict=True)
    prior=prior.expanduser().resolve(strict=True)
    audit=AUDIT.resolve(strict=True)
    if audit not in capture.parents or audit not in prior.parents:
        raise ValueError("All sources must be private .audit paths")
    if out.exists() or out.is_symlink():
        raise FileExistsError("Refuse overwrite prior evidence")
    parent=out.expanduser().parent.resolve(strict=True)
    if capture.parent!=parent or prior.parent.parent!=parent:
        raise ValueError("New report must be a sibling of prior private M5 captures")
    mf=capture/"manifest.json"
    if digest(mf)!=ACTUAL_MANIFEST_SHA256:
        raise ValueError("Capture manifest changed since original SHA audit")
    meta=json.loads(mf.read_text(encoding="utf-8"))
    if (meta.get("schema")!=SOURCE_SCHEMA or
        meta.get("status")!="captured_ctrl_c_at_free_stress"
        or meta.get("source_mic_sha256")!=ACTUAL_MIC_SHA256):
        raise ValueError("Wrong source manifest or mic SHA provenance")
    micfile=capture/"microphone-chaos-raw.wav"
    if digest(micfile)!=ACTUAL_MIC_SHA256:
        raise ValueError("Actual physical microphone source hash changed")
    song=Path(meta["music_source"]["path"]).resolve(strict=True)
    if meta["music_source"].get("sha256")!=ACTUAL_SONG_SHA256 or digest(song)!=ACTUAL_SONG_SHA256:
        raise ValueError("Exact physical song source hash mismatch")
    if digest(prior)!=PRIOR_ALIGNMENT_SHA256:
        raise ValueError("Earlier two-restart alignment report hash changed")
    previous=json.loads(prior.read_text(encoding="utf-8"))
    if (previous.get("schema")!="cuewire-M5-chaos-reference-onset-lag-diagnostics-v1"
       or previous.get("sha256",{}).get("original_microphone_sha256")!=ACTUAL_MIC_SHA256
       or previous.get("sha256",{}).get("original_farend_song_sha256")!=ACTUAL_SONG_SHA256
       or previous.get("sha256",{}).get("original_manifest_sha256")!=ACTUAL_MANIFEST_SHA256):
        raise ValueError("Previous aligned report not from exact Chaos-001")
    prev_trials=previous.get("separate_process_restarts",[])
    events=meta.get("playback_software_start_stop_events",[])
    if len(prev_trials)!=2 or len(events)!=2:
        raise ValueError("Two original playback restarts required")
    # Original stereo song > 290sec; read ONLY 43 secs, after full SHA audit.
    source=read_raw_wave(song,2,max_frames=43*RATE)
    recording=read_raw_wave(micfile,1)[:,0]
    if len(recording)!=meta["physical_microphone_analysis"]["recorded_frames"]:
        raise ValueError("Microphone sample count changed")
    sos=sp.butter(4,BAND,btype="bandpass",output="sos",fs=RATE)
    filtered_song=np.column_stack((
        sp.sosfiltfilt(sos,source[:,0]),
        sp.sosfiltfilt(sos,source[:,1]),
    ))
    filtered_mic=sp.sosfiltfilt(sos,recording)
    trials=[]
    for i,(original,event) in enumerate(zip(prev_trials,events)):
        if original.get("playback_instance")!=i+1 or event.get("instance")!=i+1:
            raise ValueError("Original source playback event numbering changed")
        if abs(original["playback_process_started_at_mic_wall_s"]-
               event["start_since_recorder_launch_s"])>1e-5:
            raise ValueError("Playback launch time mismatch")
        found=original["estimate"]
        if (not found.get("valid_echo_lag")
                or found.get("peak_highmid_normalized_correlation",0)<.06):
            raise ValueError("Prior lag estimate was not a usable diagnostic candidate")
        lag=found["lag_samples_from_mic_start"]
        # All MUSIC-ON windows remain strictly before a music stop,
        # including +/-120ms search and 300ms safety margin.
        windows=WINDOWS_FIRST if i==0 else WINDOWS_SECOND
        end=max(window[1] for window in windows)
        if lag/RATE+end+.3>=event["end_since_recorder_launch_s"]:
            raise ValueError("Validation music extends into physical speaker-OFF")
        control=evaluate(filtered_song,filtered_mic,lag,windows,
                         prefiltered=True)
        trials.append({
            "playback_instance":i+1,
            "prior_peak_correlation":found["peak_highmid_normalized_correlation"],
            "prior_channel":found["best_channel"],
            "prior_after_process_launch_ms":found["lag_after_process_launch_ms"],
            "holdout":control,
        })
    raw_hashes={
        "capture_manifest_sha256":digest(mf),
        "microphone_sha256":digest(micfile),
        "song_sha256":digest(song),
        "prior_alignment_report_sha256":digest(prior),
    }
    if (raw_hashes["capture_manifest_sha256"]!=ACTUAL_MANIFEST_SHA256
        or raw_hashes["microphone_sha256"]!=ACTUAL_MIC_SHA256
        or raw_hashes["song_sha256"]!=ACTUAL_SONG_SHA256
        or raw_hashes["prior_alignment_report_sha256"]!=PRIOR_ALIGNMENT_SHA256):
        raise RuntimeError("Original immutable evidence mutated during validation")
    output={
        "schema":"cuewire-M5-chaos-multiple-window-music-lag-consistency-v1",
        "status":"numeric_only_posthoc_no_gate",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "original_sources_sha256":raw_hashes,
        "trials":trials,
        "gain_not_inferred":True,
        "original_audio_modified":False,
        "played_or_exported_audio":False,
        "warnings":[
            "Holdouts test frequency-band reference-to-mic similarity at PRIOR fixed lag across different 2.7-second song segments.",
            "Local ±120ms peak searches merely describe apparent time drift; peaks can be spurious under acoustic doubletalk and recurring riffs.",
            "This is not signed causal or production synchronization; shared physical digital clock / buffer startup uncertainty is unmeasured.",
            "Audio content is real simultaneous human/recorded vocal/music/keyboard/mouse in long final stage.",
            "A clean target version of the SAME live doubletalk utterance is not available, so near-end speech quality is not directly measurable.",
            "Original song reference is stereo; downmix selection can affect real AEC implementation.",
        ],
    }
    dest=parent/out.name
    dest.mkdir(mode=0o700)
    report=dest/"alignment-consistency.json"
    with report.open("x",encoding="utf-8") as f:
        json.dump(output,f,indent=2,sort_keys=True,ensure_ascii=False)
        f.write("\n")
    with (dest/"SHA256SUMS").open("x",encoding="utf-8") as f:
        f.write(digest(report)+"  alignment-consistency.json\n")
    print("M5 two-restart acoustic lag consistency: numeric-only")
    for trial in trials:
        print("Trial",trial["playback_instance"],
              "prior fixed lag ms",round(
                  trial["holdout"]["prior_lag_time_s"]*1000,4))
        best=trial["prior_channel"]
        for window in trial["holdout"]["windows"]:
            w=window["channels"][best]
            print(" song",window["source_song_window_s"],
                  "fixed corr",w["fixed_lag_abs_normalized_correlation"],
                  "±120ms best corr",w["best_local_abs_normalized_correlation"],
                  "local shift ms",w["best_local_minus_prior_ms"])
    print("Report:",report)
    return output


def main()->None:
    os.umask(0o077)
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture",type=Path,required=True)
    ap.add_argument("--prior-alignment",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    try:
        analyze(args.capture,args.prior_alignment,args.out)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as e:
        ap.exit(1,"ERROR: "+str(e)+"\n")


if __name__=="__main__":
    main()
