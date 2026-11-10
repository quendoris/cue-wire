#!/usr/bin/env python3
"""M6 Physical-007: frozen preferred C006 vs residual attack+tail candidates.

Offline-only private experiment. The physical original + anonymous events
remain unchanged. Only the class keyboard_press/release and kernel monotonic
event time are read; never identify keys, buttons, text or pointer motion.
No PipeWire settings changes, live input, recording, autoplay or daemons.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import numpy as np

import voice_m6_physical_tail_ab as tail006
import voice_m6_physical_percussive_ab as previous006
from voice_m6_residual_comb import (
    GAINS, LOWBAND_AUDIT_HZ, HIGHBAND_AUDIT_HZ,
    keyboard_event_gate, spectral_references,
    add_keyboard_only_corrections, band_rms,
    protected_lowband_error_db,
)
from voice_m6_physical_event_align import RATE, read_capture, digest
from voice_m6_physical_click_ab import (
    SOURCE_MIC_SHA, SOURCE_EVENTS_SHA, SOURCE_MANIFEST_SHA,
    ALIGN_REPORT_SHA, validate_alignment, wav_write, phase_bounds,
)

A="A_006_percussive_strong"
SCENES={
    "R1_mouse_isolated":"mouse_isolated",
    "R2_keyboard_isolated":"keyboard_isolated",
    "R3_both_holdout":"both_holdout",
    "R4_voice_only_holdout":"voice_only_holdout",
    "R5_voice_clicks_holdout":"voice_clicks_holdout",
}
MAX_LOWBAND_RELATIVE_ERROR_DB=-45.0


def prepare_tracks(mic:np.ndarray,manifest:dict,
                   events:list[dict],offsets:dict,caps:dict):
    """Keep EXACT previous user-favorite C006 waveform as candidate A."""
    old_tracks,raw,existing_event_mask,voice_guard,locked,old_report=(
        previous006.build_options(mic,manifest,events,offsets,caps))
    baseline=old_tracks["C_006_percussive_strong"]
    if not np.array_equal(
            baseline[existing_event_mask==0],
            raw[existing_event_mask==0]):
        raise RuntimeError("C006 favorite already violates off-event PCM rule")
    gate=keyboard_event_gate(
        existing_event_mask,events,offsets,
        manifest["session_start_monotonic_ns"])
    short,long,method=spectral_references(
        baseline.astype(np.float64)/32768.,
        mic,voice_guard)
    variations=add_keyboard_only_corrections(
        baseline,gate,short,long)
    tracks={A:baseline,**variations}
    for name,samples in tracks.items():
        if not np.array_equal(
                samples[existing_event_mask==0],
                raw[existing_event_mask==0]):
            raise RuntimeError("Candidate changed source outside ANY event: "+name)
        if not np.array_equal(
                samples[gate==0],baseline[gate==0]):
            raise RuntimeError("Candidate changed preferred C006 outside keyboard events")
    return tracks,raw,gate,voice_guard,locked,{
        "original_006":old_report,
        "residual_spectral_model":method,
        "keyboard_event_gate_sample_fraction":round(
            float(np.mean(gate>0)),7),
        "timestamp_fields_used":["kind","t_monotonic_ns"],
        "individual_key_or_button_id_accessed":False,
        "preferred_006_C_is_frozen":True,
        "additional_change_only_during_verified_keyboard_events":True,
    }


def audit_scenes(manifest:dict,raw:np.ndarray,tracks:dict,
                 gate:np.ndarray,protection:np.ndarray,
                 locked:np.ndarray)->dict:
    t0=manifest["session_start_monotonic_ns"]
    by={p["phase"]:p for p in manifest["phases"]}
    outcome={}
    for label,phase in SCENES.items():
        a,b=phase_bounds(by[phase],t0,len(raw))
        before=band_rms(tracks[A][a:b],HIGHBAND_AUDIT_HZ)
        original=band_rms(raw[a:b],HIGHBAND_AUDIT_HZ)
        change={}
        for name,samples in tracks.items():
            if not np.array_equal(
                    samples[a:b][gate[a:b]==0],
                    tracks[A][a:b][gate[a:b]==0]):
                raise RuntimeError("Frozen C006 not preserved outside keyboard gate")
            r=band_rms(samples[a:b],HIGHBAND_AUDIT_HZ)
            change[name]={
                "vs_user_preferred_C006_db":round(
                    20*math.log10((r+1e-20)/(before+1e-20)),5),
                "vs_unprocessed_physical_mic_db":round(
                    20*math.log10((r+1e-20)/(original+1e-20)),5),
                "extra_changed_PCM16_samples":int(
                    np.count_nonzero(
                        samples[a:b]!=tracks[A][a:b])),
            }
        outcome[label]={
            "phase":phase,
            "duration_s":round((b-a)/RATE,6),
            "keyboard_event_mask_fraction":round(
                float(np.mean(gate[a:b]>0)),7),
            "voice_core_fraction":round(float(np.mean(locked[a:b])),7),
            "band_1300_9000_change_is_not_clean_click_SNR":True,
            "candidates":change,
        }
    if any(
            outcome["R1_mouse_isolated"]["candidates"][name][
                "extra_changed_PCM16_samples"]>0 for name in GAINS):
        raise RuntimeError("Mouse-only speech-free phase was modified by keyboard model")
    # The original recording's original "voice only" stage includes a
    # few keyboard markers, so it is NOT certified clean speech.
    a,b=phase_bounds(by["voice_clicks_holdout"],t0,len(raw))
    chosen=(protection[a:b]>.90)&(gate[a:b]>.05)
    if np.count_nonzero(chosen)<2000:
        raise RuntimeError("Not enough voice+keyboard physical overlap to audit")
    for name in GAINS:
        error=protected_lowband_error_db(
            tracks[A][a:b],tracks[name][a:b],chosen)
        outcome["R5_voice_clicks_holdout"]["candidates"][name][
            "new_90_650Hz_error_relative_to_C006_db"]=round(error,3)
        if error>MAX_LOWBAND_RELATIVE_ERROR_DB:
            raise RuntimeError("Physical voice fundamental safety proxy failed: "+name)
    # This is just a descriptive physical pilot gate: algorithm choices
    # were explored on the SAME recording; not an unbiased holdout.
    r5=outcome["R5_voice_clicks_holdout"]["candidates"]
    if not (r5["B_007_attack"]["vs_user_preferred_C006_db"]<0
            and r5["C_007_attack_and_tail"]["vs_user_preferred_C006_db"]
            <r5["B_007_attack"]["vs_user_preferred_C006_db"]):
        raise RuntimeError("No incremental physical highband attenuation")
    return outcome


def run(source:Path,alignment:Path,dest:Path)->dict:
    os.umask(0o077)
    if source.is_symlink() or alignment.is_symlink():
        raise ValueError("Symlinked physical capture is forbidden")
    source=source.expanduser().resolve(strict=True)
    alignment=alignment.expanduser().resolve(strict=True)
    dest=dest.expanduser().absolute()
    if (source.name!="M6-physical-events-001"
            or source.parent.name!="voice-aec"
            or source.parent.parent.name!=".audit"
            or alignment.parent.parent!=source.parent
            or dest.exists() or dest.is_symlink()
            or dest.parent.resolve(strict=True)!=source.parent):
        raise ValueError("Require existing private capture and NEW private sibling output")
    manifest,mic,events=read_capture(source)
    for name,expected in (
        ("microphone.wav",SOURCE_MIC_SHA),
        ("anonymous-events.jsonl",SOURCE_EVENTS_SHA),
        ("manifest.json",SOURCE_MANIFEST_SHA),
    ):
        if digest(source/name)!=expected:
            raise ValueError("Wrong original physical evidence SHA256 "+name)
    offsets=validate_alignment(source,alignment,manifest)
    _,energy=tail006.envelope(mic,tail006.BAND)
    caps,calibration=tail006.calibrate_tails(
        manifest,events,offsets,energy)
    tracks,raw,gate,protect,locked,methods=prepare_tracks(
        mic,manifest,events,offsets,caps)
    measured=audit_scenes(
        manifest,raw,tracks,gate,protect,locked)
    for path,expected in (
        (source/"microphone.wav",SOURCE_MIC_SHA),
        (source/"anonymous-events.jsonl",SOURCE_EVENTS_SHA),
        (source/"manifest.json",SOURCE_MANIFEST_SHA),
        (alignment,ALIGN_REPORT_SHA),
    ):
        if digest(path)!=expected:
            raise RuntimeError("Physical input mutated during investigation")
    result={
        "schema":"cuewire-M6-physical-007-keyboard-residual-AB-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "preferred_reference":"C_006_percussive_strong",
        "reference_reconstructed_with_previous_006_pipeline":True,
        "original_mic_sha256":SOURCE_MIC_SHA,
        "anonymous_events_sha256":SOURCE_EVENTS_SHA,
        "original_manifest_sha256":SOURCE_MANIFEST_SHA,
        "alignment_report_sha256":ALIGN_REPORT_SHA,
        "calibration_only_no_voice_mouse_keyboard_phases":calibration,
        "learned_tail_caps":caps,
        "model":methods,
        "three_candidates":{A:{"unchanged_preferred_006":True},**{
            name:{"attack_gain":g[0],"tail_gain":g[1]}
            for name,g in GAINS.items()}},
        "scene_metrics":measured,
        "source_and_preferred_reference_not_overwritten":True,
        "outside_keyboard_event_gate_exact_C006_PCM16":True,
        "individual_key_button_coordinates_text_collected":False,
        "noncausal_STFT_and_pitch_estimation_requires_future_audio":True,
        "speech_consonants_and_intelligibility_not_certified":True,
        "parameters_explored_on_same_original_recording":True,
        "numeric_results_descriptive_not_unbiased_generalization":True,
        "automatic_playback_or_recording":False,
        "PipeWire_routes_or_system_settings_changed":False,
        "file_sha256":{},
    }
    dest.mkdir(mode=0o700)
    phases={p["phase"]:p for p in manifest["phases"]}
    for scene,phase in SCENES.items():
        a,b=phase_bounds(phases[phase],
                         manifest["session_start_monotonic_ns"],len(raw))
        folder=dest/scene
        folder.mkdir(mode=0o700)
        for name,pcm in tracks.items():
            output=folder/(name+".wav")
            wav_write(output,pcm[a:b])
            result["file_sha256"][str(output.relative_to(dest))]=digest(output)
    report=dest/"m6-physical-007-report.json"
    report.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",
                      encoding="utf-8")
    checks={**result["file_sha256"],report.name:digest(report)}
    (dest/"SHA256SUMS").write_text(
        "".join(f"{sha}  {name}\n"
                for name,sha in sorted(checks.items())),encoding="utf-8")
    return {
        "status":"EXPERIMENTAL_NONCAUSAL",
        "private_report":str(report),
        "R5_new_vs_preferred_C006":{
            name:measured["R5_voice_clicks_holdout"]["candidates"][name]
            for name in GAINS},
        "source_microphone_changed":False,
        "automatic_playback":False,
    }


def main()->None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture",type=Path,required=True)
    parser.add_argument("--alignment",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    a=parser.parse_args()
    try:
        print(json.dumps(run(a.capture,a.alignment,a.out),
                         indent=2,sort_keys=True))
    except (OSError,ValueError,RuntimeError,TypeError,
            KeyError,FloatingPointError) as err:
        parser.exit(1,"ERROR: "+str(err)+"\n")


if __name__=="__main__":
    main()
