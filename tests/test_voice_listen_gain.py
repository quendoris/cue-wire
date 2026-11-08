#!/usr/bin/env python3
"""Offline immutable gain-audition test: no mic, no speaker, no PipeWire."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave
from array import array

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/"tools/voice_listen_gain.py"


def write_wav(path,values):
    with wave.open(str(path),"wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        samples=array("h",values)
        if sys.byteorder!="little":
            samples.byteswap()
        audio.writeframes(samples.tobytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args):
    return subprocess.run([sys.executable,str(APP),*map(str,args)],
                          text=True,capture_output=True,timeout=10)


def main():
    root=ROOT/".audit"/("M4-listening-copy-test-"+str(os.getpid()))
    root.mkdir(parents=True,exist_ok=False)
    try:
        raw=root/"raw.wav"
        wave_values=[1000 if i%2 else -1000 for i in range(48000)]
        write_wav(raw,wave_values)
        original=sha(raw)
        out=root/"listen-gain.wav"
        result=run("--in",raw,"--out",out)
        assert result.returncode==0,(result.stdout,result.stderr)
        assert sha(raw)==original
        assert out.is_file()
        report=json.loads(out.with_suffix(".json").read_text())
        assert report["schema"]=="cuewire-M4-listen-only-gain-v1"
        assert report["input_sha256"]==original
        assert report["output_sha256"]==sha(out)
        assert report["input_measurements"]["frames"]==48000
        assert report["output_measurements"]["frames"]==48000
        assert report["gain"]["chosen_gain_db"]>0
        assert report["output_measurements"]["rms_dbfs"]>-23.15
        assert report["output_measurements"]["peak_dbfs"]<=-3.0
        assert report["output_measurements"]["clipped_samples"]==0
        repeat=run("--in",raw,"--out",out)
        assert repeat.returncode!=0
        assert "already exists" in repeat.stderr
        assert sha(raw)==original

        # A single transient peak must cap the gain; fixed-gain mode
        # cannot silently compress a plosive to make other words louder.
        spiky=root/"spiky.wav"
        values=[200 if i%2 else -200 for i in range(48000)]
        values[100]=29000
        write_wav(spiky,values)
        outpeak=root/"spiky-listen.wav"
        peak=run("--in",spiky,"--out",outpeak)
        assert peak.returncode==0,(peak.stdout,peak.stderr)
        rp=json.loads(outpeak.with_suffix(".json").read_text())
        assert rp["gain"]["gain_limited_by_peak"]
        assert rp["output_measurements"]["peak_dbfs"]<=-2.999
        assert rp["output_measurements"]["clipped_samples"]==0

        # Digital silence must not be amplified into an audition.
        zero=root/"silent.wav"
        write_wav(zero,[0]*4800)
        nosound=run("--in",zero,"--out",root/"silent-listen.wav")
        assert nosound.returncode!=0
        assert not (root/"silent-listen.wav").exists()

        # No output can be written to a public/nonprivate location.
        outside=run("--in",raw,"--out","/tmp/cuewire-untrusted-listen.wav")
        assert outside.returncode!=0
        assert "private" in outside.stderr
        print("PASS M4 gain listen: immutable raw SHA, RMS boost, "
              "peak ceiling, no clip, no overwrite, no silence boost, private-only")
    finally:
        shutil.rmtree(root)


if __name__=="__main__":
    main()
