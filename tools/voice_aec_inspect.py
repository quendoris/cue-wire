#!/usr/bin/env python3
"""CueWire M5: READ-ONLY PipeWire/Pulse sink/mic/reference discovery.

Lists the user's existing named playback sinks and source monitors.
DOES NOT open streams or capture samples; no defaults, modules,
services, routes or configuration files are modified.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def pactl(*args):
    env=os.environ.copy()
    env["LC_ALL"]="C"
    p=subprocess.run(["pactl",*args],capture_output=True,text=True,
                     timeout=8,env=env,check=False)
    if p.returncode:
        raise RuntimeError("Read-only pactl "+str(args)+" failed: "+p.stderr[-700:])
    if len(p.stdout)>2_000_000:
        raise RuntimeError("Unexpectedly large pactl response")
    return p.stdout


def short_nodes(text,kind):
    out=[]
    for n,line in enumerate(text.splitlines(),1):
        parts=line.split("\t")
        if len(parts)<4:
            # pactl list short uses tabs; failing closed is preferable to
            # inventing a channel/device mapping from partial output.
            if line.strip():
                raise ValueError("Malformed pactl "+kind+" line "+str(n))
            continue
        ident,name,driver,form=parts[:4]
        if not ident.isdigit() or len(name)>512:
            raise ValueError("Unexpected pactl "+kind+" ID/name on line "+str(n))
        out.append({"id":int(ident),"name":name,"driver":driver,
                    "sample_spec":form,
                    "state":parts[4].strip() if len(parts)>4 else None})
    return out


def defaults(info):
    found={}
    for line in info.splitlines():
        for label,key in (("Default Sink:","default_sink"),
                          ("Default Source:","default_source"),
                          ("Server Name:","server_name")):
            if line.startswith(label):
                found[key]=line.partition(":")[2].strip()
    return found


def inspect():
    sinks=short_nodes(pactl("list","short","sinks"),"sinks")
    sources=short_nodes(pactl("list","short","sources"),"sources")
    info=defaults(pactl("info"))
    monitors=[]
    for source in sources:
        for sink in sinks:
            if source["name"]==sink["name"]+".monitor":
                monitors.append({"sink":sink["name"],
                                 "monitor_source":source["name"],
                                 "sink_id":sink["id"],
                                 "source_id":source["id"]})
    return {"schema":"cuewire-M5-echo-reference-discovery-v1",
            "mode":"read-only",
            **info,
            "output_sinks":sinks,
            "input_sources":[s for s in sources
                             if s["name"] not in {m["monitor_source"] for m in monitors}],
            "playback_monitor_candidates":monitors,
            "other_sources":[s for s in sources
                             if s["name"].endswith(".monitor") and
                             s["name"] not in {m["monitor_source"] for m in monitors}],
            "warnings":[
                "No microphone or playback streams were opened or recorded.",
                "The DEFAULT output sink is not necessarily the loudspeaker used in games.",
                "Only monitor sources with exact <sink>.monitor name are auto-matched.",
                "An available monitor node is not yet proof of synchronized AEC reference.",
                "Use explicit physical mic/speaker names before AEC capture; no routing changed."
            ]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,
                   help="Optional fresh LOCAL JSON report; existing files never overwritten")
    args=p.parse_args()
    result=inspect()
    text=json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n"
    if args.out:
        if args.out.exists():
            p.error("Report exists; refusing to overwrite.")
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.open("x",encoding="utf-8").write(text)
        print("Created report (read-only PipeWire metadata):",args.out,file=sys.stderr)
    print(text,end="")
    if not result["playback_monitor_candidates"]:
        print("NOTE: no exact sink.monitor candidate. Do not guess playback reference.",
              file=sys.stderr)


if __name__=="__main__":
    main()
