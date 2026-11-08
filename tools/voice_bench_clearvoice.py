#!/usr/bin/env python3
"""Standalone ClearerVoice-Studio adapter. No background service or device changes."""
import argparse
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("input_wav", type=Path)
    p.add_argument("output_wav", type=Path)
    args = p.parse_args()
    if not args.input_wav.is_file(): p.error("input WAV does not exist")
    if args.output_wav.exists(): p.error("output already exists")
    from clearvoice import ClearVoice
    model = ClearVoice(task="speech_enhancement", model_names=["MossFormer2_SE_48K"])
    enhanced = model(input_path=str(args.input_wav), online_write=False)
    args.output_wav.parent.mkdir(parents=True, exist_ok=True)
    model.write(enhanced, output_path=str(args.output_wav))
    if not args.output_wav.is_file():
        raise RuntimeError("ClearVoice returned without producing a WAV")
    print("ClearVoice output:", args.output_wav)

if __name__ == "__main__":
    main()
