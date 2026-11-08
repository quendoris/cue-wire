#!/usr/bin/env python3
"""Read-only health audit of CueWire H2 event logs. Never touches keyboard.

Checks completeness, duplicated down, unpaired up, hanging buttons and
contaminated chords. A long press is not proof of a missed release:
suspect status is deliberately diagnostic, not an invented key-up.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


def audit(events, *, suspicious_ms=2000):
    active={}
    counts=Counter()
    pressed=Counter()
    released=Counter()
    duplicates=Counter()
    orphan=Counter()
    bad_chords=[]
    times=[]
    stages=[]
    source=None
    for index, ev in enumerate(events,1):
        if not isinstance(ev,dict):
            raise ValueError(f"Non-object JSON event at line {index}")
        kind=ev.get("kind")
        event=ev.get("event")
        t=ev.get("at_ms")
        keys=ev.get("keys",[])
        if not isinstance(t,int) or t<0 or not isinstance(keys,list):
            raise ValueError(f"Malformed event at line {index}")
        if times and t<times[-1]:
            raise ValueError(f"Non-monotonic event clock at line {index}")
        times.append(t)
        counts[f"{kind}/{event}"]+=1
        if event=="connected" and kind=="system":
            source=ev.get("source")
        if kind=="raw":
            if len(keys)!=1 or not isinstance(keys[0],str):
                raise ValueError(f"Bad raw key field at line {index}")
            k=keys[0]
            if event=="down":
                pressed[k]+=1
                if k in active:
                    duplicates[k]+=1
                else:
                    active[k]=t
            elif event=="up":
                released[k]+=1
                if k not in active:orphan[k]+=1
                else:del active[k]
        if kind=="gesture" and event=="chord_start":
            # Unconfirmed physical state: a down older than 2s can be
            # intentionally held. We only label it "possibly polluted".
            old={k:t-active[k] for k in keys if k in active
                 and t-active[k]>=suspicious_ms}
            if old:
                bad_chords.append({"line":index,"at_ms":t,
                                   "possibly_stale_keys":old,
                                   "chord_size":len(keys)})
        if kind=="system" and event=="reset":
            stages.append({"line":index,"at_ms":t,
                           "unreleased_at_reset":dict(active)})
            active.clear()
    unresolved={}
    for stage in stages:
        unresolved.update(stage["unreleased_at_reset"])
    unresolved.update(active)
    clean_presses=sum(pressed.values())-sum(duplicates.values())
    output={
        "schema":"cuewire-keypad-health-audit-v1",
        "event_count":len(events),
        "recording_duration_ms":times[-1]-times[0] if times else 0,
        "transport":source,
        "unique_keys_seen":sorted(set(pressed)|set(released)),
        "raw_down":sum(pressed.values()),
        "raw_up":sum(released.values()),
        "duplicate_down":sum(duplicates.values()),
        "duplicate_down_keys":dict(sorted(duplicates.items())),
        "unpaired_up":sum(orphan.values()),
        "unreleased_at_end":dict(sorted(unresolved.items())),
        "gestures":{
            "single":counts["gesture/single"],
            "double":counts["gesture/double"],
            "hold_start":counts["gesture/hold_start"],
            "hold_end":counts["gesture/hold_end"],
            "chord_start":counts["gesture/chord_start"],
            "chord_end":counts["gesture/chord_end"],
        },
        "possibly_polluted_chords_over_threshold":{
            "threshold_ms":suspicious_ms,
            "count":len(bad_chords),
            "sample":bad_chords[:3],
        },
        "expected_unique_down_count":clean_presses,
        "complete_raw_press_release_pairs":sum(released.values())-sum(orphan.values()),
        "diagnostic_status":"UNRELIABLE_KEY_UP_STREAM" if
            unresolved or orphan or duplicates else "BALANCED",
        "limitations":[
            "A key left down at Ctrl+C may have been physically held.",
            "Repeated down may result from a missed release or a genuine key repeat.",
            "Two-second held keys may be intentional; chords are suspicious, not disproven.",
            "The log cannot identify whether the device, compositor or bridge lost a release.",
        ],
    }
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("jsonl",type=Path)
    p.add_argument("--threshold-ms",type=int,default=2000)
    p.add_argument("--out",type=Path,help="Optional fresh JSON report; never overwrite")
    args=p.parse_args()
    if not 500<=args.threshold_ms<=30000:p.error("--threshold-ms must be 500..30000")
    if not args.jsonl.is_file():p.error("JSONL not found")
    events=[]
    with args.jsonl.open(encoding="utf-8") as f:
        for i,line in enumerate(f,1):
            if line.strip():
                try:events.append(json.loads(line))
                except json.JSONDecodeError as err:raise ValueError(f"Malformed JSONL at line {i}") from err
    report=audit(events,suspicious_ms=args.threshold_ms)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with args.out.open("x",encoding="utf-8") as f:
            json.dump(report,f,ensure_ascii=False,indent=2)
            f.write("\n")


if __name__=="__main__":
    main()
