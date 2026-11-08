#!/usr/bin/env python3
"""Deterministic private blind listening kit for CueWire model comparison.

Inputs: one finished voice-benchmark run folder with inputs/{clean,noisy}.wav
and results/*.wav. Outputs in a SEPARATE directory: gain-matched copies for
manual listening, plus answer key. Never uses a sound device or network.
"""
import argparse
import array
import hashlib
import json
import math
import random
import sys
import wave
from pathlib import Path

RATE = 48_000

def read_wav(path):
    with wave.open(str(path),"rb") as src:
        assert (src.getframerate(),src.getnchannels(),src.getsampwidth())==(RATE,1,2),path
        raw=src.readframes(src.getnframes())
    pcm=array.array("h")
    pcm.frombytes(raw)
    if sys.byteorder!="little": pcm.byteswap()
    return [v/32768.0 for v in pcm]

def rms(data):
    return math.sqrt(sum(v*v for v in data)/len(data))

def write_wav(path, samples):
    pcm=array.array("h",(max(-32768,min(32767,round(x*32767))) for x in samples))
    if sys.byteorder!="little": pcm.byteswap()
    with wave.open(str(path),"wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def normalized(signal, target_dbfs, peak_dbfs):
    current=rms(signal)
    if current<1e-10:
        raise ValueError("silent track cannot be gain matched")
    goal=10**(target_dbfs/20.0)
    gain=min(12.0,goal/current)
    ceiling=10**(peak_dbfs/20.0)
    max_peak=max(abs(s) for s in signal)
    if max_peak*gain>ceiling: gain=ceiling/max_peak
    return [float(v*gain) for v in signal],gain

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir",type=Path)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--seed",type=int,default=20261108)
    p.add_argument("--target-dbfs",type=float,default=-28.0)
    p.add_argument("--peak-ceiling-dbfs",type=float,default=-3.0)
    p.add_argument("--max-tracks",type=int,default=24)
    args=p.parse_args()
    if not -45<=args.target_dbfs<=-18 or not -12<=args.peak_ceiling_dbfs<=-1:
        p.error("Invalid loudness/peak safety settings")
    if not 1<=args.max_tracks<=100: p.error("Invalid max-tracks")
    if args.out.exists(): p.error("Output exists: use a fresh folder")
    candidates=sorted((args.run_dir/"results").glob("*.wav"))
    if not candidates: p.error("Run contains no successful model WAVs")
    if len(candidates)>args.max_tracks:
        p.error("Too many outputs; choose a larger --max-tracks")
    clean=args.run_dir/"inputs"/"clean.wav"
    noisy=args.run_dir/"inputs"/"noisy.wav"
    if not clean.is_file() or not noisy.is_file(): p.error("Missing reference/noisy WAV")
    order=list(candidates)
    random.Random(args.seed).shuffle(order)
    args.out.mkdir(parents=True)
    report={"schema":"cuewire-blind-listening-v1",
            "seed":args.seed,
            "target_rms_dbfs":args.target_dbfs,
            "peak_ceiling_dbfs":args.peak_ceiling_dbfs,
            "reference":{},"tracks":{}}
    for name,src in (("reference_clean.wav",clean),("reference_noisy.wav",noisy)):
        out,gain=normalized(read_wav(src),args.target_dbfs,args.peak_ceiling_dbfs)
        path=args.out/name
        write_wav(path,out)
        report["reference"][name]={"source_sha256":sha(src),"wav_sha256":sha(path),"gain":gain}
    for i,source in enumerate(order,1):
        name=f"track_{i:02}.wav"
        path=args.out/name
        out,gain=normalized(read_wav(source),args.target_dbfs,args.peak_ceiling_dbfs)
        write_wav(path,out)
        report["tracks"][name]={"model":source.stem,"source_sha256":sha(source),
                                "wav_sha256":sha(path),"gain":gain}
    (args.out/"answer_key.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(f"Created {len(order)} gain-matched numbered tracks. No playback started.")
    print("FIRST listen without reading answer_key.json. Keep system volume fixed.")
    print(f"  pw-play {args.out}/reference_clean.wav")
    print(f"  pw-play {args.out}/reference_noisy.wav")
    print(f'  for f in "{args.out}"/track_*.wav; do echo "$f"; pw-play "$f"; done')
    print(f"Then reveal the model map: {args.out}/answer_key.json")

if __name__=="__main__":
    main()
