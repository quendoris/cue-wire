#!/usr/bin/env python3
"""CueWire M4: non-destructive louder *listening copy* of a voice WAV.

Only fixed digital gain. NO compression, limiting, denoising, AEC, mic
capture, audio playback, device changes or daemon. Two-pass mono PCM16
48kHz analysis, hard 3 dBFS sample-peak ceiling, independent SHA checks.
The RAW WAV is never modified. Output and JSON are new, private, immutable.
"""
from __future__ import annotations

import argparse
from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import wave

RATE=48000
AUDIT=(Path(__file__).resolve().parents[1]/".audit").resolve()


def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda:src.read(1<<20),b""):
            h.update(chunk)
    return h.hexdigest()


def pcm_frames(path):
    with wave.open(str(path),"rb") as wav:
        if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth(),
            wav.getcomptype())!=(RATE,1,2,"NONE"):
            raise ValueError("Expected uncompressed PCM16 mono 48kHz WAV")
        count=wav.getnframes()
        if count==0:
            raise ValueError("Empty WAV")
        for _ in range(0,count,32768):
            raw=wav.readframes(32768)
            if not raw:
                raise ValueError("Truncated WAV data")
            samples=array("h")
            samples.frombytes(raw)
            if sys.byteorder!="little":
                samples.byteswap()
            yield samples


def measure(path):
    count=0
    squares=0
    maxabs=0
    clipping=0
    for samples in pcm_frames(path):
        count+=len(samples)
        for value in samples:
            a=abs(value)
            if a>maxabs:maxabs=a
            squares+=value*value
            if a>=32767:clipping+=1
    if count==0:
        raise ValueError("No samples")
    rms=math.sqrt(squares/count)/32768
    peak=maxabs/32768
    return {
        "frames":count,
        "duration_s":round(count/RATE,6),
        "peak_dbfs":round(20*math.log10(max(peak,1e-12)),5),
        "rms_dbfs":round(20*math.log10(max(rms,1e-12)),5),
        "clipped_samples":clipping,
        "_peak":peak,
        "_rms":rms,
    }


def prepare_gain(before,target_rms_dbfs,peak_ceiling_dbfs,max_gain_db):
    peak=before["_peak"]
    rms=before["_rms"]
    if rms<1e-8:
        raise ValueError("Source essentially silent; refusing to amplify noise")
    goal=10**(target_rms_dbfs/20)
    # PCM16 quantization rounds to the nearest integer. A gain chosen
    # exactly at the floating-point dB ceiling can round UP by one LSB,
    # producing a valid WAV but a misleading post-render safety failure.
    # Reserve two integer sample steps BEFORE rendering.
    ceiling_integer=max(1,math.floor(32768*10**(peak_ceiling_dbfs/20))-2)
    ceiling=ceiling_integer/32768
    requested=max(1.0,goal/rms)  # Never LOWER a quiet voice to match RMS.
    peak_limit=(ceiling/peak) if peak>0 else 1.0
    gain=min(requested,10**(max_gain_db/20),peak_limit)
    if gain<=0 or not math.isfinite(gain):
        raise ValueError("Invalid gain; refusing audio generation")
    return gain,{
        "requested_gain_db":round(20*math.log10(requested),5),
        "chosen_gain_db":round(20*math.log10(gain),5),
        "chosen_gain_linear":round(gain,12),
        "max_gain_db":max_gain_db,
        "target_rms_dbfs":target_rms_dbfs,
        "sample_peak_ceiling_dbfs":peak_ceiling_dbfs,
        "gain_limited_by_peak":peak_limit+1e-10<min(requested,10**(max_gain_db/20)),
        "gain_limited_by_cap":10**(max_gain_db/20)+1e-10<min(requested,peak_limit),
    }


def convert(source,dest,gain):
    # Exclusive "xb": never truncate an existing file or follow a symlink.
    with dest.open("xb") as file:
        with wave.open(file,"wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(RATE)
            for samples in pcm_frames(source):
                processed=array("h")
                for v in samples:
                    n=round(v*gain)
                    processed.append(max(-32768,min(32767,n)))
                if sys.byteorder!="little":
                    processed.byteswap()
                output.writeframesraw(processed.tobytes())


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in",dest="source",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path,
                   help="New listen-only WAV inside repo's private .audit directory")
    p.add_argument("--target-rms-dbfs",type=float,default=-23.0)
    p.add_argument("--peak-ceiling-dbfs",type=float,default=-3.0)
    p.add_argument("--max-gain-db",type=float,default=24.0)
    args=p.parse_args()
    if not (-40<=args.target_rms_dbfs<=-16 and
            -12<=args.peak_ceiling_dbfs<=-1 and
            0<=args.max_gain_db<=36):
        p.error("Safety bounds: RMS -40..-16, peak ceiling -12..-1, max gain 0..36 dB")
    source=args.source.expanduser().resolve(strict=True)
    output=args.out.expanduser().resolve()
    if not AUDIT.is_dir() or AUDIT not in output.parents:
        p.error("Output must be inside the existing private repo .audit directory")
    if output.suffix.lower()!=".wav" or source==output:
        p.error("Output must be a separate .wav file")
    reportpath=output.with_suffix(".json")
    if output.exists() or output.is_symlink() or reportpath.exists() or reportpath.is_symlink():
        p.error("WAV or report already exists; refusing overwrite, choose another name")
    beforehash=digest(source)
    before=measure(source)
    gain,decision=prepare_gain(before,args.target_rms_dbfs,
                               args.peak_ceiling_dbfs,args.max_gain_db)
    # This is a purely fixed-gain audition derivative, not a loudness
    # normalizer for long dynamic programs or final mastering.
    output.parent.mkdir(parents=True,exist_ok=True)
    convert(source,output,gain)
    after=measure(output)
    if after["clipped_samples"] or after["_peak"]>10**(args.peak_ceiling_dbfs/20)+1e-6:
        raise RuntimeError("Safety check failed: derived WAV exceeds configured sample-peak ceiling")
    if beforehash!=digest(source):
        raise RuntimeError("RAW source changed during listening-copy creation")
    cleanup=lambda m:{key:value for key,value in m.items() if not key.startswith("_")}
    report={
        "schema":"cuewire-M4-listen-only-gain-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "mode":"fixed_gain_derivative_no_DSP",
        "input":source.name,
        "input_sha256":beforehash,
        "input_measurements":cleanup(before),
        "output":output.name,
        "output_sha256":digest(output),
        "output_measurements":cleanup(after),
        "gain":decision,
        "safety_notes":[
            "RAW input is unchanged. This WAV is for audition only.",
            "Output sample peaks capped; inter-sample true peaks not measured.",
            "Whole-file RMS includes silence, not perceptual speech loudness.",
            "No automatic compression/limiting, EQ, denoising or AEC.",
            "A boost also raises room noise and cannot recover clipped/weak detail.",
            "No audio played, uploaded or routed by this tool."
        ],
    }
    with reportpath.open("x",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2,sort_keys=True)
        f.write("\n")
    print("PASS: protected raw input sha256:",beforehash)
    print("Original peak/RMS:",before["peak_dbfs"],"/",before["rms_dbfs"],"dBFS")
    print("Listening gain:",decision["chosen_gain_db"],"dB")
    print("Listen-only peak/RMS:",after["peak_dbfs"],"/",after["rms_dbfs"],"dBFS")
    if decision["gain_limited_by_peak"]:
        print("NOTICE: PEAK ceiling limited the loudness boost; studio compression must be evaluated separately")
    print("Output:",output)
    print("Report:",reportpath)
    print("RAW file was not modified; NO playback was started.")


if __name__=="__main__":
    main()
