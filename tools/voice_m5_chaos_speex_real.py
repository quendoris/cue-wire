#!/usr/bin/env python3
"""CueWire M5 actual 90s physical chaos: numeric-only native Speex comparison.

One already captured physical near-end voice/music/keyboard/mouse mic WAV
plus an exact SHA256-verified stereo music source with TWO independently
aligned launches. Run native Speex, native zero-reference Speex, and the
previously tested dual-Speex real-minus-zero correction with optional
OFFLINE 3800Hz echo-only guard.

No digital injected near-end voice: all speech is actual physical sound.
No clean duplicate of live doubletalk voice. We MAY measure total
signal energy and digital-reference-linked coherence/energy, but NOT
actual speech SNR/intelligibility/timbre or exact echo power in doubletalk.
The true stereo-to-speaker transfer, playback startup timing and clock
drift are unknown. Retain each original lag; no lag optimization on
candidate outputs. Two stereo reference variants are compared posthoc:
(a) mean of L+R on both restarts, (b) initial 2.7s best-channel
selection per restart (mean 1st / left 2nd). Strictly no WAV exports.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import numpy as np

from voice_m5_aec import AUDIT, RATE, digest, db, rms
from voice_m5_chaos_reference_probe import (
    ACTUAL_MANIFEST_SHA256, ACTUAL_MIC_SHA256,
    ACTUAL_SONG_SHA256, SOURCE_SCHEMA, read_raw_wave,
)
from voice_m5_speex_baseline import (
    exact_pcm16, run_speex_native, load_speex, PRE_PEAK_SAMPLES,
)
from voice_m5_speex_delta import delta_from_native
from voice_m5_echo_floor import linear_spectral_proxy, combined_band_proxy

PINNED_FIRST_ALIGNMENT_SHA256 = "ea1855721441b4a859a4409af707f0ff554953184b5bd634519a9349cc67b243"
PINNED_CONSISTENCY_SHA256 = "c3d89762d5fc3147876ab45af1546f117ab342960396cd41df3be5d98d13f3e7"
CHANNEL_MODES = {
    "mean_both_restarts": ("stereo_arithmetic_mean","stereo_arithmetic_mean"),
    "original_peak_channel_each": ("stereo_arithmetic_mean","left"),
}
STAGE_INCLUDE = ("quiet_before","mouse_music","keyboard_music",
                 "both_music","clean_voice","free_stress")
SPEAKER_STAGES = ("mouse_music","keyboard_music","both_music","free_stress")


def stage_indices(row:dict,n:int)->tuple[int,int]:
    a=round((row["start_since_recorder_launch_s"]+.75)*RATE)
    b=round((row["end_since_recorder_launch_s"]-.75)*RATE)
    if a<0 or b>n or b-a<4*RATE:
        raise ValueError("Invalid physical stage sample interval")
    return a,b


def build_farend_reference(
    stereo_song:np.ndarray,n_mic:int,
    trials:list[dict],events:list[dict],
    channel_names:tuple[str,str],
    volume:float
)->tuple[np.ndarray,dict]:
    """Known stereo source times into physical PCM mic clock.

    Initial onset lag is from original reference-to-mic, placing AEC
    reference 10ms BEFORE acoustic onset as the historical native
    SpeexDSP protocol used. Playback stop is only a wall-clock bound,
    not sample-perfect source end. Each segment starts at song t=0.
    """
    if (stereo_song.ndim!=2 or stereo_song.shape[1]!=2
        or stereo_song.shape[0]<46*RATE or n_mic<80*RATE
        or len(trials)!=2 or len(events)!=2
        or not .05<=volume<=.60
        or len(channel_names)!=2):
        raise ValueError("Bad source stereo, sample clock, or two restarts")
    # The real capture manifest has exact monotonic launch/stop markers.
    # A synthetic fixture omitted the second launch marker in an earlier
    # regression: reject incomplete evidence explicitly instead of KeyError.
    required_event_fields=(
        "instance", "music_file_start_s",
        "start_since_recorder_launch_s", "end_since_recorder_launch_s",
    )
    for i,event in enumerate(events):
        for field in required_event_fields:
            if field not in event:
                raise ValueError(f"Playback event {i+1} missing required {field}")
        launch=event["start_since_recorder_launch_s"]
        stop=event["end_since_recorder_launch_s"]
        if (not isinstance(launch,(int,float)) or
            not isinstance(stop,(int,float)) or
            not math.isfinite(launch) or not math.isfinite(stop) or
            launch < 0 or stop <= launch):
            raise ValueError(f"Playback event {i+1} invalid launch/stop times")
    far=np.zeros(n_mic,dtype=np.float64)
    spans=[]
    for i,(trial,event,chan) in enumerate(zip(trials,events,channel_names)):
        if (trial.get("playback_instance")!=i+1 or
            event.get("instance")!=i+1 or
            trial.get("estimate",{}).get("valid_echo_lag") is not True or
            event.get("music_file_start_s")!=0.0):
            raise ValueError("Wrong prior physical playback events")
        lag=int(trial["estimate"]["lag_samples_from_mic_start"])
        start=lag-PRE_PEAK_SAMPLES
        last=min(n_mic,round(event["end_since_recorder_launch_s"]*RATE))
        samples=last-start
        if (start<0 or samples<12*RATE or
            samples>len(stereo_song) or
            (i==0 and last>=round(events[1]["start_since_recorder_launch_s"]*RATE))):
            raise ValueError("Invalid music range or speaker-OFF gap")
        if chan=="left":
            x=stereo_song[:samples,0]
        elif chan=="right":
            x=stereo_song[:samples,1]
        elif chan=="stereo_arithmetic_mean":
            x=.5*(stereo_song[:samples,0]+stereo_song[:samples,1])
        else:
            raise ValueError("Unknown stereo reference channel")
        y=volume*x
        if np.max(np.abs(y))>=.98:
            raise ValueError("Music reference would PCM clip; refuse")
        far[start:last]=y
        spans.append({
            "playback_instance":i+1,
            "used_reference_channel":chan,
            "pinned_prior_lag_samples":lag,
            "pinned_prior_lag_seconds":round(lag/RATE,7),
            "reference_start_on_mic_wav_s":round(start/RATE,7),
            "reference_end_process_stop_approx_s":round(last/RATE,7),
            "rendered_samples":samples,
            "rendered_duration_s":round(samples/RATE,6),
            "farend_reference_10ms_ahead_of_detected_mic_lag":True,
            "source_song_start_s":0.0,
            "stop_not_audio_clock_synchronized":True,
        })
    return exact_pcm16(far),{
        "music_pcm_sha256_not_exported":True,
        "reference_events":spans,
        "non_audio_clock_synchronized_playback_stops":True,
    }


def numeric_metrics(raw:np.ndarray,
                    outputs:dict[str,np.ndarray],
                    far_pcm:np.ndarray,stage:dict)->dict:
    a,b=stage_indices(stage,len(raw))
    mic=raw[a:b].astype(np.float64)/32768.
    far=far_pcm[a:b].astype(np.float64)/32768.
    speaker=stage["name"] in SPEAKER_STAGES
    result={
        "phase":stage["name"],
        "approx_original_mic_wav_s":[round(a/RATE,5),round(b/RATE,5)],
        "raw_rms_dbfs":round(db(rms(mic)),5),
        "farend_rms_dbfs":round(db(rms(far)),5),
        "source_music_requested":stage["music_requested"],
        "speaker_active_tag":speaker,
        "compare_exact_same_physical_samples":True,
        "processors":{},
    }
    for name,y in outputs.items():
        chunk=y[a:b]
        diff=chunk-mic
        vals={
            "output_rms_dbfs":round(db(rms(chunk)),5),
            "fullband_input_minus_output_rms_db":
                round(db(rms(mic))-db(rms(chunk)),5),
            "processed_minus_input_rms_dbfs":
                round(db(rms(diff)),5),
            "sample_exact_max_abs_difference":
                round(float(np.max(np.abs(diff))),10),
            "output_max_abs":round(float(np.max(np.abs(chunk))),7),
        }
        if speaker:
            if rms(far)<1e-8:
                vals["reference_linked_suppression"]="undefined_no_farend"
            else:
                proxy=linear_spectral_proxy(far,mic,chunk)
                combined=combined_band_proxy({"result":proxy},"result")
                vals["reference_linked_500_4000hz"]={
                    "linked_proxy_before_dbfs":combined["linked_proxy_before_dbfs"],
                    "linked_proxy_after_dbfs":combined["linked_proxy_after_dbfs"],
                    "linked_proxy_suppression_db":combined["linked_proxy_suppression_db"],
                    "interpretation":"finite-sample coherence proxy, not isolated real echo power when local speech/clicks occur",
                }
        else:
            vals["real_untouched_voice_truth_available"]=False
            vals["direct_raw_similarity_only_not_intelligibility_metric"]=True
        result["processors"][name]=vals
    return result


def compare(capture_dir:Path,initial_path:Path,
            consistency_path:Path,out_dir:Path,
            library_loader=load_speex)->dict:
    audit=AUDIT.resolve(strict=True)
    capture_dir=capture_dir.expanduser().resolve(strict=True)
    initial_path=initial_path.expanduser().resolve(strict=True)
    consistency_path=consistency_path.expanduser().resolve(strict=True)
    if any(audit not in path.parents for path in (
        capture_dir,initial_path,consistency_path)):
        raise ValueError("Physical mic and source reports must be private")
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("Numeric report already exists; no overwrite")
    parent=out_dir.expanduser().parent.resolve(strict=True)
    if (parent!=capture_dir.parent or
        initial_path.parent.parent!=parent or
        consistency_path.parent.parent!=parent):
        raise ValueError("Output must be fresh sibling of exact original sources")
    manifest=capture_dir/"manifest.json"
    micfile=capture_dir/"microphone-chaos-raw.wav"
    if (digest(manifest)!=ACTUAL_MANIFEST_SHA256 or
        digest(micfile)!=ACTUAL_MIC_SHA256 or
        digest(initial_path)!=PINNED_FIRST_ALIGNMENT_SHA256 or
        digest(consistency_path)!=PINNED_CONSISTENCY_SHA256):
        raise ValueError("Source report or physical mic SHA256 mismatch")
    meta=json.loads(manifest.read_text(encoding="utf-8"))
    initial=json.loads(initial_path.read_text(encoding="utf-8"))
    consistency=json.loads(consistency_path.read_text(encoding="utf-8"))
    if (meta.get("schema")!=SOURCE_SCHEMA
        or meta.get("status")!="captured_ctrl_c_at_free_stress"
        or meta.get("source_mic_sha256")!=ACTUAL_MIC_SHA256
        or meta.get("music_stream_volume")!=.60
        or initial.get("schema")!="cuewire-M5-chaos-reference-onset-lag-diagnostics-v1"
        or consistency.get("schema")!="cuewire-M5-chaos-multiple-window-music-lag-consistency-v1"
        or initial.get("sha256",{}).get("original_microphone_sha256")!=ACTUAL_MIC_SHA256
        or consistency.get("original_sources_sha256",{}).get("microphone_sha256")!=ACTUAL_MIC_SHA256
        or consistency.get("original_sources_sha256",{}).get("prior_alignment_report_sha256")!=PINNED_FIRST_ALIGNMENT_SHA256):
        raise ValueError("Sources are not the audited physical M5 Chaos-001 evidence")
    song=Path(meta["music_source"]["path"]).expanduser().resolve(strict=True)
    if (meta["music_source"].get("sha256")!=ACTUAL_SONG_SHA256
        or digest(song)!=ACTUAL_SONG_SHA256):
        raise ValueError("Music source SHA256 changed")
    mic=read_raw_wave(micfile,1)[:,0]
    song_samples=read_raw_wave(song,2,max_frames=47*RATE)
    raw=exact_pcm16(mic)
    if len(raw)!=meta["physical_microphone_analysis"]["recorded_frames"]:
        raise ValueError("Original microphone frame count differs")
    stages=meta.get("recorded_phases",[])
    if ([s.get("name") for s in stages]!=list(STAGE_INCLUDE)):
        raise ValueError("Different actual physical experiment stage list")
    events=meta.get("playback_software_start_stop_events",[])
    trials=initial.get("separate_process_restarts",[])
    if len(events)!=2 or len(trials)!=2 or len(consistency.get("trials",[]))!=2:
        raise ValueError("Two exact restarts and one multiwindow validation required")
    for i,t in enumerate(trials):
        if (t["estimate"]["lag_samples_from_mic_start"]!=
            consistency["trials"][i]["holdout"]["prior_lag_samples"]):
            raise ValueError("Prior lag source mismatch across independent audits")
    lib,libname=library_loader()
    no_far=np.zeros(len(raw),dtype=np.int16)
    zero_wet,zero_meta=run_speex_native(
        raw,no_far,library=lib,library_name=libname)
    results={}
    for tag,channels in CHANNEL_MODES.items():
        far,farinfo=build_farend_reference(
            song_samples,len(raw),trials,events,channels,.60)
        standard,stdmeta=run_speex_native(
            raw,far,library=lib,library_name=libname)
        plain,plain_settings=delta_from_native(
            raw,standard,zero_wet,guard=False)
        guarded,guard_settings=delta_from_native(
            raw,standard,zero_wet,guard=True)
        variants={
            "original_raw":raw.astype(np.float64)/32768.,
            "native_speex":standard,
            "native_speex_zero_farend":zero_wet,
            "dual_speex_delta":plain,
            "dual_speex_delta_guarded_offline":guarded,
        }
        phase_scores=[numeric_metrics(raw,variants,far,stage) for stage in stages]
        results[tag]={
            "channel_strategy":channels,
            "speaker_reference_reconstruction":farinfo,
            "native_library_processor":stdmeta,
            "native_zero_reference_processor":zero_meta,
            "raw_delta_settings":plain_settings,
            "guarded_delta_settings":guard_settings,
            "phase_summaries":phase_scores,
        }
    originals={
        "original_manifest":(manifest,ACTUAL_MANIFEST_SHA256),
        "original_microphone":(micfile,ACTUAL_MIC_SHA256),
        "original_digital_song":(song,ACTUAL_SONG_SHA256),
        "initial_alignment_report":(initial_path,PINNED_FIRST_ALIGNMENT_SHA256),
        "multiwindow_consistency_report":(consistency_path,PINNED_CONSISTENCY_SHA256),
    }
    if any(digest(path)!=sha for path,sha in originals.values()):
        raise RuntimeError("Source changed while processing; refuse numeric report")
    report={
        "schema":"cuewire-M5-chaos-native-speex-vs-dual-delta-real-physical-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "status":"offline_descriptive_physical_doubletalk_NO_speech_ground_truth",
        "original_sha256":{name:sha for name,(_,sha) in originals.items()},
        "original_stereo_music_source":meta["music_source"]["path"],
        "two_separate_pinned_reference_lags_samples":[
            t["estimate"]["lag_samples_from_mic_start"] for t in trials],
        "music_reference_injection_prelead_ms":PRE_PEAK_SAMPLES*1000/RATE,
        "source_music_stream_gain":.60,
        "native_speex_library":libname,
        "zero_farend_output_reused_between_channel_strategies":True,
        "cases":results,
        "source_wav_files_mutated":False,
        "exported_audio_files":False,
        "audio_playback_or_device_access":False,
        "auto_route_or_sink_gain_changes":False,
        "real_physical_nearend_voice_recorded":True,
        "same_clean_target_speech_during_music_available":False,
        "speech_preservation_certified":False,
        "approved_for_virtual_microphone":False,
        "limitations":[
            "Reference to mic offset was fitted using preexisting music windows; stateful Speex adapts throughout the new actual talk.",
            "Both candidate channel choices were based on prior inspected evidence and are not blinded.",
            "Physical microphones are not synchronized with digital playback clocks; per-song restart process stop time is approximate.",
            "The 10ms earlier audio reference is previous CueWire Speex protocol, not independently optimal latency.",
            "Known cleaner live speech stage has NO MUSIC but is not the exact same utterance as the overlapping speech stage.",
            "Total RMS change during doubletalk cannot distinguish canceled echo from damaged live speech.",
            "Music-linked coherence estimates in mixed speech are *uncalibrated* finite-sample proxies, not exact echo power.",
            "The 513-tap zero-phase FIR used on the differential correction is NONCAUSAL OFFLINE, not production streaming.",
            "No audio was emitted: this is a numeric diagnostic only, not a perceptual comparison.",
        ],
    }
    dst=parent/out_dir.name
    dst.mkdir(mode=0o700)
    file=dst/"physical-chaos-speex.json"
    with file.open("x",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2,sort_keys=True)
        f.write("\n")
    with (dst/"SHA256SUMS").open("x",encoding="utf-8") as f:
        f.write(digest(file)+"  physical-chaos-speex.json\n")
    print("CueWire M5 native Speex vs differential actual chaotic doubletalk: NUMERIC")
    for tag,c in results.items():
        print("\n",tag)
        for stage in c["phase_summaries"]:
            print(" ",stage["phase"],"raw RMS",
                  stage["raw_rms_dbfs"],"dBFS")
            for mode in ("native_speex","dual_speex_delta",
                         "dual_speex_delta_guarded_offline"):
                x=stage["processors"][mode]
                echo=x.get("reference_linked_500_4000hz",{})
                print("   ",mode,"output RMS",x["output_rms_dbfs"],
                      "echo-linked 500..4000 suppression",
                      echo.get("linked_proxy_suppression_db"))
    print("NO export and NO clean same-utterance voice truth. Output:",file)
    return report


def main()->None:
    os.umask(0o077)
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture",type=Path,required=True)
    ap.add_argument("--initial-alignment",type=Path,required=True)
    ap.add_argument("--consistency",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    try:
        compare(args.capture,args.initial_alignment,args.consistency,args.out)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as error:
        ap.exit(1,"ERROR: "+str(error)+"\n")


if __name__=="__main__":
    main()
