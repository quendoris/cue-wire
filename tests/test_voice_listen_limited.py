#!/usr/bin/env python3
"""M4: verify +2 dB audition via peak envelope never overwrites RAW."""
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/"tools/voice_listen_limited.py"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wav_write(path,values):
    with wave.open(str(path),"wb") as w:
        w.setframerate(48000)
        w.setnchannels(1)
        w.setsampwidth(2)
        a=array("h",values)
        if sys.byteorder!="little":a.byteswap()
        w.writeframes(a.tobytes())


def invoke(*args):
    return subprocess.run([sys.executable,str(APP),*map(str,args)],
                          capture_output=True,text=True,timeout=15)


def measure(path):
    with wave.open(str(path),"rb") as w:
        b=w.readframes(w.getnframes())
        a=array("h")
        a.frombytes(b)
        if sys.byteorder!="little":a.byteswap()
    count=len(a)
    rms=math.sqrt(sum(v*v for v in a)/count)/32768
    peak=max(abs(v) for v in a)/32768
    return 20*math.log10(peak),20*math.log10(rms),count


def main():
    directory=ROOT/".audit"/("M4-limiter-test-"+str(os.getpid()))
    directory.mkdir(parents=True,exist_ok=False)
    try:
        # Typical raw: -38 dBFS low-level voice with a -12 dBFS plosive.
        vals=[int(410*math.sin(2*math.pi*170*i/48000))
              for i in range(48000)]
        vals[4700]=8100
        vals[15000]=-8100
        raw=directory/"raw.wav"
        wav_write(raw,vals)
        rawhash=digest(raw)
        output=directory/"listen-limited-v3.wav"
        ret=invoke("--in",raw,"--out",output,
                   "--extra-db",2,"--peak-ceiling-dbfs",-1)
        assert ret.returncode==0,(ret.stdout,ret.stderr)
        doc=json.loads(output.with_suffix(".json").read_text())
        assert doc["schema"]=="cuewire-M4-listen-limited-v1"
        assert doc["input_sha256"]==rawhash
        assert doc["output_sha256"]==digest(output)
        assert digest(raw)==rawhash
        assert doc["input_measurements"]["frames"]==48000
        assert doc["output_measurements"]["frames"]==48000
        assert doc["output_measurements"]["clipped_samples"]==0
        assert doc["limiter"]["maximum_envelope_reduction_db"]>=1.8
        assert doc["limiter"]["integer_guard_corrected_samples"]==0
        before_peak,before_rms,_=measure(raw)
        out_peak,out_rms,_=measure(output)
        assert out_peak<=-0.999, out_peak
        assert out_rms-before_rms>9
        # Compare with maximum NO-compression gain at same -1 dBFS
        # ceiling: a +2 dB lookahead derivative should be noticeably
        # louder on speech segments, without sample clipping.
        peak_safe_db=-1-before_peak
        fixed=(10**(peak_safe_db/20))
        theoretical_fixed_rms=before_rms+peak_safe_db
        assert out_rms-theoretical_fixed_rms>1.4,\
            (before_rms,out_rms,theoretical_fixed_rms,ret.stdout)
        second=invoke("--in",raw,"--out",output)
        assert second.returncode!=0
        assert "exists" in second.stderr
        assert digest(raw)==rawhash
        outside=invoke("--in",raw,"--out","/tmp/voice-listen-unsafe.wav")
        assert outside.returncode!=0
        assert "private" in outside.stderr
        nearly_silent=directory/"silence.wav"
        wav_write(nearly_silent,[0]*48000)
        rejected=invoke("--in",nearly_silent,
                        "--out",directory/"silent-limited.wav")
        assert rejected.returncode!=0
        assert not (directory/"silent-limited.wav").exists()
        print("PASS M4 +2dB peak limiter: gain above fixed ceiling, "
              "sample-peak compliant, intact SHA, no overwrite/silence/outside writes")
    finally:
        shutil.rmtree(directory)


if __name__=="__main__":
    main()
