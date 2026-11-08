#!/usr/bin/env python3
"""Corrupt a real mono 48 kHz PCM16 voice sample reproducibly.

Standard library only. No recording devices are accessed.
Use --clean with a file you own or a sample with a documented license.
"""
import argparse
import array
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import wave

RATE = 48_000


def load(path):
    with wave.open(str(path), "rb") as f:
        if (f.getnchannels(), f.getsampwidth(), f.getframerate(), f.getcomptype()) != (
            1, 2, RATE, "NONE"
        ):
            raise ValueError("input must be PCM16 mono 48 kHz WAV")
        data = array.array("h")
        data.frombytes(f.readframes(f.getnframes()))
    if sys.byteorder != "little":
        data.byteswap()
    if not data:
        raise ValueError("empty voice recording")
    return [x / 32768.0 for x in data]


def write(path, audio):
    data = array.array("h", (max(-32768, min(32767, round(x * 32767)))
                              for x in audio))
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setframerate(RATE)
        f.setsampwidth(2)
        f.writeframes(data.tobytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--clean", required=True, type=Path)
    p.add_argument("--out", default="voice-fixture", type=Path)
    p.add_argument("--seed", default=20261108, type=int)
    p.add_argument("--noise", default=0.045, type=float)
    p.add_argument("--hum", default=0.02, type=float)
    p.add_argument("--bleed", default=0.035, type=float)
    p.add_argument("--echo", default=0.22, type=float)
    p.add_argument("--drive", default=1.9, type=float)
    args = p.parse_args()
    if any(not math.isfinite(x) or x < 0.0 or x > 3.0 for x in (
        args.noise, args.hum, args.bleed, args.echo, args.drive
    )):
        raise ValueError("parameters must be finite, nonnegative and <=3")
    clean = load(args.clean)
    random_source = random.Random(args.seed)
    delay = round(RATE * 0.12)
    damaged = []
    for i, sample in enumerate(clean):
        t = i / RATE
        echo = args.echo * clean[i-delay] if i >= delay else 0.0
        hum = args.hum * math.sin(2 * math.pi * 50 * t)
        guitar_like = args.bleed * (
            math.sin(2 * math.pi * 165 * t) +
            0.35 * math.sin(2 * math.pi * 330 * t)
        ) * (0.5 + 0.5 * math.sin(2 * math.pi * 1.7 * t))
        noise = args.noise * (2 * random_source.random() - 1)
        damaged.append(0.75 * math.tanh(args.drive * (
            sample + echo + hum + guitar_like + noise
        )))
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "voice_bad.wav"
    write(path, damaged)
    manifest = {
        "version": "voice-fixture-v1",
        "origin": str(args.clean),
        "input_sha256": sha(args.clean),
        "bad_sha256": sha(path),
        "sample_rate": RATE,
        "frames": len(clean),
        "seed": args.seed,
        "noise": args.noise,
        "hum": args.hum,
        "bleed": args.bleed,
        "echo": args.echo,
        "drive": args.drive,
    }
    (args.out / "fixture.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(f"Created {path}, source SHA256: {manifest['input_sha256']}")
    print(f"Corrupted SHA256: {manifest['bad_sha256']}")


if __name__ == "__main__":
    main()
