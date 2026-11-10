#!/usr/bin/env python3
"""M6 Physical-002: offline event-triggered, waveform-aware anti-click pilot.

Do NOT infer button/key identity. The only event fields read are anonymous
kind + monotonic timestamp. Train offsets on M6 Physical-001 isolated
recording, as ALREADY pinned by the independent heldout alignment JSON.

This is an exploratory, NON-CAUSAL, zero-phase subband experiment:
derive a separate inverted highband correction from microphone waveform,
but ONLY in short windows around acoustic transients anchored to input
events. Two predetermined gains share the same per-event trigger mask.
No training on near-end speech, no voice-clean ground-truth claims,
no recording, monitoring, system routing changes, or automatic playback.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import wave

import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter1d

from voice_m6_physical_event_align import (
    RATE, KINDS, digest, read_capture,
)

SOURCE_MIC_SHA = "601649839c4a4ab2a507d75f1002cc74ae8f658e9c32c3edb2a1a03cf2892db2"
SOURCE_EVENTS_SHA = "07a6fb52ddb514458d0c2b92b2772010472700686239dd3d225036799d3013d3"
SOURCE_MANIFEST_SHA = "f5b6d1d780cc482b757d3cfc73b9fba29fcf808c7079ef0bd6fe62ce9eeb0284"
ALIGN_REPORT_SHA = "34619ae0edaa49292d1166a59d425fd0e97880ce42be7bf1926848f8c7e4fb16"

# Constants fixed before generating the exported listening pack. Their
# choices were informed by an exploratory analysis of this same recording:
# any heldout energy reduction is DESCRIPTIVE, NOT unbiased model selection.
LOW_HZ = 1300
HIGH_HZ = 9000
MIN_SNR_RATIO = 4.0
MIN_PEAK_RMS = 0.0005
GAINS = {"B_conservative":0.40, "C_stronger":0.75}
SCENES = (
    ("R1_mouse_isolated", "mouse_isolated"),
    ("R2_keyboard_isolated", "keyboard_isolated"),
    ("R3_both_holdout", "both_holdout"),
    ("R4_voice_only_holdout", "voice_only_holdout"),
    ("R5_voice_clicks_holdout", "voice_clicks_holdout"),
)


def samples_from_ms(ms:float)->int:
    return round(ms*RATE/1000.)


def phase_bounds(phase:dict, start_ns:int, total:int)->tuple[int,int]:
    a=round((phase["start_monotonic_ns"]-start_ns)*RATE/1e9)
    b=round((phase["end_monotonic_ns"]-start_ns)*RATE/1e9)
    # pw-record stopped about 175ms before the final wall-clock phase
    # timestamp in this pinned physical capture. Clip ONLY this bounded
    # terminal discrepancy; never extrapolate or manufacture audio.
    if b > total and 0 < b-total <= round(.25*RATE):
        b=total
    if not 0<=a<b<=total:
        raise ValueError("Recorded physical phase does not fit mic PCM")
    return a,b


def sample_mask(mic:np.ndarray, manifest:dict, events:list[dict],
                offsets:dict[str,int])->tuple[np.ndarray,np.ndarray,dict]:
    """Single non-causal highband, event-anchored *adaptive* anti-wave.

    A per-event highband peak, local quiet reference and measured phase
    determine whether/where compensation happens. Overlaps are combined
    using max mask, never summed into dangerous over-subtraction.
    The returned correction is -mask*highband, additive to the RAW mic.
    """
    if mic.ndim!=1 or len(mic)<RATE or not np.all(np.isfinite(mic)):
        raise ValueError("Invalid physical mic signal")
    if set(offsets)!=set(KINDS) or any(
            type(v) is not int or v < -50 or v > 100
            for v in offsets.values()):
        raise ValueError("Missing independent class acoustic offsets")
    sos=signal.butter(
        4,[LOW_HZ,HIGH_HZ],btype="bandpass",fs=RATE,output="sos")
    # Zero-phase is required for this OFFLINE proof only. A causal
    # real-time implementation must be designed and separately validated.
    highband=signal.sosfiltfilt(sos,mic)
    power=np.maximum(0.,uniform_filter1d(
        highband*highband,size=samples_from_ms(1.),mode="nearest"))
    strength=np.sqrt(power)
    mask=np.zeros(len(mic),dtype=np.float32)
    counts={k:0 for k in KINDS}
    rejected={k:0 for k in KINDS}
    start_ns=manifest["session_start_monotonic_ns"]
    for event in events:
        kind=event["kind"]
        center=round(
            (event["t_monotonic_ns"]-start_ns)*RATE/1e9
            - offsets[kind]*RATE/1000.)
        radius=samples_from_ms(5. if kind.startswith("mouse") else 9.)
        # Background ends 31ms BEFORE the reference marker, avoiding
        # accidentally using that event's own highband spike.
        if center<samples_from_ms(100.) or center+radius>=len(mic)-samples_from_ms(10.):
            rejected[kind]+=1
            continue
        peak=center-radius+int(np.argmax(
            strength[center-radius:center+radius+1]))
        baseline=float(np.median(
            strength[center-samples_from_ms(93.):
                     center-samples_from_ms(31.)]))
        if (strength[peak]<MIN_PEAK_RMS or
                strength[peak]/max(baseline,1e-8)<MIN_SNR_RATIO):
            rejected[kind]+=1
            continue
        left=samples_from_ms(2.5 if kind.startswith("mouse") else 3.)
        right=samples_from_ms(4.5 if kind.startswith("mouse") else 5.)
        lo,hi=peak-left,peak+right
        if lo<0 or hi>=len(mic):
            rejected[kind]+=1
            continue
        before=np.sin(np.linspace(0.,np.pi/2,left+1))**2
        after=np.cos(np.linspace(0.,np.pi/2,right+1))**2
        taper=np.concatenate((before[:-1],after)).astype(np.float32)
        mask[lo:hi+1]=np.maximum(mask[lo:hi+1],taper)
        counts[kind]+=1
    return mask,highband,{
        "accepted_events_by_class":counts,
        "untriggered_or_out_of_bounds_events":rejected,
        "original_event_count":len(events),
        "mask_changed_samples":int(np.count_nonzero(mask)),
        "mask_changed_fraction":round(float(np.count_nonzero(mask))/len(mask),7),
        "event_identity_accessed":False,
        "live_voice_used_for_model_fitting":False,
        "future_samples_required":True,
    }


def to_pcm16(samples:np.ndarray)->np.ndarray:
    if (samples.ndim!=1 or not np.all(np.isfinite(samples))
            or np.max(np.abs(samples))>=.98):
        raise ValueError("Output would be unsafe/clipped; no limiter")
    return np.rint(samples*32768.).astype("<i2")


def wav_write(path:Path, data:np.ndarray)->None:
    if data.dtype!=np.dtype("<i2") or data.ndim!=1 or len(data)==0:
        raise ValueError("Expected nonempty PCM16 mono")
    with path.open("xb") as out:
        with wave.open(out,"wb") as wav:
            wav.setframerate(RATE)
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.writeframes(data.tobytes())


def rms(x:np.ndarray)->float:
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


def db_ratio(a:float,b:float)->float:
    if a<=0 or b<=0:
        raise ValueError("Cannot score a silent interval")
    return 20*math.log10(a/b)


def validate_alignment(source:Path, alignment:Path, manifest:dict)->dict:
    if (alignment.is_symlink() or
            alignment.name!="m6-physical-event-alignment.json" or
            digest(alignment)!=ALIGN_REPORT_SHA):
        raise ValueError("Expected pinned M6 physical alignment report")
    report=json.loads(alignment.read_text(encoding="utf-8"))
    if (report.get("schema")!="cuewire-M6-physical-event-audio-alignment-v1"
            or report.get("acceptance") is not True
            or report.get("mic_sample_zero_monotonic_ns_separately_identified") is not False
            or report.get("mechanical_click_cancellation_performed") is not False
            or report.get("source_mic_sha256")!=SOURCE_MIC_SHA
            or report.get("source_events_sha256")!=SOURCE_EVENTS_SHA
            or report.get("source_manifest_sha256")!=SOURCE_MANIFEST_SHA
            or report.get("heldout_permutations")!=2000):
        raise ValueError("Physical alignment fails source/heldout contract")
    details=report["class_results"]
    if (set(details)!=set(KINDS)
            or any(not details[k]["passed_independent_holdout"]
                   for k in KINDS)):
        raise ValueError("Not all mechanical event classes passed heldout")
    return {
        k:int(details[k]["effective_acoustic_offset_ms"])
        for k in KINDS
    }


def run(source:Path, alignment:Path, out:Path)->dict:
    os.umask(0o077)
    if source.is_symlink() or alignment.is_symlink():
        raise ValueError("Cannot follow source links")
    source=source.expanduser().resolve(strict=True)
    alignment=alignment.expanduser().resolve(strict=True)
    if (source.name!="M6-physical-events-001"
            or source.parent.name!="voice-aec"
            or source.parent.parent.name!=".audit"
            or alignment.parent.parent!=source.parent):
        raise ValueError("Require original private M6 capture and alignment")
    out=out.expanduser().absolute()
    if (out.exists() or out.is_symlink()
            or out.parent.resolve(strict=True)!=source.parent):
        raise FileExistsError("Refuse nonprivate or existing M6 output")
    manifest,mic,events=read_capture(source)
    for name,expected in (
        ("microphone.wav",SOURCE_MIC_SHA),
        ("anonymous-events.jsonl",SOURCE_EVENTS_SHA),
        ("manifest.json",SOURCE_MANIFEST_SHA),
    ):
        if digest(source/name)!=expected:
            raise ValueError("Wrong physical source SHA256 "+name)
    offsets=validate_alignment(source,alignment,manifest)
    mask,highband,mask_report=sample_mask(mic,manifest,events,offsets)
    raw=to_pcm16(mic)
    tracks={"A_original":raw}
    for name,alpha in GAINS.items():
        candidate=to_pcm16(mic-alpha*mask*highband)
        if not np.array_equal(candidate[mask==0],raw[mask==0]):
            raise ValueError("Any sample outside event mask was modified")
        tracks[name]=candidate

    # Event-only, non-speech gate: candidates must improve highband
    # energy over a stage never used to train the four time offsets.
    sos=signal.butter(4,[LOW_HZ,HIGH_HZ],btype="bandpass",fs=RATE,output="sos")
    spectra={
        role:signal.sosfiltfilt(sos,track.astype(np.float64)/32768.)
        for role,track in tracks.items()
    }
    by_name={p["phase"]:p for p in manifest["phases"]}
    metrics={}
    zero_ns=manifest["session_start_monotonic_ns"]
    for folder,phase_name in SCENES:
        a,b=phase_bounds(by_name[phase_name],zero_ns,len(raw))
        raw_rms=rms(spectra["A_original"][a:b])
        scene={
            "phase":phase_name,
            "duration_s":round((b-a)/RATE,6),
            "source_bandpass_rms_dbfs":
                round(20*math.log10(raw_rms+1e-20),5),
            "modified_sample_count":int(np.count_nonzero(mask[a:b])),
            "modified_sample_fraction":
                round(float(np.count_nonzero(mask[a:b]))/(b-a),7),
            "bandpass_energy_change_db":{
                name:round(db_ratio(rms(spectra[name][a:b]),raw_rms),5)
                for name in GAINS
            },
        }
        if phase_name=="voice_only_holdout":
            scene["raw_PCM16_preserved_outside_event_mask"]=all(
                np.array_equal(tracks[name][a:b][mask[a:b]==0],
                               raw[a:b][mask[a:b]==0])
                for name in GAINS)
            scene["contains_real_anonymous_input_events"]=True
            if not scene["raw_PCM16_preserved_outside_event_mask"]:
                raise ValueError("Protected voice changed outside event mask")
        metrics[folder]=scene
    if (metrics["R3_both_holdout"]["bandpass_energy_change_db"][
            "C_stronger"]>=0):
        raise ValueError("Strong anti-click candidate worsens heldout band RMS")

    # Re-verify unchanged immutable evidence before any output is created.
    for file,sha in (
        (source/"microphone.wav",SOURCE_MIC_SHA),
        (source/"anonymous-events.jsonl",SOURCE_EVENTS_SHA),
        (source/"manifest.json",SOURCE_MANIFEST_SHA),
        (alignment,ALIGN_REPORT_SHA),
    ):
        if digest(file)!=sha:
            raise RuntimeError("Input mutated during physical anti-click research")
    report={
        "schema":"cuewire-M6-physical-waveform-aware-click-AB-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "source_microphone_sha256":SOURCE_MIC_SHA,
        "source_anonymous_events_sha256":SOURCE_EVENTS_SHA,
        "source_alignment_report_sha256":ALIGN_REPORT_SHA,
        "original_signal_modified":False,
        "output_private":True,
        "click_detector_input_fields":["kind","t_monotonic_ns"],
        "any_key_or_button_identity_known":False,
        "algorithm":"event_anchor + per_event_acoustic_peak + zero_phase_subband_anti_wave",
        "per_event_peak_search_ms":{"mouse":5,"keyboard":9},
        "bandpass_hz":[LOW_HZ,HIGH_HZ],
        "trigger_ratio_vs_local_baseline":MIN_SNR_RATIO,
        "minimum_peak_rms":MIN_PEAK_RMS,
        "listening_candidates":GAINS,
        "candidate_letters_not_randomized":True,
        "per_scene_measurements":metrics,
        "mask_summary":mask_report,
        "speech_quality_or_click_isolated_ground_truth_proved":False,
        "offline_only_not_causal_or_realtime":True,
        "parameter_choices_informed_by_exploratory_physical_heldout_analysis":True,
        "heldout_reduction_is_descriptive_not_unbiased_model_selection":True,
        "physical_button_or_key_identity_captured":False,
        "system_audio_configuration_changed":False,
        "automatic_playback":False,
        "sha256":{},
    }
    out.mkdir(mode=0o700)
    for folder,phase_name in SCENES:
        place=out/folder
        place.mkdir(mode=0o700)
        a,b=phase_bounds(by_name[phase_name],zero_ns,len(raw))
        for role,track in tracks.items():
            name=f"{role}.wav"
            path=place/name
            wav_write(path,track[a:b])
            report["sha256"][f"{folder}/{name}"]=digest(path)
    report_path=out/"m6-physical-click-ab.json"
    report_path.write_text(
        json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    checks={**report["sha256"],report_path.name:digest(report_path)}
    (out/"SHA256SUMS").write_text(
        "".join(f"{sha}  {name}\n"
                for name,sha in sorted(checks.items())),encoding="utf-8")
    return {
        "status":"EXPERIMENTAL_OFFLINE",
        "out":str(out),
        "wav_count":len(SCENES)*len(tracks),
        "protected_voice_original_outside_event_windows":metrics[
            "R4_voice_only_holdout"]["raw_PCM16_preserved_outside_event_mask"],
        "physical_noise_reduction_measured_but_not_ground_truth_proved":True,
        "heldout_both_changes_db":metrics[
            "R3_both_holdout"]["bandpass_energy_change_db"],
        "speech_click_changes_db":metrics[
            "R5_voice_clicks_holdout"]["bandpass_energy_change_db"],
        "automatic_playback":False,
    }


def main()->None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture",required=True,type=Path)
    parser.add_argument("--alignment",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    args=parser.parse_args()
    try:
        print(json.dumps(run(args.capture,args.alignment,args.out),
                         sort_keys=True,indent=2))
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,wave.Error) as exc:
        parser.exit(1,"ERROR: "+str(exc)+"\n")


if __name__=="__main__":
    main()
