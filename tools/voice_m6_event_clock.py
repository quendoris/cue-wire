#!/usr/bin/env python3
"""M6 opt-in, foreground-only event TIMING collector and offline clock bridge.

collect: ONLY monotonic times + keyboard/mouse press/release classes.
         Never write keycodes, text, coordinates or device names; no children.
         Requires explicitly chosen /dev/input device paths, explicit consent,
         optional python-evdev, limited duration, private .audit output.
convert: requires an independently verified timestamp of MIC PCM sample zero,
         AND a measured event-to-acoustic offset. Neither can be inferred
         correctly just from the pw-record process launch time.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import time
import wave

from voice_m6_event_anc import (
    RATE, PRE, POST, ALLOWED_EVENTS, assert_private, sha256,
)


def serialize_class_event(kind: str, monotonic_ns: int) -> str:
    if (kind not in ALLOWED_EVENTS or
            type(monotonic_ns) is not int or monotonic_ns <= 0):
        raise ValueError("Invalid coarse input event")
    return json.dumps({"kind": kind, "t_monotonic_ns": monotonic_ns},
                      separators=(",", ":"), sort_keys=True) + "\n"


def convert_events(lines: str, sample_zero_monotonic_ns: int,
                   acoustic_offset_samples: int, num_frames: int
                   ) -> list[dict]:
    """Measured common-clock conversion; refuse uncalibrated event markers.

    The caller is responsible for providing a REAL sample-zero anchor
    and acoustic offset derived from a deliberate click-calibration run.
    No process-start approximation is silently accepted.
    """
    if (type(sample_zero_monotonic_ns) is not int or
            sample_zero_monotonic_ns <= 0 or
            type(acoustic_offset_samples) is not int or
            abs(acoustic_offset_samples) > RATE or
            num_frames <= 2 * (PRE + POST)):
        raise ValueError("Invalid PCM timing calibration")
    result = []
    last = -1
    for line_no, line in enumerate(lines.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid event timing JSONL line {line_no}") from exc
        if (not isinstance(row, dict)
                or set(row) != {"kind", "t_monotonic_ns"}
                or row.get("kind") not in ALLOWED_EVENTS
                or type(row.get("t_monotonic_ns")) is not int
                or row["t_monotonic_ns"] <= last):
            raise ValueError(f"Invalid/unsorted/private event line {line_no}")
        last = row["t_monotonic_ns"]
        sample = round((last-sample_zero_monotonic_ns) * RATE / 1e9)
        sample += acoustic_offset_samples
        if not PRE <= sample < num_frames - POST:
            raise ValueError("Event outside calibrated recording/edge")
        result.append({"kind": row["kind"], "sample": sample})
    return result


def collect(args: argparse.Namespace) -> None:
    if not args.acknowledge_input_timing_privacy:
        raise ValueError("Explicit confirmation of typing-rhythm privacy required")
    if not 1 <= args.seconds <= 120:
        raise ValueError("Limit recording to 1..120 seconds")
    if not args.keyboard_device and not args.mouse_device:
        raise ValueError("Specify physical keyboard and/or mouse event device")
    if (args.keyboard_device and args.mouse_device
            and args.keyboard_device == args.mouse_device):
        raise ValueError("Specify distinct physical devices")
    import importlib
    try:
        evdev = importlib.import_module("evdev")
    except ImportError as exc:
        raise RuntimeError(
            "Optional local python-evdev is needed for opt-in input timing") from exc
    root = (Path(__file__).resolve().parents[1] / ".audit").resolve(strict=True)
    out = assert_private(args.out, root, existing=False)
    opened = []
    selector = selectors.DefaultSelector()
    try:
        for label, path in (
            ("keyboard", args.keyboard_device),
            ("mouse", args.mouse_device),
        ):
            if not path:
                continue
            devpath = Path(path)
            if (not str(devpath).startswith("/dev/input/event")
                    or devpath.is_symlink()):
                raise ValueError("Only explicitly named /dev/input/eventN allowed")
            device = evdev.InputDevice(str(devpath))
            opened.append(device)
            selector.register(device, selectors.EVENT_READ, data=label)
        # Open only after all devices pass validation, exclusive create.
        with out.open("x", encoding="utf-8") as target:
            print(
                "M6 private input TIMING ONLY (no key identity/text/coords). "
                "Ctrl+C to stop; max %ss." % args.seconds, flush=True)
            end_ns = time.monotonic_ns() + round(args.seconds * 1e9)
            count = 0
            while time.monotonic_ns() < end_ns:
                timeout = min(.15, max(
                    0., (end_ns - time.monotonic_ns()) / 1e9))
                for key, _ in selector.select(timeout):
                    for event in key.fileobj.read():
                        # EV_KEY: 1=down, 0=up, 2=repeat ignored.
                        if event.type != evdev.ecodes.EV_KEY or event.value not in (0, 1):
                            continue
                        # Time of read, NOT a certified sample-time stamp.
                        stamp = time.monotonic_ns()
                        label = key.data
                        kind = label + ("_press" if event.value == 1 else "_release")
                        target.write(serialize_class_event(kind, stamp))
                        count += 1
                        if count > 20_000:
                            raise ValueError("Event limit reached")
    except KeyboardInterrupt:
        print("Ctrl+C: closed event devices; private timing saved", flush=True)
    finally:
        selector.close()
        for device in opened:
            device.close()
    print("Private event timing JSONL saved; no audio/text collected", flush=True)


def convert(args: argparse.Namespace) -> None:
    if not (args.sample_zero_verified and args.offset_measured):
        raise ValueError(
            "Refuse unverified process-launch timestamp or guessed acoustic lag")
    audit = (Path(__file__).resolve().parents[1] / ".audit").resolve(strict=True)
    src = assert_private(args.input, audit, existing=True)
    mic = assert_private(args.mic, audit, existing=True)
    dst = assert_private(args.out, audit, existing=False)
    with wave.open(str(mic), "rb") as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),
                wav.getcomptype()) != (1, 2, RATE, "NONE"):
            raise ValueError("Expected microphone PCM16 mono 48kHz WAV")
        frames = wav.getnframes()
    markers = convert_events(
        src.read_text(encoding="utf-8"),
        args.sample_zero_monotonic_ns,
        args.acoustic_offset_samples,
        frames,
    )
    with dst.open("x", encoding="utf-8") as target:
        for row in markers:
            target.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps({
        "status": "converted_to_mic_pcm_clock_using_operator_verified_anchor",
        "event_count": len(markers),
        "mic_sha256": sha256(mic),
        "timing_source_sha256": sha256(src),
        "event_markers_sha256": sha256(dst),
        "output": str(dst),
        "identity_or_text_saved": False,
        "synchronization_actually_certified_by_script": False,
    }, indent=2))


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    cmd = p.add_subparsers(dest="command", required=True)
    collect_parser = cmd.add_parser("collect")
    collect_parser.add_argument("--keyboard-device", type=str)
    collect_parser.add_argument("--mouse-device", type=str)
    collect_parser.add_argument("--seconds", type=float, default=30.)
    collect_parser.add_argument(
        "--acknowledge-input-timing-privacy", action="store_true")
    collect_parser.add_argument("--out", type=Path, required=True)
    convert_parser = cmd.add_parser("convert")
    convert_parser.add_argument("--input", type=Path, required=True)
    convert_parser.add_argument("--mic", type=Path, required=True)
    convert_parser.add_argument(
        "--sample-zero-monotonic-ns", type=int, required=True)
    convert_parser.add_argument(
        "--acoustic-offset-samples", type=int, required=True)
    convert_parser.add_argument("--sample-zero-verified", action="store_true")
    convert_parser.add_argument("--offset-measured", action="store_true")
    convert_parser.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    try:
        if args.command == "collect":
            collect(args)
        else:
            convert(args)
    except (OSError, ValueError, RuntimeError, wave.Error) as exc:
        p.exit(1, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
