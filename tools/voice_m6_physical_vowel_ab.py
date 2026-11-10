#!/usr/bin/env python3
"""M6 Physical-005: voiced-vowel continuity vs aggressive click attenuation.

Compare former M6-004 voice guard (A), M6-004 aggressive anti-wave with
full speech bypass (B) and 10% residual speech-region anti-wave (C).
No keyboard/button identity, no recording, PipeWire, background process,
automatic playback, routing, or system changes.

Uses only immutable private original M6 physical WAV and anonymous events,
verified source hashes, and independently established event/audio lags.
Pitch periodicity fitted to speech-FREE isolated phases, never R5 speech.
Offline NONCAUSAL; NOT a real-time filter or proven source separation.
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

import voice_m6_physical_aggressive_ab as old
import voice_m6_physical_tail_ab as tail
from voice_m6_physical_vowel_guard import (
    integer_dry_wet, phase_samples, voicing_protection,
)
from voice_m6_physical_event_align import RATE, read_capture, digest
from voice_m6_physical_click_ab import (
    SOURCE_MIC_SHA, SOURCE_EVENTS_SHA, SOURCE_MANIFEST_SHA,
    ALIGN_REPORT_SHA, validate_alignment, wav_write,
)

SCENES = {
    'R1_mouse_isolated':'mouse_isolated',
    'R2_keyboard_isolated':'keyboard_isolated',
    'R3_both_holdout':'both_holdout',
    'R4_voice_only_holdout':'voice_only_holdout',
    'R5_voice_clicks_holdout':'voice_clicks_holdout',
}
# Experimental audible compromise: preserves 90% of original signal
# rather than aggressively cutting voiced audio.
PARTIAL_VOICED_ANTI_FRACTION = .10


def make_vowel_tracks(mic:np.ndarray, manifest:dict,
                      events:list[dict], offsets:dict, caps:dict):
    old_tracks, raw, event_mask, event_report = old.make_tracks(
        mic, manifest, events, offsets, caps)
    protection, locked, guard_report = voicing_protection(mic,manifest)
    if protection.shape!=mic.shape or locked.shape!=mic.shape:
        raise RuntimeError('Voice protector output is not sample aligned')
    aggressive=old_tracks['B_004_aggressive']
    previous_guard=old_tracks['C_004_voice_guard']
    wet_lock=1.0-protection
    wet_partial=1.0-(1.0-PARTIAL_VOICED_ANTI_FRACTION)*protection
    result={
        'A_004_voice_guard':previous_guard,
        'B_005_vowel_lock':integer_dry_wet(raw,aggressive,wet_lock),
        'C_005_vowel_soft':integer_dry_wet(raw,aggressive,wet_partial),
    }
    if not np.array_equal(result['B_005_vowel_lock'][locked],raw[locked]):
        raise RuntimeError('Sustained vowel samples are not EXACT original PCM')
    for name,pcm in result.items():
        if not np.array_equal(pcm[event_mask==0],raw[event_mask==0]):
            raise RuntimeError('Off-event PCM differs from original microphone: '+name)
    return result,raw,event_mask,protection,locked,{
        'voice_guard':guard_report,
        'aggressive_detector':event_report,
        'protected_voice_core_has_exact_source_pcm16':True,
        'same_source_event_mask_as_M6_004':True,
        'heldout_speech_not_used_for_voice_level_calibration':True,
        'user_key_or_button_identity_accessed':False,
    }


def measure_scene(raw:np.ndarray, candidates:dict[str,np.ndarray],
                  manifest:dict, mask:np.ndarray,
                  protection:np.ndarray,locked:np.ndarray)->dict:
    t0=manifest['session_start_monotonic_ns']
    phase_map={p['phase']:p for p in manifest['phases']}
    sos=signal.butter(4,(1300,9000),btype='bandpass',
                      fs=RATE,output='sos')
    output={}
    for label,phase_name in SCENES.items():
        a,b=phase_samples(phase_map[phase_name],t0,len(raw))
        def rms(z):
            q=signal.sosfiltfilt(
                sos,z[a:b].astype(np.float64)/32768.)
            return float(np.sqrt(np.mean(q*q)))
        baseline=rms(raw)
        results={}
        for name,pcm in candidates.items():
            if not np.array_equal(pcm[a:b][mask[a:b]==0],
                                  raw[a:b][mask[a:b]==0]):
                raise RuntimeError('An exported scene modifies off-event mic')
            diff=20*math.log10((rms(pcm)+1e-30)/(baseline+1e-30))
            results[name]=round(diff,5)
        protected_fraction=float(np.mean(locked[a:b]))
        true_core=locked[a:b]
        output[label]={
            'source_phase':phase_name,
            'duration_s':round((b-a)/RATE,6),
            'protected_voice_core_fraction':round(protected_fraction,6),
            'mean_protection_strength':round(
                float(np.mean(protection[a:b])),6),
            'original_exact_under_005_B_protected_core':bool(
                np.array_equal(candidates['B_005_vowel_lock'][a:b][true_core],
                               raw[a:b][true_core])),
            'event_mask_fraction':round(float(np.mean(mask[a:b]!=0)),6),
            'band_1300_9000_total_rms_db_vs_original':results,
            'is_voice_quality_or_click_snr_measurement':False,
        }
    # A gate must NOT suppress clicks by incorrectly classifying
    # a keyboard-only scene as a continuous voiced human utterance.
    if any(output[k]['protected_voice_core_fraction']>.02
           for k in ('R1_mouse_isolated','R2_keyboard_isolated','R3_both_holdout')):
        raise RuntimeError('False positive voicing would hide real keyboard clicks')
    # A gate that doesn't identify voice in either speech stage cannot
    # address the operator's observed long vowel truncation.
    if any(output[k]['protected_voice_core_fraction']<.2
           for k in ('R4_voice_only_holdout','R5_voice_clicks_holdout')):
        raise RuntimeError('Voice core detector did not locate recorded speech')
    if not all(s['original_exact_under_005_B_protected_core']
               for s in output.values()):
        raise RuntimeError('Voice source PCM changed while protected')
    return output


def run(source:Path,alignment:Path,dest:Path)->dict:
    os.umask(0o077)
    if source.is_symlink() or alignment.is_symlink():
        raise ValueError('No symlinked private source evidence')
    source=source.expanduser().resolve(strict=True)
    alignment=alignment.expanduser().resolve(strict=True)
    dest=dest.expanduser().absolute()
    if (source.name!='M6-physical-events-001'
            or source.parent.name!='voice-aec'
            or source.parent.parent.name!='.audit'
            or alignment.parent.parent!=source.parent
            or dest.is_symlink() or dest.exists()
            or dest.parent.resolve(strict=True)!=source.parent):
        raise ValueError('Expected immutable private M6 physical evidence and NEW sibling output')
    manifest,mic,events=read_capture(source)
    for file,expected in (
        ('microphone.wav',SOURCE_MIC_SHA),
        ('anonymous-events.jsonl',SOURCE_EVENTS_SHA),
        ('manifest.json',SOURCE_MANIFEST_SHA),
    ):
        if digest(source/file)!=expected:
            raise ValueError('Original physical evidence changed: '+file)
    offsets=validate_alignment(source,alignment,manifest)
    _,energy=tail.envelope(mic,tail.BAND)
    caps,calibration=tail.calibrate_tails(manifest,events,offsets,energy)
    tracks,raw,mask,guard,locked,model_report=make_vowel_tracks(
        mic,manifest,events,offsets,caps)
    scene_report=measure_scene(raw,tracks,manifest,mask,guard,locked)
    for path,expected in (
        (source/'microphone.wav',SOURCE_MIC_SHA),
        (source/'anonymous-events.jsonl',SOURCE_EVENTS_SHA),
        (source/'manifest.json',SOURCE_MANIFEST_SHA),
        (alignment,ALIGN_REPORT_SHA),
    ):
        if digest(path)!=expected:
            raise RuntimeError('Input mutated during offline vowel continuity trial')
    report={
        'schema':'cuewire-M6-physical-005-vowel-continuity-AB-v1',
        'created_utc':datetime.now(timezone.utc).isoformat(),
        'source_mic_sha256':SOURCE_MIC_SHA,
        'source_events_sha256':SOURCE_EVENTS_SHA,
        'source_manifest_sha256':SOURCE_MANIFEST_SHA,
        'verified_alignment_sha256':ALIGN_REPORT_SHA,
        'training_phases':['mouse_isolated','keyboard_isolated'],
        'voice_detection':'90-490Hz periodicity + source-level calibration + speech-end hold',
        'model':model_report,
        'frozen_acoustic_tail_caps_ms':caps,
        'acoustic_calibration':calibration,
        'scenes':scene_report,
        'choice_A':'old M6-004 voice guard',
        'choice_B':'M6-004 aggressive except exact unmodified PCM16 in locked vowel',
        'choice_C':'M6-004 aggressive except 90 percent bypass in locked vowel',
        'output_original_microphone_modified':False,
        'source_pcm16_exact_outside_event_mask':True,
        'offline_noncausal_processing':True,
        'autoplay_or_recording':False,
        'system_audio_settings_modified':False,
        'no_key_button_text_coordinates_captured':True,
        'speech_preservation_not_certified_for_unvoiced_or_missed_speech':True,
        'overlapping_click_reduction_during_voiced_bypass_may_be_weaker':True,
        'parameter_selection_used_same_physical_recording':True,
        'descriptive_metrics_not_unbiased_generalization':True,
        'output_sha256':{},
    }
    dest.mkdir(mode=0o700)
    phase_map={p['phase']:p for p in manifest['phases']}
    for scene,phase in SCENES.items():
        folder=dest/scene
        folder.mkdir(mode=0o700)
        a,b=phase_samples(phase_map[phase],
                          manifest['session_start_monotonic_ns'],
                          len(raw))
        for key,pcm in tracks.items():
            file=folder/(key+'.wav')
            wav_write(file,pcm[a:b])
            report['output_sha256'][str(file.relative_to(dest))]=digest(file)
    path=dest/'m6-physical-005-report.json'
    path.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n',
                    encoding='utf-8')
    hashes={**report['output_sha256'],path.name:digest(path)}
    (dest/'SHA256SUMS').write_text(
        ''.join(f'{sha}  {file}\n' for file,sha in sorted(hashes.items())),
        encoding='utf-8')
    return {
        'status':'EXPERIMENTAL_OFFLINE',
        'private_output':str(dest),
        'voice_protected_fraction_R5':scene_report[
            'R5_voice_clicks_holdout']['protected_voice_core_fraction'],
        'band_rms_R5':scene_report[
            'R5_voice_clicks_holdout']['band_1300_9000_total_rms_db_vs_original'],
        'band_rms_R3':scene_report[
            'R3_both_holdout']['band_1300_9000_total_rms_db_vs_original'],
        'voice_B_pcm16_exact_in_protected_core':True,
        'automatic_playback':False,
        'no_capture':True,
    }


def main()->None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',required=True,type=Path)
    parser.add_argument('--alignment',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    try:
        print(json.dumps(run(args.capture,args.alignment,args.out),
                         sort_keys=True,indent=2))
    except (OSError,ValueError,RuntimeError,KeyError,
            TypeError,FloatingPointError) as err:
        parser.exit(1,'ERROR: '+str(err)+'\n')


if __name__=='__main__':
    main()
