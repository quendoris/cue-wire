#!/usr/bin/env python3
"""Offline bandwidth extension with explicit crossover frequency.

A 48 kHz file may have a *muffled* effective frequency band. Raising its
sample rate alone cannot recover missing highs. The crossover is an
intentional experimental transform: <= cutoff is largely preserved,
> cutoff is synthesized. It is NOT ground-truth restoration.
"""
from __future__ import annotations
import argparse
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("source",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--engine",choices=("lavasr",),required=True)
    p.add_argument("--cutoff-hz",type=int,choices=(6000,9000),required=True)
    opts=p.parse_args()
    if not opts.source.is_file(): p.error("Input WAV missing")
    if opts.output.exists(): p.error("Refuse to overwrite old experiment")
    from audiosronnx import load_sr
    instance=load_sr(opts.engine,cutoff_hz=opts.cutoff_hz)
    dest=instance.upscale_file(str(opts.source),str(opts.output))
    if not opts.output.is_file() or opts.output.stat().st_size<1000:
        raise RuntimeError(f"Upscaler produced no output: {dest}")
    print(f"explicit crossover={opts.cutoff_hz}Hz; output={opts.output}; no reference input")

if __name__=="__main__":
    main()
