"""M6 Physical-007: residual keyboard attack + voiced-harmonic protected tail.

OFFLINE, NONCAUSAL: no input device access, PipeWire, recording, playback,
network, daemon, keyboard/button identity, or text/coordinates collection.

Operates on the ALREADY COMPENSATED and user-preferred M6-006 C waveform.
The correction is additive and only inside old M6 event masks intersected
with anonymously time-stamped KEYBOARD press/release windows. It estimates:
  (1) a short-window broadband percussive residual attack,
  (2) a long-window residual tail, suppressing bins near voice harmonics.
A detected pitch is only a *protective* cue, not a claim of perfect
human speech separation. Unvoiced consonants can still be modified.
"""
from __future__ import annotations

import numpy as np
from scipy import signal, ndimage

RATE=48000
SHORT_STFT=512
SHORT_HOP=128
LONG_STFT=2048
LONG_HOP=256
# Fixed experimental candidates. Parameter selection used SAME physical
# source as exploratory pilot: recorded metrics are descriptive only.
GAINS={
    "B_007_attack":(1.0,0.0),
    "C_007_attack_and_tail":(1.2,1.2),
}
LOWBAND_AUDIT_HZ=(90,650)
HIGHBAND_AUDIT_HZ=(1300,9000)


def pitch_protection(original:np.ndarray)->tuple[np.ndarray,np.ndarray,dict]:
    """Protect harmonic lines of confidently voiced 90-470 Hz material.

    50ms windows every 5ms; normalized autocorrelation at 2kHz.
    The 0.001 threshold is exploratory, not a validated VAD.
    """
    if (original.ndim!=1 or len(original)<RATE
            or not np.all(np.isfinite(original))):
        raise ValueError("Invalid physical voice reference")
    sos=signal.butter(4,(90,470),btype="bandpass",fs=RATE,output="sos")
    low=signal.sosfiltfilt(sos,original)
    down=signal.resample_poly(low,1,24)
    frames=np.lib.stride_tricks.sliding_window_view(
        down,100)[::10]
    centered=frames-frames.mean(axis=1,keepdims=True)
    weighted=centered*np.hanning(100)
    energy=np.sqrt(np.mean(weighted*weighted,axis=1))
    scores=[]
    for lag in range(7,23):
        left=weighted[:,:-lag]
        right=weighted[:,lag:]
        score=np.sum(left*right,axis=1)/np.sqrt(
            np.sum(left*left,axis=1)*np.sum(right*right,axis=1)+1e-24)
        scores.append(score)
    correlations=np.stack(scores,axis=1)
    best_lag=7+np.argmax(correlations,axis=1)
    certainty=np.max(correlations,axis=1)
    voiced=(certainty>.66)&(energy>.001)
    pitch=np.where(voiced,2000./best_lag,0.)
    return pitch,voiced,{
        "frame_ms":50,
        "hop_ms":5,
        "autocorrelation_min":.66,
        "energy_min":.001,
        "f0_range_hz":[2000/22,2000/7],
        "confident_periodic_frame_fraction":round(
            float(np.mean(voiced)),7),
        "not_validated_speech_activity_detection":True,
    }


def keyboard_event_gate(mask:np.ndarray,events:list[dict],
                        offsets:dict[str,int],t0:int)->np.ndarray:
    """Strict keyboard-CLASS timing only; never read actual key identity."""
    if (mask.ndim!=1 or not np.all(np.isfinite(mask))
            or np.any((mask<0)|(mask>1))):
        raise ValueError("Invalid event acoustic mask")
    allowed={"keyboard_press","keyboard_release",
             "mouse_press","mouse_release"}
    if set(offsets)!=allowed:
        raise ValueError("Unknown anonymous event timing classes")
    gate=np.zeros(len(mask),dtype=np.float32)
    for event in events:
        if (set(event)!={"kind","t_monotonic_ns"}
                or event["kind"] not in allowed
                or type(event["t_monotonic_ns"]) is not int):
            raise ValueError("Privacy-leaking or malformed input event")
        kind=event["kind"]
        if kind.startswith("mouse"):
            continue
        center=round((event["t_monotonic_ns"]-t0)*RATE/1e9
                     -offsets[kind]*RATE/1000.)
        start=max(0,center-round(.012*RATE))
        end=min(len(mask),center+round(.125*RATE))
        if end>start:
            gate[start:end]=1.
    return mask*gate


def spectral_references(
        residual:np.ndarray,original:np.ndarray,
        voice_protection:np.ndarray
        )->tuple[np.ndarray,np.ndarray,dict]:
    """Return attack and harmonic-protected tail anti-wave REFERENCES.

    STFT references have signed phase and are NOT summed in this method.
    The mixer is separate and must enforce original PCM16 outside events.
    """
    if (residual.ndim!=1 or residual.shape!=original.shape
            or residual.shape!=voice_protection.shape
            or len(residual)<RATE
            or any(not np.all(np.isfinite(x)) for x in (
                residual,original,voice_protection))
            or np.any((voice_protection<0)|(voice_protection>1))
            or max(np.max(np.abs(residual)),
                   np.max(np.abs(original)))>=.98):
        raise ValueError("Invalid finite 48kHz physical spectral inputs")
    n=len(residual)
    # Short window: track acoustic impact/attack rather than all vowels.
    f,_,spectrum=signal.stft(
        residual.astype(np.float32),fs=RATE,window="hann",
        nperseg=SHORT_STFT,noverlap=SHORT_STFT-SHORT_HOP,
        boundary="zeros",padded=True)
    amplitude=np.abs(spectrum)
    harmonic=ndimage.median_filter(
        amplitude,size=(1,25),mode="reflect")
    percussive=ndimage.median_filter(
        amplitude,size=(19,1),mode="reflect")
    frac=percussive**2/(percussive**2+harmonic**2+1e-13)
    weight=np.clip((f-1000)/1000,0,1)**2
    weight*=np.clip((14000-f)/2000,0,1)
    _,short=signal.istft(
        spectrum*(frac*weight[:,None]).astype(np.float32),
        fs=RATE,window="hann",nperseg=SHORT_STFT,
        noverlap=SHORT_STFT-SHORT_HOP,
        input_onesided=True,boundary=True)

    # Longer window: address residual ringing. Preserve confidently
    # identified low/mid voiced HARMONIC bins near multiples of f0.
    f2,seconds,spectrum2=signal.stft(
        residual.astype(np.float32),fs=RATE,window="hann",
        nperseg=LONG_STFT,noverlap=LONG_STFT-LONG_HOP,
        boundary="zeros",padded=True)
    amp=np.abs(spectrum2)
    harmonics=ndimage.median_filter(
        amp,size=(1,27),mode="reflect")
    percussives=ndimage.median_filter(
        amp,size=(21,1),mode="reflect")
    ratio=percussives**2/(percussives**2+harmonics**2+1e-13)
    f0,confident,pitch_report=pitch_protection(original)
    pitch_ids=np.clip(
        np.rint((seconds-.025)/.005).astype(int),0,len(f0)-1)
    pitches=f0[pitch_ids]
    confident_frames=confident[pitch_ids]
    sample_ids=np.clip(
        np.rint(seconds*RATE).astype(int),0,n-1)
    recognized_voice=voice_protection[sample_ids]>.5
    safe_pitch=np.where(confident_frames,np.maximum(pitches,1.),200.)
    grid=f2[:,None]
    multiple=np.rint(grid/safe_pitch[None,:])
    separation=np.abs(grid-multiple*safe_pitch[None,:])
    # 35Hz protective notch around voice harmonics (soft notches).
    interharmonic=1.-np.exp(-.5*(separation/35.)**2)
    interharmonic=np.where(
        (recognized_voice&confident_frames)[None,:],
        interharmonic,1.)
    # If voice is detected but reliable pitch is missing, leave lower
    # frequencies (up to 4200Hz) untouched by long-tail processing.
    unknown_pitch=recognized_voice&(~confident_frames)
    safe_bins=np.where(
        (f2[:,None]<4200)&unknown_pitch[None,:],
        0.,interharmonic)
    band=np.clip((f2-900)/950,0,1)**2
    band*=np.clip((13000-f2)/2000,0,1)
    long_soft=(.35+.30*ratio)*safe_bins*band[:,None]
    _,long=signal.istft(
        spectrum2*long_soft.astype(np.float32),fs=RATE,
        window="hann",nperseg=LONG_STFT,
        noverlap=LONG_STFT-LONG_HOP,
        input_onesided=True,boundary=True)
    if len(short)<n or len(long)<n:
        raise RuntimeError("STFT reconstruction does not cover physical mic")
    s=short[:n].astype(np.float64)
    l=long[:n].astype(np.float64)
    if not np.all(np.isfinite(s)) or not np.all(np.isfinite(l)):
        raise ValueError("Nonfinite attack or tail reconstruction")
    return s,l,{
        "short_stft_size":SHORT_STFT,
        "short_stft_hop":SHORT_HOP,
        "long_stft_size":LONG_STFT,
        "long_stft_hop":LONG_HOP,
        "relative_pitch_guard_width_hz":35,
        "unknown_pitch_protect_below_hz":4200,
        "lowband_is_fully_excluded_from_spectral_mask":True,
        "harmonic_protection":pitch_report,
        "voice_consonant_quality_not_proven":True,
        "future_samples_required":True,
        "individual_keyboard_key_or_button_identity_read":False,
    }


def add_keyboard_only_corrections(
        baseline_pcm:np.ndarray,gate:np.ndarray,
        attack:np.ndarray,tail:np.ndarray
        )->dict[str,np.ndarray]:
    """Generate additive anti-wave tracks, with exact C006 off-event PCM."""
    if (baseline_pcm.ndim!=1
            or baseline_pcm.dtype!=np.dtype("<i2")
            or any(v.shape!=baseline_pcm.shape for v in (
                gate,attack,tail))
            or not all(np.all(np.isfinite(v)) for v in (
                gate,attack,tail))
            or np.any((gate<0)|(gate>1))):
        raise ValueError("Bad signed PCM16, acoustic mask or anti-wave")
    out={}
    for label,(a,b) in GAINS.items():
        corrected=baseline_pcm.astype(np.float64)/32768.
        corrected-=gate.astype(np.float64)*(a*attack+b*tail)
        if np.max(np.abs(corrected))>=.98:
            raise ValueError("Spectral click candidate could clip")
        pcm=np.rint(corrected*32768.).astype("<i2")
        if not np.array_equal(pcm[gate==0],baseline_pcm[gate==0]):
            raise RuntimeError("Physical C006 changed outside keyboard event")
        out[label]=pcm
    return out


def band_rms(pcm:np.ndarray,band:tuple[int,int])->float:
    filt=signal.butter(4,band,btype="bandpass",fs=RATE,output="sos")
    x=signal.sosfiltfilt(filt,pcm.astype(np.float64)/32768.)
    return float(np.sqrt(np.mean(x*x)))


def protected_lowband_error_db(
        baseline:np.ndarray,processed:np.ndarray,
        voiced_event_samples:np.ndarray)->float:
    """Difference RMS vs C006 lowband in voice+keyboard; not speech truth."""
    if (baseline.shape!=processed.shape
            or voiced_event_samples.shape!=baseline.shape
            or not np.any(voiced_event_samples)):
        raise ValueError("Insufficient protected voiced keyboard overlap")
    sos=signal.butter(4,LOWBAND_AUDIT_HZ,btype="bandpass",
                      fs=RATE,output="sos")
    original=signal.sosfiltfilt(
        sos,baseline.astype(np.float64)/32768.)
    delta=signal.sosfiltfilt(
        sos,(processed.astype(np.float64)-
             baseline.astype(np.float64))/32768.)
    a=np.sqrt(np.mean(original[voiced_event_samples]**2))
    b=np.sqrt(np.mean(delta[voiced_event_samples]**2))
    return float(20*np.log10((b+1e-20)/(a+1e-20)))
