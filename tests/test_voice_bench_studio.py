#!/usr/bin/env python3
"""Synthetic, offline test: studio variants may not clip or change originals."""
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
import numpy as np

TOOLS=Path(__file__).resolve().parents[1]/"tools"


def write(path,arr):
    with wave.open(str(path),"wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(np.round(np.clip(arr,-0.9,0.9)*32767).astype("<i2").tobytes())


def main():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        t=np.arange(48000*2)/48000.0
        clean=0.11*np.sin(2*np.pi*187*t)+0.02*np.sin(2*np.pi*2300*t)
        noise=np.random.default_rng(9001).normal(0,0.007,len(t))
        a=root/"test.wav"
        b=root/"damaged.wav"
        write(a,clean)
        write(b,clean+noise)
        dst=root/"studio"
        proc=subprocess.run([sys.executable,str(TOOLS/"voice_bench_studio.py"),
             "--input",f"04={b}","--input",f"14={b}",
             "--clean",str(a),"--noisy",str(b),"--out",str(dst)],
             text=True,capture_output=True,timeout=50)
        assert proc.returncode==0,proc.stdout+proc.stderr
        m=json.loads((dst/"manifest.json").read_text())
        assert len(m["jobs"])==12,len(m["jobs"])
        assert (dst/"results"/"blind04__control.wav").read_bytes()==b.read_bytes()
        assert all(v["status"]=="ok" and v["sample_peak_dbfs"]<=-2.9
                   for v in m["jobs"].values())
        assert len(json.loads((dst/"scores.json").read_text())["results"])==12
        result=subprocess.run(
             ["sha256sum","-c","SHA256SUMS"],cwd=dst,
             text=True,capture_output=True,timeout=20)
        assert result.returncode==0,result.stdout+result.stderr
    print("PASS: 12 blind studio A/B WAVs, untouched controls, no clipping, SHA256")

if __name__=="__main__":
    main()
