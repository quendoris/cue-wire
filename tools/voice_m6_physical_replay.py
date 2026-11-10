#!/usr/bin/env python3
"""M6 PHYSICAL-001: reprocess EXACT SHA-pinned M5 Chaos recording OFFLINE.

Build two independent anti-music tracks (frozen and causal gain-updated)
from physical speaker-only stages and the previously verified farend
reference. Compare with RAW and native Speex; NEVER invent input event
timestamps or claim clicks suppressed. Clean voice stage is a hard PCM16
preservation gate. Four candidates per scene; no playback or system writes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
from scipy import signal

from voice_m5_aec import AUDIT, RATE, db, digest, rms
from voice_m5_chaos_reference_probe import (
    ACTUAL_MANIFEST_SHA256, ACTUAL_MIC_SHA256, ACTUAL_SONG_SHA256,
    SOURCE_SCHEMA, read_raw_wave,
)
from voice_m5_chaos_speex_real import (
    PINNED_FIRST_ALIGNMENT_SHA256, PINNED_CONSISTENCY_SHA256,
    build_farend_reference, CHANNEL_MODES,
)
from voice_m5_chaos_listen_ab import (
    ACTUAL_REAL_SPEEX_REPORT_SHA256, wav_write_exclusive,
)
from voice_m5_speex_baseline import (
    exact_pcm16, run_speex_native, load_speex,
)
from voice_m6_event_anc import predict_music

TRAIN = (12., 18.)          # frozen physical mouse+music; no near-end voice
ADAPT = (20., 26.)          # keyboard+music; no near-end voice; may contain clicks
HELDOUT = (28., 34.)        # both devices+music; NEVER used for fitting
CLEAN = (37., 43.)          # music OFF, no keyboard/mouse
SCENES = {
    "R1_clean_voice": CLEAN,
    "R2_keyboard_mouse_music": HELDOUT,
    "R3_real_doubletalk": (53., 83.),
}
TITLES = {
    "01_original_raw.wav": "raw",
    "02_speex_baseline.wav": "speex",
    "03_m6_frozen_anti_music.wav": "m6_frozen",
    "04_m6_adaptive_anti_music.wav": "m6_adaptive",
}
NFFT = 8192
TAPS = 5760              # 120 ms causal impulse response, 48kHz
RIDGE = .02
FADE = 384               # last 8 ms impulse taper
GAIN_BLOCK = 4800        # 100 ms
MAX_PREDICTED_RMS_GAIN = 5.
PEAK_CEILING = 10 ** (-3. / 20.)


def interval(times: tuple[float,float], n:int) -> tuple[int,int]:
    a, b = (round(x * RATE) for x in times)
    if not 0 <= a < b <= n:
        raise ValueError("Selected physical window outside M5 recording")
    return a, b


def in_phase(phase: dict, times: tuple[float,float], *, seconds:float=.1)->bool:
    return (phase.get("start_since_recorder_launch_s",float("inf"))+seconds <= times[0]
            and times[1] <= phase.get("end_since_recorder_launch_s",-1.)-seconds
            and phase.get("clean_voice_reference") is False)


def verify_physical(capture_dir:Path, initial_path:Path,
                    consistency_path:Path, previous_report:Path,
                    out_dir:Path) -> tuple[dict,dict,dict,np.ndarray,np.ndarray,dict]:
    audit = AUDIT.resolve(strict=True)
    paths = [capture_dir, initial_path, consistency_path, previous_report]
    paths = [p.expanduser().resolve(strict=True) for p in paths]
    capture_dir, initial_path, consistency_path, previous_report = paths
    if any(audit not in p.parents for p in paths):
        raise ValueError("M5 source evidence must remain private under .audit")
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("Never overwrite existing M6 physical output")
    out_parent = out_dir.expanduser().parent.resolve(strict=True)
    if (out_parent != capture_dir.parent or
            any(p.parent.parent != out_parent for p in
                (initial_path, consistency_path, previous_report))):
        raise ValueError("M6 output must be NEW sibling of M5 physical sources")
    manifest_path = capture_dir / "manifest.json"
    raw_path = capture_dir / "microphone-chaos-raw.wav"
    if any(p.is_symlink() for p in (manifest_path,raw_path)):
        raise ValueError("Symlinked M5 source evidence not allowed")
    expected = {
        manifest_path: ACTUAL_MANIFEST_SHA256,
        raw_path: ACTUAL_MIC_SHA256,
        initial_path: PINNED_FIRST_ALIGNMENT_SHA256,
        consistency_path: PINNED_CONSISTENCY_SHA256,
        previous_report: ACTUAL_REAL_SPEEX_REPORT_SHA256,
    }
    if any(p.is_symlink() or digest(p) != h for p,h in expected.items()):
        raise ValueError("M5 source hashes changed; refusing physical replay")
    capture = json.loads(manifest_path.read_text(encoding="utf-8"))
    first = json.loads(initial_path.read_text(encoding="utf-8"))
    consist = json.loads(consistency_path.read_text(encoding="utf-8"))
    prev = json.loads(previous_report.read_text(encoding="utf-8"))
    if (capture.get("schema") != SOURCE_SCHEMA
            or capture.get("status") != "captured_ctrl_c_at_free_stress"
            or capture.get("source_mic_sha256") != ACTUAL_MIC_SHA256
            or capture.get("music_stream_volume") != .60
            or first.get("schema") !=
                "cuewire-M5-chaos-reference-onset-lag-diagnostics-v1"
            or consist.get("schema") !=
                "cuewire-M5-chaos-multiple-window-music-lag-consistency-v1"
            or prev.get("schema") !=
                "cuewire-M5-chaos-native-speex-vs-dual-delta-real-physical-v1"):
        raise ValueError("Source M5 evidence schemas/status inconsistent")
    stages = {x.get("name"):x for x in capture.get("recorded_phases",[])}
    if not all(s in stages for s in
               ("mouse_music","keyboard_music","both_music","clean_voice","free_stress")):
        raise ValueError("M5 physical stage annotations unavailable")
    for phase_name, window in (
        ("mouse_music",TRAIN),("keyboard_music",ADAPT),
        ("both_music",HELDOUT)):
        if not in_phase(stages[phase_name],window):
            raise ValueError(f"{phase_name} window is not inside declared no-voice stage")
    clean = stages["clean_voice"]
    if (not clean.get("clean_voice_reference")
            or not clean.get("start_since_recorder_launch_s",1e10) <= CLEAN[0]
            or not CLEAN[1] <= clean.get("end_since_recorder_launch_s",-1)):
        raise ValueError("Music-OFF clean physical voice bounds not supported")
    song_info = capture.get("music_source",{})
    song_path = Path(song_info["path"]).expanduser().resolve(strict=True)
    # Historical music source may live outside .audit (e.g. /data/oae-research),
    # exactly as in the accepted M5 protocol; its pinned SHA256 is authoritative.
    if (song_path.is_symlink()
            or song_info.get("sha256") != ACTUAL_SONG_SHA256
            or digest(song_path) != ACTUAL_SONG_SHA256):
        raise ValueError("Missing/changed private original song source")
    mic = read_raw_wave(raw_path,1)[:,0]
    stereo = read_raw_wave(song_path,2,max_frames=47*RATE)
    if (len(mic) != capture["physical_microphone_analysis"]["recorded_frames"]
            or len(mic) < round(83.*RATE)):
        raise ValueError("Unexpected physical microphone length")
    events = capture.get("playback_software_start_stop_events",[])
    trials = first.get("separate_process_restarts",[])
    if len(events) != 2 or len(trials) != 2:
        raise ValueError("Expected both original music playback restarts")
    far_pcm, far_meta = build_farend_reference(
        stereo, len(mic), trials, events,
        CHANNEL_MODES["mean_both_restarts"], .60)
    return (capture, first, prev, mic, far_pcm.astype(np.float64)/32768.,
            {"original_evidence_sha256":{
                p.name: h for p,h in expected.items()},
             "original_music_sha256":ACTUAL_SONG_SHA256,
             "reference_metadata":far_meta})


def fit_frozen_physical(mic:np.ndarray, reference:np.ndarray,
                        train:tuple[int,int],
                        taps:int=TAPS)->tuple[np.ndarray,dict]:
    """Frozen 120-ms real room model: fit ONLY 12–18s prior to any holdout."""
    if taps != TAPS or len(mic) != len(reference):
        raise ValueError("Only frozen M6 physical pilot FIR configuration")
    a,b = train
    ref = reference[a:b]
    obs = mic[a:b]
    if rms(ref) < .0001 or rms(obs) < .00001:
        raise ValueError("Physical speaker-only calibration too quiet")
    _,pxx = signal.welch(ref, fs=RATE, nperseg=NFFT,
                         noverlap=NFFT//2)
    _,pxy = signal.csd(ref,obs,fs=RATE,nperseg=NFFT,
                       noverlap=NFFT//2)
    ridge = max(1e-16, RIDGE * float(np.mean(pxx)))
    impulse = np.fft.irfft(pxy/(pxx+ridge),n=NFFT)
    fir = impulse[:taps].copy()
    fir[-FADE:] *= np.linspace(1.,0.,FADE)
    if not np.all(np.isfinite(fir)) or rms(fir) > .2:
        raise ValueError("Unstable/nonfinite physical FIR")
    pred = predict_music(reference, fir)
    p = pred[a:b]
    energy = float(np.dot(p,p))
    if energy <= 1e-10:
        raise ValueError("Degenerate physical FIR music prediction")
    gain = float(np.dot(p,obs) / energy)
    if not .05 <= gain <= 3.:
        raise ValueError("Physical training gain out of [0.05,3]")
    fir *= gain
    pred = pred * gain
    if rms(pred[a:b]) > MAX_PREDICTED_RMS_GAIN * rms(obs):
        raise ValueError("Music FIR implausibly large compared with mic")
    return fir,{
        "estimator":"regularized_speaker_only_CSD_causal_FIR",
        "fir_taps":taps,"fir_tail_ms":taps/RATE*1000,
        "nfft":NFFT,"ridge_fraction":RIDGE,
        "train_bounds_seconds":list(TRAIN),
        "gain_from_training":round(gain,8),
        "clicks_present_during_training":True,
        "human_voice_present_during_training":False,
        "frozen_during_real_doubletalk":True,
    }


def gain_update_physical(mic:np.ndarray, prediction:np.ndarray,
                         adapt:tuple[int,int],
                         frames:int=GAIN_BLOCK)->tuple[np.ndarray,dict]:
    """Causal 100-ms robust amplitude adaptation in no-voice stage ONLY.

    Within each block, estimate from reference and MIC, but apply it only
    to the NEXT block. Trim large residuals to reduce keyboard-click bias.
    Never adapt in heldout 28–34s, clean speech, or doubletalk.
    """
    if len(mic) != len(prediction) or frames != GAIN_BLOCK:
        raise ValueError("Invalid M6 physical adaptation")
    begin,end = adapt
    curve = np.ones(len(mic),dtype=np.float64)
    cur = 1.
    accepted = 0
    rejected = 0
    for start in range(begin,end,frames):
        stop = min(start+frames,end)
        curve[start:stop] = cur
        if stop-start != frames:
            continue
        p = prediction[start:stop]
        y = mic[start:stop]
        energy = float(np.dot(p,p))
        if energy < 1e-6 or rms(p) < 3e-5:
            rejected += 1
            continue
        residual = y-cur*p
        threshold = max(4./32768.,3.5*float(
            np.median(np.abs(residual))))
        select = np.abs(residual) <= threshold
        if int(np.count_nonzero(select)) < int(.65*frames):
            rejected += 1
            continue
        psel,ysel = p[select],y[select]
        den = float(np.dot(psel,psel))
        if den < 1e-6:
            rejected += 1
            continue
        estimate = float(np.dot(psel,ysel)/den)
        if not np.isfinite(estimate) or not .5 <= estimate <= 1.5:
            rejected += 1
            continue
        cur = float(np.clip(.85*cur+.15*estimate,.5,1.5))
        accepted += 1
    curve[end:] = cur
    return curve,{
        "gain_updates_accepted":accepted,
        "gain_updates_rejected":rejected,
        "gain_update_time_seconds":list(ADAPT),
        "gain_update_uses_only_no_voice_phase":True,
        "click_contamination_possible_despite_robust_trimming":True,
        "heldout_28_34_used_for_fit":False,
        "frozen_gain_after_adaptation":round(cur,7),
        "frozen_during_physical_doubletalk":True,
        "influence_of_future_mic_on_earlier_gain":False,
    }


def describe_window(raw:np.ndarray, far:np.ndarray,
                    tracks:dict[str,np.ndarray],
                    bounds:tuple[float,float])->dict:
    a,b = interval(bounds,len(raw))
    out = {}
    # Hold fixed source-reference PSD weights for every output comparison.
    # Magnitude-squared coherence is a reference-link proxy, NOT a clean
    # voice truth metric. It can be biased by phase drift and other sources.
    active_ref = rms(far[a:b]) > 1e-5
    if active_ref:
        frequency, reference_psd = signal.welch(
            far[a:b], fs=RATE, nperseg=4096, noverlap=2048)
        band = (frequency >= 200) & (frequency <= 4000)
        weight = reference_psd[band]
        weight_sum = float(np.sum(weight))
        if weight_sum <= 1e-20:
            active_ref = False
    for name,track in tracks.items():
        item = {
            "rms_dbfs":round(db(rms(track[a:b])),5),
            "change_rms_dbfs":round(db(rms(track[a:b]-raw[a:b])),5)
        }
        if active_ref:
            frequencies, coherence = signal.coherence(
                far[a:b], track[a:b], fs=RATE,
                nperseg=4096, noverlap=2048)
            if not np.array_equal(frequencies, frequency):
                raise ValueError("Incompatible reference coherence frequency grid")
            item["source_psd_weighted_coherence_200_4000hz"] = round(
                float(np.dot(coherence[band], weight) / weight_sum), 6)
        out[name] = item
    # On mixed sound this is NOT ERLE nor speaker-only echo removal.
    out["reference_presence_rms_dbfs"] = round(db(rms(far[a:b])),5)
    out["source_bound_seconds"] = list(bounds)
    out["not_an_echo_specific_or_speech_quality_metric"] = True
    return out


def run(capture:Path,initial:Path,consistency:Path,previous:Path,
        out:Path, native_loader=load_speex)->dict:
    os.umask(0o077)
    old,first,study,mic,far,evidence = verify_physical(
        capture, initial, consistency, previous, out)
    raw_pcm = exact_pcm16(mic)
    far_pcm = exact_pcm16(far)
    # M5's reference was originally placed 10ms ahead of detected mic
    # onset; use the SAME frozen aligned reference for both M6 and Speex
    # to avoid unfair hidden alignment advantages.
    train = interval(TRAIN,len(mic))
    adapt = interval(ADAPT,len(mic))
    fir, fit = fit_frozen_physical(mic,far,train)
    predicted = predict_music(far,fir)
    if np.any(~np.isfinite(predicted)):
        raise ValueError("Nonfinite physical music predictor")
    gaincurve, dynamics = gain_update_physical(mic,predicted,adapt)
    frozen = mic - predicted
    adaptive = mic - predicted*gaincurve
    lib,path = native_loader()
    speex,speex_metadata = run_speex_native(
        raw_pcm, far_pcm, library=lib, library_name=path)
    tracks = {
        "raw":mic,
        "speex":speex,
        "m6_frozen":frozen,
        "m6_adaptive":adaptive,
    }
    if any(len(x) != len(mic) or not np.all(np.isfinite(x))
           for x in tracks.values()):
        raise ValueError("Physical replay tracks are invalid")
    global_peak = max(float(np.max(np.abs(v))) for v in tracks.values())
    if not 0 < global_peak < .98:
        raise ValueError("Unsafe physical output peak / clipping")
    common_gain = min(1.,PEAK_CEILING/global_peak)
    a,b = interval(CLEAN,len(mic))
    clean_control = {}
    shared_clean_raw = exact_pcm16(mic[a:b] * common_gain)
    for role in ("m6_frozen","m6_adaptive"):
        difference = np.count_nonzero(
            exact_pcm16(tracks[role][a:b]) != raw_pcm[a:b])
        postgain_diff = np.count_nonzero(
            exact_pcm16(tracks[role][a:b]*common_gain)
            != shared_clean_raw)
        if difference or postgain_diff:
            raise ValueError(
                f"Physical clean-voice PCM16 preservation failed: {role} "
                f"before_gain={difference} after_shared_gain={postgain_diff}")
        clean_control[role] = {
            "pcm16_changed_samples_before_gain":0,
            "pcm16_changed_samples_after_shared_gain":0,
            "checked_frames":b-a}
    # Source data must still be bit-exact just before writing ANY new WAVs.
    audit_sources = [
        (capture/"manifest.json", ACTUAL_MANIFEST_SHA256),
        (capture/"microphone-chaos-raw.wav", ACTUAL_MIC_SHA256),
        (initial, PINNED_FIRST_ALIGNMENT_SHA256),
        (consistency, PINNED_CONSISTENCY_SHA256),
        (previous, ACTUAL_REAL_SPEEX_REPORT_SHA256),
        (Path(old["music_source"]["path"]).expanduser(),
         ACTUAL_SONG_SHA256),
    ]
    if any(digest(path) != expected for path,expected in audit_sources):
        raise RuntimeError("Pinned physical evidence changed during DSP")
    # All processing is complete before creating an output directory.
    audit = AUDIT.resolve(strict=True)
    out_parent = out.expanduser().parent.resolve(strict=True)
    dest = out_parent/out.name
    if dest.exists() or dest.is_symlink() or audit not in dest.parents:
        raise FileExistsError("Output exists or not in .audit")
    metrics = {
        name:describe_window(mic,far,tracks,span)
        for name,span in {
            "training_mouse_music":TRAIN,
            "adaptive_training_keyboard_music":ADAPT,
            "heldout_both_devices_music":HELDOUT,
            "clean_voice_speaker_off":CLEAN,
            "real_doubletalk":(53.,83.)
        }.items()
    }
    report = {
        "schema":"cuewire-M6-physical-M5-replay-music-only-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "source_recording":"M5-chaos-001-original-90s-physical-mic",
        "source_sha256":evidence,
        "strategy":"music_ANC_from_independent_known_PCM_reference",
        "mechanical_event_timestamps_exist":False,
        "physical_mouse_keyboard_event_compensation_performed":False,
        "clicks_not_claimed_removed":True,
        "originals_modified":False,
        "automatic_playback":False,
        "automatic_recording":False,
        "audio_routing_or_key_bindings_changed":False,
        "nearend_doubletalk_speech_quality_proved":False,
        "music_model":fit,
        "dynamic_gain":dynamics,
        "clean_voice_PCM16_gate_before_shared_gain":clean_control,
        "replayed_standard_speex":speex_metadata,
        "global_common_gain":{"linear":common_gain,
                              "db":20*math.log10(common_gain),
                              "applies_to_all_variants_and_scenes":True,
                              "no_compressor_or_limiter":True},
        "windows":metrics,
        "output_sha256":{},
    }
    dest.mkdir(mode=0o700)
    for scene, bounds in SCENES.items():
        folder=dest/scene
        folder.mkdir(mode=0o700)
        start,stop=interval(bounds,len(mic))
        for filename,role in TITLES.items():
            samples=exact_pcm16(tracks[role][start:stop]*common_gain)
            wav_write_exclusive(folder/filename,samples)
            report["output_sha256"][f"{scene}/{filename}"] = digest(folder/filename)
    (dest/"physical-m6-report.json").write_text(
        json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    report["output_sha256"]["physical-m6-report.json"] = digest(
        dest/"physical-m6-report.json")
    (dest/"SHA256SUMS").write_text("".join(
        f"{hashval}  {filename}\n"
        for filename,hashval in report["output_sha256"].items()),
        encoding="utf-8")
    return {
        "schema":report["schema"],
        "output":str(dest),
        "physical_90s_recording_reused":True,
        "n_wavs":len(SCENES)*len(TITLES),
        "all_sources_pinned_sha256":True,
        "clean_voice_PCM16_gate":clean_control,
        "heldout_energy_proxies":metrics["heldout_both_devices_music"],
        "doubletalk_energy_proxies":metrics["real_doubletalk"],
        "dynamic_gain":dynamics,
        "common_gain_db":round(20*math.log10(common_gain),5),
        "actual_mouse_keyboard_event_compensation":False,
    }


def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture",required=True,type=Path)
    p.add_argument("--initial-alignment",required=True,type=Path)
    p.add_argument("--consistency",required=True,type=Path)
    p.add_argument("--previous-report",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args()
    try:
        print(json.dumps(run(
            args.capture,args.initial_alignment,args.consistency,
            args.previous_report,args.out),indent=2,sort_keys=True))
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,
            np.linalg.LinAlgError) as err:
        p.exit(1,"ERROR: "+str(err)+"\n")


if __name__=="__main__":
    main()
