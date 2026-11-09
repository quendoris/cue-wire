#!/usr/bin/env python3
"""CueWire M5 external baseline: actual SpeexDSP 1.2.1 native AEC, offline.

One *sequentially adaptive* SpeexDSP echo canceler per existing physical
speaker-OFF / ON / OFF 0.15 and 0.30 capture. Compare the same fixed
10..16s physical mic heldout reference window and OFF noise intervals
as the immutable CueWire M5 echo-floor report. This is NOT a blind
acceptance benchmark, and NOT SpeexDSP denoise or preprocess mode.

Hard requirements: existing local libspeexdsp.so.1, already pinned
exact original WAV/manifest SHA256s; no network, no module loading,
no audio devices, no playback, no PipeWire route/volume changes,
no audio output files, no background processes, no autostart.
"""
from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time

import numpy as np

from voice_m5_aec import AUDIT, RATE, db, digest, read_wav, rms
from voice_m5_frozen_heldout import erle_db, indices
from voice_m5_echo_floor import (
    linear_spectral_proxy, background_analysis,
    combined_band_proxy, EVAL_S,
)
from voice_m5_playback_coherence import (
    checked_take, LOW_MANIFEST_SHA, HIGH_MANIFEST_SHA,
    LOW_MIC_SHA, HIGH_MIC_SHA, REFERENCE_SHA, SOURCE_MANIFEST_SHA,
)

FRAME_MS = 10
FRAME_SAMPLES = RATE * FRAME_MS // 1000  # 480 mono samples
TAIL_MS = 120
TAIL_SAMPLES = RATE * TAIL_MS // 1000  # 5760 mono samples
PRE_PEAK_SAMPLES = RATE // 100  # predeclared 10ms, like CueWire
# Original, already audited 0.15/0.30 capture alignment results, not tuned
# using the following evaluation interval or on Speex outputs.
LAG_015_SAMPLES = 386952  # 8061.50000ms
LAG_030_SAMPLES = 387967  # 8082.64583ms
SPEEX_ECHO_SET_SAMPLING_RATE = 24
SPEEX_ECHO_GET_SAMPLING_RATE = 25
SPEEX_ECHO_GET_FRAME_SIZE = 3


def load_speex():
    """Resolve only installed native DSP library; no installation or subprocess."""
    path = ctypes.util.find_library("speexdsp")
    if not path:
        raise RuntimeError(
            "libspeexdsp.so.1 missing; run pacman -Q speexdsp (read-only). "
            "No auto-install or fallback to a different AEC.")
    lib = C.CDLL(path)
    lib.speex_echo_state_init.argtypes = [C.c_int, C.c_int]
    lib.speex_echo_state_init.restype = C.c_void_p
    lib.speex_echo_ctl.argtypes = [C.c_void_p, C.c_int, C.c_void_p]
    lib.speex_echo_ctl.restype = C.c_int
    lib.speex_echo_cancellation.argtypes = [
        C.c_void_p, C.POINTER(C.c_int16),
        C.POINTER(C.c_int16), C.POINTER(C.c_int16)]
    lib.speex_echo_cancellation.restype = None
    lib.speex_echo_state_destroy.argtypes = [C.c_void_p]
    lib.speex_echo_state_destroy.restype = None
    return lib, path


def exact_pcm16(signal: np.ndarray) -> np.ndarray:
    """Bit-exact PCM16 round trip from voice_m5_aec.read_wav float values."""
    if signal.ndim != 1 or not np.all(np.isfinite(signal)):
        raise ValueError("Require finite mono samples")
    if float(np.max(np.abs(signal))) >= 1.0:
        raise ValueError("Refuse unsafe fullscale samples for baseline")
    return np.ascontiguousarray(
        np.rint(np.asarray(signal, dtype=np.float64)*32768).astype("<i2"))


def render_reference_on_mic_timeline(reference: np.ndarray,
                                     n_mic: int, lag: int,
                                     per_stream_volume: float) -> np.ndarray:
    """Far-end in recorder sample clock; only trusted prior time offset.

    AEC receives the DIGITAL reference scaled by commanded pw-play
    PER-STREAM volume. The actual sink gain/physical room response
    is unknown and learned by adaptive Speex. Do not pretend the
    digital source reference is the measured loudspeaker output.
    """
    if (reference.ndim != 1 or len(reference) != 20*RATE or
            n_mic < 35*RATE or
            not 0 < per_stream_volume <= 1. or
            not PRE_PEAK_SAMPLES <= lag or
            lag-PRE_PEAK_SAMPLES+len(reference) > n_mic):
        raise ValueError("Invalid pinned reference, length or alignment")
    output = np.zeros(n_mic, dtype=np.float64)
    offset = lag-PRE_PEAK_SAMPLES
    output[offset:offset+len(reference)] = reference*per_stream_volume
    if not np.all(np.isfinite(output)) or np.max(np.abs(output)) >= 1.:
        raise ValueError("Reference amplitude invalid")
    return exact_pcm16(output)


def run_speex_native(mic_pcm: np.ndarray, far_pcm: np.ndarray,
                     library=None, library_name: str | None = None) -> tuple[np.ndarray, dict]:
    """Process every 10ms frame in temporal order with one adaptive state.

    This is the DIRECT synchronous speex_echo_cancellation API.
    Never uses deprecated playback/capture two-frame internal queue.
    Speex adaptivity continues THROUGH the 10-16s evaluation and
    must be labeled separately from frozen CueWire FIR results.
    """
    if (mic_pcm.ndim != 1 or far_pcm.ndim != 1 or
            len(mic_pcm) != len(far_pcm) or
            not len(mic_pcm) or
            mic_pcm.dtype != np.dtype("<i2") or
            far_pcm.dtype != np.dtype("<i2") or
            not mic_pcm.flags.c_contiguous or
            not far_pcm.flags.c_contiguous):
        raise ValueError("Speex requires equal-length contiguous mono PCM16 arrays")
    lib, library_path = load_speex() if library is None else (library, library_name or "injected-library")
    state = lib.speex_echo_state_init(FRAME_SAMPLES, TAIL_SAMPLES)
    if not state:
        raise RuntimeError("Native Speex echo state initialization failed")
    out = np.zeros(len(mic_pcm), dtype=np.int16)
    cptr = C.POINTER(C.c_int16)
    total_frames = (len(mic_pcm)+FRAME_SAMPLES-1)//FRAME_SAMPLES
    start_ns = time.perf_counter_ns()
    try:
        samplerate = C.c_int(RATE)
        if lib.speex_echo_ctl(state, SPEEX_ECHO_SET_SAMPLING_RATE,
                              C.byref(samplerate)) != 0:
            raise RuntimeError("Cannot set native Speex sample rate 48kHz")
        checkrate = C.c_int(-1)
        if lib.speex_echo_ctl(state, SPEEX_ECHO_GET_SAMPLING_RATE,
                              C.byref(checkrate)) != 0 or checkrate.value != RATE:
            raise RuntimeError("Native Speex sample-rate verification failed")
        checksize = C.c_int(-1)
        if lib.speex_echo_ctl(state, SPEEX_ECHO_GET_FRAME_SIZE,
                              C.byref(checksize)) != 0 or checksize.value != FRAME_SAMPLES:
            raise RuntimeError("Native Speex 10ms frame-size verification failed")
        # Only the last frame is zero-padded; resulting sample count is
        # EXACLY the original recording length, so no silent tail leaks.
        mic_buffer = np.zeros(FRAME_SAMPLES,dtype=np.int16)
        ref_buffer = np.zeros(FRAME_SAMPLES,dtype=np.int16)
        out_buffer = np.zeros(FRAME_SAMPLES,dtype=np.int16)
        for i in range(total_frames):
            a,b=i*FRAME_SAMPLES,min((i+1)*FRAME_SAMPLES,len(mic_pcm))
            n=b-a
            mic_buffer.fill(0)
            ref_buffer.fill(0)
            mic_buffer[:n] = mic_pcm[a:b]
            ref_buffer[:n] = far_pcm[a:b]
            lib.speex_echo_cancellation(
                state, mic_buffer.ctypes.data_as(cptr),
                ref_buffer.ctypes.data_as(cptr),
                out_buffer.ctypes.data_as(cptr))
            out[a:b]=out_buffer[:n]
        elapsed_s=(time.perf_counter_ns()-start_ns)/1e9
    finally:
        lib.speex_echo_state_destroy(state)
    return out.astype(np.float64)/32768., {
        "implementation": "native SpeexDSP speex_echo_cancellation, no preprocessor",
        "loaded_library": library_path,
        "sampling_rate_hz": RATE,
        "frame_ms": FRAME_MS,
        "frame_samples": FRAME_SAMPLES,
        "filter_tail_ms": TAIL_MS,
        "filter_tail_samples": TAIL_SAMPLES,
        "time_order": "continuous_input_frame_order_including_speaker_OFF",
        "adaptation": "continues_during_heldout_standard_speex_streaming",
        "aec_extra_postprocessing": False,
        "n_frames": total_frames,
        "processing_wall_seconds": round(elapsed_s, 6),
        "signal_seconds": round(len(mic_pcm)/RATE, 6),
        "offline_real_time_factor": round(elapsed_s/(len(mic_pcm)/RATE), 6),
        "frame_duration_is_not_measured_total_end_to_end_latency": True,
    }


def one_take(reference: np.ndarray, mic: np.ndarray, manifest: dict,
             lag: int, speex_lib, speex_lib_name: str) -> dict:
    vol = float(manifest["speaker_play_volume"])
    far = render_reference_on_mic_timeline(reference, len(mic), lag, vol)
    mic_pcm = exact_pcm16(mic)
    residual, impl = run_speex_native(mic_pcm, far, library=speex_lib,
                                      library_name=speex_lib_name)
    a,b=indices(EVAL_S,lag,len(mic))
    x=reference[round(EVAL_S[0]*RATE):round(EVAL_S[1]*RATE)]
    y=mic[a:b]
    r=residual[a:b]
    spectrum=linear_spectral_proxy(x,y,r)
    spectrum["full_band_erle_db"]=round(erle_db(y,r),5)
    # Baseline diagnostic OFF-period impact: unnecessary attenuation
    # of actual background indicates that native implementation is
    # changing speaker-OFF microphone despite zero far reference.
    unrelated=reference[10*RATE:15*RATE]
    off_phases={}
    for label,start,end in (("before",2,7),("after",30,35)):
        pp=linear_spectral_proxy(
            unrelated,mic[start*RATE:end*RATE],
            residual[start*RATE:end*RATE])
        pp["physical_mic_time_s"]=[start,end]
        off_phases[label]=pp
    fake_take={"speex_streaming_aec":spectrum}
    proxy=combined_band_proxy(fake_take,"speex_streaming_aec")
    return {
        "per_stream_volume":vol,
        "fixed_original_lag_samples":lag,
        "fixed_original_lag_ms":round(1000*lag/RATE,5),
        "ref_injected_to_mic_offset_samples":lag-PRE_PEAK_SAMPLES,
        "pre_peak_reference_shift_ms":PRE_PEAK_SAMPLES*1000/RATE,
        "digital_render_gain_nominal":vol,
        "native_processor":impl,
        "heldout_reference_time_s":list(EVAL_S),
        "heldout_speaker_only":spectrum,
        "speech_band_500_4000hz_proxy":proxy,
        "raw_physical_speaker_off_floor":background_analysis(reference,mic),
        "speaker_off_speex_impact":off_phases,
        "voice_preservation_unknown":True,
    }


def analyze(low_dir: Path, high_dir: Path, out_dir: Path,
            speex_loader=load_speex) -> dict:
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError("Output numeric report exists; never overwrite")
    low_dir=low_dir.expanduser().resolve(strict=True)
    high_dir=high_dir.expanduser().resolve(strict=True)
    audit=AUDIT.resolve(strict=True)
    if low_dir==high_dir or audit not in low_dir.parents or audit not in high_dir.parents:
        raise ValueError("Need two distinct private reference-pinned M5 captures")
    parent=out_dir.expanduser().parent.resolve(strict=True)
    if parent!=high_dir.parent:
        raise ValueError("Output must be new sibling of recorded takes")
    ml,hl,yl=checked_take(low_dir,LOW_MANIFEST_SHA,LOW_MIC_SHA,.15)
    mh,hh,yh=checked_take(high_dir,HIGH_MANIFEST_SHA,HIGH_MIC_SHA,.30)
    for name in ("source_mic_name","speaker_sink_name","reference_source_capture"):
        if ml.get(name)!=mh.get(name):
            raise ValueError("Source mic/sink/digital reference mismatch")
    source_dir=Path(ml["reference_source_capture"]).expanduser().resolve(strict=True)
    if audit not in source_dir.parents:
        raise ValueError("Reference source must be private local .audit")
    source_ref=source_dir/"reference-sent.wav"
    source_mf=source_dir/"manifest.json"
    if digest(source_ref)!=REFERENCE_SHA or digest(source_mf)!=SOURCE_MANIFEST_SHA:
        raise ValueError("Digital reference or manifest SHA256 mismatch")
    reference=read_wav(source_ref)
    if len(reference)!=20*RATE:
        raise ValueError("Digital reference must be exactly 20s at 48kHz")
    native, library_path=speex_loader()
    low=one_take(reference,yl,ml,LAG_015_SAMPLES,native,library_path)
    high=one_take(reference,yh,mh,LAG_030_SAMPLES,native,library_path)
    inputs={
        "volume015_manifest":(low_dir/"manifest.json",hl["manifest"]),
        "volume015_mic":(low_dir/"microphone-aba-raw.wav",hl["physical_mic_raw"]),
        "volume030_manifest":(high_dir/"manifest.json",hh["manifest"]),
        "volume030_mic":(high_dir/"microphone-aba-raw.wav",hh["physical_mic_raw"]),
        "digital_reference":(source_ref,REFERENCE_SHA),
        "digital_reference_manifest":(source_mf,SOURCE_MANIFEST_SHA),
    }
    if any(digest(p)!=sha for p,sha in inputs.values()):
        raise RuntimeError("Immutable physical baseline inputs changed during analysis")
    report={
        "schema":"cuewire-M5-SpeexDSP-native-AEC-only-benchmark-v1",
        "status":"retrospective_external_adaptive_baseline_not_production_accepted",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "library_resolved":library_path,
        "source_sha256":{k:v for k,(_,v) in inputs.items()},
        "fixed_lags_from_preexisting_M5_reference_coherence":
            {"volume015":LAG_015_SAMPLES,"volume030":LAG_030_SAMPLES},
        "volume_015":low,
        "volume_030":high,
        "heldout_not_used_for_parameter_selection":True,
        "speex_process_adapts_during_heldout":True,
        "no_preprocessor_noise_suppression_or_AGC":True,
        "no_processed_audio_exported":True,
        "voice_preservation_validated":False,
        "real_time_end_to_end_latency_measured":False,
        "original_prospective_0_67881_db_gate_remains_failed":True,
        "warnings":[
            "CANCELER IS ADAPTIVE through holdout; CueWire FIR is frozen. Label comparison ASYMMETRIC.",
            "Alignment and nominal pre-peak shift were previously selected, not optimized on this Speex result.",
            "The reference is scaled by per-stream pw-play nominal volume, not a measured loudspeaker PCM loopback.",
            "The physical analog sink level/latency and mic hardware DSP are not captured.",
            "No doubletalk voice preservation tests were performed with this external baseline.",
            "Native Speex audio processing is 16-bit fixed-point; a quantization difference vs float CueWire exists.",
            "No raw audio was exported; all unprocessed/private WAV inputs are unchanged.",
            "This diagnostic reuses previously inspected music-only data; it is NOT new prospective acceptance.",
            "RTF excludes library-load time, disk IO and end-to-end scheduling or audio device latency.",
        ],
    }
    dest=parent/out_dir.name
    dest.mkdir(mode=0o700)
    file=dest/"speex-aec-only.json"
    with file.open("x",encoding="utf-8") as stream:
        json.dump(report,stream,sort_keys=True,indent=2,ensure_ascii=False)
        stream.write("\n")
    with (dest/"SHA256SUMS").open("x",encoding="utf-8") as stream:
        stream.write(digest(file)+"  speex-aec-only.json\n")
    print("SpeexDSP native AEC-only baseline: physical 0.15/0.30 OFF/ON/OFF")
    for name,take in (("0.15",low),("0.30",high)):
        print(" ",name,"fullband ERLE",take["heldout_speaker_only"]["full_band_erle_db"],
              "dB 500..4000Hz linked proxy suppression",
              take["speech_band_500_4000hz_proxy"]["linked_proxy_suppression_db"],
              "dB runtime RTF",take["native_processor"]["offline_real_time_factor"])
    print("No WAV files, no hardware access, no production approval, old FAIL unchanged")
    print("Report:",file)
    return report


def main()->None:
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--low-capture",required=True,type=Path)
    p.add_argument("--high-capture",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    a=p.parse_args()
    try:
        analyze(a.low_capture,a.high_capture,a.out)
    except (OSError,RuntimeError,ValueError,KeyError) as e:
        p.exit(1,"ERROR: "+str(e)+"\n")


if __name__=="__main__":
    main()
