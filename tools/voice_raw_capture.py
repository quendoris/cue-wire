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
        if meta["frames"]<RATE:
            raise RuntimeError("Recording too short (<1 second) or empty")
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
    rms=math.sqrt(square_sum/seen)/32768
    meta.update({
        "peak_dbfs":round(20*math.log10(max(max_abs/32768,1e-12)),3),
        "rms_dbfs":round(20*math.log10(max(rms,1e-12)),3),
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
    parent=os.getpid()
    process=None
    reason="unexpected"
    print("CueWire M4 RAW capture:",args.mic,flush=True)
    print("Recording into new private file:",wav,flush=True)
    print("No output monitoring, denoising, AEC or audio system changes.",flush=True)
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
    if not wav.exists():
        raise RuntimeError("Recorder created no WAV; see "+str(stderr))
    meta=verify_wav(wav)
    if process and reason=="recorder_exited" and process.returncode!=0:
        raise RuntimeError("Recorder exited unexpectedly. "+str(stderr))
    manifest={
        "schema":"cuewire-M4-raw-capture-v1",
        "status":"recorded_unprocessed_raw",
        "mic_node_name":args.mic,
        "mic_pactl_state_at_start":device.get("state"),
        "mic_pactl_sample_spec_at_start":device.get("sample_spec"),
        "start_utc":start,"end_utc":datetime.now(timezone.utc).isoformat(),
        "stop_reason":reason,
        "recorder":"pw-record",
        "recorder_command":command[:-1]+["raw.wav"],
        "wav":"raw.wav","wav_sha256":sha256(wav),
        "measurements":meta,
        "explicit_processing":{
            "denoise":False,"aec":False,"eq":False,"compression":False,
            "manual_gain_normalization":False,"playback":False,
            "virtual_microphone":False
        },
        "limits":[
            "WAV is the direct software-captured source, not proof the USB mic has no internal DSP.",
            "Physical mic and PipeWire graph may have prior gain, AGC or processing.",
            "A saved sample format cannot prove the ADC's physical bit depth.",
            "No model checkpoint or studio processing was invoked."
        ]
    }
    (out/"manifest.json").write_text(json.dumps(
        manifest,ensure_ascii=False,indent=2,sort_keys=True)+"\n",
        encoding="utf-8")
    print("PASS: RAW WAV kept unchanged; no stage processing or upload.")
    print("Length:",meta["duration_s"],"seconds; peak:",meta["peak_dbfs"],
          "dBFS; RMS:",meta["rms_dbfs"],"dBFS; clipped samples:",
          meta["clipped_samples"])
    print("SHA-256:",manifest["wav_sha256"])
    print("Manifest:",out/"manifest.json")
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
    args=p.parse_args()
    if args.command=="inspect":
        print(json.dumps({"physical_sources":[x for x in get_sources()
                            if not x["name"].endswith(".monitor")]},
                         ensure_ascii=False,indent=2))
    else:
        if not 10<=args.max_seconds<=300:
            p.error("Max duration must be 10..300 seconds")
        record(args)


if __name__=="__main__":
    main()
