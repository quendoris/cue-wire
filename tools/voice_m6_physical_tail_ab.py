#!/usr/bin/env python3
"""M6 Physical-003: event-conditioned, dynamic acoustic-tail anti-wave pilot.

Offline-only noncausal DSP. No keyboard/button identity, audio capture, input
listener, automatic playback, system audio changes, daemon or autostart.
Uses immutable original M6 Physical-001 source and independently verified
physical event alignment. Short event windows protect OFF-EVENT raw PCM16.
All training of model duration and voice guard happens only on isolated,
speech-free physical calibration phases. No clean-doubletalk ground truth.
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
from scipy import signal, ndimage

from voice_m6_physical_event_align import RATE, KINDS, digest, read_capture
from voice_m6_physical_click_ab import (
    SOURCE_MIC_SHA, SOURCE_EVENTS_SHA, SOURCE_MANIFEST_SHA,
    ALIGN_REPORT_SHA, validate_alignment, phase_bounds, to_pcm16,
    wav_write,
)

BAND = (1300, 9000)
VOICE_BAND = (150, 850)
SCENES = {
    "R1_mouse_isolated": "mouse_isolated",
    "R2_keyboard_isolated": "keyboard_isolated",
    "R3_both_holdout": "both_holdout",
    "R4_voice_only_holdout": "voice_only_holdout",
    "R5_voice_clicks_holdout": "voice_clicks_holdout",
}
GAIN = 0.75
VOICE_QUANTILE = 0.98
VOICE_MULTIPLIER = 1.25
MIN_ISOLATED = 8


def ms(value: float) -> int:
    return round(value * RATE / 1000.)


def event_centers(events: list[dict], offsets: dict[str, int],
                  t0: int, size: int) -> list[tuple[int, str, int]]:
    if set(offsets) != set(KINDS) or any(
            type(value) is not int or abs(value) > 100
            for value in offsets.values()):
        raise ValueError("Unverified acoustic offset classes")
    result = []
    for event in events:
        kind = event["kind"]
        if kind not in KINDS or set(event) != {"kind", "t_monotonic_ns"}:
            raise ValueError("Unexpected or identifying event fields")
        center = round((event["t_monotonic_ns"]-t0)*RATE/1e9
                       - offsets[kind]*RATE/1000.)
        if ms(125) <= center < size-ms(125):
            result.append((center, kind, event["t_monotonic_ns"]))
    return result


def envelope(data: np.ndarray, band: tuple[int, int]
             ) -> tuple[np.ndarray, np.ndarray]:
    if data.ndim != 1 or len(data) < RATE or not np.all(np.isfinite(data)):
        raise ValueError("Invalid mono acoustic data")
    sos = signal.butter(4, band, btype="bandpass",
                        fs=RATE, output="sos")
    filtered = signal.sosfiltfilt(sos, data)  # OFFLINE; uses future samples
    power = ndimage.uniform_filter1d(
        filtered*filtered, size=ms(1), mode="nearest")
    return filtered, np.sqrt(np.maximum(power, 0.))


def acoustic_peak(center: int, kind: str,
                  energy: np.ndarray) -> tuple[int, float, float] | None:
    radius = ms(5 if kind.startswith("mouse") else 9)
    if center < ms(120) or center+ms(120) >= len(energy):
        return None
    peak = center-radius+int(np.argmax(
        energy[center-radius:center+radius+1]))
    background = float(np.median(
        energy[center-ms(93):center-ms(31)]))
    amplitude = float(energy[peak])
    if amplitude < .0005 or amplitude < 4*max(background, 1e-9):
        return None
    return peak, background, amplitude


def tail_samples(peak: int, background: float, amplitude: float,
                 energy: np.ndarray, max_ms: int) -> int:
    """Stop after 4/5 ms back at baseline, or frozen calibration cap."""
    end = min(len(energy)-1, peak+ms(max_ms))
    local = energy[peak:end+1]
    smooth = ndimage.uniform_filter1d(local, size=ms(2))
    below = smooth < max(1.5*background, .10*amplitude)
    duration = len(below)-1
    for index in range(ms(3), len(below)-ms(6), ms(1)):
        if np.mean(below[index:index+ms(5)]) > .8:
            duration = index
            break
    return duration


def calibrate_tails(manifest: dict, events: list[dict],
                    offsets: dict, energy: np.ndarray
                    ) -> tuple[dict[str, int], dict]:
    """Learn only from SPEAKER-OFF and NO-SPEECH isolated phases."""
    t0 = manifest["session_start_monotonic_ns"]
    phases = {phase["phase"]: phase for phase in manifest["phases"]}
    centers = event_centers(events, offsets, t0, len(energy))
    points = np.array([pos for pos, _, _ in centers], dtype=np.int64)
    caps = {}
    summary = {}
    for category in ("mouse", "keyboard"):
        phase = phases[category+"_isolated"]
        durations = []
        for pos, kind, stamp in centers:
            if (not kind.startswith(category)
                    or not phase["start_monotonic_ns"]
                    <= stamp < phase["end_monotonic_ns"]):
                continue
            # Avoid overlapping press and release acoustic calibration.
            if np.count_nonzero(np.abs(points-pos) < ms(65)) != 1:
                continue
            found = acoustic_peak(pos, kind, energy)
            if found is None:
                continue
            p, baseline, peak = found
            duration = tail_samples(p, baseline, peak, energy, 120)
            durations.append(duration/RATE*1000)
        if len(durations) < MIN_ISOLATED:
            raise ValueError("Insufficient isolated acoustic events: "+category)
        p90 = float(np.percentile(durations, 90))
        cap = int(np.clip(math.ceil(p90+12),
                          20 if category == "mouse" else 50,
                          60 if category == "mouse" else 110))
        caps[category] = cap
        summary[category] = {
            "isolated_events": len(durations),
            "tail_median_ms": round(float(np.median(durations)), 3),
            "tail_p75_ms": round(float(np.percentile(durations, 75)), 3),
            "tail_p90_ms": round(p90, 3),
            "maximum_tail_ms_frozen_before_heldout": cap,
            "training_phase": category+"_isolated",
            "no_speech_used_for_fit": True,
        }
    return caps, summary


def voice_guard(data: np.ndarray, manifest: dict
                ) -> tuple[np.ndarray, dict]:
    """Conservative *heuristic* low-band voice protection.

    Calibrate voice activity threshold ONLY from mouse/keyboard isolated
    NO-VOICE phases. Doesn't claim reliable speech/VAD classification.
    """
    low, _ = envelope(data, VOICE_BAND)
    rms18 = np.sqrt(np.maximum(0., ndimage.uniform_filter1d(
        low*low, size=ms(18), mode="nearest")))
    blocks = len(data)//48
    ms_energy = np.mean(
        rms18[:blocks*48].reshape(-1, 48), axis=1)
    sustained = ndimage.median_filter(
        ms_energy, size=61, mode="nearest")
    t0 = manifest["session_start_monotonic_ns"]
    phases = {phase["phase"]: phase for phase in manifest["phases"]}
    train = []
    for key in ("mouse_isolated", "keyboard_isolated"):
        a, b = phase_bounds(phases[key], t0, len(data))
        train.append(sustained[a//48:b//48])
    threshold = float(
        np.quantile(np.concatenate(train), VOICE_QUANTILE)
        * VOICE_MULTIPLIER)
    if threshold < 1e-6:
        raise ValueError("Insufficient voice-protection calibration level")
    excess = np.clip((sustained-threshold)/threshold, 0, 1)
    smooth = ndimage.gaussian_filter1d(excess, sigma=18)
    gain = np.clip(1-.85*smooth, .15, 1)
    samples = np.pad(
        np.repeat(gain, 48),
        (0, len(data)-len(gain)*48), mode="edge")
    return samples, {
        "training_only_speech_free": True,
        "lowband_hz": list(VOICE_BAND),
        "training_quantile": VOICE_QUANTILE,
        "threshold_multiplier": VOICE_MULTIPLIER,
        "threshold_rms": round(threshold, 9),
        "maximum_antiwave_suppression_on_detected_voice": .85,
        "speech_classification_or_quality_certified": False,
    }


def dynamic_mask(manifest: dict, events: list[dict], offsets: dict,
                 energy: np.ndarray, caps: dict
                 ) -> tuple[np.ndarray, dict]:
    n = len(energy)
    mask = np.zeros(n, dtype=np.float32)
    accepted, rejected = Counter(), Counter()
    durations = []
    for pos, kind, _ in event_centers(
            events, offsets, manifest["session_start_monotonic_ns"], n):
        detected = acoustic_peak(pos, kind, energy)
        if detected is None:
            rejected[kind] += 1
            continue
        peak, background, level = detected
        category = kind.split("_", 1)[0]
        length = tail_samples(
            peak, background, level, energy, caps[category])
        before = ms(3 if category == "mouse" else 4)
        start, end = peak-before, peak+length
        if start < 0 or end >= n:
            rejected[kind] += 1
            continue
        values = energy[peak:end+1]
        bkg = max(background, 1e-8)
        # Event-dependent excess spectral energy fraction, not a
        # global speech/noise gate or a fixed 8ms window.
        fraction = np.sqrt(
            np.maximum(0., values**2-(1.7*bkg)**2)
        ) / np.maximum(values, 1e-8)
        opening = np.sin(
            np.linspace(0, np.pi/2, before+1))**2
        taper = np.empty(before+len(fraction), dtype=np.float64)
        taper[:before] = opening[:-1]*fraction[0]
        taper[before:] = fraction
        fade = min(ms(5), len(taper))
        taper[-fade:] *= np.cos(
            np.linspace(0, np.pi/2, fade))**2
        if not np.any(taper > 0):
            rejected[kind] += 1
            continue
        mask[start:end+1] = np.maximum(
            mask[start:end+1], taper.astype(np.float32))
        durations.append(length/RATE*1000)
        accepted[kind] += 1
    if not durations:
        raise ValueError("No real acoustic event confirmed")
    return mask, {
        "accepted_events": dict(accepted),
        "rejected_events": dict(rejected),
        "modified_samples": int(np.count_nonzero(mask)),
        "modified_fraction": round(
            float(np.count_nonzero(mask))/n, 7),
        "applied_tail_median_ms": round(
            float(np.median(durations)), 3),
        "applied_tail_p90_ms": round(
            float(np.percentile(durations, 90)), 3),
        "event_fields_used": ["kind", "t_monotonic_ns"],
        "key_or_button_identity_read": False,
    }


def band_rms(x: np.ndarray, sos) -> float:
    filtered = signal.sosfiltfilt(sos, x.astype(float)/32768.)
    return float(np.sqrt(np.mean(filtered*filtered)))


def describe(manifest: dict, original: np.ndarray,
             tracks: dict[str, np.ndarray], mask: np.ndarray,
             guard: np.ndarray) -> dict:
    sos = signal.butter(
        4, BAND, btype="bandpass", fs=RATE, output="sos")
    t0 = manifest["session_start_monotonic_ns"]
    phases = {p["phase"]: p for p in manifest["phases"]}
    result = {}
    for name, phase in SCENES.items():
        a, b = phase_bounds(phases[phase], t0, len(original))
        rms_raw = band_rms(original[a:b], sos)
        entries = {}
        for role, data in tracks.items():
            if not np.array_equal(
                    data[a:b][mask[a:b] == 0],
                    original[a:b][mask[a:b] == 0]):
                raise RuntimeError("PCM16 outside event windows changed")
            entries[role] = round(
                20*math.log10(
                    (band_rms(data[a:b], sos)+1e-30)
                    / (rms_raw+1e-30)), 5)
        result[name] = {
            "phase": phase,
            "duration_s": round((b-a)/RATE, 6),
            "band_rms_change_db": entries,
            "affected_fraction": round(
                float(np.count_nonzero(mask[a:b]))/(b-a), 7),
            "mean_voice_guard": round(float(np.mean(guard[a:b])), 6),
            "original_pcm16_preserved_outside_events": True,
            "band_rms_is_not_speech_quality_or_click_truth": True,
        }
    return result


def run(source: Path, alignment: Path, output: Path) -> dict:
    os.umask(0o077)
    if source.is_symlink() or alignment.is_symlink():
        raise ValueError("Refuse linked input evidence")
    source = source.expanduser().resolve(strict=True)
    alignment = alignment.expanduser().resolve(strict=True)
    output = output.expanduser().absolute()
    if (source.name != "M6-physical-events-001"
            or source.parent.name != "voice-aec"
            or source.parent.parent.name != ".audit"
            or alignment.parent.parent != source.parent
            or output.exists() or output.is_symlink()
            or output.parent.resolve(strict=True) != source.parent):
        raise ValueError("Private immutable M6 inputs and NEW sibling output required")
    manifest, physical, events = read_capture(source)
    for name, value in (
            ("microphone.wav", SOURCE_MIC_SHA),
            ("anonymous-events.jsonl", SOURCE_EVENTS_SHA),
            ("manifest.json", SOURCE_MANIFEST_SHA)):
        if digest(source/name) != value:
            raise ValueError("Physical source hash mismatch: "+name)
    offsets = validate_alignment(source, alignment, manifest)
    highband, env = envelope(physical, BAND)
    caps, calibration = calibrate_tails(manifest, events, offsets, env)
    mask, mask_details = dynamic_mask(
        manifest, events, offsets, env, caps)
    guard, guard_details = voice_guard(physical, manifest)
    correction = -mask.astype(np.float64)*highband
    original = to_pcm16(physical)
    tracks = {
        "B_adaptive_tail": to_pcm16(physical+GAIN*correction),
        "C_voice_guard": to_pcm16(physical+GAIN*correction*guard),
    }
    for value in tracks.values():
        if not np.array_equal(value[mask == 0], original[mask == 0]):
            raise RuntimeError("Physical speech changed outside event mask")
    metrics = describe(manifest, original, tracks, mask, guard)
    if metrics["R3_both_holdout"]["band_rms_change_db"][
            "B_adaptive_tail"] >= 0:
        raise RuntimeError("Adaptive tail failed physical mixed-device check")
    # Verify immutable real inputs AGAIN before making output.
    for filename, fingerprint in (
            (source/"microphone.wav", SOURCE_MIC_SHA),
            (source/"anonymous-events.jsonl", SOURCE_EVENTS_SHA),
            (source/"manifest.json", SOURCE_MANIFEST_SHA),
            (alignment, ALIGN_REPORT_SHA)):
        if digest(filename) != fingerprint:
            raise RuntimeError("Physical audio/events changed during processing")
    report = {
        "schema": "cuewire-M6-physical-adaptive-tail-AB-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_mic_sha256": SOURCE_MIC_SHA,
        "source_events_sha256": SOURCE_EVENTS_SHA,
        "alignment_sha256": ALIGN_REPORT_SHA,
        "training_phases": ["mouse_isolated", "keyboard_isolated"],
        "tail_calibration": calibration,
        "frozen_tail_caps_ms": caps,
        "event_mask": mask_details,
        "voice_protection": guard_details,
        "scene_metrics": metrics,
        "source_pcm_modified": False,
        "noncausal_zero_phase_uses_future_audio": True,
        "automatic_playback": False,
        "system_audio_or_input_changed": False,
        "key_or_button_identity_collected": False,
        "real_speech_quality_certified": False,
        "parameters_explored_on_same_physical_recording": True,
        "heldout_energy_reduction_descriptive_not_unbiased": True,
        "pcm16_off_event_exact": True,
        "candidate_parameters": {
            "B_adaptive_tail": {"linear_gain": GAIN, "voice_guard": False},
            "C_voice_guard": {"linear_gain": GAIN, "voice_guard": True},
        },
        "output_sha256": {},
    }
    output.mkdir(mode=0o700)
    for scene, phase in SCENES.items():
        folder = output/scene
        folder.mkdir(mode=0o700)
        bounds = next(p for p in manifest["phases"] if p["phase"] == phase)
        a, b = phase_bounds(bounds, manifest["session_start_monotonic_ns"],
                            len(original))
        for name, data in {"A_original": original, **tracks}.items():
            wav_path = folder/(name+".wav")
            wav_write(wav_path, data[a:b])
            report["output_sha256"][str(wav_path.relative_to(output))] = digest(wav_path)
    report_path = output/"m6-physical-tail-report.json"
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    checks = {**report["output_sha256"], report_path.name: digest(report_path)}
    (output/"SHA256SUMS").write_text(
        "".join(f"{value}  {name}\n"
                for name, value in sorted(checks.items())),
        encoding="utf-8")
    return {
        "status": "EXPERIMENTAL_OFFLINE",
        "output": str(output),
        "tail_calibration": calibration,
        "event_mask": mask_details,
        "scene_metrics": metrics,
        "automatic_playback": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    a = parser.parse_args()
    try:
        print(json.dumps(run(a.capture, a.alignment, a.out),
                         sort_keys=True, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError,
            TypeError, FloatingPointError) as exc:
        parser.exit(1, "ERROR: "+str(exc)+"\n")


if __name__ == "__main__":
    main()
