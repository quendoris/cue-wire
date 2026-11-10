#!/usr/bin/env python3
"""M6: OFFLINE event-anchored anti-noise prototype, NOT a live microphone filter.

Separate, inspectable causal FIR music estimate and event-conditioned acoustic
click templates. Both compensation tracks are generated from independent
references; the original physical mic is never an input to the estimator
during speech. A gain updater may use mic ONLY on explicitly marked
speech-free, click-free windows; it freezes during doubletalk.

No microphone/audio device opens, no event device reads, no key capture,
network, PipeWire modifications, background services, or auto-playback.
Only the explicit operator's private .audit directory may contain WAVs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import wave

import numpy as np
from scipy.signal import fftconvolve

RATE = 48000
ALLOWED_EVENTS = frozenset({
    "mouse_press", "mouse_release", "keyboard_press", "keyboard_release"
})
PRE = 240            # 5ms BEFORE input event (physical click may lead input)
POST = 3600          # 75ms after event
PEAK_LIMIT = 0.98
AUDIO = ("mic", "farend")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def assert_private(path: Path, audit: Path, *, existing: bool) -> Path:
    path = path.expanduser()
    if path.is_symlink():
        raise ValueError("Refuse symlink at private input or output")
    if existing:
        path = path.resolve(strict=True)
        if not path.is_file():
            raise ValueError("Expected existing file: " + str(path))
    else:
        if path.exists():
            raise FileExistsError("Output already exists: " + str(path))
        path = path.parent.resolve(strict=True) / path.name
    if audit not in path.parents:
        raise ValueError("All recordings and reports must stay within .audit")
    return path


def pcm16_read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate(),
                w.getcomptype()) != (1, 2, RATE, "NONE"):
            raise ValueError("Expected 48kHz mono uncompressed PCM16 WAV: "
                             + str(path))
        n = w.getnframes()
        raw = w.readframes(n)
    if len(raw) != 2 * n:
        raise ValueError("Truncated PCM16 WAV")
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.


def pcm16_write(path: Path, data: np.ndarray) -> None:
    if (data.ndim != 1 or not len(data) or
            not np.all(np.isfinite(data)) or
            float(np.max(np.abs(data))) > PEAK_LIMIT):
        raise ValueError("Unsafe output peak, nonfinite, empty or not mono")
    pcm = np.rint(data * 32768.).astype("<i2")
    with path.open("xb") as handle:
        with wave.open(handle, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm.tobytes())


def check_signal(mic: np.ndarray, reference: np.ndarray) -> None:
    if (mic.ndim != 1 or reference.ndim != 1 or len(mic) != len(reference)
            or len(mic) < 2 * RATE
            or not np.all(np.isfinite(mic))
            or not np.all(np.isfinite(reference))
            or float(np.max(np.abs(mic))) >= 1
            or float(np.max(np.abs(reference))) >= 1):
        raise ValueError("Need finite, synchronized 48kHz mono streams >=2s")


def windows_from_strings(spec: list[str], size: int) -> list[tuple[int, int]]:
    result = []
    for token in spec:
        try:
            a, b = (round(float(v) * RATE) for v in token.split(":"))
        except (ValueError, TypeError):
            raise ValueError("Window must be START:END in seconds") from None
        if not 0 <= a < b <= size or b - a < RATE // 20:
            raise ValueError("Invalid / too short window: " + token)
        result.append((a, b))
    ordered = sorted(result)
    if any(x[1] > y[0] for x, y in zip(ordered, ordered[1:])):
        raise ValueError("Overlapping annotated windows")
    return ordered


def read_events(path: Path, size: int) -> list[dict]:
    """No device ID, actual key, text, password or mouse coordinates accepted.

    All sample positions are on the MICROPHONE PCM clock, after calibration.
    Unanchored wall / monotonic timestamps are intentionally rejected.
    """
    events = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Bad event JSONL line {line_no}: {exc}") from exc
        if (not isinstance(e, dict) or set(e) != {"kind", "sample"}
                or e.get("kind") not in ALLOWED_EVENTS
                or type(e.get("sample")) is not int
                or not PRE <= e["sample"] < size - POST):
            raise ValueError(f"Invalid/private/unsynchronized event at line {line_no}")
        events.append({"kind": e["kind"], "sample": e["sample"]})
    if len(events) > 100_000:
        raise ValueError("Unreasonably many event markers")
    return sorted(events, key=lambda e: e["sample"])


def events_outside_windows(events: list[dict],
                           windows: list[tuple[int, int]], pad: int = POST) -> None:
    if any(a - pad <= ev["sample"] < b + pad
           for a, b in windows for ev in events):
        raise ValueError("Events overlap claimed music-only calibration/update")


def train_music_fir(mic: np.ndarray, ref: np.ndarray,
                    windows: list[tuple[int, int]], taps: int = 128,
                    stride: int = 8, ridge: float = 1e-4) -> tuple[np.ndarray, dict]:
    """Small regularized causal FIR, trained ONLY on explicit speaker-only data.

    Reference must be sample-aligned to mic BEFORE entry. No unsupervised lag
    guessing or fitting on near-end speech; adaptation is frozen elsewhere.
    """
    check_signal(mic, ref)
    if (not windows or not 8 <= taps <= 256
            or not 1 <= stride <= 32 or not 0 < ridge <= 0.1):
        raise ValueError("Unsafe music model size / calibration parameters")
    gram = np.zeros((taps, taps), dtype=np.float64)
    rhs = np.zeros(taps, dtype=np.float64)
    # Strided feature view, not a large N x taps dense materialization.
    features = np.lib.stride_tricks.sliding_window_view(
        np.pad(ref, (taps - 1, 0)), taps)
    for a, b in windows:
        if not 0 <= a < b <= len(mic) or b - a < RATE // 20:
            raise ValueError("Bad music calibration interval")
        xx = np.ascontiguousarray(features[a:b:stride, ::-1])
        yy = mic[a:b:stride]
        gram += xx.T @ xx
        rhs += xx.T @ yy
    energy = float(np.trace(gram)) / taps
    if not np.isfinite(energy) or energy <= 1e-6:
        raise ValueError("Speaker-only calibration reference is silent")
    coeff = np.linalg.solve(gram + ridge * energy * np.eye(taps), rhs)
    if not np.all(np.isfinite(coeff)) or float(np.max(np.abs(coeff))) > 8:
        raise ValueError("Ill-conditioned/unsafe music FIR")
    fitted = predict_music(ref, coeff)
    cal_energy = sum(float(np.dot(mic[a:b], mic[a:b])) for a, b in windows)
    cal_error = sum(float(np.dot(
        mic[a:b]-fitted[a:b], mic[a:b]-fitted[a:b]))
        for a, b in windows)
    fit_improvement_db = 10. * math.log10(
        max(cal_energy, 1e-30) / max(cal_error, 1e-30))
    return coeff, {
        "speaker_only_training_residual_reduction_db":
            round(fit_improvement_db, 5),
        "not_a_doubletalk_or_perceptual_quality_metric": True,
        "fir_taps": taps, "training_stride_samples": stride,
        "ridge_fraction": ridge, "training_windows_samples": windows,
        "training_frames_subsampled":
            sum(len(range(a, b, stride)) for a, b in windows),
        "reference_is_explicit_same_sample_clock": True,
        "causal_filter": True,
    }


def delay_reference(reference: np.ndarray, lag_samples: int) -> np.ndarray:
    """Apply known nonnegative acoustic delay BEFORE the short causal FIR.

    For example, a far-end reference intentionally advanced by 10ms
    relative to the physical echo requires lag_samples=480 at 48kHz.
    No fitting on the user's speech and no hidden lag search.
    """
    if (reference.ndim != 1 or type(lag_samples) is not int
            or not 0 <= lag_samples <= RATE // 2):
        raise ValueError("Need a measured nonnegative <=500ms reference lag")
    result = np.zeros_like(reference)
    if lag_samples == 0:
        result[:] = reference
    else:
        result[lag_samples:] = reference[:-lag_samples]
    return result


def predict_music(ref: np.ndarray, fir: np.ndarray) -> np.ndarray:
    if (ref.ndim != 1 or fir.ndim != 1 or
            len(fir) < 1 or not np.all(np.isfinite(fir))):
        raise ValueError("Invalid music FIR")
    return fftconvolve(ref, fir, mode="full")[:len(ref)].astype(np.float64)


def causal_gain_curve(mic: np.ndarray, prediction: np.ndarray,
                      speech_free: list[tuple[int, int]],
                      events: list[dict], window_frames: int = 4800,
                      inertia: float = .25) -> tuple[np.ndarray, dict]:
    """Adapt amplitude only AFTER completing a certified speech-free window.

    No future speech, no near-end optimization in doubletalk. A held
    coefficient carries forward when the music-only mask is absent.
    """
    if (len(mic) != len(prediction) or not 0 < inertia <= 1 or
            window_frames < 480):
        raise ValueError("Invalid gain configuration")
    events_outside_windows(events, speech_free)
    gains = np.ones(len(mic), dtype=np.float64)
    current = 1.
    updates = 0
    for start in range(0, len(mic), window_frames):
        end = min(len(mic), start + window_frames)
        gains[start:end] = current
        if end - start < window_frames:
            continue
        if not any(a <= start and end <= b for a, b in speech_free):
            continue
        p = prediction[start:end]
        yy = mic[start:end]
        den = float(np.dot(p, p))
        if den < .00001:
            continue
        estimate = float(np.dot(p, yy)) / den
        if not np.isfinite(estimate) or estimate < 0:
            continue
        estimate = float(np.clip(estimate, 0.35, 2.0))
        current = (1. - inertia) * current + inertia * estimate
        updates += 1
    return gains, {
        "update_count": updates, "window_frames": window_frames,
        "inertia": inertia, "speech_free_windows_samples": speech_free,
        "doubletalk_updates_allowed": False, "click_windows_excluded": True,
        "min_gain": round(float(np.min(gains)), 6),
        "max_gain": round(float(np.max(gains)), 6),
    }


def train_click_templates(mic: np.ndarray, reference: np.ndarray,
                          events: list[dict],
                          pre: int = PRE, post: int = POST
                          ) -> tuple[dict[str, np.ndarray], dict]:
    """Median per-kind click shape from isolated, speech-free events.

    Caller must explicitly certify *no human voice* in calibration.
    No model learns a template from mixed near-end speech.
    """
    if not 1 <= pre <= 2400 or not 240 <= post <= 9600:
        raise ValueError("Invalid event window")
    by_kind: dict[str, list[np.ndarray]] = {}
    for i, ev in enumerate(events):
        n = ev["sample"]
        a, b = n - pre, n + post
        if a < 0 or b > len(mic):
            raise ValueError("Click calibration event near edge")
        if any(a < other["sample"] + post and
               b > other["sample"] - pre
               for j, other in enumerate(events) if i != j):
            raise ValueError("Overlapping calibration events cannot isolate template")
        if float(np.max(np.abs(reference[a:b]))) > 1/32768.:
            raise ValueError("Music was ON during claimed click-only calibration")
        snippet = mic[a:b].copy()
        snippet -= float(np.mean(snippet[:max(1, pre // 2)]))
        by_kind.setdefault(ev["kind"], []).append(snippet)
    templates: dict[str, np.ndarray] = {}
    details = {}
    for kind, samples in by_kind.items():
        if len(samples) < 3:
            raise ValueError("Need >=3 isolated calibration events for " + kind)
        tmpl = np.median(np.stack(samples), axis=0).astype(np.float64)
        # Minimum end fade avoids introducing an abrupt template boundary.
        fade = min(240, post // 4)
        tmpl[-fade:] *= np.linspace(1., 0., fade)
        if np.max(np.abs(tmpl)) < 1/32768.:
            raise ValueError("Click calibration class effectively silent")
        if np.max(np.abs(tmpl)) > .50:
            raise ValueError("Suspiciously loud click template")
        templates[kind] = tmpl
        details[kind] = {
            "isolated_event_count": len(samples),
            "window_samples_before": pre,
            "window_samples_after": post,
            "template_rms_dbfs": round(20*math.log10(
                float(np.sqrt(np.mean(tmpl**2))) + 1e-12), 5),
        }
    return templates, details


def predict_clicks(size: int, events: list[dict],
                   templates: dict[str, np.ndarray],
                   pre: int = PRE) -> tuple[np.ndarray, dict]:
    clicks = np.zeros(size, dtype=np.float64)
    used = Counter()
    missed = Counter()
    for ev in events:
        tmpl = templates.get(ev["kind"])
        if tmpl is None:
            missed[ev["kind"]] += 1
            continue
        n = ev["sample"]
        start = n - pre
        end = start + len(tmpl)
        if start < 0 or end > size:
            raise ValueError("Event is too close to boundary for template")
        clicks[start:end] += tmpl
        used[ev["kind"]] += 1
    return clicks, {
        "predicted_event_count": dict(used),
        "unknown_or_uncalibrated_event_count": dict(missed),
        "event_gain_estimation_from_live_speech": False,
        "input_key_identity_or_text_used": False,
    }


def compensate(mic: np.ndarray, ref: np.ndarray,
               events: list[dict], fir: np.ndarray,
               templates: dict[str, np.ndarray],
               gain_update_windows: list[tuple[int, int]] | None = None
               ) -> tuple[dict[str, np.ndarray], dict]:
    check_signal(mic, ref)
    music = predict_music(ref, fir)
    gain, gain_info = causal_gain_curve(
        mic, music, gain_update_windows or [], events)
    music = music * gain
    clicks, event_info = predict_clicks(len(mic), events, templates)
    output = mic - music - clicks
    tracks = {
        "raw": mic.copy(),
        "anti_music": -music,
        "anti_clicks": -clicks,
        "music_only": mic - music,
        "combined": output,
    }
    max_peak = max(float(np.max(np.abs(v))) for v in tracks.values())
    if not np.isfinite(max_peak) or max_peak > PEAK_LIMIT:
        raise ValueError(
            "M6 would clip (peak %.5f): no automatic limiting/attenuation" %
            max_peak)
    return tracks, {
        "music_gain": gain_info, "clicks": event_info,
        "max_absolute_output_peak": max_peak,
        "no_speech_aware_filtering_or_postprocessing": True,
        "no_nearend_learning_during_doubletalk": True,
        "sample_rate": RATE, "timestamp_unit": "microphone_sample_index",
        "offline_only": True,
    }


def run(args: argparse.Namespace) -> dict:
    os.umask(0o077)
    if not args.speech_free_calibration_confirmed:
        raise ValueError("Explicit isolated/no-voice calibration consent required")
    if not args.reference_sample_clock_confirmed:
        raise ValueError("Explicit microphone/reference sample-clock alignment required")
    repo = Path(__file__).resolve().parents[1]
    audit = (repo / ".audit").resolve(strict=True)
    cal_mic_p = assert_private(args.cal_mic, audit, existing=True)
    cal_ref_p = assert_private(args.cal_reference, audit, existing=True)
    cal_events_p = assert_private(args.cal_events, audit, existing=True)
    mic_p = assert_private(args.mic, audit, existing=True)
    ref_p = assert_private(args.reference, audit, existing=True)
    events_p = assert_private(args.events, audit, existing=True)
    out = args.out.expanduser()
    if out.is_symlink() or out.exists():
        raise FileExistsError("Refuse to overwrite private output")
    out = out.parent.resolve(strict=True) / out.name
    if audit not in out.parents:
        raise ValueError("Output directory must be under .audit")
    for path in (cal_mic_p, cal_ref_p, cal_events_p,
                 mic_p, ref_p, events_p):
        if path == out or out in path.parents:
            raise ValueError("Output cannot contain or replace inputs")

    cmic, cref = pcm16_read(cal_mic_p), pcm16_read(cal_ref_p)
    mic, ref = pcm16_read(mic_p), pcm16_read(ref_p)
    check_signal(cmic, cref)
    check_signal(mic, ref)
    cref = delay_reference(cref, args.acoustic_lag_samples)
    ref = delay_reference(ref, args.acoustic_lag_samples)
    c_events = read_events(cal_events_p, len(cmic))
    events = read_events(events_p, len(mic))
    m_windows = windows_from_strings(args.music_only, len(cmic))
    update = windows_from_strings(args.speech_free_update, len(mic))
    events_outside_windows(c_events, m_windows)
    coeff, fir_report = train_music_fir(
        cmic, cref, m_windows, taps=args.taps)
    templates, template_report = train_click_templates(cmic, cref, c_events)
    tracks, process_report = compensate(
        mic, ref, events, coeff, templates, update)

    report = {
        "schema": "cuewire-M6-event-anchored-offline-ANC-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "state": "experimental_offline_not_live",
        "calibration_speech_free_certified_by_operator": True,
        "references_aligned_by_operator_to_mic_sample_clock": True,
        "timestamp_alignment_independently_verified": False,
        "explicit_acoustic_lag_samples": args.acoustic_lag_samples,
        "lag_pinned_by_operator_not_fitted_on_doubletalk": True,
        "calibration_audio": {
            "physical_mic_sha256": sha256(cal_mic_p),
            "playback_reference_sha256": sha256(cal_ref_p),
            "event_markers_sha256": sha256(cal_events_p),
        },
        "evaluation_audio": {
            "physical_mic_sha256": sha256(mic_p),
            "playback_reference_sha256": sha256(ref_p),
            "event_markers_sha256": sha256(events_p),
        },
        "music_fir": fir_report,
        "click_templates": template_report,
        "evaluation": process_report,
        "original_audio_modified": False,
        "automatic_playback": False,
        "automatic_recording_or_input_capture": False,
        "system_audio_or_input_config_modified": False,
        "private_audio_only": True,
        "nearend_speech_quality_proved": False,
        "output_sha256": {},
    }
    filenames = {
        "raw": "01_physical_raw.wav",
        "anti_music": "02_inverted_music_estimate.wav",
        "anti_clicks": "03_inverted_click_estimate.wav",
        "music_only": "04_mic_minus_music.wav",
        "combined": "05_combined_compensation.wav",
    }
    out.mkdir(mode=0o700)
    for key, filename in filenames.items():
        path = out / filename
        pcm16_write(path, tracks[key])
        report["output_sha256"][filename] = sha256(path)
    (out / "m6-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n"
                for name, digest in report["output_sha256"].items())
        + f"{sha256(out / 'm6-report.json')}  m6-report.json\n",
        encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cal-mic", type=Path, required=True)
    parser.add_argument("--cal-reference", type=Path, required=True)
    parser.add_argument("--cal-events", type=Path, required=True)
    parser.add_argument("--music-only", action="append", default=[],
                        metavar="START:END", help="Speaker-only, no speech, no clicks")
    parser.add_argument("--speech-free-calibration-confirmed",
                        action="store_true", help="Operator verified ALL calibration has no voice")
    parser.add_argument("--reference-sample-clock-confirmed",
                        action="store_true", help="Operator verified both farend WAVs share physical mic sample clock")
    parser.add_argument("--mic", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--speech-free-update", action="append", default=[],
                        metavar="START:END", help="Explicit speech+click-free gain updates")
    parser.add_argument("--acoustic-lag-samples", type=int, default=0,
                        help="Measured nonnegative delay from farend samples to microphone, 0..24000")
    parser.add_argument("--taps", type=int, default=128)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args)
        print(json.dumps({
            "status": result["state"],
            "output_sha256": result["output_sha256"],
            "calibrated_events": result["click_templates"],
            "music_fir": result["music_fir"],
        }, indent=2))
    except (OSError, ValueError, KeyError, TypeError,
            wave.Error, np.linalg.LinAlgError) as exc:
        parser.exit(1, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
