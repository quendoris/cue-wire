#!/usr/bin/env python3
"""CueWire M5: exploratory dual-Speex reference-DIFFERENTIAL echo correction.

Hypothesis from actual Speex native zero-far-end and synthetic M4 voice
results, registered post-hoc BEFORE running this experiment:

    delta = Speex(mic, digital_farend) - Speex(mic, zero_farend)
    output = original_mic_pcm16 + delta

The two SpeexDSP engines are independently initialized, processed from
the BEGINNING of the exact same full physical recording, and never
carry shared state. Speaker-OFF noise and microphone frontend effects
should largely cancel IF common to both branches. A state-dependent
difference or nonlinear doubletalk can violate that expectation.
A previously chosen noncausal 3800Hz echo-only guard is separately
applied to the delta as a descriptive highband safety comparator.

Reuse SAME two immutable M5 physical OFF/ON/OFF WAVs, reference WAV,
verified original M4 voice RAW and original prior 6-second synthetic
voice report, and evaluate ONLY 10..16s with known voice digitally
injected at -50 and -39dBFS. No independent physical doubletalk, no
causal live processing or production gate. Never save processed audio.
No audio devices, routing, changes, autostart, network or daemons.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import numpy as np

from voice_m5_aec import AUDIT, RATE, db, digest, read_wav, rms
from voice_m5_frozen_heldout import indices, erle_db
from voice_m5_guarded_fir import guarded_echo
from voice_m5_echo_floor import EVAL_S, linear_spectral_proxy, combined_band_proxy
from voice_m5_playback_coherence import (
    checked_take, LOW_MANIFEST_SHA, HIGH_MANIFEST_SHA,
    LOW_MIC_SHA, HIGH_MIC_SHA, REFERENCE_SHA, SOURCE_MANIFEST_SHA,
)
from voice_m5_known_stem import read_voice_excerpt, validate_m4_voice_manifest
from voice_m5_speex_baseline import (
    exact_pcm16, render_reference_on_mic_timeline,
    run_speex_native, load_speex,
    LAG_015_SAMPLES, LAG_030_SAMPLES,
)
from voice_m5_speex_known_voice import (
    construct_mixture, known_stem_interference,
    score_incremental_voice, VOICE_TARGETS_DBFS, VOICE_SECONDS,
)

# Exact actual 2026-11-09 SHA256 of the independent prior M4 digital
# injection / native Speex speech transfer report.
ORIGINAL_VOICE_REPORT_SHA256 = (
    "75e96c0ce587417738b0fb5bbca00040d3b85e85231ca281cc19fd2b7fcfeee7"
)
M4_VOICE_START_S = 6.0
OFF_INTERVALS = (("before",2.,7.),("after",30.,35.))


def delta_from_native(raw_pcm: np.ndarray,
                      real_far_result: np.ndarray,
                      zero_far_result: np.ndarray,
                      guard: bool = False) -> tuple[np.ndarray, dict]:
    """Mix only far-end-dependent correction into original, unfiltered mic.

    The output is a NUMERIC array, never an audio file. The -3.8kHz
    guard is the already-chosen 513-tap noncausal echo-estimate-only
    filter, applied ONLY to Speex far-end difference, not to the mic.
    """
    if (raw_pcm.ndim != 1 or real_far_result.ndim != 1 or
            zero_far_result.ndim != 1 or not
            (len(raw_pcm) == len(real_far_result) == len(zero_far_result))
            or raw_pcm.dtype != np.dtype("<i2") or
            any(not np.all(np.isfinite(arr))
                for arr in (real_far_result,zero_far_result))):
        raise ValueError("Expect identical-length native mono PCM16 and finite Speex float output")
    correction = real_far_result - zero_far_result
    if guard:
        correction, guard_settings = guarded_echo(correction)
    else:
        guard_settings = {
            "enabled": False,
            "echo_estimate_only": True,
            "correction_filtered": False,
        }
    result = raw_pcm.astype(np.float64)/32768. + correction
    if not np.all(np.isfinite(result)) or float(np.max(np.abs(result))) >= .98:
        raise ValueError("Hybrid numerical output unsafe peak/nonfinite")
    return result, guard_settings


def absolute_speaker_only(reference: np.ndarray,
                          input_pcm: np.ndarray,
                          processed: np.ndarray,
                          lag: int) -> dict:
    a,b=indices(EVAL_S,lag,len(input_pcm))
    original=input_pcm[a:b].astype(np.float64)/32768.
    out=processed[a:b]
    music=reference[round(EVAL_S[0]*RATE):round(EVAL_S[1]*RATE)]
    measures=linear_spectral_proxy(music,original,out)
    measures["full_band_erle_db"]=round(erle_db(original,out),5)
    measures["speech_band_500_4000hz_reference_linked_proxy"] = (
        combined_band_proxy({"result":measures},"result"))
    return measures


def quiet_reference_control(original: np.ndarray,
                            corrected: np.ndarray) -> list[dict]:
    out=[]
    for phase,a,b in OFF_INTERVALS:
        raw=original[round(a*RATE):round(b*RATE)]
        wet=corrected[round(a*RATE):round(b*RATE)]
        out.append({
            "mic_time_s":[a,b],
            "phase":phase,
            "raw_rms_dbfs":round(db(rms(raw)),5),
            "corrected_rms_dbfs":round(db(rms(wet)),5),
            "corrected_minus_raw_rms_db":round(db(rms(wet))-db(rms(raw)),5),
            "correction_rms_dbfs":round(db(rms(wet-raw)),5)
        })
    return out


def one_take(reference: np.ndarray, mic: np.ndarray,
             voice: np.ndarray, volume: float, lag: int,
             native, library_name: str) -> dict:
    original=exact_pcm16(mic)
    raw=original.astype(np.float64)/32768.
    far=render_reference_on_mic_timeline(reference,len(mic),lag,volume)
    zero=np.zeros(len(original),dtype=np.int16)
    real_base,real_metadata=run_speex_native(
        original,far,library=native,library_name=library_name)
    zero_base,zero_metadata=run_speex_native(
        original,zero,library=native,library_name=library_name)
    delta_base,plain_settings=delta_from_native(
        original,real_base,zero_base)
    guard_base,guard_settings=delta_from_native(
        original,real_base,zero_base,guard=True)
    speaker_only={}
    for name,processed in (
            ("raw_bypass",raw),
            ("normal_native_speex",real_base),
            ("zero_far_native_speex",zero_base),
            ("reference_difference_raw",delta_base),
            ("reference_difference_guarded",guard_base)):
        speaker_only[name]=absolute_speaker_only(
            reference,original,processed,lag)
    speaker_only["speaker_off_delta_impact"] = {
        "reference_difference_raw":quiet_reference_control(raw,delta_base),
        "reference_difference_guarded":quiet_reference_control(raw,guard_base)
    }
    a,b=indices(EVAL_S,lag,len(mic))
    voice_results=[]
    for target in VOICE_TARGETS_DBFS:
        mixed,stem,stem_info=construct_mixture(
            mic,voice,a,b,target)
        mixed_raw=mixed.astype(np.float64)/32768.
        music_wet,music_info=run_speex_native(
            mixed,far,library=native,library_name=library_name)
        null_wet,null_info=run_speex_native(
            mixed,zero,library=native,library_name=library_name)
        hybrid,hybrid_spec=delta_from_native(
            mixed,music_wet,null_wet,guard=False)
        guarded,guard_spec=delta_from_native(
            mixed,music_wet,null_wet,guard=True)
        configurations={
            "raw_bypass":(mixed_raw,raw),
            "normal_native_speex":(music_wet,real_base),
            "zero_far_native_speex":(null_wet,zero_base),
            "reference_difference_raw":(hybrid,delta_base),
            "reference_difference_guarded":(guarded,guard_base)
        }
        results={}
        for name,(processed,base) in configurations.items():
            incremental=processed[a:b]-base[a:b]
            results[name]={
                "voice_incremental_transfer":
                    score_incremental_voice(stem,incremental),
                "absolute_output_known_stem_snr":
                    known_stem_interference(stem,processed[a:b]),
                "reference_on_while_physical_nearend_captured":False,
            }
        voice_results.append({
            "voice":stem_info,
            "results":results,
            "native_speex_music_processing":music_info,
            "native_speex_zero_processing":null_info,
        })
    return {
        "per_stream_volume":volume,
        "historical_fixed_lag_samples":lag,
        "reference_eval_time_s":list(EVAL_S),
        "physical_mic_known_voice_injection_time_s":[round(a/RATE,6),round(b/RATE,6)],
        "native_speex_reference_on":real_metadata,
        "native_speex_zero_reference":zero_metadata,
        "delta_only_settings":plain_settings,
        "guarded_delta_settings":guard_settings,
        "speaker_only":speaker_only,
        "synthetic_known_voice_cases":voice_results,
        "noncausal_guard_not_real_time":True,
        "physical_simultaneous_doubletalk":False,
    }


def analyze(low_dir: Path, high_dir: Path, voice_dir: Path,
            original_report_path: Path, out_dir: Path,
            native_loader=load_speex) -> dict:
    low_dir=low_dir.expanduser().resolve(strict=True)
    high_dir=high_dir.expanduser().resolve(strict=True)
    voice_dir=voice_dir.expanduser().resolve(strict=True)
    original_report_path=original_report_path.expanduser().resolve(strict=True)
    audit=AUDIT.resolve(strict=True)
    if (low_dir==high_dir or any(
        audit not in p.parents for p in (
            low_dir,high_dir,voice_dir,original_report_path
        ))):
        raise ValueError("Original sources and voice report must be under private .audit")
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("Numeric output exists; no overwrite")
    parent=out_dir.expanduser().parent.resolve(strict=True)
    if (parent!=high_dir.parent or
            original_report_path.parent.parent!=parent):
        raise ValueError("Output must be a fresh sibling of original M5 reports")
    if digest(original_report_path)!=ORIGINAL_VOICE_REPORT_SHA256:
        raise ValueError("First known-voice Speex report SHA256 mismatch")
    old=json.loads(original_report_path.read_text(encoding="utf-8"))
    if (old.get("schema")!="cuewire-M5-SpeexDSP-incremental-synthetic-nearend-voice-v1"
            or old.get("physical_voice_capture_during_music") is not False
            or old.get("voice_preservation_certified") is not False
            or old.get("voice_target_rms_dbfs") != list(VOICE_TARGETS_DBFS)
            or old.get("m4_voice_excerpt_start_seconds") != M4_VOICE_START_S
            or old.get("m4_voice_excerpt_duration_seconds") != VOICE_SECONDS):
        raise ValueError("Original M4 digital-injection study parameters mismatch")
    ml,hl,yl=checked_take(low_dir,LOW_MANIFEST_SHA,LOW_MIC_SHA,.15)
    mh,hh,yh=checked_take(high_dir,HIGH_MANIFEST_SHA,HIGH_MIC_SHA,.30)
    for field in ("source_mic_name","speaker_sink_name","reference_source_capture"):
        if ml[field]!=mh[field]:
            raise ValueError("M5 physical inputs differ")
    refdir=Path(ml["reference_source_capture"]).expanduser().resolve(strict=True)
    if audit not in refdir.parents:
        raise ValueError("Digital reference outside private source directory")
    reference_file=refdir/"reference-sent.wav"
    reference_mf=refdir/"manifest.json"
    if (digest(reference_file)!=REFERENCE_SHA or
            digest(reference_mf)!=SOURCE_MANIFEST_SHA):
        raise ValueError("M5 digital music WAV or manifest SHA256 mismatch")
    music=read_wav(reference_file)
    if len(music)!=20*RATE:
        raise ValueError("Original digital music length not 20s")
    voice_mf=voice_dir/"manifest.json"
    voice_file=voice_dir/"raw.wav"
    voice_meta=json.loads(voice_mf.read_text(encoding="utf-8"))
    voice_hash,voice_schema,voice_backend=validate_m4_voice_manifest(voice_meta)
    if digest(voice_file)!=voice_hash:
        raise ValueError("M4 physical voice RAW SHA256 mismatch")
    voice=read_voice_excerpt(voice_file,M4_VOICE_START_S,VOICE_SECONDS)
    if len(voice)!=round(VOICE_SECONDS*RATE):
        raise ValueError("Need the previous identical complete six second M4 excerpt")
    src={
        "digital_reference": (reference_file,REFERENCE_SHA),
        "digital_reference_manifest": (reference_mf,SOURCE_MANIFEST_SHA),
        "level015_manifest":(low_dir/"manifest.json",hl["manifest"]),
        "level015_raw_mic":(low_dir/"microphone-aba-raw.wav",hl["physical_mic_raw"]),
        "level030_manifest":(high_dir/"manifest.json",hh["manifest"]),
        "level030_raw_mic":(high_dir/"microphone-aba-raw.wav",hh["physical_mic_raw"]),
        "m4_voice_manifest":(voice_mf,digest(voice_mf)),
        "m4_voice_raw":(voice_file,voice_hash),
    }
    hashes={k:v for k,(_,v) in src.items()}
    if old["source_sha256"]!=hashes:
        raise ValueError("Source hashes differ from original known-voice Speex report")
    lib,libpath=native_loader()
    v015=one_take(music,yl,voice,.15,LAG_015_SAMPLES,lib,libpath)
    v030=one_take(music,yh,voice,.30,LAG_030_SAMPLES,lib,libpath)
    if (digest(original_report_path)!=ORIGINAL_VOICE_REPORT_SHA256
            or any(digest(p)!=sha for p,sha in src.values())):
        raise RuntimeError("Source evidence changed during delta analysis")
    report={
        "schema":"cuewire-M5-SpeexDSP-dual-branch-reference-delta-v1",
        "status":"exploratory_posthoc_synthetic_heldout_no_production_pass",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "source_sha256":hashes,
        "original_speex_known_voice_report_sha256":ORIGINAL_VOICE_REPORT_SHA256,
        "native_speex_library":libpath,
        "formula":"input_pcm16 + Speex(input, real_far) - Speex(input, zero_far)",
        "speaker_only_and_voice_report_015":v015,
        "speaker_only_and_voice_report_030":v030,
        "zero_far_branch_and_real_far_branch_reset_independently":True,
        "voice_nearend_only_synthetic":True,
        "voice_preservation_proven_in_real_doubletalk":False,
        "approved_for_live_mic":False,
        "all_origins_hash_verified":True,
        "no_audio_emitted":True,
        "no_aec_or_voice_production_threshold_chosen":True,
        "historical_0_67881_db_prospective_AEC_gate_still_failed":True,
        "warnings":[
            "This dual-Speex delta formula is motivated by prior results; retrospective and not independent.",
            "Zero-farend Speex is a processing stage with independent adaptive state, not an ideal invariant frontend.",
            "A voice-corrupting differential state effect can persist; the output is not inherently voice-safe.",
            "Causal version requires two live synchronized DSP states and measured CPU budget and algorithmic latency.",
            "Noncausal 3800Hz Hann 513-tap correction guard is strictly offline, never a live-ready filter.",
            "Original raw physical recordings, M4 near-end voice and source manifests were not modified or exported.",
            "No actual physical doubletalk or room/microphone AGC reaction to voice injection was recorded.",
            "All absolute and incremental digital voice scores are research measures, not PESQ/STOI/MOS.",
            "No AEC acceptance gate or original failed prospectively registered result has changed.",
        ],
    }
    out=parent/out_dir.name
    out.mkdir(mode=0o700)
    dest=out/"speex-delta.json"
    with dest.open("x",encoding="utf-8") as fd:
        json.dump(report,fd,sort_keys=True,ensure_ascii=False,indent=2)
        fd.write("\n")
    with (out/"SHA256SUMS").open("x",encoding="utf-8") as fd:
        fd.write(digest(dest)+"  speex-delta.json\n")
    print("M5 dual-Speex differential reference-only echo correction: NUMERIC")
    for label,take in (("0.15",v015),("0.30",v030)):
        for name in ("raw_bypass","normal_native_speex",
                     "reference_difference_raw","reference_difference_guarded"):
            v=take["speaker_only"][name]
            print(label,name,"speaker-only full ERLE",
                  v["full_band_erle_db"],"dB 500-4000Hz linked reduction",
                  v["speech_band_500_4000hz_reference_linked_proxy"][
                      "linked_proxy_suppression_db"])
        for case in take["synthetic_known_voice_cases"]:
            print("Voice level",case["voice"]["target_voice_rms_dbfs"],"dBFS")
            for mode,score in case["results"].items():
                print(" ",mode,"incremental voice SNR",
                      score["voice_incremental_transfer"][
                          "incremental_voice_response_snr_db"],
                      "known-stem output SNR",
                      score["absolute_output_known_stem_snr"]["known_stem_snr_db"])
    print("NOT real physical doubletalk. NO audio output. Previous FAIL persists.")
    print("Report:",dest)
    return report


def main()->None:
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--low-capture",type=Path,required=True)
    p.add_argument("--high-capture",type=Path,required=True)
    p.add_argument("--voice",type=Path,required=True)
    p.add_argument("--original-voice-report",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    try:
        analyze(args.low_capture,args.high_capture,args.voice,
                args.original_voice_report,args.out)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as e:
        p.exit(1,"ERROR: "+str(e)+"\n")


if __name__=="__main__":
    main()
