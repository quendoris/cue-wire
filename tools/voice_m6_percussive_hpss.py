"""M6 Physical-006 — event-only harmonic/percussive separation (offline).

The INPUT MIC is NEVER overwritten. A reference transient/noise waveform is
estimated from current microphone spectrogram; it is SUBTRACTED only under
already authenticated anonymous event masks and previous M6-005 voice
protection. Harmonic-percussive source separation is an imperfect heuristic:
consonants may be percussive too. This is NOT proof of clean speech.

No key/button identity, text, pointer movement, input devices, capture,
auto playback, audio routing or background processes. Noncausal STFT
median filtering requires future samples; NOT for live microphones.
"""
from __future__ import annotations

import numpy as np
from scipy import signal, ndimage

RATE=48000
STFT_WINDOW=2048
STFT_HOP=256
HARMONIC_TIME_FRAMES=19
PERCUSSIVE_FREQ_BINS=21
LOW_PROTECTED_HZ=650
FULL_SUBTRACTION_FROM_HZ=1450
HIGH_TAPER_START_HZ=11500
HIGH_TAPER_END_HZ=13500
GAINS={"B_006_percussive":0.65,"C_006_percussive_strong":1.0}


def harmonic_percussive_reference(mic:np.ndarray)->tuple[np.ndarray,dict]:
    """Estimate the current NONHARMONIC highband acoustic waveform.

    STFT median across time estimates persistent harmonic material.
    STFT median across frequencies estimates broadband percussive bursts.
    The quadratic soft-mask only selects a fraction of each spectral bin.
    Frequencies below 650Hz are EXACTLY zero in the STFT mask; above
    11500Hz smoothly taper off. Reconstruction is noncausal.
    """
    if (mic.ndim!=1 or len(mic)<RATE
            or not np.all(np.isfinite(mic))
            or float(np.max(np.abs(mic)))>=.98):
        raise ValueError("Expected safe finite 48kHz mono physical mic")
    frequency,_,spectrum=signal.stft(
        mic.astype(np.float32),fs=RATE,window="hann",
        nperseg=STFT_WINDOW,noverlap=STFT_WINDOW-STFT_HOP,
        boundary="zeros",padded=True)
    magnitude=np.abs(spectrum)
    harmonic=ndimage.median_filter(
        magnitude,size=(1,HARMONIC_TIME_FRAMES),mode="reflect")
    percussive=ndimage.median_filter(
        magnitude,size=(PERCUSSIVE_FREQ_BINS,1),mode="reflect")
    perc_energy=percussive*percussive
    harm_energy=harmonic*harmonic
    soft_mask=perc_energy/(perc_energy+harm_energy+1e-13)
    frequency_weight=np.clip(
        (frequency-LOW_PROTECTED_HZ)/
        (FULL_SUBTRACTION_FROM_HZ-LOW_PROTECTED_HZ),0,1)**2
    taper=np.clip(
        (HIGH_TAPER_END_HZ-frequency)/
        (HIGH_TAPER_END_HZ-HIGH_TAPER_START_HZ),0,1)
    frequency_weight*=taper
    spectral_correction=(
        spectrum*(soft_mask*frequency_weight[:,None]).astype(np.float32))
    _,reference=signal.istft(
        spectral_correction,fs=RATE,window="hann",
        nperseg=STFT_WINDOW,noverlap=STFT_WINDOW-STFT_HOP,
        input_onesided=True,boundary=True)
    if len(reference)<len(mic):
        raise RuntimeError("Incomplete synthesized percussive reference")
    reference=reference[:len(mic)].astype(np.float64)
    if not np.all(np.isfinite(reference)):
        raise ValueError("Nonfinite percussive noise estimate")
    return reference,{
        "type":"noncausal STFT harmonic-percussive median soft mask",
        "stft_samples":STFT_WINDOW,
        "stft_hop_samples":STFT_HOP,
        "harmonic_median_time_frames":HARMONIC_TIME_FRAMES,
        "percussive_median_frequency_bins":PERCUSSIVE_FREQ_BINS,
        "reference_frequency_low_guard_hz":LOW_PROTECTED_HZ,
        "reference_full_band_from_hz":FULL_SUBTRACTION_FROM_HZ,
        "reference_upper_taper_hz":[
            HIGH_TAPER_START_HZ,HIGH_TAPER_END_HZ],
        "event_timestamps_used_during_STFT_separation":False,
        "real_voice_or_click_sources_individually_identified":False,
        "key_or_button_identity_required":False,
        "future_samples_required":True,
    }


def add_event_limited_reference(
        raw:np.ndarray, baseline:np.ndarray, percussive:np.ndarray,
        event_mask:np.ndarray, voice_protection:np.ndarray
        )->dict[str,np.ndarray]:
    """Add a separately estimated inverse *only during voiced click events*.

    The previous user-preferred C_005 baseline remains completely
    unaltered outside BOTH event_mask and voiced protection. Nothing
    is changed in the original PCM16; all corrections are additive.
    """
    if (raw.ndim!=1 or raw.dtype!=np.dtype("<i2")
            or baseline.dtype!=np.dtype("<i2")
            or baseline.shape!=raw.shape
            or any(x.shape!=raw.shape for x in (
                percussive,event_mask,voice_protection))
            or not all(np.all(np.isfinite(x)) for x in (
                percussive,event_mask,voice_protection))
            or np.any((event_mask<0)|(event_mask>1))
            or np.any((voice_protection<0)|(voice_protection>1))):
        raise ValueError("Invalid signed-PCM16 and event/vowel protective mask")
    if not np.array_equal(raw[event_mask==0],baseline[event_mask==0]):
        raise ValueError("Physical-005 baseline changed audio outside events")
    wet=event_mask.astype(np.float64)*voice_protection
    correction=-(wet*percussive)*32768.
    candidates={}
    for name,gain in GAINS.items():
        values=np.rint(baseline.astype(np.float64)+gain*correction)
        if np.max(np.abs(values))>=.98*32768:
            raise ValueError("Potential PCM16 clip in additional anti-wave")
        out=values.astype("<i2")
        if (not np.array_equal(out[event_mask==0],baseline[event_mask==0])
                or not np.array_equal(out[voice_protection==0],
                                      baseline[voice_protection==0])
                or not np.array_equal(out[wet==0],baseline[wet==0])):
            raise RuntimeError("Modified a sample outside voiced event mask")
        candidates[name]=out
    return candidates


def lowband_delta_db(
        a:np.ndarray,b:np.ndarray,selected:np.ndarray
        )->float:
    """Auditable LOWBAND distortion proxy, NOT proof of speech naturalness."""
    if (a.shape!=b.shape or selected.shape!=a.shape
            or not np.any(selected)):
        raise ValueError("No protected coincident voice-event sample")
    filt=signal.butter(4,(90,650),fs=RATE,
                       btype="bandpass",output="sos")
    base=signal.sosfiltfilt(filt,a.astype(np.float64)/32768.)
    delta=signal.sosfiltfilt(
        filt,(b.astype(np.float64)-a.astype(np.float64))/32768.)
    orig_rms=float(np.sqrt(np.mean(base[selected]**2)))
    diff_rms=float(np.sqrt(np.mean(delta[selected]**2)))
    return float(20*np.log10(
        (diff_rms+1e-20)/(orig_rms+1e-20)))
