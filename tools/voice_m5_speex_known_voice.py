#!/usr/bin/env python3
"""CueWire M5: exact clean near-end synthetic voice perturbation of native Speex.

Uses the two SHA256-pinned speaker-only M5 physical A/B/A captures and
one user-supplied SHA256-manifest-verified M4 RAW voice-only capture.
Add 6s of *digitally mixed* voice ONLY to source reference 10..16s.
Replay native Speex DSP from RESET per take, voice level and render
condition: real speaker reference and exactly ZERO reference.

For a processor F, compute incremental voice response
    F(original mic + digitized voice, reference)
  - F(original mic, reference)
against exactly the PCM16-injected voice stem. Report voice-response
gain/error/SNR and total mixture-to-known-stem interference. Compare
to original CueWire guarded reference-only FIR (speech incremental
response is mathematically unchanged).

This is synthetic speaker + voice interaction only; NOT physical
simultaneous double-talk or proof of real voice intelligibility.
Original physical WAVs/M4 are read-only SHA256-verified.
No audio device access, PipeWire manipulation, playback, loopback,
processed WAV, autostart, or background daemons. No production gate.
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
from voice_m5_frozen_heldout import fit_frozen, indices
from voice_m5_guarded_fir import guarded_echo
from voice_m5_known_stem import (
    read_voice_excerpt, validate_m4_voice_manifest, VOICE_OFFSET_S,
)
from voice_m5_echo_floor import EVAL_S
from voice_m5_playback_coherence import (
    checked_take, LOW_MANIFEST_SHA, HIGH_MANIFEST_SHA,
    LOW_MIC_SHA, HIGH_MIC_SHA, REFERENCE_SHA, SOURCE_MANIFEST_SHA,
)
from voice_m5_speex_baseline import (
    exact_pcm16, render_reference_on_mic_timeline, run_speex_native,
    load_speex, LAG_015_SAMPLES, LAG_030_SAMPLES,
)

# Prespecified two near-end speech levels; no tuning on output metrics.
VOICE_TARGETS_DBFS = (-50.0, -39.0)
VOICE_SECONDS = 6.0
BANDS_HZ = ((500, 1500), (1500, 4000), (4000, 8000), (8000, 16000))


def score_incremental_voice(stem: np.ndarray,
                            response: np.ndarray) -> dict:
    """Known digital perturbation: avoid conflating echo cancellation and speech."""
    if (stem.ndim != 1 or response.ndim != 1 or
            len(stem) != len(response) or len(stem) < 3*RATE or
            not np.all(np.isfinite(stem)) or
            not np.all(np.isfinite(response))):
        raise ValueError("Expected finite matching >=3s voice and response")
    sp = float(np.dot(stem, stem))
    if sp < 1e-12:
        raise ValueError("Injected digital voice stem is silent")
    err = response - stem
    power_err = float(np.dot(err, err))
    rms_error = math.sqrt(power_err/len(err))
    input_rms = rms(stem)
    gain = float(np.dot(response, stem)/sp)
    result = {
        "known_injected_voice_rms_dbfs": round(db(input_rms), 5),
        "incremental_voice_response_rms_dbfs": round(db(rms(response)), 5),
        "incremental_voice_transfer_gain": round(gain, 7),
        "incremental_voice_transfer_gain_db":
            round(20*math.log10(max(abs(gain),1e-16)),5),
        "incremental_voice_response_error_rms_dbfs":
            round(db(rms_error),5),
        "incremental_voice_response_snr_db":
            round(10*math.log10(sp/max(power_err,1e-24)),5),
        "mean_square_response_error_ratio_to_voice":
            round(power_err/max(sp,1e-24),9),
    }
    per_band=[]
    for lo,hi in BANDS_HZ:
        sos=signal.butter(4,[lo,hi],btype="bandpass",
                          fs=RATE,output="sos")
        voice_band=signal.sosfiltfilt(sos,stem)
        actual_band=signal.sosfiltfilt(sos,response)
        band_power=float(np.dot(voice_band,voice_band))
        diff_power=float(np.dot(actual_band-voice_band,
                                actual_band-voice_band))
        if band_power < 1e-13:
            per_band.append({"band_hz":[lo,hi],"score_available":False,
                             "reason":"near-end voice band energy too small"})
            continue
        per_band.append({
            "band_hz":[lo,hi],
            "score_available":True,
            "near_end_voice_band_rms_dbfs":round(db(rms(voice_band)),5),
            "near_end_incremental_gain":round(
                float(np.dot(actual_band,voice_band)/band_power),7),
            "near_end_transfer_error_snr_db":
                round(10*math.log10(
                    band_power/max(diff_power,1e-24)),5)
        })
    result["frequency_bands"]=per_band
    return result


def known_stem_interference(known: np.ndarray,
                            actual_output: np.ndarray) -> dict:
    if len(known)!=len(actual_output) or not len(known):
        raise ValueError("Clean stem and actual observed signal must match")
    undesired = actual_output-known
    return {
        "known_voice_stem_rms_dbfs":round(db(rms(known)),5),
        "interference_relative_to_clean_stem_rms_dbfs":
            round(db(rms(undesired)),5),
        "known_stem_snr_db":round(
            10*math.log10(
                max(rms(known)**2,1e-24)/
                max(rms(undesired)**2,1e-24)),5)
    }


def construct_mixture(mic: np.ndarray, voice: np.ndarray,
                      mic_start: int, mic_end: int,
                      target_level_dbfs: float
                      ) -> tuple[np.ndarray, np.ndarray, dict]:
    """Mix 6s voice into ALREADY physical speaker-only mic on 10..16s."""
    if (mic.ndim!=1 or voice.ndim!=1 or
            mic_end-mic_start!=round(VOICE_SECONDS*RATE) or
            len(voice)<mic_end-mic_start or
            not np.isfinite(target_level_dbfs) or
            not -65<=target_level_dbfs<=-20):
        raise ValueError("Invalid bounded 6s known-voice synthetic injection")
    excerpt=voice[:mic_end-mic_start]
    level=db(rms(excerpt))
    if level<-65 or float(np.max(np.abs(excerpt))) >= 32767/32768:
        raise ValueError("M4 known voice excerpt too quiet/clipped")
    factor=10**((target_level_dbfs-level)/20)
    stem=excerpt*factor
    modified=mic.copy()
    modified[mic_start:mic_end] += stem
    if np.max(np.abs(modified))>=.98:
        raise ValueError("Synthetic doubletalk peak unsafe; refusing clipping")
    # Actual PCM16 input difference is the ground-truth injected voice
    # signal, including rounding, not an unrealistic ideal float signal.
    raw=exact_pcm16(mic)
    mixed=exact_pcm16(modified)
    real_stem=(mixed[mic_start:mic_end].astype(np.int32)-
               raw[mic_start:mic_end].astype(np.int32)).astype(np.float64)/32768.
    if rms(real_stem)<1e-6:
        raise ValueError("Digitized injected near-end voice is silent")
    return mixed, real_stem, {
        "original_excerpt_rms_dbfs":round(level,5),
        "target_voice_rms_dbfs":target_level_dbfs,
        "digitized_injected_voice_rms_dbfs":round(db(rms(real_stem)),5),
        "voice_scale":round(factor,8),
        "voice_samples":len(real_stem),
        "real_mic_or_room_nearend_speech_simulated":False,
    }


def process_take(reference: np.ndarray, mic: np.ndarray,
                 voice: np.ndarray, lag: int,
                 volume: float, lib, lib_name: str) -> dict:
    start,end=indices(EVAL_S,lag,len(mic))
    if end-start!=round(VOICE_SECONDS*RATE):
        raise AssertionError("M5 reference evaluation not fixed at six seconds")
    original_pcm=exact_pcm16(mic)
    real_far=render_reference_on_mic_timeline(reference,len(mic),lag,volume)
    zero_far=np.zeros(len(original_pcm),dtype=np.int16)
    raw_reference_voice=mic[start:end]

    # The frozen CueWire echo predictor is fitted ONLY on the physical
    # speaker-only earlier 3..9s. It is independent of injected stem.
    predicted, model=fit_frozen(reference,mic,lag)
    protected, guard_meta=guarded_echo(predicted)
    cue_speakeronly=raw_reference_voice-protected[start:end]
    speaker_snr={"reference_only_guarded_FIR": {
        "speaker_only_residual_rms_dbfs":round(db(rms(cue_speakeronly)),5),
        "trained_with_injected_voice":False,
        "echo_only_3800hz_noncausal_guard":True,
    }}
    base={}
    for mode,far in (("real_far_end",real_far),("zero_far_end",zero_far)):
        base_out,base_processor=run_speex_native(
            original_pcm,far,library=lib,library_name=lib_name)
        base[mode]=(base_out,far)
        speaker_snr[mode]={
            "speaker_only_residual_rms_dbfs":
                round(db(rms(base_out[start:end])),5),
            "base_processing":base_processor,
        }

    voice_cases=[]
    for target in VOICE_TARGETS_DBFS:
        mixed,stem,stem_meta=construct_mixture(
            mic,voice,start,end,target)
        float_mixed=mixed.astype(np.float64)/32768.
        # Exact reference-only guarded FIR cannot touch the incremental
        # known-stem signal. The quantified difference from a float
        # clean voice is precisely quantization, not a speech enhancer.
        cue_with=float_mixed[start:end]-protected[start:end]
        cue_response=cue_with-cue_speakeronly
        case={
            "voice":stem_meta,
            "raw_synthetic_mixture_snr":
                known_stem_interference(stem,float_mixed[start:end]),
            "cuewire_guarded_fir": {
                "incremental_voice_transfer":
                    score_incremental_voice(stem,cue_response),
                "known_stem_output_snr":
                    known_stem_interference(stem,cue_with),
                "baseline_speaker_only_residual_rms_dbfs":
                    speaker_snr["reference_only_guarded_FIR"][
                        "speaker_only_residual_rms_dbfs"],
            },
        }
        for mode in ("real_far_end","zero_far_end"):
            base_out,far=base[mode]
            wet,stats=run_speex_native(
                mixed,far,library=lib,library_name=lib_name)
            wet_part=wet[start:end]
            # Incremental voice response includes adaptation STATE
            # changes caused by injected synthetic speech.
            response=wet_part-base_out[start:end]
            case[mode]={
                "incremental_voice_transfer":
                    score_incremental_voice(stem,response),
                "known_stem_output_snr":
                    known_stem_interference(stem,wet_part),
                "speakerdetector_nearend_interaction_mode":
                    "digital_synthetic_only",
                "processing_stats":stats,
            }
        voice_cases.append(case)
    return {
        "per_stream_volume":volume,
        "lag_fixed_historical_samples":lag,
        "speech_injection_only_mic_wav_interval_s":
            [round(start/RATE,6),round(end/RATE,6)],
        "reference_source_eval_s":list(EVAL_S),
        "speaker_only_control":speaker_snr,
        "voice_levels":voice_cases,
        "cuewire_frozen_fir_train_only":model,
        "cuewire_guard_config":guard_meta,
        "physical_simultaneous_doubletalk":False,
        "speex_reinitialized_per_condition":True,
    }


def analyze(low_dir: Path, high_dir: Path,
            voice_dir: Path, out_dir: Path,
            voice_start_s: float=VOICE_OFFSET_S,
            lib_loader=load_speex) -> dict:
    low_dir=low_dir.expanduser().resolve(strict=True)
    high_dir=high_dir.expanduser().resolve(strict=True)
    voice_dir=voice_dir.expanduser().resolve(strict=True)
    audit=AUDIT.resolve(strict=True)
    if (low_dir==high_dir or any(
            audit not in p.parents for p in (low_dir,high_dir,voice_dir))):
        raise ValueError("Must use private physical M5 captures and separate M4 RAW")
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("New private output required; no overwrite")
    parent=out_dir.expanduser().parent.resolve(strict=True)
    if parent!=high_dir.parent:
        raise ValueError("New report must be alongside high-volume M5 capture")
    ml,hl,yl=checked_take(low_dir,LOW_MANIFEST_SHA,LOW_MIC_SHA,.15)
    mh,hh,yh=checked_take(high_dir,HIGH_MANIFEST_SHA,HIGH_MIC_SHA,.30)
    for field in ("source_mic_name","speaker_sink_name","reference_source_capture"):
        if ml[field]!=mh[field]:
            raise ValueError("Different physical devices or M5 digital reference")
    refdir=Path(ml["reference_source_capture"]).expanduser().resolve(strict=True)
    if audit not in refdir.parents:
        raise ValueError("M5 digital reference outside .audit")
    source=refdir/"reference-sent.wav"
    source_mf=refdir/"manifest.json"
    if digest(source)!=REFERENCE_SHA or digest(source_mf)!=SOURCE_MANIFEST_SHA:
        raise ValueError("M5 source reference SHA256 mismatch")
    music=read_wav(source)
    if len(music)!=20*RATE:
        raise ValueError("Incorrect M5 music reference duration")
    manifest_path=voice_dir/"manifest.json"
    voice_meta=json.loads(manifest_path.read_text(encoding="utf-8"))
    voice_hash,voice_schema,voice_backend=validate_m4_voice_manifest(voice_meta)
    voice_file=voice_dir/"raw.wav"
    if digest(voice_file)!=voice_hash:
        raise ValueError("Original M4 RAW voice SHA256 mismatch")
    if not math.isfinite(voice_start_s) or voice_start_s<0 or voice_start_s>280:
        raise ValueError("M4 voice excerpt start is outside allowed range")
    clean_voice=read_voice_excerpt(voice_file,voice_start_s,VOICE_SECONDS)
    if len(clean_voice)<round(VOICE_SECONDS*RATE):
        raise ValueError("Need full 6-second clean M4 voice for symmetric comparison")
    lib,libname=lib_loader()
    low=process_take(music,yl,clean_voice,LAG_015_SAMPLES,.15,lib,libname)
    high=process_take(music,yh,clean_voice,LAG_030_SAMPLES,.30,lib,libname)
    sources={
        "level015_manifest":(low_dir/"manifest.json",hl["manifest"]),
        "level015_raw_mic":(low_dir/"microphone-aba-raw.wav",hl["physical_mic_raw"]),
        "level030_manifest":(high_dir/"manifest.json",hh["manifest"]),
        "level030_raw_mic":(high_dir/"microphone-aba-raw.wav",hh["physical_mic_raw"]),
        "digital_reference":(source,REFERENCE_SHA),
        "digital_reference_manifest":(source_mf,SOURCE_MANIFEST_SHA),
        "m4_voice_manifest":(manifest_path,digest(manifest_path)),
        "m4_voice_raw":(voice_file,voice_hash),
    }
    if any(digest(p)!=expected for p,expected in sources.values()):
        raise RuntimeError("Original private WAV or manifest changed during experiment")
    report={
        "schema":"cuewire-M5-SpeexDSP-incremental-synthetic-nearend-voice-v1",
        "status":"synthetic_known_stem_no_physical_doubletalk_or_production_pass",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "source_sha256":{key:sha for key,(p,sha) in sources.items()},
        "native_library":libname,
        "m4_voice_capture_schema":voice_schema,
        "m4_voice_capture_backend":voice_backend,
        "m4_voice_excerpt_start_seconds":voice_start_s,
        "m4_voice_excerpt_duration_seconds":VOICE_SECONDS,
        "voice_target_rms_dbfs":list(VOICE_TARGETS_DBFS),
        "mic_playback_015":low,
        "mic_playback_030":high,
        "physical_voice_capture_during_music":False,
        "physical_microphone_agc_during_doubletalk_simulated":False,
        "voice_preservation_certified":False,
        "approved_for_live_virtual_mic":False,
        "no_aec_hybrid_cascaded_or_tuned":True,
        "prospective_old_0_67881_db_gate_still_failed":True,
        "audio_export_count":0,
        "warnings":[
            "M4 near-end clean voice was recorded separately, not simultaneously with M5 music/speaker-only physical microphone.",
            "Synthetic digital addition does not reproduce physical mic AGC/DSP or acoustic interaction with a near-end speaker.",
            "Speex is nonlinear/stateful: its increment under voice measures combined input and adaptive state response.",
            "No voice-data-driven tuning, no live microphone, no WAV output, no system PipeWire routing or volume modification.",
            "The CueWire guarded FIR uses a centered noncausal 513-tap echo-only guard: offline research only.",
            "Clean voice stem response is not PESQ/STOI/MOS, listening quality or calibrated near-end speaker truth.",
            "This uses prior investigated M5 and M4 sources, NOT an independent prospective experiment.",
            "Speex real-far vs zero-far is an algorithmic ablation, not a clean physical echo-only ground truth.",
            "Any future serial AEC hybrid must prove it does not damage near-end speech and avoid double subtraction.",
        ],
    }
    out=parent/out_dir.name
    out.mkdir(mode=0o700)
    dest=out/"known-voice-speex.json"
    with dest.open("x",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,sort_keys=True,indent=2)
        f.write("\n")
    with (out/"SHA256SUMS").open("x",encoding="utf-8") as f:
        f.write(digest(dest)+"  known-voice-speex.json\n")
    print("M5 native Speex known near-end synthetic-only speech experiment")
    for label,take in (("0.15",low),("0.30",high)):
        for case in take["voice_levels"]:
            print(label,"voice",case["voice"]["target_voice_rms_dbfs"],"dBFS")
            for mode in ("cuewire_guarded_fir","real_far_end","zero_far_end"):
                result=case[mode]["incremental_voice_transfer"]
                signal_snr=case[mode]["known_stem_output_snr"]
                print(" ",mode,"voice transfer gain",
                      result["incremental_voice_transfer_gain"],
                      "voice transfer error SNR",
                      result["incremental_voice_response_snr_db"],
                      "total known-stem SNR",signal_snr["known_stem_snr_db"])
    print("This is synthetic injection, NOT real physical doubletalk.")
    print("No original audio modified or exported; old FAIL preserved.")
    print("Report:",dest)
    return report


def main() -> None:
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--low-capture",type=Path,required=True)
    p.add_argument("--high-capture",type=Path,required=True)
    p.add_argument("--voice",type=Path,required=True)
    p.add_argument("--voice-start-s",type=float,default=VOICE_OFFSET_S)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    try:
        analyze(args.low_capture,args.high_capture,args.voice,args.out,
                voice_start_s=args.voice_start_s)
    except (ValueError,OSError,RuntimeError,KeyError,TypeError) as e:
        p.exit(1,"ERROR: "+str(e)+"\n")


if __name__=="__main__":
    main()
