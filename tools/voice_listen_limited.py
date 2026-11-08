#!/usr/bin/env python3
"""CueWire M4: +N dB louder private listening derivative with gentle peak control.

The pre-gain exceeds the RAW sample-peak-only gain by --extra-db, then
a *lookahead offline peak envelope* attenuates brief transients to
prevent PCM16 sample clipping. Not mastering, de-essing, a true-peak
limiter or real-time audio. No original files overwritten, no
microphone playback/routing, no services, no audio uploaded.

Algorithm: attenuation demand at each sample, backward exponentially
smoothed attack (lookahead), forward exponentially smoothed release,
and a final conservative PCM16 integer sample-peak ceiling.
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
ROOT=Path(__file__).resolve().parents[1]
AUDIT=(ROOT/".audit").resolve()
MAX_DURATION=300


def digest(path):
    sha=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            sha.update(chunk)
    return sha.hexdigest()


def read_raw(path):
    with wave.open(str(path),"rb") as w:
        if (w.getframerate(),w.getnchannels(),w.getsampwidth(),
            w.getcomptype())!=(RATE,1,2,"NONE"):
            raise ValueError("Require PCM16 mono 48kHz WAV")
        count=w.getnframes()
        if count<48000 or count>RATE*MAX_DURATION:
            raise ValueError("Input duration must be 1..300 seconds")
        raw=w.readframes(count)
        if len(raw)!=2*count:
            raise ValueError("Truncated WAV payload")
    vals=array("h")
    vals.frombytes(raw)
    if sys.byteorder!="little":vals.byteswap()
    if len(vals)!=count:
        raise ValueError("WAV header does not match PCM samples")
    return vals


def measures(samples):
    count=len(samples)
    if not count:
        raise ValueError("Empty sample buffer")
    square_sum=0
    high=0
    clipped=0
    for v in samples:
        a=abs(v)
        if a>high:high=a
        square_sum+=v*v
        if a>=32767:clipped+=1
    peak=high/32768
    rms=math.sqrt(square_sum/count)/32768
    return {
        "frames":count,
        "duration_s":round(count/RATE,6),
        "peak_dbfs":round(20*math.log10(max(peak,1e-12)),5),
        "rms_dbfs":round(20*math.log10(max(rms,1e-12)),5),
        "clipped_samples":clipped,
    }


def limiter(samples,preamp_db,ceiling_db,attack_ms,release_ms):
    """Fully offline 2-pass lookahead envelope; return new PCM and metadata."""
    gain=10**(preamp_db/20)
    ceiling_integer=min(32766,max(1,math.floor(
        32768*10**(ceiling_db/20))-4))
    attack=math.exp(-1/(RATE*attack_ms/1000))
    release=math.exp(-1/(RATE*release_ms/1000))
    count=len(samples)
    demand=array("f",[0.0])*count
    upcoming=0.0
    peak_required_reduction=0.0

    # Reverse prepass gives attenuation time to ramp up before
    # an impending transient; does not introduce latency into WAV.
    for i in range(count-1,-1,-1):
        amplified=abs(samples[i])*gain
        needed=0.0 if amplified<=ceiling_integer else (
            1.0-ceiling_integer/amplified)
        if needed>peak_required_reduction:
            peak_required_reduction=needed
        upcoming=max(needed,upcoming*attack)
        demand[i]=upcoming

    processed=array("h")
    current=0.0
    attenuation_frames=0
    peak_att_db=0.0
    corrected_samples=0
    for i,value in enumerate(samples):
        current=max(demand[i],current*release)
        if current>1e-7:
            attenuation_frames+=1
            reduction_db=-20*math.log10(max(1-current,1e-9))
            if reduction_db>peak_att_db:peak_att_db=reduction_db
        output=round(value*gain*(1-current))
        if output>ceiling_integer:
            output=ceiling_integer
            corrected_samples+=1
        elif output< -ceiling_integer:
            output=-ceiling_integer
            corrected_samples+=1
        processed.append(output)
    return processed,{
        "pre_gain_db":round(preamp_db,6),
        "pre_gain_linear":round(gain,9),
        "peak_ceiling_dbfs":ceiling_db,
        "ceiling_sample_integer":ceiling_integer,
        "attack_ms":attack_ms,
        "release_ms":release_ms,
        "maximum_envelope_reduction_db":round(peak_att_db,5),
        "maximum_instantaneous_required_reduction_db":round(
            -20*math.log10(max(1-peak_required_reduction,1e-9)),5),
        "frames_with_nonzero_envelope":attenuation_frames,
        "integer_guard_corrected_samples":corrected_samples,
        "type":"two_pass_peak_lookahead_envelope",
    }


def write_pcm(path,samples):
    # Exclusive-create mode rejects symlink collision and overwrite.
    with path.open("xb") as dst:
        with wave.open(dst,"wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            if sys.byteorder!="little":
                samples.byteswap()
            w.writeframes(samples.tobytes())


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in",dest="source",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path,
                   help="New listen-only derivative in private .audit directory")
    p.add_argument("--extra-db",type=float,default=2,
                   help="Additional pre-gain beyond raw's peak-safe fixed gain")
    p.add_argument("--peak-ceiling-dbfs",type=float,default=-1)
    p.add_argument("--attack-ms",type=float,default=10)
    p.add_argument("--release-ms",type=float,default=100)
    p.add_argument("--max-preamp-db",type=float,default=24)
    args=p.parse_args()
    all_numbers=[args.extra_db,args.peak_ceiling_db,args.attack_ms,
                 args.release_ms,args.max_preamp_db]
    if not all(map(math.isfinite,all_numbers)):
        p.error("All numeric parameters must be finite")
    if not (0<=args.extra_db<=6 and -9<=args.peak_ceiling_dbfs<=-1 and
            3<=args.attack_ms<=30 and 30<=args.release_ms<=500 and
            0<=args.max_preamp_db<=30):
        p.error("Allowed: extra 0..6, ceiling -9..-1, attack 3..30ms, "
                "release 30..500ms, max preamp 0..30dB")
    source=args.source.expanduser().resolve(strict=True)
    output=args.out.expanduser().resolve()
    if not AUDIT.is_dir() or AUDIT not in output.parents or \
            output.suffix.lower()!=".wav" or output==source:
        p.error("Output must be a DIFFERENT .wav inside the local private .audit directory")
    report=output.with_suffix(".json")
    if output.exists() or output.is_symlink() or report.exists() or report.is_symlink():
        p.error("Output WAV or report exists; do not overwrite a previous experiment")
    rawhash=digest(source)
    samples=read_raw(source)
    before=measures(samples)
    if before["rms_dbfs"]< -80:
        p.error("Recorded WAV is effectively silent; refusing to amplify noise")
    if before["clipped_samples"]>0:
        p.error("RAW input is clipped. Duplicating clipping with gain is not a repair.")
    # A conventional fixed-gain normalization would stop at the sample
    # peak ceiling; request N more dB, then tame only necessary peaks.
    peak_gain=args.peak_ceiling_dbfs-before["peak_dbfs"]
    requested_gain=peak_gain+args.extra_db
    applied_gain=min(requested_gain,args.max_preamp_db)
    if applied_gain<-24:
        p.error("Input needs >24 dB reduction; review source.")
    rendered,info=limiter(samples,applied_gain,args.peak_ceiling_dbfs,
                          args.attack_ms,args.release_ms)
    after=measures(rendered)
    max_allowed=20*math.log10(info["ceiling_sample_integer"]/32768)
    if after["clipped_samples"] or after["peak_dbfs"]>max_allowed+0.00002:
        raise RuntimeError("Internal limiter PCM sample-peak acceptance failed")
    if digest(source)!=rawhash:
        raise RuntimeError("RAW source changed while rendering; refusing publication")
    output.parent.mkdir(parents=True,exist_ok=True)
    write_pcm(output,rendered)
    # Re-read both, to detect output corruption and source tampering.
    if digest(source)!=rawhash or measures(read_raw(output))!=after:
        raise RuntimeError("Post-write integrity validation failed; derivative not certified")
    result={
        "schema":"cuewire-M4-listen-limited-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "purpose":"audition_only_not_mastering",
        "input":source.name,
        "input_sha256":rawhash,
        "input_measurements":before,
        "output":output.name,
        "output_sha256":digest(output),
        "output_measurements":after,
        "requested_extra_db_beyond_peak_safe_gain":args.extra_db,
        "requested_pre_gain_db":round(requested_gain,6),
        "peak_safe_input_gain_db":round(peak_gain,6),
        "observed_rms_increase_db":round(after["rms_dbfs"]-before["rms_dbfs"],5),
        "limiter":info,
        "safety":[
            "RAW WAV byte-identical SHA verified and NOT overwritten.",
            "No playback, mic access, audio graph or system settings changes.",
            "Only sample-peak ceiling verified; TRUE PEAK not measured.",
            "Lookahead and release envelope change transient dynamics; listen for pumping.",
            "No de-noising, AEC, compression profile, studio mastering or speech-LUFS claim.",
            "Whole-file RMS includes pauses and silence."
        ]
    }
    with report.open("x",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2,sort_keys=True)
        f.write("\n")
    print("PASS: unchanged raw SHA256",rawhash)
    print("Before: peak",before["peak_dbfs"],"RMS",before["rms_dbfs"],"dBFS")
    print("Preamp:",info["pre_gain_db"],"dB (extra",args.extra_db,
          "dB beyond a peak-safe gain)")
    print("Limiter maximal gain reduction:",
          info["maximum_envelope_reduction_db"],"dB")
    print("After: peak",after["peak_dbfs"],"RMS",after["rms_dbfs"],"dBFS")
    print("Actual RMS increase:",result["observed_rms_increase_db"],"dB")
    print("New listening WAV:",output)
    print("Report:",report)
    print("Source unchanged; no sound played.")


if __name__=="__main__":
    main()
