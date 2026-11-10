#!/usr/bin/env python3
"""CueWire M5 Chaos-001: voluntary private A/B WAVs from *real* doubletalk.

Reproduce SHA256-pinned original 90s physical microphone,
native SpeexDSP, and the previously audited offline guarded
dual-Speex differential using original acoustic music reference
and both frozen playback lags. The original song remains on
the user's computer. No new sound is captured or synthesized.

The explicit listening exception to numeric-only research tools
is based on the user's standing request to hear original vs best
AEC. All WAV exports are PRIVATE under .audit, only on command.
NO playback, default sink changes, virtual devices, monitoring,
daemon, PipeWire access, mutation of sources or network/upload.

Four frozen listening segments, all mono PCM16 48kHz:
  12..18s mouse + music
  28..34s mouse + keyboard + music
  37..43s actual unaccompanied near-end speech
  53..83s actual difficult near-end speech + song + clicks
All three variants within and ACROSS segments get ONE common
attenuation gain, if necessary, to protect against PCM clipping;
otherwise unity. No file-specific loudness compensation,
compression, enhancement, denoise or psychoacoustic processing.
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

from voice_m5_aec import AUDIT,RATE,digest,db,rms
from voice_m5_chaos_reference_probe import (
    ACTUAL_MANIFEST_SHA256,ACTUAL_MIC_SHA256,ACTUAL_SONG_SHA256,
    read_raw_wave,SOURCE_SCHEMA,
)
from voice_m5_chaos_speex_real import (
    PINNED_FIRST_ALIGNMENT_SHA256,PINNED_CONSISTENCY_SHA256,
    build_farend_reference,CHANNEL_MODES,
)
from voice_m5_speex_baseline import (
    exact_pcm16,run_speex_native,load_speex,
)
from voice_m5_speex_delta import delta_from_native

ACTUAL_REAL_SPEEX_REPORT_SHA256=(
    "3fdb522dbbcbe456709c11987d1e795c9b9fc9f086b91718dff374cd6919ad58"
)
SEGMENTS=(
    ("01_mouse_and_music",12.0,18.0),
    ("02_mouse_keyboard_music",28.0,34.0),
    ("03_real_voice_no_music",37.0,43.0),
    ("04_real_full_chaos",53.0,83.0),
)
TRACKS=(
    ("01_unprocessed_physical_microphone.wav","original_raw"),
    ("02_native_speex_aec.wav","native_speex"),
    ("03_guarded_dual_speex_delta.wav","dual_speex_delta_guarded_offline"),
)
PEAK_CEILING_DBFS=-3.0


def checked_pcm_variants(samples:dict[str,np.ndarray],
                         segments=SEGMENTS
                         )->tuple[dict[str,list[np.ndarray]],dict]:
    """Shared attenuation only; preserve relative level across all cases."""
    if set(samples)!=set(x[1] for x in TRACKS):
        raise ValueError("Expected exactly raw, native Speex, guarded delta")
    sizes={len(x) for x in samples.values()}
    if len(sizes)!=1 or next(iter(sizes))<89*RATE:
        raise ValueError("Mismatch/full physical microphone recording missing")
    if any(x.ndim!=1 or not np.all(np.isfinite(x))
           for x in samples.values()):
        raise ValueError("Only finite identical-length mono tracks")
    peak=max(float(np.max(np.abs(array))) for array in samples.values())
    if not 0<peak<.98:
        raise ValueError("Unsafe/silent waveform, refuse listening export")
    gain=min(1.0,10**(PEAK_CEILING_DBFS/20)/peak)
    result={}
    for label,start_s,end_s in segments:
        a,b=round(start_s*RATE),round(end_s*RATE)
        if not 0<a<b<=next(iter(sizes)):
            raise ValueError("Invalid fixed listening segment")
        result[label]=[
            exact_pcm16(samples[key][a:b]*gain)
            for _,key in TRACKS
        ]
    return result,{
        "one_gain_for_ALL_files_in_ALL_segments":True,
        "gain_linear":round(gain,9),
        "gain_db":round(20*math.log10(gain),6),
        "source_global_peak_dbfs":round(db(peak),6),
        "post_gain_peak_ceiling_dbfs":PEAK_CEILING_DBFS,
        "per_variant_or_per_segment_normalization":False,
        "limiter_compressor_noise_suppression_added":False,
    }


def verify_clean_pcm16(raw_pcm:np.ndarray, guarded:np.ndarray,
                       start:int, end:int)->dict:
    """Require actual PCM16 sample identity, not float FFT bit identity.

    The guard uses fftconvolve, which can introduce sub-LSB float
    roundoff even in mathematically zero correction intervals.
    Never smooth, clamp or substitute the corrected speech: fail if
    even one quantized sample differs from the physical microphone.
    """
    if (raw_pcm.dtype!=np.dtype("<i2") or raw_pcm.ndim!=1
        or guarded.ndim!=1 or len(raw_pcm)!=len(guarded)
        or not 0<=start<end<=len(raw_pcm)):
        raise ValueError("Invalid clean physical voice PCM16 comparison")
    baseline=raw_pcm[start:end]
    clean=guarded[start:end]
    candidate=exact_pcm16(clean)
    difference=candidate.astype(np.int32)-baseline.astype(np.int32)
    changed=int(np.count_nonzero(difference))
    if changed:
        raise RuntimeError(
            "Clean physical voice not sample-exact in PCM16: "
            f"{changed} changed samples, "
            f"max {int(np.max(np.abs(difference)))} LSB")
    float_baseline=baseline.astype(np.float64)/32768.
    return {
        "sample_count":len(baseline),
        "pcm16_changed_samples":0,
        "sample_exact_PCM16_before_gain":True,
        "raw_vs_guarded_strict_float_equal":
            bool(np.array_equal(float_baseline,clean)),
        "max_abs_float_delta":
            float(np.max(np.abs(clean-float_baseline))),
    }


def wav_write_exclusive(path:Path,samples:np.ndarray):
    """Never overwrite; Wave does not support 'xb' directly."""
    if (samples.dtype!=np.dtype("<i2") or
        samples.ndim!=1 or
        not samples.flags.c_contiguous or
        not len(samples)):
        raise ValueError("Mono contiguous signed PCM16 required")
    with path.open("xb") as fd:
        with wave.open(fd,"wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(RATE)
            file.writeframes(samples.tobytes())
    with wave.open(str(path),"rb") as file:
        if (file.getnchannels(),file.getsampwidth(),
            file.getframerate(),file.getnframes()) != (
            1,2,RATE,len(samples)):
            raise RuntimeError("WAV header failed readback")


def export(capture_dir:Path,initial_path:Path,consistency_path:Path,
           previous_report:Path,out_dir:Path,
           library_loader=load_speex)->dict:
    capture_dir=capture_dir.expanduser().resolve(strict=True)
    initial_path=initial_path.expanduser().resolve(strict=True)
    consistency_path=consistency_path.expanduser().resolve(strict=True)
    previous_report=previous_report.expanduser().resolve(strict=True)
    audit=AUDIT.resolve(strict=True)
    if any(audit not in p.parents for p in (
        capture_dir,initial_path,consistency_path,previous_report)):
        raise ValueError("Required pinned input not under private .audit")
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("Never overwrite existing audio export")
    parent=out_dir.expanduser().parent.resolve(strict=True)
    if (parent!=capture_dir.parent or
        any(p.parent.parent!=parent for p in (
            initial_path,consistency_path,previous_report))):
        raise ValueError("Must write new sibling of physical M5 evidence")
    raw_path=capture_dir/"microphone-chaos-raw.wav"
    manifest_path=capture_dir/"manifest.json"
    accepted={
        "physical_mic":(raw_path,ACTUAL_MIC_SHA256),
        "capture_manifest":(manifest_path,ACTUAL_MANIFEST_SHA256),
        "initial_alignment":(initial_path,PINNED_FIRST_ALIGNMENT_SHA256),
        "multiwindow_consistency":(consistency_path,PINNED_CONSISTENCY_SHA256),
        "audited_real_speex_report":(
            previous_report,ACTUAL_REAL_SPEEX_REPORT_SHA256),
    }
    if any(digest(p)!=expected for p,expected in accepted.values()):
        raise ValueError("Pinned physical/source numeric SHA256 mismatch")
    capture=json.loads(manifest_path.read_text(encoding="utf-8"))
    first=json.loads(initial_path.read_text(encoding="utf-8"))
    consistency=json.loads(consistency_path.read_text(encoding="utf-8"))
    study=json.loads(previous_report.read_text(encoding="utf-8"))
    if (capture.get("schema")!=SOURCE_SCHEMA
        or capture.get("source_mic_sha256")!=ACTUAL_MIC_SHA256
        or capture.get("status")!="captured_ctrl_c_at_free_stress"
        or first.get("schema")!="cuewire-M5-chaos-reference-onset-lag-diagnostics-v1"
        or consistency.get("schema")!="cuewire-M5-chaos-multiple-window-music-lag-consistency-v1"
        or study.get("schema")!="cuewire-M5-chaos-native-speex-vs-dual-delta-real-physical-v1"
        or study.get("original_sha256",{}).get("original_microphone")!=ACTUAL_MIC_SHA256
        or study.get("original_sha256",{}).get("initial_alignment_report")!=PINNED_FIRST_ALIGNMENT_SHA256
        or study.get("original_sha256",{}).get("multiwindow_consistency_report")!=PINNED_CONSISTENCY_SHA256
        or capture.get("music_stream_volume")!=.60):
        raise ValueError("Inputs do not describe same real M5 chaos study")
    song_path=Path(capture["music_source"]["path"]).expanduser().resolve(strict=True)
    if (capture["music_source"].get("sha256")!=ACTUAL_SONG_SHA256
        or digest(song_path)!=ACTUAL_SONG_SHA256):
        raise ValueError("Pinned original copyrighted music WAV changed")
    raw_pcm=exact_pcm16(read_raw_wave(raw_path,1)[:,0])
    n=len(raw_pcm)
    if n!=capture["physical_microphone_analysis"]["recorded_frames"]:
        raise ValueError("Real 90s microphone frame count mismatch")
    stereo=read_raw_wave(song_path,2,max_frames=47*RATE)
    events=capture["playback_software_start_stop_events"]
    trials=first["separate_process_restarts"]
    far,ref_metadata=build_farend_reference(
        stereo,n,trials,events,
        CHANNEL_MODES["mean_both_restarts"],.60)
    zero=np.zeros(n,dtype=np.int16)
    lib,libpath=library_loader()
    standard,standard_info=run_speex_native(
        raw_pcm,far,library=lib,library_name=libpath)
    null,null_info=run_speex_native(
        raw_pcm,zero,library=lib,library_name=libpath)
    guarded,guard_meta=delta_from_native(
        raw_pcm,standard,null,guard=True)
    raw=raw_pcm.astype(np.float64)/32768.
    signal={
        "original_raw":raw,
        "native_speex":standard,
        "dual_speex_delta_guarded_offline":guarded,
    }
    # Reproduction check against the user's report: NO tolerance-based
    # optimistic success if DSP differs. They are per-physical-phase
    # original/residual RMS, not a clean-speech SNR metric.
    original_cases=study["cases"]["mean_both_restarts"]["phase_summaries"]
    by_phase={p["phase"]:p for p in original_cases}
    phase_data={}
    for phase,start,end in SEGMENTS:
        if phase not in ("03_real_voice_no_music","04_real_full_chaos"):
            continue
        truth=by_phase["clean_voice" if phase.startswith("03") else "free_stress"]
        a,b=truth["approx_original_mic_wav_s"]
        aa,bb=round(a*RATE),round(b*RATE)
        for key,y in signal.items():
            actual=round(db(rms(y[aa:bb])),5)
            declared=truth["processors"][key]["output_rms_dbfs"]
            if abs(actual-declared)>.002:
                raise RuntimeError(
                    f"Native processing not reproducible: {phase}/{key} "
                    f"RMS {actual} != pinned {declared}")
        phase_data[phase]={
            "prior_physical_stage_bounds_s":truth["approx_original_mic_wav_s"],
            "output_rms_reproduced_within_0_002db":True,
        }
    # Validate the actual PCM16 information invariant. FFT-based
    # guarded_echo can leave sub-LSB floating-point residue in OFF
    # intervals without changing even one original PCM16 sample.
    aa,bb=round(36.76171*RATE),round(43.26181*RATE)
    clean_pcm16=verify_clean_pcm16(raw_pcm,guarded,aa,bb)
    waves,gain=checked_pcm_variants(signal)
    # Also verify the complete 37..43s exported clean speech segment,
    # after applying the same global attenuation to all 12 WAVs.
    clean_tracks=waves["03_real_voice_no_music"]
    postgain_changed=int(np.count_nonzero(clean_tracks[0]!=clean_tracks[2]))
    if postgain_changed:
        raise RuntimeError(
            "Clean physical voice changed in shared-gain PCM16 export: "
            f"{postgain_changed} differing samples")
    clean_pcm16["sample_exact_PCM16_after_shared_gain"]=True
    # Rehash all pinned sources BEFORE writing any audio.
    accepted["source_music"]=(song_path,ACTUAL_SONG_SHA256)
    if any(digest(p)!=expected for p,expected in accepted.values()):
        raise RuntimeError("Input evidence mutated during listening reproduction")
    dest=parent/out_dir.name
    dest.mkdir(mode=0o700)
    filelist=[]
    for label,start,end in SEGMENTS:
        sub=dest/label
        sub.mkdir(mode=0o700)
        for (name,variant),pcm in zip(TRACKS,waves[label]):
            file=sub/name
            wav_write_exclusive(file,pcm)
            filelist.append({
                "relative_path":str(file.relative_to(dest)),
                "sha256":digest(file),
                "frames":len(pcm),
                "sample_rate":RATE,
                "sample_width_bytes":2,
                "channels":1,
            })
    result={
        "schema":"cuewire-M5-private-real-doubletalk-listening-AB-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "status":"manually_authorized_private_WAVs_not_auto_played",
        "previous_real_speex_numeric_sha256":ACTUAL_REAL_SPEEX_REPORT_SHA256,
        "input_sha256":{name:expected for name,(_,expected) in accepted.items()},
        "native_speex_library":libpath,
        "native_full_mic_processor":standard_info,
        "native_zero_reference_processor":null_info,
        "guarded_delta":guard_meta,
        "reference":ref_metadata,
        "global_playback_gain":gain,
        "listening_segments":list(SEGMENTS),
        "files":filelist,
        "prior_numeric_phase_checks":phase_data,
        "clean_voice_pcm16_preservation":clean_pcm16,
        "physical_speech_during_music":True,
        "same_utterance_clean_reference_in_music":False,
        "local_speech_quality_proved":False,
        "zero_processing_of_speaker_off_real_voice":True,
        "originals_modified":False,
        "automatic_playback":False,
        "automatic_microphone_or_routing_changes":False,
        "notes":[
            "These files contain the actual human near-end speech, not digitally injected synthetic voice.",
            "At music-ON times, no recording of the same utterance without loudspeaker echo exists, so perceptual A/B is subjective.",
            "Listening changes from the original cannot be called removed echo without considering speech distortion.",
            "All 12 WAVs share one global attenuation-only gain; no per-case gain changes.",
            "The 513-tap symmetric 3800Hz correction filter is NONCAUSAL and only valid for offline preview.",
            "The stereo mean(L,R) and both playback lags are from prior inspected real recording; they are not proved optimal.",
            "Private user's voice and copyrighted music must not be committed or publicly shared.",
        ],
    }
    report=dest/"listening-report.json"
    with report.open("x",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,sort_keys=True,indent=2)
        f.write("\n")
    with (dest/"SHA256SUMS").open("x",encoding="utf-8") as f:
        for item in filelist:
            f.write(item["sha256"]+"  "+item["relative_path"]+"\n")
        f.write(digest(report)+"  listening-report.json\n")
    print("Real physical near-end chaotic A/B exported, never played:")
    for label,_,_ in SEGMENTS:
        print(" ",label)
        for filename,_ in TRACKS:
            print("   ",filename)
    print("All WAVs share ONE gain:",gain["gain_db"],"dB")
    print("Original WAVs untouched. Output:",dest)
    return result


def main()->None:
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture",type=Path,required=True)
    p.add_argument("--initial-alignment",type=Path,required=True)
    p.add_argument("--consistency",type=Path,required=True)
    p.add_argument("--previous-report",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    try:
        export(a.capture,a.initial_alignment,a.consistency,
               a.previous_report,a.out)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,wave.Error) as e:
        p.exit(1,"ERROR: "+str(e)+"\n")


if __name__=="__main__":
    main()
