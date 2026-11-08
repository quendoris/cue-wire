#!/usr/bin/env python3
"""Opt-in ONNX speech enhancement: GTCRN and 48 kHz DPDFNet through sherpa-onnx.

Models MUST already exist locally. This adapter does not download anything
and never touches audio devices. DPDFNet 'soft' uses the official 12 dB
offline attenuation limit to reduce over-suppression/metallic artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


MODELS = {
    "gtcrn": ("gtcrn_simple.onnx", "gtcrn", 0.0),
    "dpdfnet2": ("dpdfnet2_48khz_hr.onnx", "dpdfnet", 0.0),
    "dpdfnet2_soft": ("dpdfnet2_48khz_hr.onnx", "dpdfnet", 12.0),
    "dpdfnet8": ("dpdfnet8_48khz_hr.onnx", "dpdfnet", 0.0),
    "dpdfnet8_soft": ("dpdfnet8_48khz_hr.onnx", "dpdfnet", 12.0),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_wav", type=Path)
    parser.add_argument("output_wav", type=Path)
    parser.add_argument("--model", required=True, choices=sorted(MODELS))
    parser.add_argument("--models-dir", required=True, type=Path)
    options = parser.parse_args()
    name, family, attenuation = MODELS[options.model]
    model_path = options.models_dir / name
    if not model_path.is_file() or model_path.stat().st_size < 100_000:
        raise SystemExit(f"ONNX model missing/invalid: {model_path}. "
                         "Download the model explicitly using tools/voice_bench_extra_install.sh")
    if not options.input_wav.is_file():
        parser.error("Input recording not found")
    if options.output_wav.exists():
        parser.error("Output already exists; use a fresh benchmark directory")

    import numpy as np
    import sherpa_onnx
    import soundfile as sf

    info = sf.info(str(options.input_wav))
    if info.samplerate != 48000 or info.channels != 1 or info.subtype != "PCM_16":
        raise SystemExit("Only mono 48 kHz PCM16 normalized benchmark input is supported")
    samples, sample_rate = sf.read(str(options.input_wav), dtype="float32")
    samples = np.ascontiguousarray(samples)
    if not np.all(np.isfinite(samples)):
        raise SystemExit("Non-finite samples in input WAV")
    config_kwargs = {}
    if family == "gtcrn":
        config_kwargs["gtcrn"] = sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(
            model=str(model_path))
    else:
        config_kwargs["dpdfnet"] = sherpa_onnx.OfflineSpeechDenoiserDpdfNetModelConfig(
            model=str(model_path), attenuation_limit_db=attenuation)
    config = sherpa_onnx.OfflineSpeechDenoiserConfig(
        model=sherpa_onnx.OfflineSpeechDenoiserModelConfig(
            **config_kwargs, num_threads=1, provider="cpu", debug=False))
    if not config.validate():
        raise SystemExit(f"Invalid sherpa-onnx configuration: {config}")
    engine = sherpa_onnx.OfflineSpeechDenoiser(config)
    enhanced = engine(samples, sample_rate)
    output = np.asarray(enhanced.samples, dtype=np.float32)
    if output.ndim != 1 or output.size == 0 or not np.all(np.isfinite(output)):
        raise RuntimeError("Invalid ONNX output shape / NaN / empty samples")
    if np.max(np.abs(output)) > 1.001:
        raise RuntimeError("ONNX output exceeds PCM full scale, refusing silent clipping")
    output_rate = int(enhanced.sample_rate)
    if output_rate not in (16000, 48000):
        raise RuntimeError(f"Unexpected output sample rate {output_rate}")
    options.output_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(options.output_wav), output, output_rate, subtype="PCM_16")
    model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    print(json.dumps({
        "model": options.model, "engine": "sherpa-onnx",
        "model_filename": name, "model_sha256": model_hash,
        "attenuation_limit_db": attenuation,
        "input_sample_rate": sample_rate, "output_sample_rate": output_rate,
        "output_frames": int(output.size), "model_download": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
