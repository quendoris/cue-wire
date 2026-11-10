#!/usr/bin/env python3
"""M6 Physical-004: aggressive anonymous event-conditioned multi-band anti-wave.

NO device reads, capture, audio playback, routing, daemons or persistence.
Uses original private physical PCM16 + anonymous press/release markers.
Fits event timing/duration only with previously verified isolated no-voice
stages. Three listening options: A=M6-003 strong, B=more aggressive
wideband, C=wideband with a separate low-band speech-protection heuristic.

OFFLINE / NONCAUSAL research, not an approved real-time microphone DSP.
A bandpower reduction is NOT a proven click-source SNR or preserved voice.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import numpy as np
from scipy import signal

import voice_m6_physical_tail_ab as previous
from voice_m6_physical_event_align import (
    KINDS, RATE, digest, read_capture,
)
from voice_m6_physical_click_ab import (
    SOURCE_MIC_SHA, SOURCE_EVENTS_SHA, SOURCE_MANIFEST_SHA,
    ALIGN_REPORT_SHA, validate_alignment, phase_bounds,
    to_pcm16, wav_write,
)

# These fixed parameters were selected by exploratory analysis on
# this SAME 44-second original recording, not independent model selection.
# Later holdout statistics are therefore DESCRIPTIVE ONLY.
WIDE_BAND = (650, 12000)
LOWMID_BAND = (260, 650)
SCORE_BAND = (1300, 9000)
TRIGGER_RELATIVE_TO_BASELINE = 1.7
TRIGGER_MIN_RMS = .0002
PEAK_SEARCH_MS = {"mouse": 7, "keyboard": 12}
END_BASELINE_MULTIPLIER = 1.1
KEYBOARD_MIN_TAIL_MS = 35
MOUSE_MIN_TAIL_MS = 8
KEYBOARD_EXTRA_TAIL_MS = 55
MOUSE_EXTRA_TAIL_MS = 23
MASK_INTERIOR_FLOOR = .85
STRENGTH_HIGH = .98
STRENGTH_LOWMID = .55
GUARD_HIGH_UNVOICED_FLOOR = .25

SCENES = {
    "R1_mouse_isolated":"mouse_isolated",
    "R2_keyboard_isolated":"keyboard_isolated",
    "R3_both_holdout":"both_holdout",
    "R4_voice_only_holdout":"voice_only_holdout",
    "R5_voice_clicks_holdout":"voice_clicks_holdout",
}


def ms(value:float)->int:
    return round(value*RATE/1000.)


def aggressive_mask(manifest:dict, events:list[dict],
                    offsets:dict[str,int], envelope:np.ndarray,
                    calibrated_caps:dict[str,int]) -> tuple[np.ndarray,dict]:
    """Threshold and full-tail envelope frozen from isolated calibration.

    Expands the original M6-003 short-wave mask while retaining acoustic
    confirmation at every anonymous input event. No key/button codes.
    """
    n=len(envelope)
    if (n < RATE or not np.all(np.isfinite(envelope))
            or set(calibrated_caps)!={"mouse","keyboard"}
            or set(offsets)!=set(KINDS)):
        raise ValueError("Invalid PCM envelope or frozen per-class training")
    centers=previous.event_centers(
        events,offsets,manifest["session_start_monotonic_ns"],n)
    mask=np.zeros(n,dtype=np.float32)
    counts=Counter()
    rejected=Counter()
    durations=[]
    for pos,kind,_ in centers:
        category=kind.split("_",1)[0]
        if category not in ("mouse","keyboard"):
            raise ValueError("Identifying/invalid event kind")
        radius=ms(PEAK_SEARCH_MS[category])
        if pos < ms(105) or pos+radius>=n:
            rejected[kind]+=1
            continue
        peak=pos-radius+int(np.argmax(envelope[pos-radius:pos+radius+1]))
        baseline=float(np.median(envelope[pos-ms(100):pos-ms(26)]))
        strength=float(envelope[peak])
        if (strength<TRIGGER_MIN_RMS
                or strength<TRIGGER_RELATIVE_TO_BASELINE*max(baseline,1e-8)):
            rejected[kind]+=1
            continue
        cap=int(np.clip(
            calibrated_caps[category]+(
                KEYBOARD_EXTRA_TAIL_MS if category=="keyboard"
                else MOUSE_EXTRA_TAIL_MS),
            20 if category=="mouse" else 50,
            80 if category=="mouse" else 130))
        length=previous.tail_samples(
            peak,baseline,strength,envelope,cap)
        length=max(length,ms(
            KEYBOARD_MIN_TAIL_MS if category=="keyboard"
            else MOUSE_MIN_TAIL_MS))
        early=ms(7 if category=="keyboard" else 5)
        start,stop=peak-early,min(n-1,peak+length)
        if start<0 or stop-start<ms(5):
            rejected[kind]+=1
            continue
        following=envelope[peak:stop+1]
        excess=np.clip(
            (following-END_BASELINE_MULTIPLIER*baseline)
            /max(strength,1e-12),0.,1.)
        interior=np.clip(
            MASK_INTERIOR_FLOOR+
            1.3*(1-MASK_INTERIOR_FLOOR)*excess,0.,1.)
        shape=np.empty(stop-start+1,dtype=np.float64)
        shape[:early]=(np.sin(
            np.linspace(0,np.pi/2,early+1))[:-1]**2)*interior[0]
        shape[early:]=interior
        fade=min(ms(8),len(shape))
        shape[-fade:]*=(np.cos(
            np.linspace(0,np.pi/2,fade))**2)
        # MAX instead of sum prevents overlapping event masks exceeding 1.
        mask[start:stop+1]=np.maximum(
            mask[start:stop+1],shape.astype(np.float32))
        counts[kind]+=1
        durations.append(length/RATE*1000.)
    if not durations:
        raise ValueError("No measurable anonymous acoustic events")
    if np.any(mask<0) or np.any(mask>1):
        raise ValueError("Mask amplitude out of unit range")
    return mask,{
        "detected_by_class":dict(counts),
        "not_detected_by_class":dict(rejected),
        "fraction_samples_in_event_windows":
            round(float(np.count_nonzero(mask))/n,7),
        "event_window_sample_count":int(np.count_nonzero(mask)),
        "median_tail_ms":round(float(np.median(durations)),3),
        "p90_tail_ms":round(float(np.percentile(durations,90)),3),
        "maximum_tail_ms":round(float(max(durations)),3),
        "event_fields_used":["kind","t_monotonic_ns"],
        "key_or_button_identity_collected":False,
    }


def make_tracks(mic:np.ndarray,manifest:dict,events:list[dict],
                offsets:dict, caps:dict) -> tuple[
                    dict[str,np.ndarray],np.ndarray,np.ndarray,dict]:
    """Create physically additive anti-waves and exact PCM16 guards."""
    _,event_envelope=previous.envelope(mic,SCORE_BAND)
    old_mask,_=previous.dynamic_mask(
        manifest,events,offsets,event_envelope,caps)
    old_highband,_=previous.envelope(mic,SCORE_BAND)
    new_mask,report=aggressive_mask(
        manifest,events,offsets,event_envelope,caps)
    high,_=previous.envelope(mic,WIDE_BAND)
    lowmid,_=previous.envelope(mic,LOWMID_BAND)
    voice_guard,voice_report=previous.voice_guard(mic,manifest)
    original=to_pcm16(mic)
    old_correction=-old_mask.astype(np.float64)*old_highband
    # The new inverse estimate is a parallel signed physical waveform,
    # not a replacement of the original microphone.
    parallel_anti=-new_mask.astype(np.float64)*(
        STRENGTH_HIGH*high+STRENGTH_LOWMID*lowmid)
    # Voice guard NEVER changes processing outside event windows;
    # lowband gets full guard, wideband gets partial protection to
    # retain meaningful click suppression during doubletalk.
    guarded_anti=-new_mask.astype(np.float64)*(
        STRENGTH_HIGH*(GUARD_HIGH_UNVOICED_FLOOR+
                       (1-GUARD_HIGH_UNVOICED_FLOOR)*voice_guard)*high
        +STRENGTH_LOWMID*voice_guard*lowmid)
    tracks={
        "A_003_adaptive":to_pcm16(mic+.75*old_correction),
        "B_004_aggressive":to_pcm16(mic+parallel_anti),
        "C_004_voice_guard":to_pcm16(mic+guarded_anti),
    }
    if not np.array_equal(tracks["A_003_adaptive"][old_mask==0],
                          original[old_mask==0]):
        raise RuntimeError("M6-003 baseline changed outside its trigger")
    for key in ("B_004_aggressive","C_004_voice_guard"):
        if not np.array_equal(tracks[key][new_mask==0],
                              original[new_mask==0]):
            raise RuntimeError("M6-004 changed physical PCM outside event mask")
    if not np.all(np.isfinite(parallel_anti)):
        raise ValueError("Non-finite acoustic inverse estimate")
    report["voice_guard"]=voice_report
    report["legacy_003_event_sample_fraction"]=round(
        float(np.count_nonzero(old_mask))/len(mic),7)
    report["new_event_mask_has_greater_coverage"]=bool(
        np.count_nonzero(new_mask)>np.count_nonzero(old_mask))
    return tracks,original,new_mask,report


def scenes_report(manifest:dict,tracks:dict,
                  original:np.ndarray,new_mask:np.ndarray)->dict:
    sos=signal.butter(4,SCORE_BAND,fs=RATE,
                      btype="bandpass",output="sos")
    t0=manifest["session_start_monotonic_ns"]
    by={p["phase"]:p for p in manifest["phases"]}
    result={}
    for label,phase in SCENES.items():
        a,b=phase_bounds(by[phase],t0,len(original))
        def rms(y:np.ndarray)->float:
            z=signal.sosfiltfilt(
                sos,y.astype(np.float64)/32768.)
            return float(np.sqrt(np.mean(z*z)))
        reference=rms(original[a:b])
        entries={}
        for name,pcm in tracks.items():
            entries[name]=round(20*math.log10(
                (rms(pcm[a:b])+1e-30)/(reference+1e-30)),5)
        for name in ("B_004_aggressive","C_004_voice_guard"):
            if not np.array_equal(
                    tracks[name][a:b][new_mask[a:b]==0],
                    original[a:b][new_mask[a:b]==0]):
                raise RuntimeError("Changed source PCM outside triggers")
        result[label]={
            "source_phase":phase,
            "duration_seconds":round((b-a)/RATE,6),
            "event_sample_fraction":round(
                float(np.count_nonzero(new_mask[a:b]))/(b-a),6),
            "band_1300_9000_rms_change_db_vs_original":entries,
            "sample_exact_outside_confirmed_event_windows":True,
            "is_click_snr_or_speech_quality":False,
        }
    return result


def run(source:Path,alignment:Path,out:Path)->dict:
    os.umask(0o077)
    if source.is_symlink() or alignment.is_symlink():
        raise ValueError("Symlinked source not allowed")
    source=source.expanduser().resolve(strict=True)
    alignment=alignment.expanduser().resolve(strict=True)
    out=out.expanduser().absolute()
    if (source.name!="M6-physical-events-001"
            or source.parent.name!="voice-aec"
            or source.parent.parent.name!=".audit"
            or alignment.parent.parent!=source.parent
            or out.is_symlink() or out.exists()
            or out.parent.resolve(strict=True)!=source.parent):
        raise ValueError("Expected immutable private M6 capture and new output")
    manifest,mic,events=read_capture(source)
    for name,expected in (
        ("microphone.wav",SOURCE_MIC_SHA),
        ("anonymous-events.jsonl",SOURCE_EVENTS_SHA),
        ("manifest.json",SOURCE_MANIFEST_SHA)):
        if digest(source/name)!=expected:
            raise ValueError("Original private physical input digest mismatch")
    offsets=validate_alignment(source,alignment,manifest)
    _,event_energy=previous.envelope(mic,SCORE_BAND)
    caps,training=previous.calibrate_tails(
        manifest,events,offsets,event_energy)
    tracks,original,mask,mask_report=make_tracks(
        mic,manifest,events,offsets,caps)
    comparisons=scenes_report(manifest,tracks,original,mask)
    both=comparisons["R3_both_holdout"][
        "band_1300_9000_rms_change_db_vs_original"]
    if both["B_004_aggressive"]>=both["A_003_adaptive"]:
        raise RuntimeError("Physical aggressive candidate failed vs M6-003")
    if not mask_report["new_event_mask_has_greater_coverage"]:
        raise RuntimeError("More aggressive mode did not cover more events")
    for path,expected in (
        (source/"microphone.wav",SOURCE_MIC_SHA),
        (source/"anonymous-events.jsonl",SOURCE_EVENTS_SHA),
        (source/"manifest.json",SOURCE_MANIFEST_SHA),
        (alignment,ALIGN_REPORT_SHA)):
        if digest(path)!=expected:
            raise RuntimeError("Source changed while generating private A/B")
    report={
        "schema":"cuewire-M6-physical-004-aggressive-event-AB-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "original_mic_sha256":SOURCE_MIC_SHA,
        "anonymous_events_sha256":SOURCE_EVENTS_SHA,
        "verified_alignment_sha256":ALIGN_REPORT_SHA,
        "physical_capture_unchanged":True,
        "training_phases":["mouse_isolated","keyboard_isolated"],
        "independent_timing_was_frozen":True,
        "frozen_calibrated_tail_caps_ms":caps,
        "isolated_training_durations":training,
        "event_mask":mask_report,
        "scenes":comparisons,
        "source_pcm_preserved_outside_event_windows":True,
        "event_identity_collected":False,
        "offline_zero_phase_requires_future_samples":True,
        "system_audio_or_input_settings_changed":False,
        "new_recording_or_playback":False,
        "model_parameters_explored_on_same_capture":True,
        "holdout_energy_change_descriptive_not_unbiased":True,
        "speech_integrity_during_clicks_certified":False,
        "clean_voice_ground_truth_during_doubletalk_exists":False,
        "parameters":{
            "wideband_hz":list(WIDE_BAND),
            "lowmid_hz":list(LOWMID_BAND),
            "high_antiwave_gain":STRENGTH_HIGH,
            "lowmid_antiwave_gain":STRENGTH_LOWMID,
            "relative_trigger":TRIGGER_RELATIVE_TO_BASELINE,
            "minimum_trigger_rms":TRIGGER_MIN_RMS,
            "min_keyboard_tail_ms":KEYBOARD_MIN_TAIL_MS,
            "keyboard_extra_cap_ms":KEYBOARD_EXTRA_TAIL_MS,
            "mask_floor_during_event":MASK_INTERIOR_FLOOR,
        },
        "sha256":{},
    }
    out.mkdir(mode=0o700)
    for label,phase in SCENES.items():
        folder=out/label
        folder.mkdir(mode=0o700)
        a,b=phase_bounds(next(p for p in manifest["phases"]
                              if p["phase"]==phase),
                         manifest["session_start_monotonic_ns"],
                         len(original))
        for name,pcm in tracks.items():
            path=folder/(name+".wav")
            wav_write(path,pcm[a:b])
            report["sha256"][str(path.relative_to(out))]=digest(path)
    dest=out/"m6-physical-004-report.json"
    dest.write_text(
        json.dumps(report,sort_keys=True,indent=2)+"\n",
        encoding="utf-8")
    sums={**report["sha256"],dest.name:digest(dest)}
    (out/"SHA256SUMS").write_text(
        "".join(f"{value}  {name}\n"
                for name,value in sorted(sums.items())),
        encoding="utf-8")
    return {
        "status":"EXPERIMENTAL_NONCAUSAL",
        "private_output":str(out),
        "mouse_keyboard_heldout_db":both,
        "speech_clicks_db":comparisons["R5_voice_clicks_holdout"][
            "band_1300_9000_rms_change_db_vs_original"],
        "event_mask_fraction":mask_report[
            "fraction_samples_in_event_windows"],
        "audio_playback":False,
        "voice_preservation_not_yet_proven":True,
    }


def main()->None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture",required=True,type=Path)
    parser.add_argument("--alignment",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    a=parser.parse_args()
    try:
        print(json.dumps(
            run(a.capture,a.alignment,a.out),
            sort_keys=True,indent=2))
    except (OSError,ValueError,RuntimeError,TypeError,
            KeyError,FloatingPointError) as exc:
        parser.exit(1,"ERROR: "+str(exc)+"\n")


if __name__=="__main__":
    main()
