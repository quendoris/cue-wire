#!/usr/bin/env python3
"""Offline RNNoise using system or explicitly provided librnnoise.so.
No model downloads, no audio devices, no resident background process.
Mono signed PCM16 48000-Hz input and output.
"""
import argparse
import array
import ctypes
import ctypes.util
import json
import math
import os
import sys
import wave
from pathlib import Path


def run(input_wav, output_wav):
    name=os.environ.get("CUEWIRE_BENCH_RNNOISE_LIBRARY")
    if name:
        if not Path(name).is_file():
            raise RuntimeError("Custom librnnoise file missing: "+name)
    else:
        name=ctypes.util.find_library("rnnoise")
    if not name:
        raise RuntimeError("librnnoise not found. Check: pacman -Q rnnoise")
    lib=ctypes.CDLL(name)
    lib.rnnoise_get_frame_size.argtypes=[]
    lib.rnnoise_get_frame_size.restype=ctypes.c_int
    lib.rnnoise_create.argtypes=[ctypes.c_void_p]
    lib.rnnoise_create.restype=ctypes.c_void_p
    lib.rnnoise_destroy.argtypes=[ctypes.c_void_p]
    lib.rnnoise_destroy.restype=None
    fp=ctypes.POINTER(ctypes.c_float)
    lib.rnnoise_process_frame.argtypes=[ctypes.c_void_p,fp,fp]
    lib.rnnoise_process_frame.restype=ctypes.c_float
    n=lib.rnnoise_get_frame_size()
    if n!=480:
        raise RuntimeError("Unexpected RNNoise frame size "+str(n))
    with wave.open(str(input_wav),"rb") as f:
        if (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getcomptype()) != (1,2,48000,"NONE"):
            raise RuntimeError("Input must be 48 kHz mono PCM16 WAV")
        count=f.getnframes()
        if not 1<=count<=48000*180:
            raise RuntimeError("Bad duration")
        pcm=f.readframes(count)
    samples=array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder!="little":
        samples.byteswap()
    if len(samples)!=count:
        raise RuntimeError("Truncated WAV")
    state=lib.rnnoise_create(None)
    if not state:
        raise RuntimeError("Could not initialize default RNNoise model")
    src=(ctypes.c_float*n)()
    dst=(ctypes.c_float*n)()
    result=array.array("h")
    try:
        for offset in range(0,count,n):
            size=min(n,count-offset)
            for i in range(n):
                src[i]=float(samples[offset+i]) if i<size else 0.0
            lib.rnnoise_process_frame(state,dst,src)
            for i in range(size):
                value=float(dst[i])
                if not math.isfinite(value):
                    raise RuntimeError("RNNoise output non-finite")
                result.append(max(-32768,min(32767,round(value))))
    finally:
        lib.rnnoise_destroy(state)
    if sys.byteorder!="little":
        result.byteswap()
    if output_wav.exists():
        raise RuntimeError("Refusing to overwrite output WAV")
    output_wav.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(output_wav),"wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(result.tobytes())
    print(json.dumps({"backend":"rnnoise_system_library",
         "library":name,"input_frames":count,
         "output_frames":len(result),"frame_size":n},sort_keys=True))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_wav",type=Path)
    p.add_argument("output_wav",type=Path)
    opts=p.parse_args()
    run(opts.input_wav,opts.output_wav)
