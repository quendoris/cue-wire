#!/usr/bin/env python3
"""CueWire M3: reproducible, offline STUDIO voice post-production benchmark.

Inputs B1 (frozen restored speech) and B0 (clean recording) are each
processed independently. NO denoising/voice synthesis/reference leakage.
No playback, network, devices, resident services, or system changes.
Never overwrites a prior run or original WAV.

The built-in DSP is deliberately conservative; profiles are starting
hypotheses, never claims of a studio-perfect voice.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import ndimage, signal

RATE=48000
PROFILES = {
    "control": {"hp": 0, "eq": [], "deess": 0.0, "ratio": 1.0},
    "transparent": {"hp": 65, "eq": [(260,-0.6,0.75),(3000,0.7,0.9)],
                    "deess": 1.0, "ratio": 1.35},
    "intimate": {"hp": 65, "eq": [(175,0.6,0.7),(340,-0.7,1.0),
                                 (2900,0.4,0.8)], "deess": 1.2, "ratio": 1.45},
    "articulate": {"hp": 80, "eq": [(320,-1.0,1.0),(3100,1.2,0.9),
                                   (7200,0.4,0.7)], "deess": 1.5, "ratio": 1.45},
    "soft": {"hp": 65, "eq": [(250,-0.6,0.8),(2700,0.2,0.8),
                            (6400,-0.7,0.8)], "deess": 1.8, "ratio": 1.30},
    "radio": {"hp": 85, "eq": [(310,-1.3,0.9),(3100,1.0,0.8),
                             (7600,-0.3,0.9)], "deess": 1.7, "ratio": 2.0},
    "deess_only": {"hp": 0, "eq": [], "deess": 2.0, "ratio": 1.0},
    "compress_only": {"hp": 0, "eq": [], "deess": 0.0, "ratio": 1.75},
}


def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def db(value):
    return 20*math.log10(max(float(value),1e-12))


def rms(x):
    return math.sqrt(float(np.mean(np.square(np.asarray(x,dtype=np.float64)))))


def read(path):
    info=sf.info(path)
    if (info.samplerate,info.channels,info.subtype)!=(RATE,1,"PCM_16"):
        raise ValueError(f"Only 48kHz mono PCM16 accepted, found {info}: {path}")
    if info.duration<1 or info.duration>180:
        raise ValueError("Audio must be 1..180 seconds")
    x,_=sf.read(path,dtype="float64")
    if x.ndim!=1 or not np.all(np.isfinite(x)):
        raise ValueError("Non-finite/stereo input")
    return x


def bell(x,hz,gain_db,q):
    """Standard RBJ biquad peaking EQ, causal and length-preserving."""
    w=2*math.pi*hz/RATE
    A=10**(gain_db/40)
    a=math.sin(w)/(2*q)
    co=math.cos(w)
    b=np.array([1+a*A,-2*co,1-a*A])
    den=np.array([1+a/A,-2*co,1-a/A])
    return signal.lfilter(b/den[0],den/den[0],x)


def deess(x,max_atten_db):
    if max_atten_db<=0:return x
    high=signal.sosfilt(signal.butter(2,5100,fs=RATE,btype="highpass",output="sos"),x)
    env=np.sqrt(np.maximum(ndimage.uniform_filter1d(high**2,size=round(0.012*RATE)),0.0))
    threshold=max(0.0009,float(np.quantile(env,0.82)))
    excess=np.clip((env-threshold)/max(threshold,1e-9),0.0,1.0)
    dynamic=np.power(10.0,-max_atten_db*excess/20.0)
    smooth=ndimage.uniform_filter1d(dynamic,size=round(0.014*RATE))
    return x-high*(1.0-smooth)


def compress(x,ratio):
    if ratio<=1:return x,{"threshold_dbfs":None,"max_reduction_db":0.0}
    envelope=np.sqrt(np.maximum(ndimage.uniform_filter1d(x**2,size=round(.020*RATE)),0))
    lev=20*np.log10(np.maximum(envelope,1e-10))
    active=lev[lev>max(-52.0,float(np.quantile(lev,0.70))-12)]
    threshold=float(np.quantile(active,0.55)) if len(active)>32 else -24.0
    raw=np.clip(np.maximum(0.0,lev-threshold)*(1-1/ratio),0,4.0)
    smooth=ndimage.uniform_filter1d(raw,size=round(.040*RATE))
    gain=10**(-smooth/20.0)
    return x*gain,{"threshold_dbfs":round(threshold,3),
                   "max_reduction_db":round(float(np.max(smooth)),4)}


def true_peak(x):
    """4x oversampled sampled-peak approximation, not a certified meter."""
    if len(x)==0:return 0.0
    return float(np.max(np.abs(signal.resample_poly(x,4,1))))


def loudness(x):
    try:
        import pyloudnorm as pyln
    except ImportError:
        return None
    value=float(pyln.Meter(RATE).integrated_loudness(x))
    return value if math.isfinite(value) else None


def finish(x,target_lufs,ceiling_dbfs=-1.5,max_makeup_db=10):
    """Loudness match with a STRICT oversampled peak cap, no hard clipping.

    Gain is bounded, and if the target conflicts with true peak,
    reduce gain rather than silently distort. LRA unreliable on 3s clips.
    """
    before=loudness(x)
    if before is not None:
        desired=target_lufs-before
        method="BS1770 integrated loudness"
    else:
        desired=-24-db(rms(x))
        method="RMS fallback; NOT an LUFS measurement"
    desired=float(np.clip(desired,-6,max_makeup_db))
    factor=10**(desired/20)
    peak=true_peak(x)
    ceiling=10**(ceiling_dbfs/20)
    if peak*factor>ceiling:
        factor=ceiling/max(peak,1e-12)
    out=x*factor
    if true_peak(out)>ceiling*(1+1e-8):
        raise RuntimeError("True peak ceiling exceeded after trim")
    return out,{"initial_lufs":before,"target_lufs":target_lufs,
                "final_lufs":loudness(out),
                "gain_db":round(db(factor),5),
                "normalization":method,
                "estimated_true_peak_dbfs":round(db(true_peak(out)),4),
                "safety_ceiling_dbfs":ceiling_dbfs,
                "target_reached":abs(loudness(out)-target_lufs)<0.6
                    if loudness(out) is not None else False}


def apply_native(x,profile):
    setting=PROFILES[profile]
    if profile=="control":
        return x.copy(),{"stages":["untouched PCM16 control"]}
    y=x.copy()
    stages=[]
    if setting["hp"]:
        y=signal.sosfilt(signal.butter(
            2,setting["hp"],btype="highpass",fs=RATE,output="sos"),y)
        stages.append(f"causal_highpass_{setting['hp']}Hz")
    for hz,gain,q in setting["eq"]:
        y=bell(y,hz,gain,q)
        stages.append(f"parametric_{hz}Hz_{gain:+g}dB_Q{q:g}")
    if setting["deess"]:
        y=deess(y,setting["deess"])
        stages.append(f"dynamic_deesser_max_{setting['deess']}dB")
    y,comp=compress(y,setting["ratio"])
    if setting["ratio"]>1:stages.append(f"downward_compressor_ratio_{setting['ratio']}")
    return y,{"stages":stages,"compression":comp}


def apply_pedalboard(x,profile):
    """OPTIONAL GPLv3 alternative implementation; never bundled in core."""
    from pedalboard import Pedalboard,PeakFilter,HighpassFilter,Compressor,BrickwallLimiter
    if profile=="pedalboard_clean":
        chain=[HighpassFilter(cutoff_frequency_hz=70),
               PeakFilter(cutoff_frequency_hz=300,q=0.8,gain_db=-0.8),
               PeakFilter(cutoff_frequency_hz=2900,q=0.8,gain_db=0.6),
               Compressor(threshold_db=-26,ratio=1.4,attack_ms=15,release_ms=130),
               BrickwallLimiter(ceiling_db=-2,true_peak=True)]
    elif profile=="pedalboard_presence":
        chain=[HighpassFilter(cutoff_frequency_hz=75),
               PeakFilter(cutoff_frequency_hz=270,q=0.8,gain_db=-1.1),
               PeakFilter(cutoff_frequency_hz=3400,q=0.9,gain_db=1.2),
               Compressor(threshold_db=-26,ratio=1.7,attack_ms=10,release_ms=140),
               BrickwallLimiter(ceiling_db=-2,true_peak=True)]
    else:raise ValueError(profile)
    y=np.asarray(Pedalboard(chain)(x.astype("float32"),float(RATE)),dtype=np.float64).reshape(-1)
    if len(y)!=len(x):raise RuntimeError("Pedalboard changed time length")
    return y,{"stages":[type(v).__name__ for v in chain],
              "engine":"spotify.pedalboard (GPL-3.0)"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",action="append",required=True,
                        help="Repeat LABEL=/absolute/path.wav e.g. B1=...")
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--include-pedalboard",action="store_true",
                        help="Opt-in GPLv3 third-party engine (must already be installed)")
    parser.add_argument("--target-lufs",type=float,default=-20)
    args=parser.parse_args()
    if not -27<=args.target_lufs<=-16:parser.error("Target loudness must be -27..-16 LUFS")
    if args.out.exists():parser.error("Output exists; never overwrite an experiment.")
    inputs={}
    for item in args.input:
        label,sep,path=item.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z][a-zA-Z0-9_-]{0,20}",label):
            parser.error("Input format LABEL=/path.wav")
        if label in inputs:parser.error("Duplicate input label")
        source=Path(path).expanduser().resolve()
        if not source.is_file():parser.error(f"Missing input file {source}")
        inputs[label]=source
    if len(inputs)>3:parser.error("At most three independent inputs")
    extra=["pedalboard_clean","pedalboard_presence"] if args.include_pedalboard else []
    if extra:
        try:import pedalboard
        except ImportError as e:parser.error("pedalboard missing; install in an isolated optional venv first")
    args.out.mkdir(parents=True)
    (args.out/"results").mkdir()
    state={"schema":"cuewire-studio-master-v1","inputs":{},
           "target_lufs":args.target_lufs,"profiles":list(PROFILES)+extra,
           "models":{},"outputs":{},"output_codec":"mono PCM16 48000Hz",
           "policy":{"no_denoising":True,"no_reference_conditioning":True,
                     "no_generated_reverb":True,"no_automatic_upload":True,
                     "true_peak_is_4x_estimate_not_certified":True}}
    try:state["software"]={m:importlib.metadata.version(m) for m in
                         ("numpy","scipy","soundfile")}
    except importlib.metadata.PackageNotFoundError:pass
    try:state["software"]["pyloudnorm"]=importlib.metadata.version("pyloudnorm")
    except importlib.metadata.PackageNotFoundError:pass
    for label,path in inputs.items():
        x=read(path)
        state["inputs"][label]={"source_sha256":sha(path),"frames":len(x),
                                "duration_s":round(len(x)/RATE,6)}
        for profile in list(PROFILES)+extra:
            if profile.startswith("pedalboard"):
                raw,detail=apply_pedalboard(x,profile)
            else:
                raw,detail=apply_native(x,profile)
            if not np.all(np.isfinite(raw)) or len(raw)!=len(x):
                raise RuntimeError(f"Non-finite output / wrong duration: {profile}")
            if profile=="control":
                y=x.copy()
                match={"normalization":"untouched (control)","final_lufs":loudness(x),
                       "estimated_true_peak_dbfs":db(true_peak(x))}
            else:
                y,match=finish(raw,args.target_lufs)
            if np.max(np.abs(y))>1.0:
                raise RuntimeError("Output peak would clip")
            dest=args.out/"results"/f"{label}__{profile}.wav"
            sf.write(dest,y,RATE,subtype="PCM_16")
            final=read(dest)
            state["outputs"][dest.stem]={
                "source":label,"profile":profile,
                "output_sha256":sha(dest),"frames":len(final),
                "rms_dbfs":round(db(rms(final)),4),
                "sample_peak_dbfs":round(db(np.max(np.abs(final))),4),
                "estimated_true_peak_dbfs":round(db(true_peak(final)),4),
                "crest_factor_db":round(db(np.max(np.abs(final)))-db(rms(final)),4),
                "stage_detail":detail,"level_detail":match,
            }
    (args.out/"manifest.json").write_text(json.dumps(
        state,indent=2,sort_keys=True,ensure_ascii=False)+"\n")
    # Metrics use clean B0 for EVALUATION only. The independent DSP
    # processors above never receive B0 while processing B1.
    scores={"schema":"cuewire-studio-score-v1",
            "reference_source":"B0" if "B0" in inputs else None,
            "warning":"SI-SDR/correlation measure waveform similarity, NOT natural studio voice quality.",
            "items":{}}
    if "B0" in inputs:
        from voice_bench_metrics import score
        reference=read(inputs["B0"])
        for name in state["outputs"]:
            candidate=read(args.out/"results"/(name+".wav"))
            scores["items"][name]=score(reference,candidate)
    else:
        scores["warning"]+=" No clean reference provided, so no SI-SDR."
    (args.out/"scores.json").write_text(json.dumps(
        scores,indent=2,sort_keys=True)+"\n")
    checks=[]
    for f in sorted(args.out.rglob("*")):
        if f.is_file() and f.name!="SHA256SUMS":
            checks.append(f"{sha(f)}  {f.relative_to(args.out)}")
    (args.out/"SHA256SUMS").write_text("\n".join(checks)+"\n")
    print(f"PASS: {len(state['outputs'])} independent, safe studio A/B WAVs.")
    print("No original file overwritten, playback started or network upload made.")
    print("Listen with stable headphone volume; blind only AFTER noting output list.")
    print("Studio mastering is not equivalent to recovering irretrievably lost speech.")


if __name__=="__main__":
    main()
