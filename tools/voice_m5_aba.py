#!/usr/bin/env python3
"""CueWire M5: one-session speaker OFF -> ON -> OFF control experiment.

Record ONE physical mic continuously, playing ONE verified existing local
M5 reference through an explicit physical speaker sink between two
silent-speaker intervals. No voice, no virtual microphone, no routing/
volume/default-setting modifications, no services, no self-monitoring.

This experiment measures power in deliberately inset phase windows; it
does NOT perform echo cancellation or prove hardware gain is unchanged
(USB microphone firmware may apply AGC).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time

import numpy as np

from voice_m5_aec import AUDIT, RATE, db, digest, read_wav, rms
from voice_m5_noise_probe import powers, description
from voice_m5_capture import (checked_speaker, checked_program,
                              private_output, spawn, stop_children,
                              validate_completed_microphone, log_excerpt)
from voice_raw_capture import choose_source


def summarize_phases(mic: np.ndarray, pre_s: int, on_s: float,
                     post_s: int) -> dict:
    """Conservative interior windows avoid unsynchronized startup/stops.

    No hardware timestamp is available: windows are approximate. The
    lead-in, playback and trailing silences must be >=8s/15s/8s.
    Only representative interior regions are measured. Results cannot
    identify the actual acoustic echo or firmware AGC from RMS alone.
    """
    if pre_s < 8 or post_s < 8 or on_s < 15:
        raise ValueError("Require at least 8s/15s/8s OFF/ON/OFF")
    actual_s = len(mic) / RATE
    if actual_s < pre_s + on_s + post_s - 2:
        raise ValueError("Mic WAV too short for all three control phases")
    windows = {
        "before_speaker_on": (2.0, pre_s - 2.0),
        "speaker_on": (pre_s + 4.0, pre_s + on_s - 4.0),
        "after_speaker_off": (actual_s - 6.0, actual_s - 2.0),
    }
    report = {}
    for name, (a_s, b_s) in windows.items():
        a = round(a_s * RATE)
        b = round(b_s * RATE)
        if b <= a or a < 0 or b > len(mic):
            raise ValueError("Invalid interior window " + name)
        segment = mic[a:b]
        summary = description(powers(mic, a, b))
        summary["segment_rms_dbfs"] = round(db(rms(segment)), 5)
        summary["segment_peak_dbfs"] = round(db(float(np.max(np.abs(segment)))), 5)
        summary["approx_wav_position_s"] = [round(a_s, 4), round(b_s, 4)]
        report[name] = summary
    baseline_power = np.mean([
        rms(mic[round(windows[k][0] * RATE):round(windows[k][1] * RATE)]) ** 2
        for k in ("before_speaker_on", "after_speaker_off")
    ])
    speaker_power = rms(mic[
        round(windows["speaker_on"][0] * RATE):
        round(windows["speaker_on"][1] * RATE)]) ** 2
    off = math.sqrt(float(baseline_power))
    on = math.sqrt(float(speaker_power))
    diff = db(on) - db(off)
    return {
        "windows": report,
        "off_combined_rms_dbfs": round(db(off), 5),
        "on_rms_dbfs": round(db(on), 5),
        "on_minus_off_rms_db": round(diff, 5),
        "conditional_off_power_fraction_if_stationary": round(
            min(1.0, float(baseline_power / max(speaker_power, 1e-24))), 6),
        "pre_post_median_rms_delta_db": round(
            report["after_speaker_off"]["p50_rms_dbfs"] -
            report["before_speaker_on"]["p50_rms_dbfs"], 5),
        "limitations": [
            "Phase boundaries are wall-clock based and approximate; only interior WAV windows are used.",
            "No synchronized audio timestamps exist between pw-record and pw-play.",
            "Mic firmware auto gain control or ambient noise changes may alter the comparison.",
            "RMS delta alone does not isolate microphone noise from acoustic echo.",
            "No voice was spoken; no near-end voice quality can be inferred.",
        ]
    }


def run(args: argparse.Namespace) -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Linux-only process-ownership guard required")
    for exe in ("pw-record", "pw-play", "pactl"):
        checked_program(exe)
    if args.source.startswith("@") or not args.source.startswith("alsa_input."):
        raise ValueError("Require one explicit physical microphone source")
    mic = choose_source(args.source)
    sink = checked_speaker(args.speaker)

    previous = args.capture.expanduser().resolve(strict=True)
    if AUDIT not in previous.parents:
        raise ValueError("Reference capture must be within local private .audit")
    old_manifest_path = previous / "manifest.json"
    old = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    if (old.get("schema") != "cuewire-M5-paired-physical-capture-v1" or
            old.get("status") != "captured_for_offline_aec"):
        raise ValueError("Existing reference capture is not a valid M5 take")
    ref = previous / "reference-sent.wav"
    reference_sha = digest(ref)
    if reference_sha != old.get("reference", {}).get("sha256"):
        raise ValueError("Existing reference WAV SHA256 mismatch")
    reference_duration = len(read_wav(ref)) / RATE
    if not 15 <= reference_duration <= 45:
        raise ValueError("Reference must be 15..45 seconds long")
    out = private_output(args.out)
    out.mkdir(mode=0o700)
    mic_file = out / "microphone-aba-raw.wav"
    expected = args.pre_seconds + reference_duration + args.post_seconds
    result = {
        "schema": "cuewire-M5-continuous-ABA-control-v1",
        "status": "running",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_mic_name": mic["name"],
        "source_mic_sample_spec": mic["sample_spec"],
        "speaker_sink_name": sink["name"],
        "speaker_play_volume": args.volume,
        "reference_source_capture": str(previous),
        "reference_source_manifest_sha256": digest(old_manifest_path),
        "reference_wav_sha256": reference_sha,
        "reference_duration_s": reference_duration,
        "requested_speaker_off_before_s": args.pre_seconds,
        "requested_speaker_off_after_s": args.post_seconds,
        "requested_total_duration_s": expected,
        "notes": [
            "Operator silent throughout; speaker OFF before and after one known reference.",
            "PipeWire sink/source explicitly targeted; system defaults unchanged.",
            "Other audio playback invalidates this controlled speaker A/B test.",
            "No microphone monitoring, virtual audio routing or AEC.",
        ]
    }
    player = None
    rec = None
    rec_cmd = ["pw-record", "--target=" + mic["name"],
               "--rate=48000", "--channels=1",
               "--channel-map=mono", "--format=s16", str(mic_file)]
    player_cmd = ["pw-play", "--target", sink["name"],
                  "--volume", str(args.volume), str(ref)]

    def wait_alive(seconds: float, stage: str, proc) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if rec is not None and rec.poll() is not None:
                raise RuntimeError("Microphone recorder exited during " + stage +
                                   ": " + log_excerpt(out / "pw-record.log"))
            if proc is not None and proc.poll() is not None and stage == "speaker_playing":
                break
            time.sleep(0.05)

    try:
        print("M5 ABA: record mic, speaker OFF", args.pre_seconds, "seconds.", flush=True)
        print("Stay silent throughout. Do not play other system audio.", flush=True)
        rec = spawn(rec_cmd, out / "pw-record.log")
        recorded_from = time.monotonic()
        wait_alive(args.pre_seconds, "speaker_off_before", None)
        print("M5 ABA: speaker ON with verified reference", flush=True)
        player = spawn(player_cmd, out / "pw-play.log")
        player_start = time.monotonic()
        result["playback_start_after_recorder_launch_s"] = round(
            player_start - recorded_from, 4)
        wait_alive(reference_duration + 8, "speaker_playing", player)
        if player.poll() is None:
            raise RuntimeError("Speaker playback exceeded reference duration + 8s")
        if player.returncode != 0:
            raise RuntimeError("Speaker playback failed: " +
                               log_excerpt(out / "pw-play.log"))
        result["playback_wall_seconds"] = round(
            time.monotonic() - player_start, 4)
        if result["playback_wall_seconds"] < reference_duration - 1.0:
            raise RuntimeError(
                "Speaker player returned too early for the expected reference; "
                "control take rejected")
        print("M5 ABA: speaker OFF again", args.post_seconds, "seconds.", flush=True)
        wait_alive(args.post_seconds, "speaker_off_after", None)
        result["mic_record_wall_seconds_before_stop"] = round(
            time.monotonic() - recorded_from, 4)
    except BaseException as exc:
        result["status"] = "failed_or_interrupted"
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        stop_children()
        if rec is not None:
            result["recorder_returncode_after_stop"] = rec.returncode
        if mic_file.exists():
            result["microphone_raw_sha256_after"] = digest(mic_file)
        if result["status"] == "running":
            try:
                meta = validate_completed_microphone(
                    mic_file, int(math.floor(expected)), out / "pw-record.log")
                if meta["measurements"]["clipped_samples"] != 0:
                    raise ValueError("ADC clipping; control take rejected")
                wave_data = read_wav(mic_file)
                result["microphone"] = meta
                result["phase_analysis"] = summarize_phases(
                    wave_data, args.pre_seconds, reference_duration, args.post_seconds)
                if digest(ref) != reference_sha:
                    raise RuntimeError("Reference modified during capture")
                result["status"] = "captured_aba_for_noise_diagnostics"
                print("PASS: continuous A/B/A mic capture, no voice processed.")
                print("Mic WAV:", mic_file)
                print("Phase RMS (off/on/off):",
                      result["phase_analysis"]["windows"]["before_speaker_on"]["segment_rms_dbfs"],
                      result["phase_analysis"]["windows"]["speaker_on"]["segment_rms_dbfs"],
                      result["phase_analysis"]["windows"]["after_speaker_off"]["segment_rms_dbfs"])
            except (OSError, RuntimeError, ValueError) as exc:
                result["status"] = "rejected_after_finalization"
                result["error"] = f"{type(exc).__name__}: {exc}"
        with (out / "manifest.json").open("x", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        with (out / "SHA256SUMS").open("x", encoding="utf-8") as f:
            for file in (mic_file, out / "manifest.json"):
                if file.is_file():
                    f.write(digest(file) + "  " + file.name + "\n")
        print("Manifest:", out / "manifest.json", flush=True)
    if result["status"] != "captured_aba_for_noise_diagnostics":
        raise RuntimeError(result.get("error", "ABA rejected; keep audit files"))


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", required=True, type=Path,
                   help="Immutable existing M5-006 capture directory for reference WAV")
    p.add_argument("--source", required=True,
                   help="Exact alsa_input.* physical PipeWire mic source")
    p.add_argument("--speaker", required=True,
                   help="Exact physical PipeWire sink")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--pre-seconds", type=int, default=8)
    p.add_argument("--post-seconds", type=int, default=8)
    p.add_argument("--volume", type=float, default=0.30)
    args = p.parse_args()
    if not 8 <= args.pre_seconds <= 20 or not 8 <= args.post_seconds <= 20:
        p.error("OFF phases must be 8..20 seconds")
    if not math.isfinite(args.volume) or not .05 <= args.volume <= .60:
        p.error("Playback volume must be 0.05..0.60")
    try:
        run(args)
    except KeyboardInterrupt:
        print("Interrupted. Owned child processes terminated; local evidence retained.",
              file=sys.stderr)
        raise SystemExit(130)
    except (ValueError, OSError, RuntimeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
