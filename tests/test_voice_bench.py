#!/usr/bin/env python3
"""Self-contained synthetic regression of metric alignment and voice benchmark.
Requires NumPy and SciPy, but NO neural models. Never opens audio devices.
"""
import math
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from voice_bench_metrics import alignment, score


def write(path, data):
    data = np.clip(data, -0.95, 0.95)
    raw = np.round(data * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setframerate(48000)
        f.setnchannels(1)
        f.setsampwidth(2)
        f.writeframes(raw.tobytes())


def main():
    rng = np.random.default_rng(123456)
    rate = 48000
    time = np.arange(rate*2) / rate
    signal = (0.18*np.sin(2*np.pi*193.0*time) +
              0.06*np.sin(2*np.pi*247.0*time) +
              0.015*np.sin(2*np.pi*901.0*time)) * (
                  0.3 + 0.7*np.sin(2*np.pi*2.0*time)**2)
    noisy = signal + 0.04*rng.standard_normal(len(signal))
    with tempfile.TemporaryDirectory() as root:
        path = Path(root)
        clean = path/"reference.wav"
        bad = path/"noise.wav"
        write(clean,signal)
        write(bad,noisy)
        lagged = np.pad(noisy, (137,0))[:len(noisy)]
        offset, corr, overlap = alignment(signal,lagged)
        assert abs(offset-137) <= 8, (offset,corr,overlap)
        assert corr > 0.75
        assert overlap > 0.99
        compare = score(signal,noisy)
        assert compare["aligned_correlation_abs"] > 0.75
        output = path/"run"
        command = [sys.executable, str(TOOLS/"voice_bench.py"),
                   "--clean", str(clean), "--noisy", str(bad),
                   "--out", str(output), "--models", "identity",
                   "--chains", ""]
        subprocess.run(command, check=True, timeout=60, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
        import json
        manifest = json.loads((output/"manifest.json").read_text())
        scores = json.loads((output/"scores.json").read_text())
        assert manifest["jobs"]["identity"]["status"] == "ok",manifest["jobs"]
        assert scores["input_pair"]["reference_pair_verified_heuristically"]
        assert len(scores["results"]) == 1
        assert scores["results"][0]["eligible_for_reference_ranking"]
        assert (output/"results"/"identity.wav").exists()
        assert (output/"SHA256SUMS").exists()
    print("PASS: timing alignment, verified pairing, identity model, scoring, manifests and SHA256")


if __name__ == "__main__":
    main()
