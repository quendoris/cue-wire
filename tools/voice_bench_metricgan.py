#!/usr/bin/env python3
"""Offline SpeechBrain MetricGAN+ benchmark adapter.

Uses the published 16 kHz VoiceBank checkpoint, not 48 kHz full-band
restoration. First explicit invocation may download model weights.
No real-time stream, recording device, autostart or system audio changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_wav", type=Path)
    parser.add_argument("output_wav", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()
    if not args.input_wav.is_file():
        parser.error("Input WAV missing")
    if args.output_wav.exists():
        parser.error("Output already exists")
    import numpy as np
    import soundfile as sf
    from speechbrain.inference.enhancement import SpectralMaskEnhancement

    model = SpectralMaskEnhancement.from_hparams(
        source="speechbrain/metricgan-plus-voicebank",
        savedir=str(args.cache_dir),
        run_opts={"device": args.device})
    enhanced = model.enhance_file(str(args.input_wav))
    if hasattr(enhanced, "detach"):
        enhanced = enhanced.detach().cpu().numpy()
    data = np.squeeze(np.asarray(enhanced, dtype=np.float32))
    if data.ndim != 1 or data.size == 0 or not np.all(np.isfinite(data)):
        raise RuntimeError("Invalid MetricGAN+ output shape / finite state")
    if np.max(np.abs(data)) > 1.001:
        raise RuntimeError("MetricGAN+ exceeded PCM full scale; refusing silent clipping")
    args.output_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.output_wav), data, 16000, subtype="PCM_16")
    # No claim of complete checkpoint provenance if weights are symlinks or
    # downloaded into additional HF caches. Full hashes are a separate audit.
    print(json.dumps({"model": "speechbrain/metricgan-plus-voicebank",
                      "output_sample_rate": 16000, "frames": int(data.size),
                      "device": args.device, "full_band_48khz": False,
                      "cache_dir": str(args.cache_dir)}, sort_keys=True))


if __name__ == "__main__":
    main()
