#!/usr/bin/env python3
"""Hardware-free NumPad H2 tests: pure gestures, IPC and binding syntax."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import cuewire_keypad as pad


def assert_one(events,name,keys):
    matches=[x for x in events if x["event"]==name]
    assert len(matches)==1,(name,matches,events)
    assert matches[0]["keys"]==keys,matches


def cases():
    events=[]
    engine=pad.Gestures(events.append,long_ms=550,double_ms=290)
    engine.input("K1","down",0)
    engine.input("K1","up",80)
    engine.tick(400)
    assert_one(events,"single",["K1"])
    events.clear()

    engine.input("K2","down",500)
    engine.input("K2","up",580)
    engine.input("K2","down",700)
    engine.input("K2","up",770)
    engine.tick(1200)
    assert_one(events,"double",["K2"])
    assert not any(x["event"]=="single" for x in events)
    events.clear()

    engine.input("K3","down",1300)
    engine.tick(1900)
    engine.input("K3","up",2150)
    assert_one(events,"hold_start",["K3"])
    assert_one(events,"hold_end",["K3"])
    assert not any(x["event"]=="single" for x in events)
    events.clear()

    engine.input("K4","down",2300)
    engine.input("K5","down",2380)
    engine.input("K5","up",2440)
    engine.input("K4","up",2460)
    engine.tick(3000)
    assert_one(events,"chord_start",["K4","K5"])
    assert_one(events,"chord_end",["K4","K5"])
    assert not any(x["event"] in ("single","double","hold_start")
                   for x in events)
    events.clear()

    # Triple chord changes: old pair ends, new three-key chord starts.
    engine.input("K7","down",3100)
    engine.input("PLUS","down",3110)
    engine.input("K8","down",3120)
    engine.input("K8","up",3250)
    engine.input("PLUS","up",3300)
    engine.input("K7","up",3330)
    engine.tick(4000)
    starts=[x["keys"] for x in events if x["event"]=="chord_start"]
    assert starts==[["K7","PLUS"],["K7","K8","PLUS"]],starts
    assert not any(x["event"]=="single" for x in events)
    events.clear()

    engine.input("DOT","up",4100)
    assert_one(events,"orphan_up",["DOT"])
    events.clear()
    engine.input("K0","down",4200)
    engine.input("K0","down",4210)
    engine.input("K0","up",4230)
    engine.tick(4700)
    assert_one(events,"repeat_ignored",["K0"])
    assert_one(events,"single",["K0"])
    events.clear()
    engine.input("K1","down",4800)
    engine.reset(4810,"device_lost")
    assert any(x["event"]=="pressed_discarded" for x in events)
    assert not engine.down and not engine.pending

    for invalid in ("workspace>>3",
                    "custom>>cuewire_keypad_v1/1/down",
                    "custom>>cuewire_keypad_v1/K1/down/extra",
                    "custom>>cuewire_keypad_v1/K1/delete"):
        assert pad.parse_socket_event(invalid) is None,invalid
    assert pad.parse_socket_event("custom>>cuewire_keypad_v1/K1/up")==("K1","up")
    assert len(pad.KEYS)==16 and len(set(pad.KEYS.values()))==16
    assert pad.KEYS["K0"]!=pad.KEYS["K1"]
    text=pad.render()
    assert text.count("hl.bind(")==32
    assert text.count("release = true")==16
    assert text.count("hl.dsp.event(")==32
    assert "Super" not in text
    assert "exec_cmd" not in text
    assert "code:90" in text and "code:87" in text
    assert "NumLock" in text
    assert "dont_inhibit = true" in pad.render(game=True)
    filt=pad.render(device="my-separate-usb-numpad")
    assert filt.count('list = { "my-separate-usb-numpad" }')==32
    try:
        pad.render(device='bad" }; os.execute("oops")')
        raise AssertionError("invalid device accepted")
    except ValueError:
        pass
    assert pad.conflicts([{"keycode":90,"key":"irrelevant"}])
    assert pad.conflicts([{"key":"KP_1","keycode":0}])
    assert not pad.conflicts([{"keycode":10,"key":"1"}])
    assert not pad.conflicts([{"key":"F1","keycode":0}])
    try:
        pad.conflicts({"keycode":90})
        raise AssertionError("bad hyprctl accepted")
    except ValueError:
        pass
    print("PASS keypad pure: gestures, held keys, double, chord, code isolation")


def live_mock():
    with tempfile.TemporaryDirectory(prefix="cuewire-keypad-") as tmp:
        runtime=Path(tmp)
        target=runtime/"hypr"/"mock-signature"
        target.mkdir(parents=True)
        sockpath=target/".socket2.sock"
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
            listener.bind(str(sockpath))
            listener.listen(1)
            env=os.environ.copy()
            env["XDG_RUNTIME_DIR"]=str(runtime)
            env["HYPRLAND_INSTANCE_SIGNATURE"]="mock-signature"
            out=runtime/"keypad.jsonl"
            proc=subprocess.Popen([
                sys.executable,str(ROOT/"tools/cuewire_keypad.py"),
                "run","--log-jsonl",str(out)],
                env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            listener.settimeout(3)
            try:
                conn,_=listener.accept()
                with conn:
                    conn.sendall(b"workspace>>3\n")
                    conn.sendall(b"custom>>cuewire_keypad_v1/K0/down\n")
                    time.sleep(.035)
                    conn.sendall(b"custom>>cuewire_keypad_v1/K0/up\n")
                    time.sleep(.37)
                stdout,stderr=proc.communicate(timeout=3)
                assert proc.returncode==0,(stdout,stderr)
                lines=[json.loads(line) for line in out.read_text().splitlines()]
                assert_one(lines,"single",["K0"])
                assert not any(x.get("event")=="workspace" for x in lines)
                assert sum(x["kind"]=="raw" for x in lines)==2
                assert any(x["event"]=="reset" for x in lines)
                print("PASS keypad mock socket2: only custom NumPad; log persists foreground only")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=3)


if __name__=="__main__":
    cases()
    live_mock()
