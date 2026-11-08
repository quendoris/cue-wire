#!/usr/bin/env python3
"""Independent SHA-256, WAV and deterministic-metadata audit of offline voice demo."""
import hashlib
import json
import math
from pathlib import Path
import sys
import wave


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_wav(path, count):
    with wave.open(str(path),"rb") as f:
        assert (f.getnchannels(),f.getframerate(),f.getsampwidth())==(1,48000,2)
        assert f.getnframes()==count
        return f.readframes(f.getnframes())


def main():
    if len(sys.argv)!=2:
        raise SystemExit("usage: audit_voice_preview.py OUTPUT_DIR")
    root=Path(sys.argv[1])
    report=json.loads((root/"report.json").read_text())
    assert report["algorithm"]=="cuewire-voice-offline-v0.1"
    assert report["sample_rate"]==48000
    assert report["noise_profile_frames"]>0 or report["profile_frames"]>0
    n=report["frames"]
    assert 0<n<=48000*60*10
    check_wav(root/"voice_before.wav",n)
    check_wav(root/"voice_after.wav",n)
    if report["fixture"]=="synthetic-voice-like-noise":
        check_wav(root/"voice_reference.wav",n)
        assert report["clean_reference_sha256"]==hash_file(root/"voice_reference.wav")
    assert report["source_file_sha256"]==hash_file(root/"voice_before.wav")
    assert report["output_file_sha256"]==hash_file(root/"voice_after.wav")
    assert math.isfinite(report["output_peak_dbfs"])
    assert report["output_peak_dbfs"] <= report["ceiling_dbfs"]+0.08
    assert report["profile_frames"]>=1
    assert report["input_overload_samples"]==0
    print("PASS voice offline: source/result/reference metadata, SHA256, WAV, peak ceiling")


if __name__=="__main__":
    main()
