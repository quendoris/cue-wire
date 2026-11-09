#!/usr/bin/env python3
"""CueWire M5: opt-in paired speaker/reference + physical ALSA microphone capture.

Explicit ONE PipeWire sink and ONE actual ALSA microphone device. A known
mono 48k PCM16 reference WAV is generated and *that exact file* is played
through pw-play, so its samples provide the digital source for offline AEC.
The digital file is NOT a sample-clock-synchronized hardware monitor.
The reference-to-microphone lag MUST be estimated later.

The first configurable speaker-only calibration interval (default 5 seconds,
up to 14 seconds for extended experiments) must contain NO operator speech.
After that, the operator reads text over the music.
No existing PipeWire source/default/sink/routes are changed and no new
monitor or virtual microphone is created. No music or mic is uploaded.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import wave

from voice_alsa_capture import physical_card
from voice_aec_inspect import pactl, short_nodes
from voice_raw_capture import child_ownership, terminate_group, sha256, verify_wav, choose_source

RATE = 48000
ROOT = Path(__file__).resolve().parents[1]
AUDIT = (ROOT / ".audit").resolve()
CHILDREN = []


def private_output(output: Path) -> Path:
    if not AUDIT.is_dir():
        raise ValueError("Missing local .audit folder")
    if output.exists() or output.is_symlink():
        raise FileExistsError("Output already exists; never overwrite")
    p = output.expanduser()
    parent = p.parent.resolve(strict=True)
    if AUDIT not in parent.parents and parent != AUDIT:
        raise ValueError("Output must be inside private .audit directory")
    return parent / p.name


def checked_speaker(name: str) -> dict:
    if not name or name.startswith("@") or name == "auto":
        raise ValueError("Specify an explicit real sink node, never default/auto")
    sinks = short_nodes(pactl("list", "short", "sinks"), "sinks")
    matches = [sink for sink in sinks if sink["name"] == name]
    if len(matches) != 1 or name.startswith("cuewire."):
        raise ValueError("Speaker sink not uniquely found, or a CueWire virtual sink")
    return matches[0]


def checked_program(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"Missing command {name}")
    return path


def spawn(cmd: list[str], log_path: Path) -> subprocess.Popen:
    parent = os.getpid()
    with log_path.open("x", encoding="utf-8") as log:
        log.write("argv: " + json.dumps(cmd, ensure_ascii=False) + "\n")
        log.flush()
        process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True,
                                   preexec_fn=lambda: child_ownership(parent))
    CHILDREN.append(process)
    return process


def stop_children() -> None:
    for process in reversed(CHILDREN):
        terminate_group(process)


def start_paired_streams(record_cmd: list[str], playback_cmd: list[str],
                         out: Path, recorder_log: str = "arecord.log"):
    """Attempt playback immediately after launching the microphone process.

    Do not wait 3 seconds for ALSA readiness; failure is detected in the
    concurrent monitoring loop, and playback is stopped on any failure.
    """
    recorder = spawn(record_cmd, out / recorder_log)
    try:
        player = spawn(playback_cmd, out / "pw-play.log")
    except BaseException:
        terminate_group(recorder)
        raise
    return recorder, player


def build_mic_recorder(args, mic_file: Path) -> tuple[list[str], str, dict]:
    """Select exactly one capture backend; never silently switch devices."""
    if args.capture_backend == "alsa":
        if args.source:
            raise ValueError("--source is valid only with --capture-backend pipewire")
        checked_program("arecord")
        card = physical_card(args.mic, args.expect_card_id)
        return (["arecord", "-D", args.mic, "-f", "S16_LE",
                 "-c", "1", "-r", str(RATE), "-t", "wav",
                 "-d", str(args.seconds + 4), str(mic_file)],
                "arecord.log",
                {"backend": "alsa", "mic_device": args.mic,
                 "mic_card_id": args.expect_card_id,
                 "mic_card_listing": card["arecord_listing"]})
    if args.capture_backend == "pipewire":
        if not args.source:
            raise ValueError("PipeWire backend requires --source EXACT_PHYSICAL_NODE")
        if not args.source.startswith("alsa_input."):
            raise ValueError("Require explicit physical alsa_input.* node, not a monitor/virtual source")
        checked_program("pw-record")
        chosen = choose_source(args.source)
        return (["pw-record", "--target=" + chosen["name"],
                 "--rate=48000", "--channels=1", "--channel-map=mono",
                 "--format=s16", str(mic_file)],
                "pw-record.log",
                {"backend": "pipewire", "mic_source_name": chosen["name"],
                 "mic_source_id_at_start": chosen["id"],
                 "mic_source_sample_spec": chosen["sample_spec"]})
    raise ValueError("Unknown recorder backend")


def log_excerpt(path: Path, limit: int = 1400) -> str:
    if not path.is_file():
        return "<no log file>"
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError as exc:
        return f"<log unreadable: {type(exc).__name__}>"


def prepare_reference(source: Path, output: Path, seconds: int) -> dict:
    checked_program("ffmpeg")
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
           "-n", "-i", str(source), "-map", "0:a:0", "-t", str(seconds),
           "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(output)]
    with (output.parent / "prepare-reference.log").open("x", encoding="utf-8") as log:
        complete = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=90,
                                  check=False)
    if complete.returncode:
        raise RuntimeError("FFmpeg reference conversion failed; see log")
    with wave.open(str(output), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(),
            wav.getcomptype()) != (RATE, 1, 2, "NONE"):
            raise RuntimeError("Prepared reference not mono PCM16 48k")
        frames = wav.getnframes()
        if not RATE * seconds <= frames <= RATE * seconds + 2:
            raise RuntimeError("Playback reference too short; supply a longer music sample")
        if not wav.readframes(1):
            raise RuntimeError("Reference audio empty")
    return {"frames": frames, "duration_s": frames / RATE,
            "sha256": sha256(output), "input_sha256": sha256(source)}


def watch_paired_streams(record, play, mic_file: Path, out: Path,
                         recorder_log: str, calibration_seconds: int,
                         expected_seconds: int, report: dict,
                         play_started: float) -> None:
    """Wait for playback, observing recorder liveness but not requiring WAV flush.

    File growth is advisory while the recording process is alive. A separate
    post-close audit (verify_wav) determines whether audio really arrived.
    """
    announcement = False
    observed_stream_bytes = False
    buffered_notice = False
    while True:
        elapsed = time.monotonic() - play_started
        # Detect a recorder process failure immediately, but do not
        # mistake audio file write buffering for a broken capture.
        # In particular pw-record/libsndfile may flush only at close.
        if record.poll() is not None:
            raise RuntimeError(
                "Microphone recorder exited (rc=" + str(record.returncode) +
                ") during playback; " + recorder_log + ": " +
                log_excerpt(out / recorder_log))
        if not observed_stream_bytes and mic_file.is_file() and mic_file.stat().st_size > 9600:
            observed_stream_bytes = True
            report["mic_bytes_observed_during_playback_at_s"] = round(elapsed, 4)
            print("M5: capture file is growing; audio integrity will be checked after close.",
                  flush=True)
        if (not observed_stream_bytes and not buffered_notice and elapsed >= 1.0):
            buffered_notice = True
            print("M5: no incremental WAV bytes yet (possibly buffered); "
                  "continuing and checking finalized WAV after playback.",
                  flush=True)
        if play.poll() is not None:
            break
        if not announcement and elapsed >= calibration_seconds:
            print("M5: CALIBRATION COMPLETE — NOW READ YOUR TEXT!", flush=True)
            announcement = True
        if elapsed > expected_seconds + 8:
            raise RuntimeError("pw-play exceeded expected playback duration")
        time.sleep(0.04)
    report["capture_wav_bytes_observed_during_playback"] = observed_stream_bytes
    if not announcement:
        raise RuntimeError("Playback ended before speaker-only calibration/reading prompt")



def validate_completed_microphone(mic_file: Path, expected_seconds: int,
                                  recorder_log_path: Path) -> dict:
    """Only a finalized recording can prove capture success."""
    if not mic_file.is_file():
        raise RuntimeError("Recorder produced no WAV; " +
                           recorder_log_path.name + ": " +
                           log_excerpt(recorder_log_path))
    finalized = verify_wav(mic_file)
    if finalized["duration_s"] < expected_seconds - 1:
        raise RuntimeError(
            "Finalized microphone WAV is too short: " +
            str(finalized["duration_s"]) + "s (requested " +
            str(expected_seconds) + "s); capture rejected. " +
            recorder_log_path.name + ": " + log_excerpt(recorder_log_path))
    if finalized["rms_dbfs"] is None or finalized["rms_dbfs"] < -85:
        raise RuntimeError(
            "Finalized microphone WAV is effectively silent (RMS " +
            str(finalized["rms_dbfs"]) + " dBFS); no valid acoustic calibration")
    return {"wav": mic_file.name, "sha256": sha256(mic_file),
            "measurements": finalized}


def experiment(args) -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Linux only (parent-death signal required)")
    for tool in ("pw-play", "ffmpeg", "pactl"):
        checked_program(tool)
    sink = checked_speaker(args.speaker)
    original = args.music.expanduser().resolve(strict=True)
    if not original.is_file() or original.stat().st_size < 1000:
        raise ValueError("Music source missing/empty")
    out = private_output(args.out)
    ref = out / "reference-sent.wav"
    mic_file = out / "microphone-raw.wav"
    # Validate the capture device and explicit backend BEFORE creating a
    # run directory; invalid mic sources must not consume M5-00N names.
    recorder_cmd, recorder_log, mic_info = build_mic_recorder(args, mic_file)
    out.mkdir(mode=0o700)
    report = {
        "schema": "cuewire-M5-paired-physical-capture-v1",
        "status": "running",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "mic_capture": mic_info,
        "speaker_sink_name": sink["name"],
        "speaker_sink_id_at_start": sink["id"],
        "source_music_path": str(original),
        "requested_duration_s": args.seconds,
        "speaker_only_calibration_s": args.calibration_seconds,
        "volume_pw_play": args.volume,
        "notes": [
            "First calibration interval requires NO local voice, other sounds or movement.",
            "Start reading when instructed, while the music continues playing.",
            "Digital reference is pre-sink audio, not sample-clock synchronized.",
            "Playback launches immediately after the selected recorder, with no readiness wait.",
            "If microphone capture fails, the player is stopped and this take is rejected.",
            "Live WAV file size is diagnostic only; PipeWire can buffer data.",
            "Valid full-length WAV plus measured audio are required after recorder close.",
            "The microphone backend is selected explicitly; no automatic fallback.",
            "PipeWire capture is a separate opt-in experiment and may fail on this mic.",
            "If sink effects, resampling, volume or mixing change, the acoustic model can drift.",
            "No audio routing, defaults, or virtual mic changes were made.",
            "This first pilot intentionally plays one known source; other system audio invalidates the reference."
        ]
    }
    try:
        report["reference"] = prepare_reference(original, ref, args.seconds)
        print("M5: input checks passed.", flush=True)
        print("Speaker:", args.speaker, "pw-play volume:", args.volume, flush=True)
        print("Mic:", mic_info, flush=True)
        print("Speaker-only calibration: first", args.calibration_seconds,
              "seconds, then READ TEST TEXT while music continues.", flush=True)
        print("Your voice will NOT be played into the headphones.", flush=True)
        playback_cmd = ["pw-play", "--target", args.speaker, "--volume",
                        str(args.volume), str(ref)]
        # Both start back-to-back: no 3-second wait for the microphone.
        # An ALSA error is reported while playback is already attempted,
        # and will immediately stop the player through finally.
        record, play = start_paired_streams(recorder_cmd, playback_cmd, out, recorder_log)
        play_started = time.monotonic()
        report["playback_launch_utc_approx"] = datetime.now(timezone.utc).isoformat()
        report["playback_start_policy"] = "launch_immediately_after_recorder_no_readiness_wait"
        print("M5: PLAYBACK LAUNCHED on", args.speaker,
              "; verifying concurrent", args.capture_backend, "recording.", flush=True)
        print("Remain silent for the first", args.calibration_seconds,
              "seconds of playback.", flush=True)
        watch_paired_streams(
            record, play, mic_file, out, recorder_log,
            args.calibration_seconds, args.seconds, report, play_started)
        report["player_returncode"] = play.returncode
        report["player_wall_seconds"] = round(time.monotonic() - play_started, 3)
        if play.returncode:
            raise RuntimeError("pw-play failed; see pw-play.log")

        # Let final reflections decay; then gracefully close and finalize WAV.
        time.sleep(0.5)
        terminate_group(record)
        report["recorder_returncode_after_stop"] = record.returncode
        report["microphone"] = validate_completed_microphone(
            mic_file, args.seconds, out / recorder_log)
        if sha256(ref) != report["reference"]["sha256"]:
            raise RuntimeError("Digital reference changed")
        report["status"] = "captured_for_offline_aec"
        print("PASS: paired WAVs stored locally; no AEC applied yet.", flush=True)
        print("Reference:", ref, flush=True)
        print("Mic RAW  :", mic_file, flush=True)
    except BaseException as exc:
        report["status"] = "failed_or_interrupted"
        report["error"] = type(exc).__name__ + ": " + str(exc)
        report["recorder_log_tail"] = log_excerpt(out / recorder_log)
        report["pw_play_log_tail"] = log_excerpt(out / "pw-play.log")
        raise
    finally:
        stop_children()
        if ref.is_file():
            report["reference_sha256_after"] = sha256(ref)
        if mic_file.is_file():
            report["mic_sha256_after"] = sha256(mic_file)
        with (out / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        print("M5 manifest:", out / "manifest.json", flush=True)


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--music", required=True, type=Path, help="Local music WAV/FLAC/etc., >=20 seconds")
    parser.add_argument("--capture-backend", choices=("alsa", "pipewire"), default="alsa",
                        help="Explicit capture backend; no automatic fallback")
    parser.add_argument("--source", help="Required exact alsa_input.* node for PipeWire capture")
    parser.add_argument("--speaker", required=True, help="EXACT pactl list short sinks name")
    parser.add_argument("--mic", default="hw:0,0", help="Direct ALSA hw:C,D")
    parser.add_argument("--expect-card-id", default="MICAudio97i",
                        help="Only used by --capture-backend alsa")
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--calibration-seconds", type=int, default=5)
    parser.add_argument("--volume", type=float, default=0.30)
    parser.add_argument("--out", type=Path, required=True, help="Fresh private .audit/voice-aec/M5-001")
    args = parser.parse_args()
    if not 15 <= args.seconds <= 45:
        parser.error("--seconds must be 15..45")
    if not 4 <= args.calibration_seconds <= 14 or args.seconds - args.calibration_seconds < 9:
        parser.error("Need 4..14s speaker-only calibration and >=9s readout")
    if not math.isfinite(args.volume) or not 0.05 <= args.volume <= 0.6:
        parser.error("--volume must be 0.05..0.6 for safe first-pilot gain")
    try:
        experiment(args)
    except KeyboardInterrupt:
        print("Interrupted. Owned audio children terminated; private evidence retained.",
              file=sys.stderr)
        raise SystemExit(130)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, wave.Error) as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
