#!/usr/bin/env python3
"""M6 independent synthetic voice-truth acceptance gate.

Reads generated WAVs and SHA256SUMS ONLY. Does not import or reuse the M6
estimator, no microphone, playback, input monitoring, routing, network, or
output files. The independent clean signal is the synthetic fixture truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import wave

import numpy as np

RATE = 48000
WAV_NAMES = (
    "01_physical_raw.wav",
    "02_inverted_music_estimate.wav",
    "03_inverted_click_estimate.wav",
    "04_mic_minus_music.wav",
    "05_combined_compensation.wav",
)
TRUTH_NAME = "synthetic-clean-voice-truth.wav"


def sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or linked private evidence: " + str(path))
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_pcm(path: Path) -> np.ndarray:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Refusing missing or linked WAV " + str(path))
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate(),
                w.getcomptype()) != (1, 2, RATE, "NONE"):
            raise ValueError("Expected mono 48k PCM16 " + str(path))
        n = w.getnframes()
        payload = w.readframes(n)
    if len(payload) != 2*n or n != 3*RATE:
        raise ValueError("Expected complete 3-second M6 synthetic WAV")
    return np.frombuffer(payload, dtype="<i2").astype(np.int32)


def read_manifest(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing/linked SHA256SUMS")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, sep, filename = line.partition("  ")
        if (not sep or len(digest) != 64 or
                any(ch not in "0123456789abcdef" for ch in digest) or
                filename in values or "/" in filename or "\\" in filename):
            raise ValueError("Malformed or duplicate SHA256SUMS entry")
        values[filename] = digest
    return values


def require_private(path: Path, audit: Path) -> Path:
    if path.is_symlink():
        raise ValueError("Linked private directory is forbidden")
    resolved = path.expanduser().resolve(strict=True)
    if audit not in resolved.parents or not resolved.is_dir():
        raise ValueError("Private M6 source must be under repository .audit")
    return resolved


def db_ratio(numerator: float, denominator: float) -> float:
    if not numerator > 0 or not denominator > 0:
        raise ValueError("Silent or invalid RMS prevents ratio")
    return 20.0 * math.log10(numerator / denominator)


def rms(x: np.ndarray) -> float:
    y = x.astype(np.float64)
    return float(np.sqrt(np.mean(y*y)))


def audit(fixture: Path, result: Path, audit_root: Path) -> dict:
    fixture = require_private(fixture, audit_root)
    result = require_private(result, audit_root)
    if fixture == result:
        raise ValueError("Fixture and result must differ")

    fm = read_manifest(fixture / "SHA256SUMS")
    if set(fm) != {
            "cal-mic.wav", "cal-reference.wav",
            "eval-mic.wav", "eval-reference.wav", TRUTH_NAME,
            "cal-events.jsonl", "eval-events.jsonl", "fixture.json"}:
        raise ValueError("Unexpected synthetic fixture hash manifest")
    for filename, digest in fm.items():
        if sha(fixture / filename) != digest:
            raise ValueError("Synthetic fixture hash mismatch: " + filename)
    fx = json.loads((fixture / "fixture.json").read_text(encoding="utf-8"))
    if (fx.get("schema") != "cuewire-M6-synthetic-acoustic-reference-fixture-v1"
            or fx.get("all_sources_synthetic") is not True
            or fx.get("human_speech_or_real_input_recorded") is not False):
        raise ValueError("Unknown or non-synthetic fixture")
    for filename, digest in fx["files_sha256"].items():
        if fm.get(filename) != digest:
            raise ValueError("Fixture report hash conflict: " + filename)

    rm = read_manifest(result / "SHA256SUMS")
    if set(rm) != set(WAV_NAMES) | {"m6-report.json"}:
        raise ValueError("Unexpected M6 result hash manifest")
    for filename, digest in rm.items():
        if sha(result / filename) != digest:
            raise ValueError("M6 result hash mismatch: " + filename)
    report = json.loads((result / "m6-report.json").read_text(encoding="utf-8"))
    if (report.get("schema") != "cuewire-M6-event-anchored-offline-ANC-v1"
            or report.get("state") != "experimental_offline_not_live"
            or report.get("nearend_speech_quality_proved") is not False
            or report.get("evaluation", {}).get(
                "no_nearend_learning_during_doubletalk") is not True):
        raise ValueError("Unexpected M6 report or doubletalk safety contract")
    if report["output_sha256"] != {
            name: rm[name] for name in WAV_NAMES}:
        raise ValueError("M6 report WAV hashes conflict")
    expected_input = {
        "physical_mic_sha256": fm["eval-mic.wav"],
        "playback_reference_sha256": fm["eval-reference.wav"],
        "event_markers_sha256": fm["eval-events.jsonl"],
    }
    if report["evaluation_audio"] != expected_input:
        raise ValueError("M6 result refers to a different evaluation fixture")

    data = [read_pcm(result / filename) for filename in WAV_NAMES]
    raw, anti_music, anti_clicks, only_music, combined = data
    truth = read_pcm(fixture / TRUTH_NAME)
    mic_eval = read_pcm(fixture / "eval-mic.wav")
    if not np.array_equal(raw, mic_eval):
        raise ValueError("Input mic was not exactly preserved in output")
    music_sum_max = int(np.max(np.abs(only_music-raw-anti_music)))
    both_sum_max = int(np.max(np.abs(combined-raw-anti_music-anti_clicks)))
    if music_sum_max or both_sum_max:
        raise ValueError(
            "Inverted tracks do not sum sample-exact in PCM16: "
            f"music={music_sum_max}LSB both={both_sum_max}LSB")

    error_before = rms(raw-truth)
    error_after = rms(combined-truth)
    speech = rms(truth)
    gain_db = db_ratio(error_before, error_after)
    residual_lsb = error_after
    measures = {
        "schema": "cuewire-M6-independent-synthetic-groundtruth-gate-v1",
        "fixture_sha256": fm["fixture.json"],
        "result_report_sha256": rm["m6-report.json"],
        "PCM16_exact_parallel_sums": True,
        "original_mic_PCM16_exact": True,
        "raw_voice_relative_interference_db":
            round(db_ratio(speech, error_before), 5),
        "combined_voice_relative_interference_db":
            round(db_ratio(speech, error_after), 5),
        "interference_rms_reduction_db": round(gain_db, 5),
        "combined_error_rms_PCM16_LSB": round(residual_lsb, 6),
        "no_external_audio_devices_used": True,
        "truth_is_only_synthetic": True,
        "physical_audio_performance_proved": False,
        "acceptance_thresholds": {
            "minimum_interference_reduction_db": 40.0,
            "maximum_residual_rms_pcm16_lsb": 1.0,
            "zero_pcm16_mix_diff_required": True,
        },
        "accepted": gain_db >= 40.0 and residual_lsb <= 1.0,
    }
    if not measures["accepted"]:
        raise ValueError(
            f"Insufficient synthetic truth recovery: {measures}")
    return measures


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixture", type=Path, required=True)
    p.add_argument("--result", type=Path, required=True)
    args = p.parse_args()
    audit_root = (Path(__file__).resolve().parents[1] / ".audit").resolve(
        strict=True)
    try:
        print(json.dumps(
            audit(args.fixture, args.result, audit_root),
            sort_keys=True, indent=2))
    except (OSError, ValueError, KeyError, TypeError, wave.Error) as exc:
        p.exit(1, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
