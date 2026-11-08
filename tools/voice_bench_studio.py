#!/usr/bin/env python3
"""CueWire offline studio-stage A/B for already-cleaned, STILL BLIND speech.

This tool does not perform denoising or infer model identity. It accepts
opaque track labels (e.g. 04, 14) and emits independent, deterministic
control and gentle EQ/dynamics candidates. The CLEAN reference is copied
only for later evaluation; never used to derive processing settings.
No device/route changes, network access, auto-play, or background service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
from scipy import fft, ndimage, signal
import soundfile as sf

RATE=48000


def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):
            h.update(block)
    return h.hexdigest()


def rms(signal_array):
    return float(np.sqrt(np.mean(np.square(signal_array.astype(np.float64)))))


def db(value):
    return float(20.0*np.log10(max(float(value),1e-12)))


def load(path:Path):
    data, rate=sf.read(path, dtype="float32", always_2d=True)
    if data.size==0:
        raise ValueError(f"Empty input: {path}")
    if data.shape[1]>2:
        raise ValueError("At most stereo input supported")
    if rate<=0 or not (8000<=rate<=192000):
        raise ValueError(f"Unsupported sampling rate: {rate}")
    x=data.mean(axis=1).astype(np.float32)
    if not np.all(np.isfinite(x)):
        raise ValueError(f"NaN or Inf in {path}")
    if len(x)<rate or len(x)>rate*180:
        raise ValueError(f"Speech length must be 1..180 seconds: {path}")
    if rate!=RATE:
        common=math.gcd(rate,RATE)
        x=signal.resample_poly(x,RATE//common,rate//common).astype(np.float32)
    if np.max(np.abs(x))>1.001:
        raise ValueError("Input exceeds full-scale PCM")
    return x


def write(path,x):
    if np.any(~np.isfinite(x)) or float(np.max(np.abs(x)))>1.0:
        raise ValueError(f"Non-finite or clipped output: {path}")
    sf.write(path,np.asarray(x,dtype=np.float32),RATE,subtype="PCM_16")


def color(x, warmth=0.0, presence=0.0, harshness=0.0):
    """Offline fixed ZERO-PHASE, broad and small spectral EQ.

    Whole-clip FFT with reflect padding. No learned vocal reconstruction;
    it cannot replace lost phonemes or frequencies. Keep all changes <=1 dB.
    """
    n=len(x)
    pad=min(max(256,int(0.15*RATE)),max(1,n-2))
    y=np.pad(x.astype(np.float64),pad,mode="reflect")
    transform=fft.rfft(y)
    freqs=fft.rfftfreq(len(y),d=1.0/RATE)
    nonzero=np.maximum(freqs,20.0)
    logf=np.log2(nonzero)
    def bump(hz,width):
        return np.exp(-0.5*((logf-np.log2(hz))/width)**2)
    gains_db=(warmth*bump(220.0,0.95)
              +presence*bump(2700.0,0.75)
              +harshness*bump(6600.0,0.60))
    gain=np.power(10.0,gains_db/20.0)
    transformed=fft.irfft(transform*gain,n=len(y))
    return transformed[pad:pad+n].astype(np.float32)


def gentle_level(x):
    """Offline slow, DOWNWARD-ONLY compression: no lift of pauses/noise."""
    # 25-ms RMS envelope then 60-ms smoothing of the target gain.
    power=ndimage.uniform_filter1d(x.astype(np.float64)**2,
                                  size=round(0.025*RATE),mode="nearest")
    level=20.0*np.log10(np.maximum(np.sqrt(np.maximum(power,0.0)),1e-9))
    threshold=-26.0
    ratio=1.45
    gain_reduction=np.clip(np.maximum(0.0,level-threshold)*(1.0-1.0/ratio),
                           0.0,2.0)
    gain=ndimage.uniform_filter1d(np.power(10.0,-gain_reduction/20.0),
                                 size=round(0.060*RATE),mode="nearest")
    return (x*gain).astype(np.float32)


def safe_match(x, input_rms, max_makeup_db=1.0, peak_ceiling_dbfs=-3.0):
    """Ensure comparing timbre rather than just differing overall loudness."""
    current=max(rms(x),1e-10)
    makeup=np.clip(input_rms/current,
                   10.0**(-max_makeup_db/20.0),
                   10.0**( max_makeup_db/20.0))
    y=x.astype(np.float64)*makeup
    peak=max(float(np.max(np.abs(y))),1e-12)
    ceiling=10.0**(peak_ceiling_dbfs/20.0)
    if peak>ceiling:
        y*=ceiling/peak
    return y.astype(np.float32)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",action="append",required=True,
                   metavar="BLIND_ID=/PATH/track.wav",
                   help="Repeat for each blind candidate, never expose prior model labels")
    p.add_argument("--clean",type=Path,required=True)
    p.add_argument("--noisy",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():
        p.error("Output exists. Use a NEW path; never overwrite previous tests.")
    labels={}
    for item in args.input:
        label,sep,path=item.partition("=")
        if not sep or not re.fullmatch(r"[0-9A-Za-z_-]{1,30}",label):
            p.error("--input format is label=/path/file.wav")
        if label in labels:
            p.error(f"Duplicate input label {label}")
        source=Path(path).expanduser()
        if not source.is_file():
            p.error(f"Missing input WAV: {source}")
        labels[label]=source
    if len(labels)>6:
        p.error("Six input tracks maximum per run")
    if not args.clean.is_file() or not args.noisy.is_file():
        p.error("Missing clean/noisy evaluation reference")
    original_clean=load(args.clean)
    original_noisy=load(args.noisy)
    args.out.mkdir(parents=True)
    for directory in ("inputs","results"):
        (args.out/directory).mkdir()
    write(args.out/"inputs"/"clean.wav",original_clean)
    write(args.out/"inputs"/"noisy.wav",original_noisy)
    manifest={"schema":"cuewire-studio-preview-v1",
              "processing_independent_of_clean_reference":True,
              "no_denoising_no_generated_noise_no_background_services":True,
              "parameters":{
                  "eq_phase":"zero-phase reflective FFT, broad bells",
                  "warmth_db":1.0,"presence_db":1.0,"harshness_db":-0.65,
                  "compression_threshold_dbfs":-26.0,
                  "compression_ratio":1.45,"maximum_reduction_db":2.0,
                  "rms_match_limit_db":1.0,"peak_ceiling_dbfs":-3.0},
              "input_source_hashes":{label:sha(src) for label,src in labels.items()},
              "jobs":{}}
    for label,source in labels.items():
        x=load(source)
        original_rms=rms(x)
        # Prelisten controls are preserved. No change to the prior blind ID.
        candidates={
            "control":x,
            "warm":color(x,warmth=1.0,harshness=-0.35),
            "clarity":color(x,presence=1.0,harshness=-0.35),
            "soft":color(x,warmth=0.55,harshness=-0.65),
            "gentle_level":gentle_level(x),
            "clarity_level":gentle_level(color(x,presence=0.8,harshness=-0.35))
        }
        for name,unmatched in candidates.items():
            y=unmatched if name=="control" else safe_match(unmatched,original_rms)
            out=args.out/"results"/f"blind{label}__{name}.wav"
            write(out,y)
            manifest["jobs"][out.stem]={
                "status":"ok","steps":["blind"+label,"studio_"+name],
                "output_sha256":sha(out),"output_frames":len(y),
                "rms_dbfs":round(db(rms(y)),4),
                "sample_peak_dbfs":round(db(np.max(np.abs(y))),4)}
    (args.out/"manifest.json").write_text(
        json.dumps(manifest,indent=2,sort_keys=True,ensure_ascii=False)+"\n")
    # Use precisely the same metrics code as the model benchmark.
    from voice_bench_metrics import collect
    collect(args.out)
    checked=[]
    for path in sorted(args.out.rglob("*")):
        if path.is_file() and path.name not in ("SHA256SUMS","manifest.json"):
            checked.append(f"{sha(path)}  {path.relative_to(args.out)}")
    (args.out/"SHA256SUMS").write_text("\n".join(checked)+"\n")
    print(f"Created {len(manifest['jobs'])} deterministic studio A/B WAVs.")
    print("No model identities revealed, and no playback or upload performed.")
    print("Listen: python tools/voice_bench_listen.py",args.out,
          "--out <fresh-listening-directory>")


if __name__=="__main__":
    main()
