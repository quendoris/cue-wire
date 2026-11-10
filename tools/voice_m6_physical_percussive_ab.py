#!/usr/bin/env python3
"""M6 Physical-006: preferred C_005 + event-gated highband percussive antiwave.

Three options, maximum: A = user-preferred M6-005 C, B/C = stronger
event-local spectral separation. All private WAV remains in .audit,
no key IDs, input monitoring, recording, playback or PipeWire changes.
Only OFFLINE/noncausal analysis, not a validated speech separation.
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

import voice_m6_physical_tail_ab as tail
import voice_m6_physical_vowel_ab as vowel
from voice_m6_physical_event_align import RATE, digest, read_capture
from voice_m6_physical_click_ab import (
    SOURCE_MIC_SHA,SOURCE_EVENTS_SHA,SOURCE_MANIFEST_SHA,
    ALIGN_REPORT_SHA,validate_alignment,wav_write,
)
from voice_m6_percussive_hpss import (
    GAINS,harmonic_percussive_reference,
    add_event_limited_reference,lowband_delta_db,
)

SCENES={
    "R1_mouse_isolated":"mouse_isolated",
    "R2_keyboard_isolated":"keyboard_isolated",
    "R3_both_holdout":"both_holdout",
    "R4_voice_only_holdout":"voice_only_holdout",
    "R5_voice_clicks_holdout":"voice_clicks_holdout",
}
BASELINE_NAME="A_005_vowel_soft"
MIN_HARMONIC_SAFETY_DB=-45.0


def build_options(mic,manifest,events,offsets,caps):
    """Reuse exactly the trusted C_005 voice-safe candidate."""
    old,raw,event_mask,protection,locked,previous_report=(
        vowel.make_vowel_tracks(mic,manifest,events,offsets,caps))
    preferred=old["C_005_vowel_soft"]
    percussive,estimate=harmonic_percussive_reference(mic)
    candidates=add_event_limited_reference(
        raw,preferred,percussive,event_mask,protection)
    tracks={BASELINE_NAME:preferred,**candidates}
    return tracks,raw,event_mask,protection,locked,{
        "hpss":estimate,
        "legacy_005":previous_report,
        "extra_anti_wave_gains":GAINS,
        "external_key_button_code_read":False,
        "extra_correction_only_under_confirmed_event_and_voice_mask":True,
        "reference_estimated_from_same_present_acoustic_pcm":True,
        "speech_source_isolation_certified":False,
    }


def summarize_phases(manifest,tracks,raw,mask,protect,locked):
    filt=signal.butter(4,(1300,9000),fs=RATE,
                      btype="bandpass",output="sos")
    t0=manifest["session_start_monotonic_ns"]
    stages={p["phase"]:p for p in manifest["phases"]}
    results={}
    for scene,phase in SCENES.items():
        a,b=vowel.phase_samples(stages[phase],t0,len(raw))
        def band_rms(pcm):
            sig=signal.sosfiltfilt(
                filt,pcm[a:b].astype(float)/32768.)
            return float(np.sqrt(np.mean(sig*sig)))
        original=band_rms(raw)
        previous=band_rms(tracks[BASELINE_NAME])
        ratios={}
        for name,pcm in tracks.items():
            ratios[name]={
                "vs_original_db":round(
                    20*math.log10((band_rms(pcm)+1e-20)/
                                  (original+1e-20)),5),
                "vs_C_005_db":round(
                    20*math.log10((band_rms(pcm)+1e-20)/
                                  (previous+1e-20)),5),
            }
            if not np.array_equal(pcm[a:b][mask[a:b]==0],
                                  raw[a:b][mask[a:b]==0]):
                raise RuntimeError("Physical source altered outside events")
        extra={name: int(np.count_nonzero(
            tracks[name][a:b]!=tracks[BASELINE_NAME][a:b]))
            for name in GAINS}
        if phase in ("mouse_isolated","keyboard_isolated","both_holdout"):
            # The validated no-speech Physical-005 must remain unchanged.
            if any(extra.values()):
                raise RuntimeError("Unexpected additional antiwave without voiced human speech")
        results[scene]={
            "source_phase":phase,
            "duration_s":round((b-a)/RATE,6),
            "event_mask_fraction":round(float(np.mean(mask[a:b]!=0)),6),
            "periodic_voice_locked_fraction":round(
                float(np.mean(locked[a:b])),6),
            "additional_modified_samples":extra,
            "band_1300_9000_rms_total":ratios,
            "is_isolated_click_SNR_or_speech_quality":False,
        }
    # Essential protective audit in actual voice+keyboard mix.
    a,b=vowel.phase_samples(
        stages["voice_clicks_holdout"],t0,len(raw))
    chosen=(protect[a:b]>.90)&(mask[a:b]>.05)
    if np.count_nonzero(chosen)<2000:
        raise RuntimeError("Insufficient protected voice+event overlap to audit")
    for name in GAINS:
        delta=lowband_delta_db(
            tracks[BASELINE_NAME][a:b],tracks[name][a:b],chosen)
        results["R5_voice_clicks_holdout"].setdefault(
            "added_lowband_90_650Hz_error_relative_to_005_db",{})[
            name]=round(delta,3)
        if delta>MIN_HARMONIC_SAFETY_DB:
            raise RuntimeError(
                "Voice fundamental / low-harmonic preservation gate failed")
        if results["R5_voice_clicks_holdout"][
                "band_1300_9000_rms_total"][name]["vs_C_005_db"]>=0:
            raise RuntimeError("New spectral correction did not lower physical band RMS")
    return results


def run(source:Path,alignment:Path,dest:Path)->dict:
    os.umask(0o077)
    if source.is_symlink() or alignment.is_symlink():
        raise ValueError("Refuse linked physical evidence")
    source=source.expanduser().resolve(strict=True)
    alignment=alignment.expanduser().resolve(strict=True)
    dest=dest.expanduser().absolute()
    if (source.name!="M6-physical-events-001"
            or source.parent.name!="voice-aec"
            or source.parent.parent.name!=".audit"
            or alignment.parent.parent!=source.parent
            or dest.is_symlink() or dest.exists()
            or dest.parent.resolve(strict=True)!=source.parent):
        raise ValueError("Expected immutable private capture and fresh .audit sibling")
    manifest,mic,events=read_capture(source)
    for name,expected in (
        ("microphone.wav",SOURCE_MIC_SHA),
        ("anonymous-events.jsonl",SOURCE_EVENTS_SHA),
        ("manifest.json",SOURCE_MANIFEST_SHA),
    ):
        if digest(source/name)!=expected:
            raise ValueError("Original physical capture was modified: "+name)
    offsets=validate_alignment(source,alignment,manifest)
    _,energy=tail.envelope(mic,tail.BAND)
    caps,calibration=tail.calibrate_tails(
        manifest,events,offsets,energy)
    tracks,raw,mask,protect,locked,algorithm=build_options(
        mic,manifest,events,offsets,caps)
    metrics=summarize_phases(
        manifest,tracks,raw,mask,protect,locked)
    # Recheck SHA256 immediately before writing private output.
    for path,expected in (
        (source/"microphone.wav",SOURCE_MIC_SHA),
        (source/"anonymous-events.jsonl",SOURCE_EVENTS_SHA),
        (source/"manifest.json",SOURCE_MANIFEST_SHA),
        (alignment,ALIGN_REPORT_SHA),
    ):
        if digest(path)!=expected:
            raise RuntimeError("Immutable original changed during export")
    report={
        "schema":"cuewire-M6-physical-006-percussive-voice-AB-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "source_microphone_sha256":SOURCE_MIC_SHA,
        "source_anonymous_events_sha256":SOURCE_EVENTS_SHA,
        "source_alignment_sha256":ALIGN_REPORT_SHA,
        "previous_user_preferred_candidate":BASELINE_NAME,
        "model":algorithm,
        "tail_caps_trained_only_on_speech_free_isolated_phases":caps,
        "calibration":calibration,
        "physical_phases":metrics,
        "additional_lowband_voice_error_gate_db":MIN_HARMONIC_SAFETY_DB,
        "speaker_reference_used":False,
        "keyboard_or_mouse_identity_read":False,
        "unchanged_source_pcm_off_event_mask":True,
        "unchanged_M6_005_on_no_speech_phases":True,
        "future_samples_needed_for_STFT_and_voiced_periodicity":True,
        "offline_only_no_auto_playback":True,
        "parameter_selection_explored_on_this_same_physical_recording":True,
        "measured_energy_change_not_unbiased_click_SNR":True,
        "audible_speech_fidelity_and_click_improvement_not_certified":True,
        "file_sha256":{},
    }
    dest.mkdir(mode=0o700)
    phases={p["phase"]:p for p in manifest["phases"]}
    for scene,phase in SCENES.items():
        a,b=vowel.phase_samples(
            phases[phase],manifest["session_start_monotonic_ns"],len(raw))
        folder=dest/scene
        folder.mkdir(mode=0o700)
        for name,pcm in tracks.items():
            path=folder/(name+".wav")
            wav_write(path,pcm[a:b])
            report["file_sha256"][str(path.relative_to(dest))]=digest(path)
    report_path=dest/"m6-physical-006-report.json"
    report_path.write_text(
        json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    sums={**report["file_sha256"],report_path.name:digest(report_path)}
    (dest/"SHA256SUMS").write_text(
        "".join(f"{value}  {name}\n" for name,value in sorted(sums.items())),
        encoding="utf-8")
    return {
        "status":"EXPERIMENTAL_NONCAUSAL",
        "output":str(dest),
        "R5_voice_clicks":metrics["R5_voice_clicks_holdout"],
        "R3_nonvoice_unchanged":True,
        "original_mic_modified":False,
        "automatic_playback":False,
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture",required=True,type=Path)
    p.add_argument("--alignment",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args()
    try:
        print(json.dumps(run(args.capture,args.alignment,args.out),
                         indent=2,sort_keys=True))
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,
            FloatingPointError) as exc:
        p.exit(1,"ERROR: "+str(exc)+"\n")


if __name__=="__main__":
    main()
