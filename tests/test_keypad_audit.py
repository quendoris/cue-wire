#!/usr/bin/env python3
"""No-hardware regression for H2 JSONL health auditor."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from cuewire_keypad_audit import audit


def row(t,kind,event,keys):
    return {"at_ms":t,"kind":kind,"event":event,"keys":keys}


def main():
    healthy=[row(0,"system","connected",[]),
             row(100,"raw","down",["K1"]),
             row(170,"raw","up",["K1"]),
             row(471,"gesture","single",["K1"]),
             row(500,"system","reset",[])]
    v=audit(healthy)
    assert v["diagnostic_status"]=="BALANCED",v
    assert (v["raw_down"],v["raw_up"])==(1,1)
    assert v["gestures"]["single"]==1
    assert not v["unreleased_at_end"]
    unhealthy=[
        row(0,"system","connected",[]),
        row(100,"raw","down",["K1"]),
        row(140,"raw","down",["K2"]),
        row(140,"gesture","chord_start",["K1","K2"]),
        row(2500,"raw","down",["K1"]),
        row(2600,"raw","down",["K3"]),
        row(2600,"gesture","chord_start",["K1","K2","K3"]),
        row(2700,"raw","up",["K3"]),
        row(3000,"raw","up",["K3"]),
        {"at_ms":3400,"kind":"diagnostic","event":"pressed_discarded",
         "keys":["K1","K2"]},
        row(3400,"system","reset",[])
    ]
    v=audit(unhealthy)
    assert v["diagnostic_status"]=="UNRELIABLE_KEY_UP_STREAM",v
    assert v["duplicate_down"]==1
    assert v["unpaired_up"]==1
    assert v["unreleased_at_end"]=={"K1":100,"K2":140}
    assert v["possibly_polluted_chords_over_threshold"]["count"]==1
    assert v["complete_raw_press_release_pairs"]==1
    try:
        audit([row(200,"raw","down",["K1"]),row(100,"raw","up",["K1"])])
        raise AssertionError("Non-monotonic events accepted")
    except ValueError:
        pass
    with tempfile.TemporaryDirectory(prefix="cuewire-H2-audit-") as tmp:
        d=Path(tmp)
        source=d/"raw.jsonl"
        dest=d/"report.json"
        source.write_text("\n".join(json.dumps(x) for x in unhealthy)+"\n")
        cmd=[sys.executable,str(ROOT/"tools/cuewire_keypad_audit.py"),
             str(source),"--out",str(dest)]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=6)
        assert p.returncode==0,p.stderr
        assert json.loads(dest.read_text())["unreleased_at_end"]=={"K1":100,"K2":140}
        assert source.read_text().count("\n")==len(unhealthy)
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=6)
        assert p.returncode!=0,"Auditor overwritten an existing report"
    print("PASS H2 audit: balanced trace, lost release, duplicate, ambiguous chord, JSONL integrity")


if __name__=="__main__":
    main()
