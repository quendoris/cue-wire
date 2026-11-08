#!/usr/bin/env python3
"""Blind-independent simple waveform quality diagnostics against a known reference.

These are diagnostic SI-SDR style and quiet-interval metrics, NOT perceived
intelligibility, true peak, LUFS or speech-quality certificates.
"""
import array
import json
import math
from pathlib import Path
import sys
import wave

RATE=48000

def read(path):
    with wave.open(str(path),"rb") as f:
        assert (f.getnchannels(),f.getframerate(),f.getsampwidth())==(1,RATE,2)
        samples=array.array("h")
        samples.frombytes(f.readframes(f.getnframes()))
    if sys.byteorder!="little":
        samples.byteswap()
    return [float(v)/32768.0 for v in samples]

def rms(segment):
    return math.sqrt(sum(x*x for x in segment)/len(segment))

def db(value):
    return 20.0*math.log10(max(1e-14,value))

def gain_invariant_sdr(reference, estimate):
    assert len(reference)==len(estimate)
    refmean=sum(reference)/len(reference)
    estmean=sum(estimate)/len(estimate)
    ref=[x-refmean for x in reference]
    est=[x-estmean for x in estimate]
    reference_power=sum(x*x for x in ref)
    projection=sum(x*y for x,y in zip(ref,est))/max(reference_power,1e-18)
    signal_power=projection*projection*reference_power
    residual_power=sum((y-projection*x)**2 for x,y in zip(ref,est))
    return 10*math.log10(max(signal_power,1e-18)/max(residual_power,1e-18))

def best_aligned_sdr(reference, estimate):
    """Diagnostic SDR after a small lag correction. STFT + high-pass can
    introduce frequency-dependent phase rotation; a single delay only
    partially compensates it. This is NOT objective intelligibility."""
    n=len(reference)
    rate=16
    best=(-1.0,0)
    for lag in range(-256,257,4):
        if lag>=0:
            left=reference[:n-lag:rate]
            right=estimate[lag::rate]
        else:
            left=reference[-lag::rate]
            right=estimate[:n+lag:rate]
        dot=sum(x*y for x,y in zip(left,right))
        es=sum(x*x for x in left)
        er=sum(y*y for y in right)
        similarity=dot*dot/max(es*er,1e-30)
        if similarity>best[0]:
            best=(similarity,lag)
    lag=best[1]
    if lag>=0:
        return gain_invariant_sdr(reference[:n-lag],estimate[lag:]),lag
    return gain_invariant_sdr(reference[-lag:],estimate[:n+lag]),lag


def main():
    if len(sys.argv)<2 or len(sys.argv)>3 or (len(sys.argv)==3 and sys.argv[2]!="--diagnostic"):
        raise SystemExit("usage: voice_quality.py VOICE_PREVIEW_DIRECTORY [--diagnostic]")
    directory=Path(sys.argv[1])
    clean=read(directory/"voice_reference.wav")
    bad=read(directory/"voice_before.wav")
    fixed=read(directory/"voice_after.wav")
    assert len(clean)==len(bad)==len(fixed)
    before=gain_invariant_sdr(clean,bad)
    after=gain_invariant_sdr(clean,fixed)
    aligned_before,lag_before=best_aligned_sdr(clean,bad)
    aligned_after,lag_after=best_aligned_sdr(clean,fixed)
    # Fixture makes the first 650 ms voice-free, so measure the residual noise
    # without confounding actual quiet consonants.
    quiet=round(0.6*RATE)
    quiet_before=db(rms(bad[:quiet]))
    quiet_after=db(rms(fixed[:quiet]))
    metrics={
      "metric_version":"voice-waveform-audit-v1",
      "sdr_before_db":round(before,3),
      "sdr_after_db":round(after,3),
      "sdr_delta_db":round(after-before,3),
      "aligned_sdr_before_db":round(aligned_before,3),
      "aligned_sdr_after_db":round(aligned_after,3),
      "aligned_sdr_delta_db":round(aligned_after-aligned_before,3),
      "alignment_before_frames":lag_before,
      "alignment_after_frames":lag_after,
      "noise_only_before_dbfs":round(quiet_before,3),
      "noise_only_after_dbfs":round(quiet_after,3),
      "noise_only_reduction_db":round(quiet_before-quiet_after,3),
    }
    (directory/"quality.json").write_text(json.dumps(metrics,indent=2,sort_keys=True)+"\n")
    assert all(math.isfinite(value) for key,value in metrics.items() if isinstance(value,float))
    if len(sys.argv)==2:
        assert after-before>0.5, (
            f"gain-invariant SDR worsened or insufficient improvement: {before:.2f} -> {after:.2f} dB"
        )
        assert quiet_after<quiet_before-8.0, (
        f"noise-only interval not quiet enough: {quiet_before:.2f} -> {quiet_after:.2f} dBFS"
    )
    print("WAVEFORM QUALITY AUDIT: "+json.dumps(metrics,sort_keys=True))
    if len(sys.argv)==2:
        print("PASS: quiet noise reduction >8 dB AND synthetic waveform SDR improves >0.5 dB; no claim on intelligibility")
    else:
        print("DIAGNOSTIC ONLY: no quality gate applied")

if __name__=="__main__":
    main()
