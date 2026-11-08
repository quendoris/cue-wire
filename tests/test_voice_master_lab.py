#!/usr/bin/env python3
"""CI synthetic studio mastering regression: no private audio or ML inference."""
import hashlib
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

TOOLS=Path(__file__).resolve().parents[1]/"tools"


def write(path,signal):
    with wave.open(str(path),"wb") as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(48000)
        f.writeframes(np.round(np.clip(signal,-.8,.8)*32767).astype("<i2").tobytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    r=np.random.default_rng(20261108)
    seconds=2
    t=np.arange(48000*seconds,dtype=np.float64)/48000
    # Speech-like harmonics, variations and sibilants; artificial test only.
    voice=(.10*np.sin(2*np.pi*181*t)+.025*np.sin(2*np.pi*363*t)
           +.015*np.sin(2*np.pi*4000*t)*((np.sin(2*np.pi*3*t)>0).astype(float)))
    voice*=.15+.85*np.sin(2*np.pi*1.3*t)**2
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp)
        original=base/"B0.wav";restored=base/"B1.wav"
        write(original,voice)
        write(restored,voice + r.normal(0,0.002,len(voice)))
        frozen_hash=sha(restored)
        out=base/"master-v1"
        p=subprocess.run([
            sys.executable,str(TOOLS/"voice_master_lab.py"),
            "--input",f"B1={restored}",
            "--input",f"B0={original}",
            "--out",str(out)],
            capture_output=True,text=True,timeout=90)
        assert p.returncode==0,p.stdout+p.stderr
        meta=json.loads((out/"manifest.json").read_text())
        assert len(meta["outputs"])==16, len(meta["outputs"])
        assert meta["inputs"]["B1"]["source_sha256"]==frozen_hash
        assert sha(restored)==frozen_hash, "Source was altered"
        assert (out/"results"/"B1__control.wav").read_bytes()==restored.read_bytes()
        assert (out/"results"/"B0__control.wav").read_bytes()==original.read_bytes()
        for name,row in meta["outputs"].items():
            pth=out/"results"/(name+".wav")
            assert sha(pth)==row["output_sha256"],name
            with wave.open(str(pth),"rb") as f:
                assert (f.getframerate(),f.getnchannels(),f.getsampwidth())==(48000,1,2)
                assert f.getnframes()==len(voice),name
            if not name.endswith("__control"):
                assert row["estimated_true_peak_dbfs"]<=-1.49,(name,row)
            assert row["rms_dbfs"]>-100,name
        # Listening must remain separated by original source and verify
        # source/output hashes, never mix B0 (clean) with B1 (restored).
        listening=base/"master-listening"
        listen=subprocess.run([
            sys.executable,str(TOOLS/"voice_master_listen.py"),
            str(out),"--out",str(listening),"--seed","88"],
            capture_output=True,text=True,timeout=50)
        assert listen.returncode==0,listen.stdout+listen.stderr
        for label in ("B0","B1"):
            key=json.loads((listening/label/"answer_key.json").read_text())
            assert key["source_id"]==label
            assert len(key["tracks"])==8
            assert len(list((listening/label).glob("track_*.wav")))==8
            assert key["source_sha256"]==meta["inputs"][label]["source_sha256"]
        repeat_listening=subprocess.run([
            sys.executable,str(TOOLS/"voice_master_listen.py"),
            str(out),"--out",str(listening)],
            capture_output=True,text=True,timeout=10)
        assert repeat_listening.returncode!=0
        result=subprocess.run(["sha256sum","-c","SHA256SUMS"],cwd=out,
                             capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stdout+result.stderr
        duplicate=subprocess.run([
            sys.executable,str(TOOLS/"voice_master_lab.py"),
            "--input",f"B1={restored}","--out",str(out)],
            capture_output=True,text=True,timeout=10)
        assert duplicate.returncode!=0
        assert "Output exists" in duplicate.stderr
        # Optional libraries are deliberately not installed in this test;
        # no third-party downloads or network calls are made.
    print("PASS: studio B0/B1 controls unchanged; 14 EQ/dynamic profiles; "
          "same length, peak-safe outputs, immutable runs and SHA256")


if __name__=="__main__":
    main()
