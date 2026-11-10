#!/usr/bin/env python3
"""M6 physical event/audio cross-validation, using existing private capture.

Train effective acoustic-to-kernel event offsets on ISOLATED mouse-only and
keyboard-only phases. Verify on separate BOTH-device heldout phase with
phase-preserving circular timestamp permutation. This tool DOES NOT know
microphone sample-zero monotonic ns separately from physical click-onset
delay; reported offsets are EMPIRICAL COMPOSITES, not hardware latency.

No actual key/button codes, text, coordinates, secrets or individual event
times are written to output. No input devices, audio devices, PipeWire, capture,
playback, environment modifications, daemons, autostart or network operations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import wave

import numpy as np
from scipy import signal
from scipy.ndimage import gaussian_filter1d, median_filter

RATE = 48000
BIN = 48  # exact 1 ms bins
KINDS = (
    "mouse_press", "mouse_release",
    "keyboard_press", "keyboard_release",
)
PARTS = ("quiet_baseline", "mouse_isolated",
         "keyboard_isolated", "both_holdout",
         "voice_only_holdout", "voice_clicks_holdout")
MIN_TRAIN = 8
MIN_HELDOUT = 8
N_PERM = 2000
SCORE_WINDOW = (-2, 5)   # 1 ms bins around expected acoustic impulse
SEARCH_MS = range(-50, 101)  # class-dependent EFFECTIVE alignment
SEED = 20261110


def digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or linked physical evidence: " + str(path))
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_capture(source: Path) -> tuple[dict, np.ndarray, list[dict]]:
    if not source.is_dir() or source.name != "M6-physical-events-001":
        raise ValueError("Expected private M6 physical event recording")
    allowed = ("microphone.wav", "anonymous-events.jsonl",
               "pw-record.stderr.log")
    manifest_path = source / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Linked manifest not allowed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema") != "cuewire-M6-physical-anonymous-events-v1"
            or manifest.get("status") != "capture_complete"
            or manifest.get("input_class_only") is not True
            or manifest.get("sample_zero_measured") is not False
            or manifest.get("no_key_event_identity_collected") is not True):
        raise ValueError("Unexpected physical anonymized capture")
    expected = manifest.get("capture_files_sha256", {})
    if set(expected) != set(allowed):
        raise ValueError("Unexpected capture file inventory")
    for name in allowed:
        if digest(source / name) != expected[name]:
            raise ValueError("SHA256 mismatch in source: " + name)
    checks = {}
    for line in (source / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        sha, sep, name = line.partition("  ")
        if not sep or name in checks:
            raise ValueError("Malformed/duplicate SHA256SUMS entry")
        checks[name] = sha
    if checks != {**expected, "manifest.json": digest(manifest_path)}:
        raise ValueError("SHA256SUMS does not match immutable capture")

    wave_path = source / "microphone.wav"
    with wave.open(str(wave_path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate(),
                w.getcomptype()) != (1, 2, RATE, "NONE"):
            raise ValueError("Physical microphone needs 48 kHz mono PCM16")
        n = w.getnframes()
        raw = w.readframes(n)
    if not 40*RATE < n < 55*RATE or len(raw) != 2*n:
        raise ValueError("Unexpected physical microphone duration")
    if manifest["microphone"].get("frames") != n:
        raise ValueError("Physical WAV length contradicts manifest")
    mic = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.
    if np.count_nonzero(np.abs(mic) >= 1.) > 0:
        raise ValueError("Clipped physical input")

    events = []
    prev = -1
    for lineno, line in enumerate(
            (source / "anonymous-events.jsonl").read_text(
                encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"Empty JSONL row {lineno}")
        row = json.loads(line)
        if (not isinstance(row, dict)
                or set(row) != {"kind", "t_monotonic_ns"}
                or row["kind"] not in KINDS
                or type(row["t_monotonic_ns"]) is not int
                or row["t_monotonic_ns"] < prev):
            raise ValueError(f"Invalid, unsorted or privacy-leaking event {lineno}")
        prev = row["t_monotonic_ns"]
        events.append(row)
    if Counter(row["kind"] for row in events) != Counter(
            manifest.get("event_counts_by_class", {})):
        raise ValueError("Event counts mismatch capture manifest")
    phases = manifest.get("phases", [])
    if [p.get("phase") for p in phases] != list(PARTS):
        raise ValueError("Missing prescribed isolated and heldout stages")
    for phase in phases:
        start, stop = (phase.get("start_monotonic_ns"),
                       phase.get("end_monotonic_ns"))
        if (type(start) is not int or type(stop) is not int
                or not start < stop):
            raise ValueError("Invalid capture phase monotonic bounds")
        observed = Counter(row["kind"] for row in events
                           if start <= row["t_monotonic_ns"] < stop)
        if observed != Counter(phase["event_counts_by_class"]):
            raise ValueError("Stage event counts mismatch")
    if (events[0]["t_monotonic_ns"] < phases[0]["start_monotonic_ns"]
            or events[-1]["t_monotonic_ns"] > phases[-1]["end_monotonic_ns"]):
        raise ValueError("Events fall outside isolated capture")
    return manifest, mic, events


def transient_feature(mic: np.ndarray) -> np.ndarray:
    """One-millisecond, highpass transient strength with local baseline removed."""
    if mic.ndim != 1 or len(mic) < RATE:
        raise ValueError("Invalid acoustic waveform")
    sos = signal.butter(
        4, [1300, 9000], btype="bandpass",
        fs=RATE, output="sos")
    filtered = signal.sosfilt(sos, mic)
    usable = len(filtered) // BIN * BIN
    envelope = np.sqrt(
        np.mean(filtered[:usable].reshape(-1, BIN)**2, axis=1))
    local = median_filter(envelope, size=251, mode="nearest")
    return gaussian_filter1d(
        np.log1p(np.maximum(0., envelope-local) * 10000.), 1.)


def timed_events(manifest: dict, events: list[dict],
                 phase_name: str, kind: str) -> np.ndarray:
    start = manifest["session_start_monotonic_ns"]
    phase = next(x for x in manifest["phases"] if x["phase"] == phase_name)
    return np.array([
        (e["t_monotonic_ns"]-start) / 1e6
        for e in events if e["kind"] == kind
        and phase["start_monotonic_ns"] <= e["t_monotonic_ns"]
        < phase["end_monotonic_ns"]
    ], dtype=np.float64)


def score_times(feature: np.ndarray, stamps_ms: np.ndarray,
                offset_ms: float) -> float:
    """Mean of local max transient strength; reject out-of-range instead of trim."""
    if len(stamps_ms) == 0:
        raise ValueError("No event timestamps for event score")
    indices = np.rint(stamps_ms - offset_ms).astype(np.int64)
    if np.any(indices + SCORE_WINDOW[0] < 0) or np.any(
            indices + SCORE_WINDOW[1] >= len(feature)):
        raise ValueError("Timestamp maps outside physical WAV")
    return float(np.mean([
        np.max(feature[i+SCORE_WINDOW[0]:i+SCORE_WINDOW[1]+1])
        for i in indices
    ]))


def align(manifest: dict, mic: np.ndarray, events: list[dict],
          n_perm: int = N_PERM) -> dict:
    if not 100 <= n_perm <= 20000:
        raise ValueError("Unsupported permutation count")
    if manifest.get("schema") != "cuewire-M6-physical-anonymous-events-v1":
        raise ValueError("Missing/invalid physical capture schema")
    checks = manifest.get("capture_files_sha256", {})
    if (not isinstance(checks, dict) or
            not all(isinstance(checks.get(key), str)
                    and len(checks[key]) == 64
                    for key in ("microphone.wav", "anonymous-events.jsonl"))):
        raise ValueError("Missing physical capture SHA256 metadata")
    feature = transient_feature(mic)
    base_ns = manifest["session_start_monotonic_ns"]
    held = next(x for x in manifest["phases"]
                if x["phase"] == "both_holdout")
    lo = (held["start_monotonic_ns"]-base_ns)/1e6
    hi = (held["end_monotonic_ns"]-base_ns)/1e6
    span = hi-lo
    if span < 5000.:
        raise ValueError("Heldout phase too short")
    output = {}
    for idx, kind in enumerate(KINDS):
        train_name = ("mouse_isolated" if kind.startswith("mouse")
                      else "keyboard_isolated")
        times_train = timed_events(manifest, events, train_name, kind)
        times_test = timed_events(manifest, events, "both_holdout", kind)
        if len(times_train) < MIN_TRAIN or len(times_test) < MIN_HELDOUT:
            raise ValueError("Not enough isolated/heldout class events: " + kind)
        fit_scores = np.array([
            score_times(feature, times_train, shift) for shift in SEARCH_MS])
        best_ms = int(np.argmax(fit_scores)) + SEARCH_MS.start
        observed = score_times(feature, times_test, best_ms)
        rng = np.random.default_rng(SEED + idx)
        controls = np.empty(n_perm)
        for i in range(n_perm):
            # Circular shift INSIDE heldout only; preserve intra-event rhythm.
            shift = rng.uniform(250., span - 250.)
            surrogate = (times_test-lo+shift) % span + lo
            controls[i] = score_times(feature, surrogate, best_ms)
        p_value = float((1 + np.count_nonzero(controls >= observed)) /
                        (1 + n_perm))
        output[kind] = {
            "isolated_training_events":len(times_train),
            "heldout_test_events":len(times_test),
            "effective_acoustic_offset_ms":best_ms,
            "training_transient_score":round(float(np.max(fit_scores)),6),
            "heldout_transient_score":round(observed,6),
            "heldout_circular_null_mean":round(float(np.mean(controls)),6),
            "heldout_circular_null_p95":round(
                float(np.quantile(controls,.95)),6),
            "heldout_permutation_p_upper":round(p_value,7),
            "passed_independent_holdout":bool(p_value <= .01),
        }
    return {
        "schema":"cuewire-M6-physical-event-audio-alignment-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "source_schema":manifest["schema"],
        "source_mic_sha256":manifest["capture_files_sha256"]["microphone.wav"],
        "source_events_sha256":manifest["capture_files_sha256"]["anonymous-events.jsonl"],
        "source_manifest_sha256":None,
        "training_phases":["mouse_isolated","keyboard_isolated"],
        "heldout_phase":"both_holdout",
        "all_training_and_test_data_from_physical_mic":True,
        "mic_sample_zero_monotonic_ns_separately_identified":False,
        "effective_offset_is_mix_of_audio_clock_and_physical_event_timing":True,
        "button_or_key_identity_read":False,
        "speaker_reference_used":False,
        "voice_and_clicks_test_waveform_modified":False,
        "mechanical_click_cancellation_performed":False,
        "heldout_permutations":n_perm,
        "random_seed":SEED,
        "class_results":output,
        "acceptance":all(v["passed_independent_holdout"]
                         for v in output.values()),
        "physical_speech_preservation_certified":False,
        "real_time_latency_certified":False,
    }


def run(source: Path, out: Path) -> dict:
    os.umask(0o077)
    source = source.expanduser().resolve(strict=True)
    if (source.parent.name != "voice-aec"
            or source.parent.parent.name != ".audit"
            or source.is_symlink()):
        raise ValueError("Source must be private .audit/voice-aec/M6-physical-events-001")
    out = out.expanduser().absolute()
    if (out.is_symlink() or out.exists()
            or out.parent.resolve(strict=True) != source.parent):
        raise ValueError("Destination must be NEW private M6 sibling")
    manifest,mic,events = read_capture(source)
    outcome = align(manifest,mic,events)
    outcome["source_manifest_sha256"] = digest(source/"manifest.json")
    # Verify source hashes again after computation to refuse changed inputs.
    for name, expected in {
        **manifest["capture_files_sha256"],
        "manifest.json":outcome["source_manifest_sha256"],
    }.items():
        if digest(source/name) != expected:
            raise RuntimeError("Physical source mutated while aligning")
    out.mkdir(mode=0o700)
    name = "m6-physical-event-alignment.json"
    payload = json.dumps(outcome,sort_keys=True,indent=2)+"\n"
    (out/name).write_text(payload,encoding="utf-8")
    (out/"SHA256SUMS").write_text(
        f"{digest(out/name)}  {name}\n",encoding="utf-8")
    return outcome


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    try:
        result=run(args.capture,args.out)
        print(json.dumps(result,indent=2,sort_keys=True))
    except (OSError,ValueError,RuntimeError,KeyError,
            TypeError,wave.Error) as exc:
        p.exit(1,"ERROR: "+str(exc)+"\n")


if __name__ == "__main__":
    main()
