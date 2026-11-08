#!/usr/bin/env python3
"""CueWire optional OFFLINE speech A/B laboratory.

No PipeWire calls, no system audio volume, no autostart, no background worker.
Input mono 48 kHz PCM16 WAV. Writes A/B/C/C+ WAVs with SHA-256 manifest.
This diagnostic depends on optional Python packages, never on CueWire runtime.

Important: temporal/spectral filtering cannot recreate buried or clipped speech.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

import noisereduce as nr
import numpy as np
from scipy import signal
import soundfile as sf

RATE = 48000
VERSION = "cuewire-voice-ab-v1"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def dB(x: float) -> float:
    return float(20 * np.log10(max(x, 1e-12)))


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


def bandshape(x: np.ndarray, low: float, high: float) -> np.ndarray:
    sos = signal.butter(4, [low, high], fs=RATE, btype="bandpass", output="sos")
    return signal.sosfiltfilt(sos, x).astype(np.float32)


def reduce(x: np.ndarray, strength: float, time_ms: float, freq_hz: float) -> np.ndarray:
    return nr.reduce_noise(
        y=x, sr=RATE, stationary=False, prop_decrease=strength,
        time_constant_s=1.25, freq_mask_smooth_hz=freq_hz,
        time_mask_smooth_ms=time_ms,
    ).astype(np.float32)


def match(x: np.ndarray, reference: np.ndarray, max_gain: float = 2.5) -> tuple[np.ndarray, float, bool]:
    # Limit makeup gain; do not try to 'fix' a buried voice with huge gain.
    gain = min(max_gain, rms(reference) / max(rms(x), 1e-10))
    y = x * gain
    peak_guard = bool(np.max(np.abs(y)) > 0.88)
    if peak_guard:
        y = y * (0.88 / np.max(np.abs(y)))
    return y.astype(np.float32), gain, peak_guard


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--cplus-strength", type=float, default=0.92,
                   help="C+ suppression strength 0..1, default 0.92")
    p.add_argument("--cplus-low-hz", type=float, default=125.0)
    p.add_argument("--cplus-high-hz", type=float, default=4600.0)
    p.add_argument("--cplus-max-gain", type=float, default=6.0,
                   help="C+ makeup gain cap, default 6x, with peak safety")
    p.add_argument("--force", action="store_true",
                   help="explicitly replace existing experiment outputs")
    args = p.parse_args()

    if (not np.isfinite(args.cplus_strength)
        or not 0.0 <= args.cplus_strength <= 1.0
        or not np.isfinite(args.cplus_low_hz)
        or not np.isfinite(args.cplus_high_hz)
        or not 50.0 <= args.cplus_low_hz < args.cplus_high_hz <= 10000.0
        or not np.isfinite(args.cplus_max_gain)
        or not 1.0 <= args.cplus_max_gain <= 12.0):
        raise SystemExit("Invalid C+ settings; see --help")

    x, sr = sf.read(args.input, dtype="float32", always_2d=False)
    if sr != RATE or x.ndim != 1:
        raise SystemExit("Input must be mono 48 kHz WAV")
    subtype = sf.info(args.input).subtype
    if subtype != "PCM_16":
        raise SystemExit(f"Input must be signed PCM16 WAV; found {subtype}")
    if len(x) == 0 or len(x) > RATE * 60 * 10 or not np.all(np.isfinite(x)):
        raise SystemExit("Empty/invalid/too-long audio")
    args.out.mkdir(parents=True, exist_ok=True)
    output_files = {
        "A": args.out / "voice_band_only_v2.wav",
        "B": args.out / "voice_mild_v2.wav",
        "C": args.out / "voice_focus_v2.wav",
        "C+": args.out / "voice_c_plus_v3.wav",
    }
    if any(path.exists() for path in output_files.values()) and not args.force:
        raise SystemExit("Outputs exist. Use a new --out DIR or --force.")

    band_only = bandshape(x, 85, 6500)
    mild = reduce(band_only, .55, 120, 350)
    focused = reduce(bandshape(x, 105, 5100), .78, 175, 450)
    c_plus = reduce(bandshape(x, args.cplus_low_hz, args.cplus_high_hz),
                    args.cplus_strength, 220, 580)
    stages = {"A": band_only, "B": mild, "C": focused, "C+": c_plus}
    report = {
        "algorithm": VERSION,
        "input_sha256": sha256(args.input),
        "input_frames": len(x),
        "input_sample_rate": RATE,
        "input_rms_dbfs": dB(rms(x)),
        "input_peak_dbfs": dB(float(np.max(np.abs(x)))),
        "c_plus_config": {
            "strength": args.cplus_strength,
            "low_hz": args.cplus_low_hz,
            "high_hz": args.cplus_high_hz,
            "time_mask_smooth_ms": 220,
            "freq_mask_smooth_hz": 580,
            "makeup_gain_limit": args.cplus_max_gain
        },
        "software": {name: importlib.metadata.version(name) for name in
                     ("numpy", "scipy", "soundfile", "noisereduce")},
        "variants": {},
        "not_guaranteed": ["natural voice", "speech intelligibility",
                           "noise source separation"],
    }
    for key, unscaled in stages.items():
        gain_limit = args.cplus_max_gain if key == "C+" else 2.5
        y, gain, peak_guard = match(unscaled, x, max_gain=gain_limit)
        path = output_files[key]
        sf.write(path, y, RATE, subtype="PCM_16")
        report["variants"][key] = {
            "filename": path.name, "sha256": sha256(path),
            "makeup_gain": gain, "peak_guard_applied": peak_guard,
            "rms_dbfs": dB(rms(y)),
            "peak_dbfs": dB(float(np.max(np.abs(y)))),
        }
    report["variants"]["A"]["processing"] = (
        "Butterworth bandpass 85-6500 Hz, order 4, zero-phase")
    report["variants"]["B"]["processing"] = (
        "A plus nonstationary NR strength .55; mask 120 ms / 350 Hz")
    report["variants"]["C"]["processing"] = (
        "Butterworth bandpass 105-5100 Hz plus NR strength .78; mask 175 ms / 450 Hz")
    report["variants"]["C+"]["processing"] = (
        f"Stronger C: bandpass {args.cplus_low_hz:g}-{args.cplus_high_hz:g} Hz, "
        f"nonstationary NR strength {args.cplus_strength:g}, "
        f"mask 220 ms / 580 Hz, gain cap {args.cplus_max_gain:g}x, peak <= 0.88")
    (args.out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("Created independent A/B/C/C+ comparisons. No audio played automatically.")
    for key, info in report["variants"].items():
        print(key, info["filename"], "RMS",
              round(info["rms_dbfs"], 2), "dBFS", "SHA", info["sha256"][:12])


if __name__ == "__main__":
    main()
