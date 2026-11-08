#!/usr/bin/env python3
"""CueWire M4: one foreground RAW capture from an EXPLICIT physical mic.

No AEC, denoising, virtual mic, EQ, routing changes, services or playback.
The recorder process is child-only and receives Linux parent-death SIGKILL
if the owner is force-killed; graceful Ctrl+C finalizes its WAV.
Records into a NEW private .audit directory, never overwrites originals.
"""
from __future__ import annotations

import argparse
from array import array
import ctypes
from datetime import datetime, timezone
import hashlib
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

from voice_aec_inspect import pactl, short_nodes

ROOT=Path(__file__).resolve().parents[1]
AUDIT=(ROOT/".audit").resolve()
RATE=48000
CHANNELS=1
WIDTH=2  # Signed 16-bit mono; match physical MICAudio-97i snapshot.


def get_sources():
    return short_nodes(pactl("list","short","sources"),"sources")


def choose_source(name):
    if not name or name.endswith(".monitor") or name.startswith("@"):
        raise ValueError("Refuse monitor/default/ambiguous input. Use exact physical mic node.name.")
    matches=[item for item in get_sources() if item["name"]==name]
    if len(matches)!=1:
        raise ValueError("Physical microphone not uniquely present in pactl sources: "+name)
    return matches[0]


def safe_dir(path):
    if not AUDIT.is_dir():
        raise ValueError("Missing repo .audit directory; create it explicitly first")
    output=path.expanduser().resolve()
    if AUDIT not in output.parents:
        raise ValueError("Private capture must be in the local repo's .audit/ folder")
    if output.exists():
        raise FileExistsError("Refusing to overwrite existing take: "+str(output))
    return output


def sha256(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def verify_wav(path):
    with wave.open(str(path),"rb") as w:
        meta={"channels":w.getnchannels(),"rate":w.getframerate(),
              "sample_width_bytes":w.getsampwidth(),
              "frames":w.getnframes(),"duration_s":round(w.getnframes()/RATE,6),
              "compression":w.getcomptype()}
        if (meta["channels"],meta["rate"],meta["sample_width_bytes"],
                meta["compression"])!=(CHANNELS,RATE,WIDTH,"NONE"):
            raise RuntimeError("Recorded WAV is not 48kHz mono PCM16: "+str(meta))
        max_abs=0
        clipped=0
        square_sum=0
        seen=0
        while True:
            raw=w.readframes(8192)
            if not raw:
                break
            samples=array("h")
            samples.frombytes(raw)
            if sys.byteorder!="little":
                samples.byteswap()
            for v in samples:
                a=abs(v)
                max_abs=max(max_abs,a)
                clipped+=a>=32767
                square_sum+=v*v
            seen+=len(samples)
        if seen!=meta["frames"]:
            raise RuntimeError("WAV payload truncated or header inconsistent")
    rms=(math.sqrt(square_sum/seen)/32768) if seen else None
    meta.update({
        "peak_dbfs":round(20*math.log10(max(max_abs/32768,1e-12)),3)
                      if seen else None,
        "rms_dbfs":round(20*math.log10(max(rms,1e-12)),3)
                     if rms is not None else None,
        "clipped_samples":clipped
    })
    return meta


def child_ownership(parent_pid):
    # PR_SET_PDEATHSIG == 1 on Linux; ensure a force-killed parent does not
    # leave an orphaned microphone recorder. Single-threaded foreground CLI.
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(1,signal.SIGKILL,0,0,0)!=0 or os.getppid()!=parent_pid:
        os._exit(127)


def terminate_group(process):
    if process.poll() is not None:
        return
    for sig,seconds in ((signal.SIGINT,3),(signal.SIGTERM,2),(signal.SIGKILL,2)):
        if process.poll() is not None:
            break
        try:
            os.killpg(process.pid,sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            continue


def recorder_stderr_excerpt(path):
    """Bounded error excerpt: don't upload the actual stderr automatically."""
    if not path.exists():
        return "<pw-record log missing>"
    text=path.read_text(encoding="utf-8",errors="replace")
    return text[-3500:] if text.strip() else "<pw-record produced no stderr>"


def record(args):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Linux-only parent-death lifecycle guard required")
    if not shutil.which("pw-record"):
        raise RuntimeError("Missing pw-record executable")
    device=choose_source(args.mic)
    out=safe_dir(args.out)
    out.mkdir(parents=True,mode=0o700)
    wav=out/"raw.wav"
    stderr=out/"pw-record.stderr.log"
    command=[
        "pw-record",
        "--target="+args.mic,
        "--rate=48000",
        "--channels=1",
        "--channel-map=mono",
        "--format=s16",
        str(wav),
    ]
    start=datetime.now(timezone.utc).isoformat()
    beginning=time.monotonic()
    parent=os.getpid()
    process=None
    reason="unexpected"
    startup_checked=False
    print("CueWire M4 RAW capture:",args.mic,flush=True)
    print("Recording into new private file:",wav,flush=True)
    print("No output monitoring, denoising, AEC or audio system changes.",flush=True)
    print("Recorder readiness is checked after",args.startup_check_sec,
          "seconds using the actual file size (even silence creates PCM frames).",
          flush=True)
    print("Start with 5 seconds of silence. Stop with Ctrl+C; hard cap:",
          args.max_seconds,"s",flush=True)
    try:
        with stderr.open("x",encoding="utf-8") as errors:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,stderr=errors,
                    start_new_session=True,
                    preexec_fn=lambda:child_ownership(parent))
            deadline=time.monotonic()+args.max_seconds
            while process.poll() is None:
                elapsed=time.monotonic()-beginning
                if not startup_checked and elapsed>=args.startup_check_sec:
                    startup_checked=True
                    try: size=wav.stat().st_size
                    except FileNotFoundError: size=0
                    # 0.5 s PCM16 48k mono is 48000 bytes. The file header
                    # may be unfinalized during capture, so use byte progress
                    # ONLY as a liveness probe, never parse the live WAV.
                    if size<48000:
                        reason="no_audio_bytes_after_startup"
                        print("ERROR: pw-record has not written even 0.5 s "
                              "of 48k mono PCM after "+
                              str(args.startup_check_sec)+" seconds. "
                              "Stopping before you read the full script.",
                              file=sys.stderr,flush=True)
                        break
                    print("READY: pw-record has written audio data; "
                          "continue the capture.",flush=True)
                if time.monotonic()>=deadline:
                    reason="maximum_duration_reached"
                    break
                time.sleep(.08)
            else:
                reason="recorder_exited"
    except KeyboardInterrupt:
        reason="user_ctrl_c"
        print("\nFinishing WAV after Ctrl+C ...",flush=True)
    finally:
        if process:
            terminate_group(process)

    exit_code=process.returncode if process else None
    error_excerpt=recorder_stderr_excerpt(stderr)
    size=wav.stat().st_size if wav.exists() else 0
    meta=None
    wav_error=None
    if wav.exists():
        try:
            meta=verify_wav(wav)
        except (RuntimeError,EOFError,ValueError, OSError, wave.Error) as exc:
            wav_error=str(exc)
    else:
        wav_error="pw-record created no WAV file"
    # A valid header with no PCM data is NOT a good recording.
    frames=meta["frames"] if meta else 0
    success=(meta is not None and frames>=RATE and
             reason!="no_audio_bytes_after_startup" and
             not (reason=="recorder_exited" and exit_code!=0))
    diagnostics={
        "recorder_returncode":exit_code,
        "recorder_elapsed_wall_sec":round(time.monotonic()-beginning,3),
        "recorded_file_bytes":size,
        "failure_description":(
            "Not enough PCM frames; source may not have connected or recorder failed."
            if frames<RATE else
            "Recorder failure or streaming startup did not pass readiness."
            if not success else None),
        "wav_parse_error":wav_error,
        "stderr_excerpt":error_excerpt if not success else None
    }
    manifest={
        "schema":"cuewire-M4-raw-capture-v2",
        "status":"recorded_unprocessed_raw" if success else "rejected_incomplete_capture",
        "mic_node_name":args.mic,
        "mic_pactl_state_at_start":device.get("state"),
        "mic_pactl_sample_spec_at_start":device.get("sample_spec"),
        "start_utc":start,"end_utc":datetime.now(timezone.utc).isoformat(),
        "stop_reason":reason,
        "recorder":"pw-record",
        "recorder_command":command[:-1]+["raw.wav"],
        "wav":"raw.wav" if wav.exists() else None,
        "wav_sha256":sha256(wav) if wav.exists() else None,
        "measurements":meta,
        "diagnostics":diagnostics,
        "explicit_processing":{
            "denoise":False,"aec":False,"eq":False,"compression":False,
            "manual_gain_normalization":False,"playback":False,
            "virtual_microphone":False
        },
        "limits":[
            "Software RAW does not prove the USB microphone firmware lacks DSP.",
            "A sample format cannot certify actual hardware ADC bit depth.",
            "No auto source switch; a disconnected mic will fail the capture.",
            "No audio data or microphone logs were uploaded."
        ]
    }
    manifest_path=out/"manifest.json"
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,
                          indent=2,sort_keys=True)+"\n",encoding="utf-8")
    if not success:
        print("REJECTED: incomplete microphone capture, source WAV preserved.",
              file=sys.stderr)
        print("Stop reason:",reason,"; frames:",frames,
              "; file bytes:",size,"; recorder exit code:",exit_code,
              file=sys.stderr)
        print("pw-record stderr (last lines):\n"+error_excerpt,
              file=sys.stderr)
        print("Diagnostic manifest:",manifest_path,file=sys.stderr)
        raise SystemExit(3)
    print("PASS: RAW WAV kept unchanged; no stage processing or upload.")
    print("Length:",meta["duration_s"],"seconds; peak:",meta["peak_dbfs"],
          "dBFS; RMS:",meta["rms_dbfs"],"dBFS; clipped samples:",
          meta["clipped_samples"])
    print("SHA-256:",manifest["wav_sha256"])
    print("Manifest:",manifest_path)
    print("Clipping note: clipped_samples should be zero on a good take.")

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("inspect",help="List source names; no capture")
    rec=sub.add_parser("record",help="Explicitly open ONE physical mic and capture a new WAV")
    rec.add_argument("--mic",required=True,help="Exact pactl node.name of physical microphone")
    rec.add_argument("--out",required=True,type=Path,
                     help="NEW folder inside local .audit/voice-captures/")
    rec.add_argument("--max-seconds",type=int,default=150)
    rec.add_argument("--startup-check-sec",type=int,default=7,
                     help="Fail early if less than 0.5 second of audio was written after N seconds")
    args=p.parse_args()
    if args.command=="inspect":
        print(json.dumps({"physical_sources":[x for x in get_sources()
                            if not x["name"].endswith(".monitor")]},
                         ensure_ascii=False,indent=2))
    else:
        if not 10<=args.max_seconds<=300:
            p.error("Max duration must be 10..300 seconds")
        if not 3<=args.startup_check_sec<=20 or args.startup_check_sec>=args.max_seconds:
            p.error("startup-check-sec must be 3..20 and less than max-seconds")
        record(args)


if __name__=="__main__":
    main()
