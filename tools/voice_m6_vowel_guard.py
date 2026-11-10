"""Offline periodic-voice protected envelope for physical M6 click study.

Detect voiced segments with a microphone-only lowband autocorrelation,
calibrated solely from pre-declared speech-free isolated physical phases.
No key/button identity, no audio capture, no device access, no playback.
Uses future samples and is not a real-time voice-activity detector.
"""
from __future__ import annotations

import numpy as np
from scipy import signal, ndimage

RATE = 48000
LOW_BAND = (90, 490)
DOWNSAMPLED_RATE = 2000
FRAME_SAMPLES = 100     # 50 ms, 2 kHz
HOP_SAMPLES = 10       # 5 ms, 2 kHz
VOICED_AUTOCORR_MIN = 0.60
CALIBRATION_QUANTILE = .95
VOICE_LEVEL_MULTIPLIER = 2.0
GAP_CLOSE_FRAMES = 13  # bridge <=60 ms within a voiced utterance
PRE_HOLD_FRAMES = 24   # preserve ~120 ms preceding a voiced estimate
POST_HOLD_FRAMES = 44 # preserve ~220 ms after a voiced estimate
EDGE_FADE_FRAMES = 10 # 50 ms gentle transition outside protected core


def phase_samples(phase: dict, t0: int, n: int) -> tuple[int, int]:
    lo = round((phase['start_monotonic_ns'] - t0) * RATE / 1e9)
    hi = round((phase['end_monotonic_ns'] - t0) * RATE / 1e9)
    if hi > n and 0 < hi-n <= round(.25*RATE):
        hi = n
    if not 0 <= lo < hi <= n:
        raise ValueError('Invalid private physical phase timestamps')
    return lo, hi


def voicing_protection(waveform: np.ndarray, manifest: dict):
    """Return float64 per-PCM-sample [0,1] VOICE PRESERVE factor.

    A value of 1 forces exact dry/original audio; 0 lets click suppressor
    operate fully. No speech-preservation claim for missed, unvoiced,
    very faint, or transient phonemes; this is a strong *vowel* gate only.
    """
    if waveform.ndim != 1 or len(waveform) < 3*RATE or not np.all(np.isfinite(waveform)):
        raise ValueError('Invalid mono waveform for voice protection')
    phase_map = {x['phase']:x for x in manifest['phases']}
    if not {'mouse_isolated','keyboard_isolated','both_holdout','voice_clicks_holdout'} <= set(phase_map):
        raise ValueError('Missing independent speech-free training stages')
    t0 = manifest['session_start_monotonic_ns']

    sos = signal.butter(4, LOW_BAND, btype='bandpass', fs=RATE, output='sos')
    bass = signal.sosfiltfilt(sos, waveform)
    down = signal.resample_poly(bass, 1, RATE//DOWNSAMPLED_RATE)
    frames = np.lib.stride_tricks.sliding_window_view(down, FRAME_SAMPLES)[::HOP_SAMPLES]
    centered = frames - np.mean(frames, axis=1, keepdims=True)
    weighted = centered * np.hanning(FRAME_SAMPLES)
    frame_level = np.sqrt(np.mean(weighted*weighted, axis=1))
    periodicity = np.zeros(len(frames), dtype=np.float64)
    for lag in range(5,24):  # f0 about 87..400Hz, allowing harmonic ratios
        earlier = weighted[:,:-lag]
        later = weighted[:,lag:]
        numerator = np.sum(earlier*later, axis=1)
        denominator = np.sqrt(np.sum(earlier**2,axis=1)*np.sum(later**2,axis=1)) + 1e-14
        periodicity = np.maximum(periodicity, numerator/denominator)

    hop_at_original_rate = RATE * HOP_SAMPLES // DOWNSAMPLED_RATE
    train = []
    for phase in ('mouse_isolated','keyboard_isolated'):
        a,b = phase_samples(phase_map[phase], t0, len(waveform))
        lo = (a + RATE//40) // hop_at_original_rate  # frame midpoint ~25ms
        hi = max(lo+1,(b - RATE//40)//hop_at_original_rate)
        hi = min(hi,len(frame_level))
        if not lo < hi:
            raise ValueError('Insufficient isolated calibration PCM')
        train.append(frame_level[lo:hi])
    baseline = float(np.quantile(np.concatenate(train),CALIBRATION_QUANTILE))
    threshold = baseline*VOICE_LEVEL_MULTIPLIER
    if threshold<=1e-8:
        raise ValueError('Nonmeasurable voice calibration background')
    raw_voiced = (periodicity>=VOICED_AUTOCORR_MIN)&(frame_level>=threshold)
    # Bridge short pitch detector dropouts, such as a consonant transition.
    # Keep the first and last frame safe by padding before closing.
    pad = GAP_CLOSE_FRAMES
    padded = np.pad(raw_voiced, (pad,pad), constant_values=False)
    joined = ndimage.binary_closing(padded,structure=np.ones(GAP_CLOSE_FRAMES,dtype=bool))[pad:-pad]
    # A future-inclusive interval around each voiced frame avoids resuming
    # attenuation on the decaying END of a prolonged vowel.
    indices = np.flatnonzero(joined)
    diff = np.zeros(len(frames)+1,dtype=np.int32)
    np.add.at(diff, np.maximum(0,indices-PRE_HOLD_FRAMES),1)
    np.add.at(diff, np.minimum(len(frames),indices+POST_HOLD_FRAMES+1),-1)
    locked_frames = np.cumsum(diff[:-1])>0
    outside_distance = ndimage.distance_transform_edt(~locked_frames)
    protected = np.where(
        locked_frames,1.0,
        np.maximum(0.0,1.0-outside_distance/EDGE_FADE_FRAMES)
    )
    at_sample=np.repeat(protected,hop_at_original_rate)
    if len(at_sample) < len(waveform):
        at_sample=np.pad(at_sample, (0,len(waveform)-len(at_sample)), mode='edge')
    protected_samples=at_sample[:len(waveform)]
    locked_samples=np.repeat(locked_frames,hop_at_original_rate)
    if len(locked_samples)<len(waveform):
        locked_samples=np.pad(locked_samples,(0,len(waveform)-len(locked_samples)),mode='edge')
    locked_samples=locked_samples[:len(waveform)]
    report={
        'speech_free_calibration_phases':['mouse_isolated','keyboard_isolated'],
        'voice_band_hz': list(LOW_BAND),
        'pitch_periodicity_min':VOICED_AUTOCORR_MIN,
        'calibration_level_quantile':CALIBRATION_QUANTILE,
        'voice_level_multiplier':VOICE_LEVEL_MULTIPLIER,
        'calibrated_minimum_frame_level':round(threshold,9),
        'pre_hold_ms':PRE_HOLD_FRAMES*5,
        'post_hold_ms':POST_HOLD_FRAMES*5,
        'outside_protection_fade_ms':EDGE_FADE_FRAMES*5,
        'locked_total_fraction':round(float(np.mean(locked_samples)),6),
        'speech_detection_not_truth':True,
        'real_time_use_certified':False,
    }
    return protected_samples,locked_samples,report


def integer_dry_wet(raw: np.ndarray, processed: np.ndarray, wet: np.ndarray) -> np.ndarray:
    """Mix integer PCM endpoints; identical raw values at wet=0 exactly."""
    if (raw.dtype!=np.dtype('int16') or processed.dtype!=np.dtype('int16')
            or raw.shape != processed.shape or wet.shape != raw.shape
            or not np.all(np.isfinite(wet)) or np.any((wet<0)|(wet>1))):
        raise ValueError('Invalid dry/wet PCM16 arguments')
    out=raw.astype(np.float64)+wet*(processed.astype(np.float64)-raw.astype(np.float64))
    # Convex interpolation of two valid PCM16 samples cannot clip.
    result=np.rint(out).astype('<i2')
    if not np.array_equal(result[wet==0],raw[wet==0]):
        raise RuntimeError('Speech dry gate changed a protected sample')
    return result
