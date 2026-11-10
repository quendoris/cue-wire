#!/usr/bin/env python3
"""CueWire M5: explicitly requested PRIVATE listening A/B audio export.

This script is the EXCEPTION to numeric-only diagnostic reports: user
explicitly asked to LISTEN to original with speaker echo/music+voice,
the previous external Speex benchmark, and best reference-differential
Speex with previously selected OFFLINE 3800Hz guarded correction.

Exactly reproduce the two six-second digital speech injections
(-50 and -39 dBFS) from previous native Speex delta experiment,
using already private original physical M5 0.30 speaker-only WAV,
unchanged digital far-end reference, and separate M4 clean voice RAW.
Mix is SYNTHETIC near-end speech, NOT physical simultaneous talk.

Exports 3x2 MONO PCM16 WAVs, only to a new user-private .audit
directory. All matched A/B files in each voice-level case receive
ONE SHARED gain computed from their joint maximum peak, with a
-3 dBFS safe peak target. No per-file loudness normalization,
limiting, compressor or gain that masks real A/B differences.

NO DEVICE ACCESS, NO AUTOMATIC PLAYBACK, NO PipeWire actions,
NO system sink/default/routing changes, NO background tasks,
NO modifications to original raw files, no writing into repo
working tree, no upload or Git commit of voice/music WAVs.
The normal voice and delta are processed from clean RESET native
Speex states on the same full microphone PCM timeline.
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

from voice_m5_aec import AUDIT, RATE, db, digest, read_wav, rms
from voice_m5_frozen_heldout import indices
from voice_m5_echo_floor import EVAL_S
from voice_m5_playback_coherence import (
    checked_take, LOW_MANIFEST_SHA, HIGH_MANIFEST_SHA,
    LOW_MIC_SHA, HIGH_MIC_SHA, REFERENCE_SHA, SOURCE_MANIFEST_SHA,
)
from voice_m5_known_stem import (
    read_voice_excerpt, validate_m4_voice_manifest,
)
from voice_m5_speex_baseline import (
    exact_pcm16, render_reference_on_mic_timeline,
    run_speex_native, load_speex,
    LAG_015_SAMPLES, LAG_030_SAMPLES,
)
from voice_m5_speex_known_voice import (
    construct_mixture, VOICE_TARGETS_DBFS, VOICE_SECONDS,
)
from voice_m5_speex_delta import (
    delta_from_native, M4_VOICE_START_S,
)

# Audited actual native dual-Speex study; never use arbitrary RAW
# without checking this study's SHA256-pinned original sources.
DELTA_REPORT_SHA256 = (
    "c7753dea1ff557bcd3e56073f3d57ad34c28ffb3ea0c7212bbdac9276d6e13cf"
)
OUTPUT_PEAK_DBFS = -3.0
WAV_NAMES = (
    "01_original_music_echo_and_injected_voice.wav",
    "02_native_speex_aec_and_injected_voice.wav",
    "03_cuewire_guarded_speex_delta_and_injected_voice.wav",
)


def matched_pcm16(variants: tuple[np.ndarray, ...]
                  ) -> tuple[list[np.ndarray], dict]:
    """Apply one COMMON playback gain, never normalize takes separately."""
    if len(variants) != 3 or any(
        a.ndim != 1 or len(a) != round(VOICE_SECONDS*RATE) or
        not np.all(np.isfinite(a)) for a in variants
    ):
        raise ValueError("Expected exactly three finite equal-length six-second mono tracks")
    peak = max(float(np.max(np.abs(x))) for x in variants)
    if peak < 1e-7 or peak >= .98:
        raise ValueError("Original/processed signals silent or unsafe peak")
    gain = (10**(OUTPUT_PEAK_DBFS/20))/peak
    result = []
    for x in variants:
        y = x*gain
        if np.max(np.abs(y)) >= .99:
            raise ValueError("Listening normalization produced unsafe peak")
        result.append(exact_pcm16(y))
    return result, {
        "same_linear_amplitude_gain_applied_to_all_three": True,
        "per_file_normalization": False,
        "shared_gain_linear": round(gain, 9),
        "shared_gain_db": round(20*math.log10(gain), 6),
        "joint_pre_gain_peak_dbfs": round(db(peak), 5),
        "joint_post_gain_target_peak_dbfs": OUTPUT_PEAK_DBFS,
        "no_limiter_equalizer_compressor_or_per_file_gain": True,
    }


def write_wav(path: Path, pcm: np.ndarray) -> None:
    if (pcm.ndim != 1 or len(pcm) != round(VOICE_SECONDS*RATE)
            or pcm.dtype != np.dtype("<i2")
            or not pcm.flags.c_contiguous):
        raise ValueError("Only mono 48k PCM16 exactly 6s is permitted")
    with path.open("xb") as file:
        with wave.open(file, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(pcm.tobytes())
    with wave.open(str(path),"rb") as verify:
        if ((verify.getnchannels(), verify.getsampwidth(),
             verify.getframerate(), verify.getnframes(),
             verify.getcomptype())
            != (1,2,RATE,round(VOICE_SECONDS*RATE),"NONE")):
            raise RuntimeError("Export WAV header failed roundtrip")


def render_one(reference: np.ndarray, mic: np.ndarray,
               voice: np.ndarray, volume: float,
               lag: int, native, library_name: str
               ) -> tuple[list[tuple[str, list[np.ndarray], dict]], dict]:
    """Return matched PCM16 triples, reference time exactly 10..16s."""
    if (len(reference) != 20*RATE or
            not math.isclose(volume,.15,abs_tol=1e-10) and
            not math.isclose(volume,.30,abs_tol=1e-10)):
        raise ValueError("Only archived .15 or .30 physical M5 captures")
    start,end = indices(EVAL_S,lag,len(mic))
    far = render_reference_on_mic_timeline(
        reference, len(mic), lag, volume)
    zero = np.zeros(len(mic), dtype=np.int16)
    render_data=[]
    for voice_level in VOICE_TARGETS_DBFS:
        injected_pcm, stem, stem_meta=construct_mixture(
            mic,voice,start,end,voice_level)
        original=injected_pcm[start:end].astype(np.float64)/32768.
        normal,metadata=run_speex_native(
            injected_pcm, far, library=native, library_name=library_name)
        null,zero_meta=run_speex_native(
            injected_pcm, zero, library=native, library_name=library_name)
        # Important: guard requires >=10 seconds; correction is
        # computed over entire ~36s stream BEFORE slicing 6s preview.
        delta_guarded,guard_meta=delta_from_native(
            injected_pcm,normal,null,guard=True)
        tracks=(original,normal[start:end],delta_guarded[start:end])
        matched,gain_meta=matched_pcm16(tracks)
        label = f"voice_{int(voice_level)}dbfs"
        render_data.append((label,matched,{
            "voice_target_rms_dbfs":voice_level,
            "synthetic_voice":stem_meta,
            "reference_time_s":list(EVAL_S),
            "physical_microphone_wav_interval_s":[round(start/RATE,6),round(end/RATE,6)],
            "native_reference_on":metadata,
            "native_reference_zero":zero_meta,
            "guard":guard_meta,
            "export_shared_gain":gain_meta,
            "digitized_stem_rms_dbfs":round(db(rms(stem)),5),
        }))
    return render_data,{
        "playback_volume":volume,
        "fixed_historical_lag_samples":lag,
        "voice_excerpt_m4_raw_start_s":M4_VOICE_START_S,
        "source_speaker_only_mic_contains_physical_music_echo":True,
        "voice_added_by_software_after_recording":True,
        "physical_nearend_simultaneous_doubletalk":False,
    }


def export(capture_dir: Path, voice_dir: Path,
           reference_report: Path, out_dir: Path,
           volume: float = .30, native_loader=load_speex) -> dict:
    capture_dir=capture_dir.expanduser().resolve(strict=True)
    voice_dir=voice_dir.expanduser().resolve(strict=True)
    reference_report=reference_report.expanduser().resolve(strict=True)
    audit=AUDIT.resolve(strict=True)
    if any(audit not in p.parents
           for p in (capture_dir,voice_dir,reference_report)):
        raise ValueError("All original recordings and report must be private .audit")
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("Listening output exists; refusing overwrite")
    parent=out_dir.expanduser().parent.resolve(strict=True)
    if parent!=capture_dir.parent or reference_report.parent.parent!=parent:
        raise ValueError("Private listening output and prior report must share .audit/voice-aec")
    if digest(reference_report)!=DELTA_REPORT_SHA256:
        raise ValueError("Verified previous dual-Speex numeric report SHA256 mismatch")
    historic=json.loads(reference_report.read_text(encoding="utf-8"))
    if (historic.get("schema")!="cuewire-M5-SpeexDSP-dual-branch-reference-delta-v1"
            or historic.get("voice_nearend_only_synthetic") is not True
            or historic.get("approved_for_live_mic") is not False):
        raise ValueError("Wrong provenance for source numerical experiment")
    if math.isclose(volume,.30,abs_tol=1e-10):
        lag=LAG_030_SAMPLES
        expected_manifest=HIGH_MANIFEST_SHA
        expected_mic=HIGH_MIC_SHA
        vol_key="speaker_only_and_voice_report_030"
        suffix="level030"
    elif math.isclose(volume,.15,abs_tol=1e-10):
        lag=LAG_015_SAMPLES
        expected_manifest=LOW_MANIFEST_SHA
        expected_mic=LOW_MIC_SHA
        vol_key="speaker_only_and_voice_report_015"
        suffix="level015"
    else:
        raise ValueError("Only source level .15 or .30 accepted")
    manifest, hashes, mic=checked_take(
        capture_dir,expected_manifest,expected_mic,volume)
    if (historic["source_sha256"][f"{suffix}_manifest"]!=hashes["manifest"]
            or historic["source_sha256"][f"{suffix}_raw_mic"]!=hashes["physical_mic_raw"]
            or historic[vol_key]["historical_fixed_lag_samples"]!=lag):
        raise ValueError("Physical capture incompatible with audited delta run")
    ref_dir=Path(manifest["reference_source_capture"]).expanduser().resolve(strict=True)
    if audit not in ref_dir.parents:
        raise ValueError("Linked music reference outside private .audit")
    music_file=ref_dir/"reference-sent.wav"
    music_meta=ref_dir/"manifest.json"
    if (digest(music_file)!=REFERENCE_SHA or
            digest(music_meta)!=SOURCE_MANIFEST_SHA or
            historic["source_sha256"]["digital_reference"]!=REFERENCE_SHA or
            historic["source_sha256"]["digital_reference_manifest"]!=SOURCE_MANIFEST_SHA):
        raise ValueError("Pinned 20s musical reference SHA256 does not match")
    reference=read_wav(music_file)
    mf=voice_dir/"manifest.json"
    voice_file=voice_dir/"raw.wav"
    metadata=json.loads(mf.read_text(encoding="utf-8"))
    voice_expected,voice_schema,voice_backend=validate_m4_voice_manifest(metadata)
    if (digest(voice_file)!=voice_expected or
            historic["source_sha256"]["m4_voice_raw"]!=voice_expected or
            historic["source_sha256"]["m4_voice_manifest"]!=digest(mf)):
        raise ValueError("M4 physical voice RAW/manifest SHA256 mismatch")
    voice=read_voice_excerpt(voice_file,M4_VOICE_START_S,VOICE_SECONDS)
    if len(voice)!=round(VOICE_SECONDS*RATE):
        raise ValueError("Expected M4 exactly six seconds at chosen offset")

    lib,libname=native_loader()
    rendered,parameters=render_one(
        reference,mic,voice,volume,lag,lib,libname)
    sources={
        "source_mic_raw":(capture_dir/"microphone-aba-raw.wav",
                          hashes["physical_mic_raw"]),
        "source_mic_manifest":(capture_dir/"manifest.json",hashes["manifest"]),
        "digital_music_reference":(music_file,REFERENCE_SHA),
        "digital_music_manifest":(music_meta,SOURCE_MANIFEST_SHA),
        "m4_clean_voice_raw":(voice_file,voice_expected),
        "m4_clean_voice_manifest":(mf,digest(mf)),
        "reference_delta_numeric_report":(reference_report,DELTA_REPORT_SHA256),
    }
    if any(digest(file)!=expected for file,expected in sources.values()):
        raise RuntimeError("Original source changed during export: refusing WAV write")
    # All validations completed before creation of output WAVs.
    final_dir=parent/out_dir.name
    final_dir.mkdir(mode=0o700)
    output_files=[]
    for label, pcm_files, case in rendered:
        case_dir=final_dir/label
        case_dir.mkdir(mode=0o700)
        for name,pcm in zip(WAV_NAMES,pcm_files):
            path=case_dir/name
            write_wav(path,pcm)
            output_files.append({
                "relative_path":str(path.relative_to(final_dir)),
                "sha256":digest(path),
                "frames":len(pcm),
                "format":"mono PCM16 48kHz",
            })
    report={
        "schema":"cuewire-M5-private-listening-AB-from-verified-delta-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "status":"requested_playback_artifacts_exported_not_auto_played",
        "previous_numeric_delta_sha256":DELTA_REPORT_SHA256,
        "original_sources_sha256":{name:sha for name,(_,sha) in sources.items()},
        "native_speex_library":libname,
        "reproduction_parameters":parameters,
        "m4_voice_raw_schema":voice_schema,
        "m4_voice_backend":voice_backend,
        "case_settings":{name:case for name,_,case in rendered},
        "files":output_files,
        "no_external_microphone_recording":True,
        "originals_unchanged":True,
        "automatically_played":False,
        "monitoring_or_audio_route_changed":False,
        "voice_recording_uploaded":False,
        "warnings":[
            "These two playback conditions combine original physical speaker echo with separately recorded, digitally injected clean M4 voice. NOT real simultaneous physical doubletalk.",
            "Three tracks within each condition have the identical common gain applied so A/B differences are not normalized away.",
            "Do not judge differences solely by wholeband loudness or a single listener's perception; objective tests remain separate.",
            "The guarded differential uses noncausal 513-tap zero-phase 3800Hz echo-estimate-only filter and is offline-only.",
            "Audio contains private voice and potentially copyrighted music: keep under private .audit; do not commit or upload it publicly.",
            "No changes to PipeWire volume, routing or default sinks; manually preview WAV only when desired.",
        ],
    }
    report_file=final_dir/"listening-manifest.json"
    with report_file.open("x",encoding="utf-8") as f:
        json.dump(report,f,sort_keys=True,ensure_ascii=False,indent=2)
        f.write("\n")
    output_files.append({
        "relative_path":"listening-manifest.json",
        "sha256":digest(report_file),
    })
    # All locally emitted files except SHA256SUMS, which is self-referential.
    with (final_dir/"SHA256SUMS").open("x",encoding="utf-8") as f:
        for file in output_files:
            f.write(file["sha256"]+"  "+file["relative_path"]+"\n")
    print("Private CueWire M5 listening A/B: physical echo + digitally mixed M4 voice")
    for item in output_files:
        print("  ",item["relative_path"])
    print("All 3 tracks per voice level share ONE gain. Playback NOT started.")
    print("No original WAVs/manifest changed; no PipeWire controls touched.")
    print("Report:",final_dir)
    return report


def main()->None:
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture",type=Path,required=True,
                   help="Exactly SHA256-pinned M5-level-030-001 or M5-level-015-001")
    p.add_argument("--voice",type=Path,required=True,
                   help="Original pinned M4-R0-ALSA-007 private RAW folder")
    p.add_argument("--delta-report",type=Path,required=True,
                   help="Original SHA256-verified M5-speex-delta-001/speex-delta.json")
    p.add_argument("--volume",type=float,choices=(.15,.30),default=.30)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    try:
        export(a.capture,a.voice,a.delta_report,a.out,volume=a.volume)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as e:
        p.exit(1,"ERROR: "+str(e)+"\n")


if __name__=="__main__":
    main()
