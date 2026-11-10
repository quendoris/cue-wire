#!/usr/bin/env python3
"""Generate a deterministic, fully synthetic M6 end-to-end fixture in .audit.

There is NO recording, PipeWire access, key logging, network, background work,
copyrighted music or human speech. Run the real M6 CLI against these files
before planning any opt-in synchronized physical M6 capture.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np

from voice_m6_event_anc import (
    RATE, PRE, POST, assert_private, pcm16_write, predict_music, sha256,
)

MUSIC_WINDOW = "0.35:1.05"
N = 3 * RATE
IMPULSE = np.array([.47, -.12, .095, -.03], dtype=np.float64)


def click_template(hz: float) -> np.ndarray:
    template = np.zeros(PRE + POST)
    n = np.arange(900)
    template[PRE + 20:PRE + 920] = (
        .070 * np.exp(-n / 160.) *
        np.cos(2. * np.pi * hz * n / RATE))
    return template


def place_clicks(samples: np.ndarray, rows: list[dict],
                 shapes: dict[str, np.ndarray]) -> None:
    for row in rows:
        at = row["sample"]
        shape = shapes[row["kind"]]
        samples[at-PRE:at-PRE+len(shape)] += shape


def fixture(out: Path) -> dict:
    os.umask(0o077)
    repo = Path(__file__).resolve().parents[1]
    audit = (repo / ".audit").resolve(strict=True)
    out = assert_private(out, audit, existing=False)
    rng = np.random.default_rng(6172026)
    ref_cal = np.zeros(N, dtype=np.float64)
    ref_cal[int(.20*RATE):int(1.10*RATE)] = rng.normal(
        0., .052, int(.90*RATE))
    ref_eval = np.zeros(N, dtype=np.float64)
    eval_first = round(.35 * RATE)
    eval_last = round(2.9 * RATE)
    ref_eval[eval_first:eval_last] = rng.normal(
        0., .052, eval_last - eval_first)
    shapes = {
        "mouse_press": click_template(1600.),
        "keyboard_press": click_template(2800.),
    }
    cal_events = []
    for kind, times in (
        ("mouse_press", (1.45, 1.85, 2.25)),
        ("keyboard_press", (1.65, 2.05, 2.45)),
    ):
        cal_events += [
            {"kind": kind, "sample": int(t*RATE)} for t in times]
    cal_events.sort(key=lambda x: x["sample"])
    eval_events = [
        {"kind": "mouse_press", "sample": int(1.35*RATE)},
        {"kind": "keyboard_press", "sample": int(1.9*RATE)},
        {"kind": "mouse_press", "sample": int(2.4*RATE)},
    ]
    cal_mic = predict_music(ref_cal, IMPULSE)
    place_clicks(cal_mic, cal_events, shapes)
    voice = np.zeros(N, dtype=np.float64)
    a, b = int(.85 * RATE), int(2.75 * RATE)
    tt = np.arange(a, b, dtype=float) / RATE
    voice[a:b] = .035 * (np.sin(2*np.pi*170*tt) +
                          .25*np.sin(2*np.pi*680*tt)) * (
        np.sin(np.linspace(0, np.pi, b-a)) ** 2
    )
    eval_mic = predict_music(ref_eval, IMPULSE) + voice
    place_clicks(eval_mic, eval_events, shapes)
    out.mkdir(mode=0o700)
    outputs = {
        "cal-mic.wav": cal_mic,
        "cal-reference.wav": ref_cal,
        "eval-mic.wav": eval_mic,
        "eval-reference.wav": ref_eval,
        "synthetic-clean-voice-truth.wav": voice,
    }
    hashes = {}
    for name, signal in outputs.items():
        pcm16_write(out / name, signal)
        hashes[name] = sha256(out / name)
    for name, rows in (
        ("cal-events.jsonl", cal_events),
        ("eval-events.jsonl", eval_events),
    ):
        (out / name).write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8")
        hashes[name] = sha256(out / name)
    report = {
        "schema": "cuewire-M6-synthetic-acoustic-reference-fixture-v1",
        "generator_seed": 6172026,
        "sample_rate": RATE,
        "music_only_calibration_window": MUSIC_WINDOW,
        "human_speech_or_real_input_recorded": False,
        "all_sources_synthetic": True,
        "zero_realtime_or_hardware_claims": True,
        "known_fir": IMPULSE.tolist(),
        "files_sha256": hashes,
    }
    (out / "fixture.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    hashes["fixture.json"] = sha256(out / "fixture.json")
    (out / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = fixture(args.out)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
