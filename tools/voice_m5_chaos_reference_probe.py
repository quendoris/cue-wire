#!/usr/bin/env python3
"""Read-only M5 Chaos-001 stereo far-end PCM integrity and echo-lag probe.

On the user's Arch, the explicitly selected *local vocal song WAV* must
be available with the SHA256 from the existing capture manifest.
Compare three possible digital reference channels (L,R,mean) to the
physical microphone and estimate each pw-play instance separately.
Use the existing CueWire 1.5..4kHz normalized cross correlation
band_lag only as a diagnostic. Clock launch timestamps are NOT
sample-synchronous audio timing. Correlation can be ambiguous,
especially with repeating riffs/music and physical talk/typing.

No microphone/audio device access; no playback; no PipeWire calls;
no resampling or sound export, no source modifications, no daemons.
Output one JSON + SHA256SUMS in a NEW private .audit sibling.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import wave

import numpy as np

from voice_m5_aec import AUDIT, RATE, digest
from voice_m5_refcoherence import band_lag

SOURCE_SCHEMA = "cuewire-M5-chaos-physical-doublestress-v1"
# Actual user-uploaded physical evidence, independently rehashed 2026-11-10.
ACTUAL_MANIFEST_SHA256 = "20d52a781d33c6df05d9bc551afb8fea5899dbd070a6690e5bfaabb3fd6b7c66"
ACTUAL_MIC_SHA256 = "63322a193843100e87124fb47fd1445604fbc6118accbc1fe489e772326980fb"
ACTUAL_SONG_SHA256 = "495d4fb1845a369129df4a659f62f18c087c7de1bff4f895dcf60a1c38351e6a"
PROBE_START = 1.0
PROBE_END = 3.7
RELIABLE_CORRELATION_MIN = 0.06  # Historical CueWire band_lag minimum, not new pass threshold.


def read_raw_wave(path:Path, channels:int, max_frames:int|None=None)->np.ndarray:
    with wave.open(str(path),"rb") as src:
        specs=(src.getframerate(),src.getnchannels(),
               src.getsampwidth(),src.getcomptype())
        if specs != (RATE,channels,2,"NONE"):
            raise ValueError(f"Expected original {RATE}Hz PCM16 {channels} channel source, got {specs}")
        frames=src.getnframes()
        if channels==1 and not (60*RATE<=frames<=300*RATE):
            raise ValueError("Physical chaotic microphone duration outside 60..300s")
        if channels==2 and frames < 30*RATE:
            raise ValueError("Music source too short for two playback trials")
        read_count=frames if max_frames is None else min(max_frames,frames)
        blob=src.readframes(read_count)
        if len(blob)!=read_count*channels*2:
            raise ValueError("Truncated PCM16 WAV")
    raw=np.frombuffer(blob,dtype="<i2").reshape(-1,channels)
    return raw.astype(np.float64)/32768.


def probe_one(digital_stereo:np.ndarray, physical_mic:np.ndarray,
              launch_time_s:float)->dict:
    if (digital_stereo.ndim!=2 or digital_stereo.shape[1]!=2
        or digital_stereo.shape[0]<round(5*RATE)
        or physical_mic.ndim!=1
        or not math.isfinite(launch_time_s)):
        raise ValueError("Invalid native 48kHz stereo song/mono mic array or time")
    candidates={
        "left":digital_stereo[:,0],
        "right":digital_stereo[:,1],
        "stereo_arithmetic_mean":.5*(digital_stereo[:,0]+digital_stereo[:,1]),
    }
    outcome={}
    valid=[]
    for name,x in candidates.items():
        try:
            lag,values=band_lag(
                x,physical_mic,launch_time_s,
                probe_start_s=PROBE_START,probe_end_s=PROBE_END)
            outcome[name]={
                "found":True,
                "lag_samples_from_mic_start":lag,
                **values,
            }
            valid.append((values["peak_highmid_normalized_correlation"],name,lag))
        except (ValueError,IndexError,ZeroDivisionError,FloatingPointError) as exc:
            outcome[name]={"found":False,"reason":str(exc)}
    if not valid:
        return {
            "status":"inconclusive_no_channel_meets_reference_correlation_minimum",
            "reference_channels":outcome,
            "best_channel":None,
            "valid_echo_lag":False,
        }
    confidence,best,lag=max(valid)
    delay_ms=round(1000*(lag/RATE-launch_time_s),5)
    plausibility=(delay_ms>=0.)
    return {
        "status":"correlated_candidate_only_not_hardware_latency"
            if plausibility else "suspect_negative_relative_delay",
        "best_channel":best,
        "peak_highmid_normalized_correlation":confidence,
        "lag_samples_from_mic_start":lag,
        "lag_after_process_launch_ms":delay_ms,
        "valid_echo_lag":bool(plausibility),
        "reference_channels":outcome,
        "ambiguity_not_automatically_ruled_out":True,
    }


def analyze(source_dir:Path, output_dir:Path)->dict:
    source_dir=source_dir.expanduser().resolve(strict=True)
    audit=AUDIT.resolve(strict=True)
    if audit not in source_dir.parents:
        raise ValueError("Source folder must be private local .audit")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Refusing to overwrite prior music-alignment findings")
    out_parent=output_dir.expanduser().parent.resolve(strict=True)
    if out_parent!=source_dir.parent:
        raise ValueError("Output must be new sibling of original .audit recording")
    mf_path=source_dir/"manifest.json"
    if digest(mf_path)!=ACTUAL_MANIFEST_SHA256:
        raise ValueError("Actual original M5 chaos manifest SHA256 mismatch")
    report=json.loads(mf_path.read_text(encoding="utf-8"))
    if (report.get("schema")!=SOURCE_SCHEMA
       or report.get("status")!="captured_ctrl_c_at_free_stress"
       or report.get("music_stream_volume")!=.6
       or report.get("manual_ctrl_c") is not True
       or report.get("sink_volume_changed") is not False):
        raise ValueError("Original M5 chaos recording/protocol not validated")
    mic_path=source_dir/report.get("raw_wav_file","")
    if report.get("raw_wav_file") != "microphone-chaos-raw.wav":
        raise ValueError("Unrecognized original physical mic WAV filename")
    if (report.get("source_mic_sha256")!=ACTUAL_MIC_SHA256 or
        digest(mic_path)!=ACTUAL_MIC_SHA256):
        raise ValueError("Original microphone WAV SHA256 mismatch")
    song_data=report.get("music_source",{})
    song_path=Path(song_data["path"]).expanduser().resolve(strict=True)
    if (song_data.get("sha256")!=ACTUAL_SONG_SHA256 or
        digest(song_path)!=ACTUAL_SONG_SHA256):
        raise ValueError("Original 0.60 music WAV SHA256 mismatch")
    if not (song_data.get("rate")==RATE and song_data.get("channels")==2):
        raise ValueError("Expected the original 48kHz stereo music source")
    events=report.get("playback_software_start_stop_events",[])
    if (len(events)!=2 or [x.get("instance") for x in events]!=[1,2]
        or any(x.get("music_file_start_s")!=0.0 for x in events)
        or any(
            x.get("end_since_recorder_launch_s") is None
            or x["start_since_recorder_launch_s"]>=x["end_since_recorder_launch_s"]
            for x in events
        )):
        raise ValueError("Expected exactly two known song restarts for this capture")
    # Only the song's first 5 seconds are required for this initial probe.
    # Full source WAS already SHA256-verified byte-for-byte above.
    far=read_raw_wave(song_path,2,max_frames=5*RATE)
    rec=read_raw_wave(mic_path,1)[:,0]
    if len(rec)!=report.get("physical_microphone_analysis",{}).get("recorded_frames"):
        raise ValueError("Microphone frame count differs from recorded provenance")
    trials=[]
    for event in events:
        launch=float(event["start_since_recorder_launch_s"])
        estimate=probe_one(far,rec,launch)
        trials.append({
            "playback_instance":event["instance"],
            "playback_process_started_at_mic_wall_s":launch,
            "reported_playback_stop_wall_s":event["end_since_recorder_launch_s"],
            "music_source_begin_s":event["music_file_start_s"],
            "estimate":estimate,
        })
    rechecked={
        "original_manifest_sha256":digest(mf_path),
        "original_microphone_sha256":digest(mic_path),
        "original_farend_song_sha256":digest(song_path)
    }
    if (rechecked["original_microphone_sha256"]!=report["source_mic_sha256"]
        or rechecked["original_farend_song_sha256"]!=song_data["sha256"]):
        raise RuntimeError("Source files changed while computing alignment")
    answer={
        "schema":"cuewire-M5-chaos-reference-onset-lag-diagnostics-v1",
        "status":"offline_frozen_unprocessed_diagnostic",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "sha256":rechecked,
        "real_mic_frames":len(rec),
        "reference_song_channels":2,
        "music_stream_volume":.6,
        "probe_source_music_window_s":[PROBE_START,PROBE_END],
        "candidates":["left","right","stereo_arithmetic_mean"],
        "correlation_minimum":RELIABLE_CORRELATION_MIN,
        "separate_process_restarts":trials,
        "no_aec_applied":True,
        "source_file_modified":False,
        "audio_played_or_exported":False,
        "limitations":[
            "Process startup wall timestamps do not equal the acoustic waveform's first sample.",
            "The peak cross-correlation may be ambiguous for repetitive music, room reflections, mic nonlinearity or local noises.",
            "The stereo average of a song is NOT the exact speaker radiation transfer function, and per-channel maxima do not establish the best real-time reference strategy.",
            "The early 1..3.7s song probe cannot prove full 24/46s clock sync or long-duration drift.",
            "Music source hash does not prove which specific other-person vocal syllables occurred in the microphone stage.",
            "No AEC quality, near-end speech preservation or production approval is inferred."
        ],
    }
    dest=out_parent/output_dir.name
    dest.mkdir(mode=0o700)
    result_file=dest/"music-alignment.json"
    with result_file.open("x",encoding="utf-8") as f:
        json.dump(answer,f,ensure_ascii=False,sort_keys=True,indent=2)
        f.write("\n")
    with (dest/"SHA256SUMS").open("x",encoding="utf-8") as f:
        f.write(digest(result_file)+"  music-alignment.json\n")
    print("Chaotic source integrity + initial far-end alignment (offline, no playback)")
    for trial in trials:
        info=trial["estimate"]
        print("  pw-play",trial["playback_instance"],info["status"],
              "channel=",info.get("best_channel"),
              "corr=",info.get("peak_highmid_normalized_correlation"),
              "after launch ms=",info.get("lag_after_process_launch_ms"))
    print("SHA256-pinned report:",result_file)
    return answer


def main()->None:
    os.umask(0o077)
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    try:
        analyze(args.capture,args.out)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,wave.Error) as exc:
        ap.exit(1,"ERROR: "+str(exc)+"\n")


if __name__=="__main__":
    main()
