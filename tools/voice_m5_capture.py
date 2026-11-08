#!/usr/bin/env python3
"""CueWire M5: opt-in paired speaker/reference + physical ALSA microphone capture.

Explicit ONE PipeWire sink and ONE actual ALSA microphone device. A known
mono 48k PCM16 reference WAV is generated and *that exact file* is played
through pw-play, so its samples provide the digital source for offline AEC.
The digital file is NOT a sample-clock-synchronized hardware monitor.
The reference-to-microphone lag MUST be estimated later.

The first 5 seconds after playback launch are intended to contain only
the speaker signal. After that, the operator reads text over the music.
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
from voice_raw_capture import child_ownership, terminate_group, sha256, verify_wav

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


def experiment(args) -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Linux only (parent-death signal required)")
    for tool in ("arecord", "pw-play", "ffmpeg", "pactl"):
        checked_program(tool)
    mic = physical_card(args.mic, args.expect_card_id)
    sink = checked_speaker(args.speaker)
    original = args.music.expanduser().resolve(strict=True)
    if not original.is_file() or original.stat().st_size < 1000:
        raise ValueError("Music source missing/empty")
    out = private_output(args.out)
    out.mkdir(mode=0o700)
    ref = out / "reference-sent.wav"
    mic_file = out / "microphone-raw.wav"
    report = {
        "schema": "cuewire-M5-paired-physical-capture-v1",
        "status": "running",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "mic_device": args.mic,
        "mic_card_id": args.expect_card_id,
        "mic_card_listing": mic["arecord_listing"],
        "speaker_sink_name": sink["name"],
        "speaker_sink_id_at_start": sink["id"],
        "source_music_path": str(original),
        "requested_duration_s": args.seconds,
        "speaker_only_calibration_s": args.calibration_seconds,
        "volume_pw_play": args.volume,
        "notes": [
            "First calibration interval requires NO local voice, other sounds or movement.",
            "Start reading when instructed, while the music continues playing.",
            "The digital reference is pre-sink audio, NOT a time-synchronized monitor.",
            "If sink effects, resampling, volume or mixing change, the acoustic model can drift.",
            "No audio routing, defaults, or virtual mic changes were made.",
            "This first pilot intentionally plays one known source; other system audio invalidates the reference."
        ]
    }
    try:
        report["reference"] = prepare_reference(original, ref, args.seconds)
        print("M5: input checks passed.", flush=True)
        print("Speaker:", args.speaker, "pw-play volume:", args.volume, flush=True)
        print("Mic:", args.mic, "card:", args.expect_card_id, flush=True)
        print("Speaker-only calibration: first", args.calibration_seconds,
              "seconds, then READ TEST TEXT while music continues.", flush=True)
        print("Your voice will NOT be played into the headphones.", flush=True)
        record = spawn(["arecord", "-D", args.mic, "-f", "S16_LE",
                        "-c", "1", "-r", str(RATE), "-t", "wav",
                        "-d", str(args.seconds + 4), str(mic_file)],
                       out / "arecord.log")
        # Do not play until at least some PCM bytes prove the physical capture started.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if record.poll() is not None:
                raise RuntimeError("ALSA recorder exited before speaker playback")
            if mic_file.is_file() and mic_file.stat().st_size > 9600:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("ALSA recorder not writing; speaker playback NOT started")

        play = spawn(["pw-play", "--target", args.speaker, "--volume",
                      str(args.volume), str(ref)], out / "pw-play.log")
        play_started = time.monotonic()
        report["playback_started_utc_approx"] = datetime.now(timezone.utc).isoformat()
        print("M5 PLAY STARTED. Please remain silent for", args.calibration_seconds,
              "seconds.", flush=True)
        announcement = False
        while True:
            elapsed = time.monotonic() - play_started
            if not announcement and elapsed >= args.calibration_seconds:
                print("M5: CALIBRATION COMPLETE — NOW READ YOUR TEXT!", flush=True)
                announcement = True
            if play.poll() is not None:
                break
            if record.poll() is not None:
                raise RuntimeError("ALSA capture stopped before end of playback")
            if elapsed > args.seconds + 8:
                raise RuntimeError("pw-play exceeded expected playback duration")
            time.sleep(0.07)
        report["player_returncode"] = play.returncode
        report["player_wall_seconds"] = round(time.monotonic() - play_started, 3)
        if play.returncode:
            raise RuntimeError("pw-play failed; see pw-play.log")

        # Let final reflections decay; then gracefully close and finalize WAV.
        time.sleep(0.5)
        terminate_group(record)
        if not mic_file.is_file():
            raise RuntimeError("ALSA produced no WAV")
        report["microphone"] = {"wav": mic_file.name,
                                "sha256": sha256(mic_file),
                                "measurements": verify_wav(mic_file)}
        if report["microphone"]["measurements"]["duration_s"] < args.seconds - 1:
            raise RuntimeError("ALSA recording suspiciously short")
        if sha256(ref) != report["reference"]["sha256"]:
            raise RuntimeError("Digital reference changed")
        report["status"] = "captured_for_offline_aec"
        print("PASS: paired WAVs stored locally; no AEC applied yet.", flush=True)
        print("Reference:", ref, flush=True)
        print("Mic RAW  :", mic_file, flush=True)
    except BaseException as exc:
        report["status"] = "failed_or_interrupted"
        report["error"] = type(exc).__name__ + ": " + str(exc)
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
    parser.add_argument("--speaker", required=True, help="EXACT pactl list short sinks name")
    parser.add_argument("--mic", default="hw:0,0", help="Direct ALSA hw:C,D")
    parser.add_argument("--expect-card-id", default="MICAudio97i")
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--calibration-seconds", type=int, default=5)
    parser.add_argument("--volume", type=float, default=0.30)
    parser.add_argument("--out", type=Path, required=True, help="Fresh private .audit/voice-aec/M5-001")
    args = parser.parse_args()
    if not 15 <= args.seconds <= 45:
        parser.error("--seconds must be 15..45")
    if not 4 <= args.calibration_seconds <= 7 or args.seconds - args.calibration_seconds < 9:
        parser.error("Need 4..7s calibration and >=9s readout")
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
