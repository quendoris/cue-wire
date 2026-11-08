#!/usr/bin/env python3
"""CueWire M4/R0: explicitly named physical ALSA capture, 48kHz mono PCM16.

Manual foreground command only. No PipeWire route/default/source changes,
no music/audio monitoring, services, virtual mic, AEC or postprocessing.
Creates an immutable private .audit take and SHA-256 manifest.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

from voice_raw_capture import (safe_dir,verify_wav,sha256,terminate_group,
                               child_ownership)

ALSA_RE=re.compile(r"^hw:(\d{1,3}),(\d{1,3})$")
CARD_RE=re.compile(r"^[A-Za-z0-9_]{1,64}$")


def physical_card(device,expected_id):
    """Verify that hw:C,D still points to the chosen actual ALSA capture card."""
    m=ALSA_RE.fullmatch(device)
    if not m or not CARD_RE.fullmatch(expected_id):
        raise ValueError("Use --device hw:C,D and --expect-card-id exact ALSA card ID")
    card,dev=map(int,m.groups())
    proc=subprocess.run(["arecord","-l"],capture_output=True,text=True,
                        timeout=7,env={**os.environ,"LC_ALL":"C"})
    if proc.returncode:
        raise RuntimeError("Cannot verify capture hardware: "+proc.stderr[-600:])
    lines=proc.stdout.splitlines()
    section=[]
    started=False
    for line in lines:
        group=re.match(r"^card\s+(\d+):\s+(\w+)\s+\[(.*?)\]",line)
        if group:
            if started:
                break
            if int(group.group(1))==card:
                started=True
                if group.group(2)!=expected_id:
                    raise RuntimeError("ALSA card has changed! Expected "+
                                       expected_id+", found "+group.group(2))
                section.append(line)
        elif started:
            section.append(line)
    if not started or not any(re.search(r"\bdevice\s+"+str(dev)+r"\s*:",line)
                              for line in section):
        raise RuntimeError("Expected ALSA physical card/device not present: "+
                           device+" / "+expected_id)
    return {"card":card,"device":dev,"card_id":expected_id,
            "arecord_listing":"\n".join(section)[:700]}


def excerpt(path):
    if not path.is_file():return "<arecord stderr log missing>"
    s=path.read_text(encoding="utf-8",errors="replace")
    return s[-2500:] if s.strip() else "<arecord stderr empty>"


def record(args):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Linux parent-death guard required")
    if not shutil.which("arecord"):
        raise RuntimeError("arecord not installed")
    hardware=physical_card(args.device,args.expect_card_id)
    out=safe_dir(args.out)
    out.mkdir(parents=True,mode=0o700)
    wav=out/"raw.wav"
    errpath=out/"arecord.stderr.log"
    argv=["arecord","-D",args.device,"-f","S16_LE","-c","1",
          "-r","48000","-t","wav","-d",str(args.max_seconds),str(wav)]
    started=datetime.now(timezone.utc).isoformat()
    begin=time.monotonic()
    process=None
    stop_reason="unknown"
    ready=False
    print("CueWire M4 ALSA: verified",args.device,"=",args.expect_card_id,flush=True)
    print("New private RAW WAV:",wav,flush=True)
    print("One foreground capture, NO routing changes or mic monitoring.",flush=True)
    print("Start with 5s silence. Press Ctrl+C to finish.",flush=True)
    try:
        with errpath.open("x",encoding="utf-8") as errors:
            parent=os.getpid()
            process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,stderr=errors,
                    start_new_session=True,
                    preexec_fn=lambda:child_ownership(parent))
            # Built-in ALSA -d duration is the primary stop; +3s grace
            # accommodates device initialization and WAV finalization.
            cutoff=begin+args.max_seconds+3
            while process.poll() is None:
                elapsed=time.monotonic()-begin
                if not ready and elapsed>=args.startup_check_sec:
                    size=wav.stat().st_size if wav.exists() else 0
                    if size<48000:
                        stop_reason="no_pcm_after_startup"
                        print("ERROR: ALSA produced <0.5 seconds PCM after "+
                              str(args.startup_check_sec)+" seconds.",
                              file=sys.stderr,flush=True)
                        break
                    ready=True
                    print("READY: ALSA audio bytes are arriving.",flush=True)
                if time.monotonic()>=cutoff:
                    stop_reason="safety_time_limit"
                    break
                time.sleep(.08)
            else:
                stop_reason="arecord_completed"
    except KeyboardInterrupt:
        stop_reason="user_ctrl_c"
        print("\nFinishing ALSA WAV ...",flush=True)
    finally:
        if process:
            terminate_group(process)

    exit_code=process.returncode if process else None
    meta=None
    failure=None
    if wav.is_file():
        try:
            meta=verify_wav(wav)
        except Exception as e:
            failure="WAV verification: "+str(e)
    else:
        failure="ALSA recorder created no WAV"
    frames=meta.get("frames",0) if meta else 0
    succeeded=(frames>=48000 and stop_reason!="no_pcm_after_startup"
               and (stop_reason=="user_ctrl_c" or exit_code==0))
    manifest={
        "schema":"cuewire-M4-raw-alsa-v1",
        "status":"recorded_unprocessed_raw" if succeeded else
                 "rejected_incomplete_capture",
        "source":"physical_alsa_hw",
        "device":args.device,
        "card_id":args.expect_card_id,
        "card_listing":hardware["arecord_listing"],
        "command":["arecord","-D",args.device,"-f","S16_LE","-c","1",
                   "-r","48000","-t","wav","-d",str(args.max_seconds),"raw.wav"],
        "start_utc":started,"end_utc":datetime.now(timezone.utc).isoformat(),
        "stop_reason":stop_reason,
        "recorder_returncode":exit_code,
        "wall_seconds":round(time.monotonic()-begin,3),
        "raw_wav":"raw.wav" if wav.is_file() else None,
        "raw_sha256":sha256(wav) if wav.is_file() else None,
        "file_bytes":wav.stat().st_size if wav.is_file() else None,
        "measurements":meta,
        "error":failure or (excerpt(errpath) if not succeeded else None),
        "processing":{"aec":False,"denoise":False,"eq":False,
                      "compression":False,"virtual_mic":False,
                      "normalization":False,"monitoring":False},
        "notes":[
            "Captured through ALSA hw device; PipeWire capture was nonfunctional.",
            "Hardware firmware may itself apply undocumented DSP.",
            "No ALSA or PipeWire defaults were modified.",
            "The complete original WAV is private and not uploaded."
        ]
    }
    (out/"manifest.json").write_text(json.dumps(
        manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n",
        encoding="utf-8")
    if not succeeded:
        print("REJECTED:",stop_reason,"frames:",frames,"arecord rc:",exit_code,
              "\n"+excerpt(errpath),
              "\nManifest:",out/"manifest.json",file=sys.stderr,flush=True)
        raise SystemExit(3)
    print("PASS: original WAV preserved; frames:",frames,
          "duration:",meta["duration_s"],"s",flush=True)
    print("Peak:",meta["peak_dbfs"],"dBFS, RMS:",meta["rms_dbfs"],
          "dBFS, clipped:",meta["clipped_samples"],flush=True)
    print("SHA256:",manifest["raw_sha256"],flush=True)
    print("Manifest:",out/"manifest.json",flush=True)


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--device",default="hw:0,0",
                   help="Actual direct ALSA capture hw:C,D, not default or plughw")
    p.add_argument("--expect-card-id",default="MICAudio97i",
                   help="Fail closed if the card occupying C,D changed")
    p.add_argument("--out",type=Path,required=True,
                   help="New subdirectory inside /data/cue-wire/.audit/")
    p.add_argument("--max-seconds",type=int,default=150)
    p.add_argument("--startup-check-sec",type=int,default=7)
    args=p.parse_args()
    if not 10<=args.max_seconds<=300:
        p.error("max-seconds must be between 10 and 300")
    if not 3<=args.startup_check_sec<=20 or args.startup_check_sec>=args.max_seconds:
        p.error("startup-check-sec must be 3..20 and < max-seconds")
    record(args)


if __name__=="__main__":
    main()
