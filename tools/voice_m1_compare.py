#!/usr/bin/env python3
"""M1 source-to-clean comparison, no voice-data upload or processing models.

Three distinctly named inputs:
 B0: test.wav (clean microphone original)
 B1: locally SHA-verified frozen blind-01 restoration
 B2: voice_before.wav (noisy speech)
It normalizes WAV format ONLY for evaluation, not the model.
"""
import argparse
import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.signal import welch
from voice_bench_metrics import load, score, rms, db, RATE


def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def canonical(src, dest):
    cmd=["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-y",
         "-i",str(src),"-map","0:a:0","-vn","-ac","1",
         "-ar",str(RATE),"-c:a","pcm_s16le",str(dest)]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    if p.returncode or not dest.is_file():
        raise RuntimeError("Failed to decode WAV: "+src.name+": "+p.stderr[-1200:])


def bands(x):
    freqs,psd=welch(x,fs=RATE,nperseg=min(4096,len(x)),detrend="constant")
    edges=[(80,250),(250,1000),(1000,4000),(4000,8000),(8000,12000),(12000,20000)]
    stats={}
    for lo,hi in edges:
        segment=(freqs>=lo)&(freqs<hi)
        energy=float(np.trapezoid(psd[segment],freqs[segment])) if segment.sum()>1 else 0.0
        stats[f"{lo}-{hi}Hz"]=round(db(math.sqrt(max(energy,0))),3)
    return stats


def describe(x):
    last=min(len(x),int(0.3*RATE))
    return {
        "duration_seconds":round(len(x)/RATE,6),
        "frame_count":int(len(x)),
        "sample_rms_dbfs":round(db(rms(x)),4),
        "sample_peak_dbfs":round(db(np.max(np.abs(x))),4),
        "last_300ms_rms_dbfs":round(db(rms(x[-last:])),4),
        "clipped_samples":int(np.count_nonzero(np.abs(x)>=32767/32768)),
        "approx_band_rms_dbfs":bands(x),
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--clean",required=True,type=Path)
    p.add_argument("--restored",required=True,type=Path)
    p.add_argument("--noisy",required=True,type=Path)
    p.add_argument("--chain-report",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args()
    if args.out.exists():
        p.error("Output report already exists; use a fresh filename.")
    for name in ("clean","restored","noisy","chain_report"):
        item=getattr(args,name)
        if not item.is_file():p.error(f"Missing {name}: {item}")
    if len({args.clean.resolve(),args.restored.resolve(),args.noisy.resolve()})!=3:
        p.error("The reference, restored, and damaged audio must be THREE distinct files.")
    chain=json.loads(args.chain_report.read_text())
    if chain.get("schema")!="cuewire-M1-frozen-processing-chain-v1":
        p.error("Unrecognized chain provenance report")
    if sha(args.restored)!=chain.get("blind_studio_wav_sha256"):
        p.error("Restored B1 does not match verified local blind-01 WAV!")
    with tempfile.TemporaryDirectory(prefix="cuewire-m1-eval-") as d:
        root=Path(d)
        data={}
        for label,path in (("B0",args.clean),("B1",args.restored),("B2",args.noisy)):
            dest=root/(label+".wav")
            canonical(path,dest)
            data[label]={"sha256":sha(path),"pcm48_sha256":sha(dest),"wav":load(dest)}
        b0,b1,b2=(data[k]["wav"] for k in ("B0","B1","B2"))
        result={
            "schema":"cuewire-M1-clean-comparison-v1",
            "source_chain_status":chain.get("status"),
            "source_chain_stages":chain.get("ordered_models"),
            "sample_rate":RATE,
            "references":{
                name:{
                    "source_file_basename":src.name,
                    "source_sha256":data[name]["sha256"],
                    "normalized_pcm48_sha256":data[name]["pcm48_sha256"],
                    "measurements":describe(data[name]["wav"])
                } for name,src in (
                    ("B0",args.clean),("B1",args.restored),("B2",args.noisy))
            },
            "restored_B1_vs_clean_B0":score(b0,b1),
            "raw_noisy_B2_vs_clean_B0":score(b0,b2),
            "limitations":[
                "The same short utterance must be present in all three recordings.",
                "Full 3-second reference and 2.97-second restoration differ in duration.",
                "SI-SDR and spectral bands cannot certify naturalness or studio voice quality.",
                "The final 300 ms correlation can be misleading if the reference tail is silent.",
                "No noisy/clean original audio was sent into model inference in this audit.",
                "This is a pre-existing output analysis; no new speech processing is performed."
            ]
        }
        a=result["restored_B1_vs_clean_B0"]["si_sdr_db"]
        b=result["raw_noisy_B2_vs_clean_B0"]["si_sdr_db"]
        result["gain_over_original_noisy_si_sdr_db"]=(
            round(a-b,4) if a is not None and b is not None else None)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2,sort_keys=True,ensure_ascii=False)+"\n")
    print("PASS: B1 verified against local frozen blind 01 and evaluated vs B0 and B2.")
    print("Ordered stages:", " -> ".join(chain["ordered_models"]))
    print("B1 vs test.wav SI-SDR:",result["restored_B1_vs_clean_B0"]["si_sdr_db"],"dB")
    print("B2 vs test.wav SI-SDR:",result["raw_noisy_B2_vs_clean_B0"]["si_sdr_db"],"dB")
    print("Original/test/restored WAVs unchanged. Report:",args.out)


if __name__=="__main__":
    main()
