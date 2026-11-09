#!/usr/bin/env python3
"""M5 external SpeexDSP AEC: silence-reference control on EXISTING physical WAVs.

A previous native SpeexDSP test with the real far-end reference produced
1.79157/3.79385 dB wholeband heldout ERLE. Even in speaker-OFF segments
the output differed, especially in 70..180 Hz. Actual SpeexDSP MDF source
includes mic DC notch filtering before subtraction.

Run the SAME 10ms/120ms native Speex AEC with exactly ZERO far-end PCM
throughout each existing 36s private capture, including music-ON seconds.
Compare resulting output to the SHA256-LOCKED previous native Speex
reference-ON report. This identifies source-independent processing
effects in this test, NOT human voice preservation or a causal
acoustic echo-only ground truth.

No microphone or speaker device access. No playback, no PipeWire
modules, no autostart, no processed audio/WAV output, no changes to
existing source or report, no filter fitting on heldout.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import numpy as np

from voice_m5_aec import AUDIT, RATE, digest, read_wav
from voice_m5_frozen_heldout import indices, erle_db
from voice_m5_echo_floor import EVAL_S, linear_spectral_proxy, combined_band_proxy
from voice_m5_playback_coherence import (
    checked_take, LOW_MANIFEST_SHA, HIGH_MANIFEST_SHA,
    LOW_MIC_SHA, HIGH_MIC_SHA, REFERENCE_SHA, SOURCE_MANIFEST_SHA,
)
from voice_m5_speex_baseline import (
    exact_pcm16, load_speex, run_speex_native,
    LAG_015_SAMPLES, LAG_030_SAMPLES, FRAME_SAMPLES, TAIL_SAMPLES,
)

# Exact digest of first real native Speex AEC-only user-supplied report.
NATIVE_REF_REPORT_SHA256 = "75ca166a0439bf5e5345a567a569549aaf81ef1f95affdc9aedcb7e929adfc01"
OFF_WINDOWS_MIC_S = (("before", 2., 7.), ("after", 30., 35.))


def zero_reference_run(mic: np.ndarray, reference: np.ndarray,
                       lag: int, native, native_lib_name: str) -> dict:
    """Keep mic identical; silence ONLY native Speex far-end PCM."""
    if len(reference) != 20*RATE or len(mic) < 35*RATE:
        raise ValueError("Need full 20s reference and physical 36s mic")
    pcm = exact_pcm16(mic)
    # Clear every far-end PCM sample: no fake music, no Speaker/USB changes.
    silent = np.zeros(len(pcm), dtype=np.int16)
    out, implementation = run_speex_native(
        pcm, silent, library=native, library_name=native_lib_name)
    a,b = indices(EVAL_S, lag, len(mic))
    start,end = (round(EVAL_S[0]*RATE),round(EVAL_S[1]*RATE))
    x = reference[start:end]
    y = mic[a:b]
    residual = out[a:b]
    heldout = linear_spectral_proxy(x,y,residual)
    heldout["full_band_erle_db"] = round(erle_db(y,residual),5)
    shadow = {"zero_reference_speex":heldout}
    proxy = combined_band_proxy(shadow,"zero_reference_speex")
    off = {}
    unrelated_ref = reference[10*RATE:15*RATE]
    for phase,low,high in OFF_WINDOWS_MIC_S:
        off[phase] = linear_spectral_proxy(
            unrelated_ref, mic[round(low*RATE):round(high*RATE)],
            out[round(low*RATE):round(high*RATE)])
        off[phase]["mic_wav_interval_s"] = [low,high]
    return {
        "processed_with_exactly_zero_far_end_pcm": True,
        "reference_buffer_all_zero": True,
        "native_processor": implementation,
        "heldout_speaker_only": heldout,
        "speech_band_500_4000hz_proxy": proxy,
        "speaker_off_source_independent_impact": off,
        "applies_same_algorithm_to_original_mic_even_during_music_on": True,
    }


def compare(original: dict, zero: dict) -> dict:
    """Difference normal-reference Speex vs zero-reference Speex.

    Both use identical physical mic data. A difference is attributable
    to including the far-end digital PCM in the algorithm, but it is
    NOT certified isolated physical echo energy: the adaptive path
    is nonlinear/stateful and internal signal processing continues.
    """
    n=original["heldout_speaker_only"]
    z=zero["heldout_speaker_only"]
    band_changes=[]
    for a,b in zip(n["bands"],z["bands"]):
        if a["band_hz"] != b["band_hz"]:
            raise ValueError("Mismatched source report frequency bands")
        band_changes.append({
            "band_hz":a["band_hz"],
            "speaker_on_erle_gain_due_to_real_reference_db":round(
                a["band_erle_db"]-b["band_erle_db"],5),
            "speaker_on_after_mic_power_normal_minus_zero_db":round(
                a["residual_band_power_dbfs"]-
                b["residual_band_power_dbfs"],5),
            "speaker_on_linked_proxy_normal_minus_zero_db":round(
                a["residual_linked_power_proxy_dbfs"]-
                b["residual_linked_power_proxy_dbfs"],5),
        })
    off=[]
    for phase,_,_ in OFF_WINDOWS_MIC_S:
        normal=original["speaker_off_speex_impact"][phase]
        silent=zero["speaker_off_source_independent_impact"][phase]
        for a,b in zip(normal["bands"],silent["bands"]):
            if a["band_hz"] != b["band_hz"]:
                raise ValueError("Mismatched speaker OFF band")
            off.append({
                "phase":phase,
                "band_hz":a["band_hz"],
                "normal_minus_zero_source_off_residual_band_db":round(
                    a["residual_band_power_dbfs"] -
                    b["residual_band_power_dbfs"],5),
                "normal_reference_speex_off_erle_db":a["band_erle_db"],
                "zero_reference_speex_off_erle_db":b["band_erle_db"],
            })
    return {
        "wholeband_erle_db_reference_on":n["full_band_erle_db"],
        "wholeband_erle_db_reference_zero":z["full_band_erle_db"],
        "wholeband_erle_gain_due_to_real_reference_db":round(
            n["full_band_erle_db"]-z["full_band_erle_db"],5),
        "500_4000hz_linked_proxy_suppression_reference_on_db":
            original["speech_band_500_4000hz_proxy"]["linked_proxy_suppression_db"],
        "500_4000hz_linked_proxy_suppression_reference_zero_db":
            zero["speech_band_500_4000hz_proxy"]["linked_proxy_suppression_db"],
        "500_4000hz_linked_suppression_gain_due_to_real_reference_db":round(
            original["speech_band_500_4000hz_proxy"]["linked_proxy_suppression_db"]-
            zero["speech_band_500_4000hz_proxy"]["linked_proxy_suppression_db"],5),
        "per_band_speaker_on":band_changes,
        "per_band_speaker_off":off,
    }


def analyze(low_dir: Path, high_dir: Path, base_report: Path, out_dir: Path,
            speex_loader=load_speex) -> dict:
    low_dir=low_dir.expanduser().resolve(strict=True)
    high_dir=high_dir.expanduser().resolve(strict=True)
    base_report=base_report.expanduser().resolve(strict=True)
    audit=AUDIT.resolve(strict=True)
    if (low_dir==high_dir or audit not in low_dir.parents or
            audit not in high_dir.parents or audit not in base_report.parents):
        raise ValueError("Original sources and benchmark must reside in private .audit")
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("Numerical output exists; never overwrite")
    out_parent=out_dir.expanduser().parent.resolve(strict=True)
    if (out_parent!=high_dir.parent or base_report.parent.parent!=out_parent):
        raise ValueError("Baseline report and output must share private voice-aec parent")
    if digest(base_report)!=NATIVE_REF_REPORT_SHA256:
        raise ValueError("Original native Speex report SHA256 mismatch")
    normal=json.loads(base_report.read_text(encoding="utf-8"))
    if (normal.get("schema")!="cuewire-M5-SpeexDSP-native-AEC-only-benchmark-v1"
            or normal.get("library_resolved") != "libspeexdsp.so.1"
            or normal.get("speex_process_adapts_during_heldout") is not True
            or normal.get("no_preprocessor_noise_suppression_or_AGC") is not True
            or normal.get("voice_preservation_validated") is not False):
        raise ValueError("Not the verified actual first native Speex comparison")
    ml,hl,yl=checked_take(low_dir,LOW_MANIFEST_SHA,LOW_MIC_SHA,.15)
    mh,hh,yh=checked_take(high_dir,HIGH_MANIFEST_SHA,HIGH_MIC_SHA,.30)
    if (ml["reference_source_capture"]!=mh["reference_source_capture"] or
            ml["source_mic_name"]!=mh["source_mic_name"] or
            ml["speaker_sink_name"]!=mh["speaker_sink_name"]):
        raise ValueError("Physical devices/source mismatch")
    source_dir=Path(ml["reference_source_capture"]).expanduser().resolve(strict=True)
    if audit not in source_dir.parents:
        raise ValueError("Digital reference must be in .audit")
    ref_file=source_dir/"reference-sent.wav"
    ref_manifest=source_dir/"manifest.json"
    hashes={
        "volume015_manifest":hl["manifest"],
        "volume015_mic":hl["physical_mic_raw"],
        "volume030_manifest":hh["manifest"],
        "volume030_mic":hh["physical_mic_raw"],
        "digital_reference":REFERENCE_SHA,
        "digital_reference_manifest":SOURCE_MANIFEST_SHA,
    }
    if normal["source_sha256"]!=hashes:
        raise ValueError("Prior native baseline does not match existing physical source SHA256")
    if digest(ref_file)!=REFERENCE_SHA or digest(ref_manifest)!=SOURCE_MANIFEST_SHA:
        raise ValueError("Original digital reference SHA256 mismatch")
    music=read_wav(ref_file)
    if len(music)!=20*RATE:
        raise ValueError("Source digital reference must be exactly 20s")
    lib, libname=speex_loader()
    zero015=zero_reference_run(yl,music,LAG_015_SAMPLES,lib,libname)
    zero030=zero_reference_run(yh,music,LAG_030_SAMPLES,lib,libname)
    comparison={
        "volume_015":compare(normal["volume_015"],zero015),
        "volume_030":compare(normal["volume_030"],zero030),
    }
    source_paths={
        "volume015_manifest":low_dir/"manifest.json",
        "volume015_mic":low_dir/"microphone-aba-raw.wav",
        "volume030_manifest":high_dir/"manifest.json",
        "volume030_mic":high_dir/"microphone-aba-raw.wav",
        "digital_reference":ref_file,
        "digital_reference_manifest":ref_manifest,
    }
    if (digest(base_report)!=NATIVE_REF_REPORT_SHA256 or
            any(digest(path)!=hashes[key] for key,path in source_paths.items())):
        raise RuntimeError("Original SHA256-pinned source data changed during experiment")
    output={
        "schema":"cuewire-M5-SpeexDSP-normal-vs-zero-farend-control-v1",
        "status":"source_independent_processing_control_not_production_acceptance",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "native_speex_library":libname,
        "source_sha256":hashes,
        "first_native_normal_reference_report_sha256":NATIVE_REF_REPORT_SHA256,
        "original_speex_uses_real_reference_10ms_frame_tail_120ms":True,
        "zero_farend_pcm_for_entire_duration":True,
        "zero_reference_run_015":zero015,
        "zero_reference_run_030":zero030,
        "normal_minus_zero_comparison":comparison,
        "off_windows_s":[[a,b] for _,a,b in OFF_WINDOWS_MIC_S],
        "voice_preservation_tested":False,
        "any_aec_threshold_redefined":False,
        "original_prospective_0_67881_db_gate_remains_failed":True,
        "audio_file_output_count":0,
        "warnings":[
            "SpeexDSP internal DC notch and other front-end processing can affect mic output even with no far-end.",
            "Zero-reference Speex is an internal algorithmic control, not raw mic bypass and not proof of speech preservation.",
            "Normal vs zero far-end difference is attributable to reference input in these recordings, not a clean isolated physical echo waveform.",
            "Original Speex adapts continuously in heldout; CueWire FIR was frozen; comparison is asymmetric.",
            "The 0.15/0.30 recordings were previously examined. This is not a prospective randomized benchmark.",
            "48kHz PCM16 quantization and unmeasured hardware source/sink latency remain shared limitations.",
            "The 513-tap echo-only guard of CueWire is offline noncausal and has no streaming certification.",
            "No microphone, audio routing, system volume, M4 voice, background service or virtual mic was touched.",
        ]
    }
    target=out_parent/out_dir.name
    target.mkdir(mode=0o700)
    dest=target/"speex-zero-reference.json"
    with dest.open("x",encoding="utf-8") as fp:
        json.dump(output,fp,ensure_ascii=False,sort_keys=True,indent=2)
        fp.write("\n")
    with (target/"SHA256SUMS").open("x",encoding="utf-8") as fp:
        fp.write(digest(dest)+"  speex-zero-reference.json\n")
    print("M5 native SpeexDSP: real far-end versus exactly ZERO far-end")
    for label in ("volume_015","volume_030"):
        stats=comparison[label]
        print(label,"ERLE with music ref",stats["wholeband_erle_db_reference_on"],
              "dB; with ZERO ref",stats["wholeband_erle_db_reference_zero"],
              "dB; additional with ref",
              stats["wholeband_erle_gain_due_to_real_reference_db"],"dB")
        print("500..4000Hz reference-linked proxy suppression",
              stats["500_4000hz_linked_proxy_suppression_reference_on_db"],
              "dB vs zero-ref",
              stats["500_4000hz_linked_proxy_suppression_reference_zero_db"],"dB")
    print("Native Speex report SHA256 verified; no new audio, no approval; old FAIL unchanged.")
    print("Report:",dest)
    return output


def main()->None:
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--low-capture",type=Path,required=True)
    p.add_argument("--high-capture",type=Path,required=True)
    p.add_argument("--original-speex-report",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    try:
        analyze(a.low_capture,a.high_capture,a.original_speex_report,a.out)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
        p.exit(1,"ERROR: "+str(exc)+"\n")


if __name__=="__main__":
    main()
