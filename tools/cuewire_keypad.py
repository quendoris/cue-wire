#!/usr/bin/env python3
"""Foreground NumPad event adapter: one Hyprland socket2 listener, JSONL logs.

No evdev access, audio, service, keybind registration, or subprocess per key.
Lua bindings send custom>>cuewire_keypad_v1/Kx/down|up events via hl.dsp.event.
"""
import argparse
import json
import os
import re
import select
import socket
import subprocess
import sys
import time
from pathlib import Path

# XKB physical code = Linux evdev keypad keycode + 8.
KEYS={"K0":90,"K1":87,"K2":88,"K3":89,"K4":83,"K5":84,"K6":85,
      "K7":79,"K8":80,"K9":81,"DOT":91,"ENTER":104,"PLUS":86,
      "MINUS":82,"STAR":63,"SLASH":106}
PREFIX="cuewire_keypad_v1/"
LONG_MS=550
DOUBLE_MS=290


def parse_socket_event(line):
    if not line.startswith("custom>>"+PREFIX):
        return None
    parts=line[len("custom>>"+PREFIX):].strip().split("/")
    if len(parts)==2 and parts[0] in KEYS and parts[1] in ("down","up"):
        return tuple(parts)
    return None


def render(device=None,game=False):
    if device is not None and not re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}",device):
        raise ValueError("Invalid exact Hyprland device name")
    lines=["-- CueWire NumPad H2: logging only; no soundboard actions.",
           "-- Review conflict check; bindings are NOT registered automatically.",
           "-- XKB code:N distinguishes the physical NumPad from number row.",
           "-- Verify physical codes with wev; NumLock key is NOT captured.",
           "-- This dispatcher never spawns a process per keystroke."]
    for key,code in KEYS.items():
        for phase in ("down","up"):
            flags=["ignore_mods = true"]
            if game:flags.append("dont_inhibit = true")
            if device:
                flags.append('device = { inclusive = true, list = { "'+device+'" } }')
            if phase=="up": flags.append("release = true")
            lines.append('hl.bind("code:'+str(code)+'", hl.dsp.event("'+PREFIX+
                         key+'/'+phase+'"), { '+", ".join(flags)+' })')
    return "\n".join(lines)+"\n"


def conflicts(data):
    if not isinstance(data,list):raise ValueError("Expected list of Hyprland bindings")
    symbols={"KP_"+str(n) for n in range(10)}
    symbols.update({"KP_DECIMAL","KP_DELETE","KP_ENTER","KP_ADD",
                    "KP_SUBTRACT","KP_MULTIPLY","KP_DIVIDE"})
    result=[]
    for row in data:
        if not isinstance(row,dict):continue
        key=str(row.get("key","")).upper()
        code=row.get("keycode",0)
        try:
            c=int(str(code),0) if isinstance(code,str) else int(code)
        except (ValueError,TypeError):c=0
        try:
            via_key=key.startswith("CODE:") and int(key[5:]) in KEYS.values()
        except ValueError:via_key=False
        if c in KEYS.values() or via_key or key in symbols:
            result.append({name:row.get(name) for name in
                           ("key","keycode","modmask","dispatcher","arg")})
    return result


class Gestures:
    """Pure state machine. Inputs (key,phase,monotonic_ms), output JSON dicts."""
    def __init__(self,emit,long_ms=LONG_MS,double_ms=DOUBLE_MS):
        if not 100<=long_ms<=5000 or not 100<=double_ms<=1500:
            raise ValueError("Invalid long/double window")
        self.emit=emit;self.long_ms=long_ms;self.double_ms=double_ms
        self.now=0;self.down={};self.pending={};self.chord=None
        self.chord_at=0;self.used=set()

    def log(self,kind,event,keys,**data):
        self.emit(dict(kind=kind,event=event,keys=list(keys),
                       at_ms=self.now,**data))

    def tick(self,at):
        if at<self.now:raise ValueError("Nonmonotonic event clock")
        self.now=at
        for key,end in list(self.pending.items()):
            if at-end>self.double_ms:
                del self.pending[key]
                self.log("gesture","single",[key],resolution_delay_ms=at-end)
        for key,state in list(self.down.items()):
            if not state["long"] and not state["chord"] and at-state["at"]>=self.long_ms:
                state["long"]=True
                self.log("gesture","hold_start",[key],held_ms=at-state["at"])

    def input(self,key,phase,at):
        if key not in KEYS or phase not in ("down","up"):
            raise ValueError("Unexpected keypad input")
        self.tick(at)
        self.log("raw",phase,[key])
        if phase=="down":
            if key in self.down:
                self.log("diagnostic","repeat_ignored",[key])
                return
            second=(key in self.pending and at-self.pending[key]<=self.double_ms)
            self.pending.pop(key,None)
            self.down[key]={"at":at,"long":False,"chord":False,"second":second}
            if len(self.down)>1:
                ordered=tuple(sorted(self.down))
                if self.chord:
                    self.log("gesture","chord_end",self.chord,held_ms=at-self.chord_at,
                             changed=True)
                self.chord=ordered;self.chord_at=at
                for k,s in self.down.items():
                    s["chord"]=True
                    self.used.add(k)
                    self.pending.pop(k,None)
                self.log("gesture","chord_start",ordered)
        else:
            state=self.down.pop(key,None)
            if state is None:
                self.log("diagnostic","orphan_up",[key])
                return
            elapsed=at-state["at"]
            if self.chord and key in self.chord:
                self.log("gesture","chord_end",self.chord,held_ms=at-self.chord_at)
                self.chord=None
            if state["chord"] or key in self.used:
                self.used.discard(key)
            elif state["long"]:
                self.log("gesture","hold_end",[key],held_ms=elapsed)
            elif state["second"]:
                self.log("gesture","double",[key],last_press_ms=elapsed)
            else:
                self.pending[key]=at

    def reset(self,at,reason):
        self.tick(at)
        if self.chord:self.log("diagnostic","chord_cancel",self.chord,reason=reason)
        if self.down:self.log("diagnostic","pressed_discarded",sorted(self.down),reason=reason)
        self.down.clear();self.pending.clear();self.chord=None;self.used.clear()
        self.log("system","reset",[],reason=reason)


def socket_path():
    runtime=os.environ.get("XDG_RUNTIME_DIR")
    instance=os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not runtime or not Path(runtime).is_dir() or not instance:
        raise RuntimeError("Hyprland environment missing: XDG_RUNTIME_DIR / HYPRLAND_INSTANCE_SIGNATURE")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}",instance):
        raise RuntimeError("Invalid Hyprland instance signature")
    return Path(runtime)/"hypr"/instance/".socket2.sock"


def listen(engine,path):
    start=time.monotonic()
    buffer=b""
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(str(path))
        sock.setblocking(False)
        engine.log("system","connected",[],source="hyprland_socket2")
        while True:
            engine.tick(int((time.monotonic()-start)*1000))
            ready,_,_=select.select([sock],[],[],.025)
            if not ready:continue
            data=sock.recv(8192)
            if not data:
                engine.reset(engine.now,"socket_disconnected")
                break
            buffer+=data
            if len(buffer)>65536:
                engine.reset(engine.now,"socket_overflow")
                raise RuntimeError("Socket2 data overflow")
            while b"\n" in buffer:
                line,buffer=buffer.split(b"\n",1)
                if len(line)>512:continue
                text=line.decode("utf-8","replace")
                if text=="configreloaded>>":
                    engine.reset(engine.now,"hyprland_config_reloaded")
                else:
                    evt=parse_socket_event(text)
                    if evt:engine.input(*evt,int((time.monotonic()-start)*1000))


def simulate(engine,script):
    if not isinstance(script,list):raise ValueError("Simulation must be JSON array")
    for ev in script:
        if not isinstance(ev,dict) or not isinstance(ev.get("at_ms"),int):
            raise ValueError("Bad event record")
        engine.input(ev.get("key"),ev.get("phase"),ev["at_ms"])
    engine.tick(engine.now+max(engine.long_ms,engine.double_ms)+100)
    engine.reset(engine.now,"simulation_finished")


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest="mode",required=True)
    render_arg=sub.add_parser("render")
    render_arg.add_argument("--device",help="Exact Hyprland name of dedicated NumPad")
    render_arg.add_argument("--game",action="store_true",help="Bypass game shortcut inhibition")
    check=sub.add_parser("check")
    check.add_argument("--fixture",type=Path)
    run=sub.add_parser("run",help="Foreground listener; Ctrl+C stops logging")
    sim=sub.add_parser("simulate")
    sim.add_argument("script",type=Path)
    for p in (run,sim):
        p.add_argument("--log-jsonl",type=Path,help="Optional new log file (never overwrite)")
        p.add_argument("--long-ms",type=int,default=LONG_MS)
        p.add_argument("--double-ms",type=int,default=DOUBLE_MS)
    args=ap.parse_args()
    if args.mode=="render":
        print(render(args.device,args.game),end="")
        return
    if args.mode=="check":
        if args.fixture:
            binds=json.loads(args.fixture.read_text())
        else:
            result=subprocess.run(["hyprctl","-j","binds"],capture_output=True,text=True,timeout=5)
            if result.returncode:raise RuntimeError("hyprctl binds failed: "+result.stderr[-500:])
            binds=json.loads(result.stdout)
        hits=conflicts(binds)
        if hits:
            print(json.dumps(hits,ensure_ascii=False,indent=2))
            raise SystemExit("Conflict: NumPad is already bound; no changes made")
        print("PASS: no conflicting NumPad bindings; no changes made")
        return
    fp=None
    if args.log_jsonl:
        args.log_jsonl.parent.mkdir(parents=True,exist_ok=True)
        fp=args.log_jsonl.open("x",encoding="utf-8",buffering=1)
    def emit(obj):
        line=json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(",",":"))
        print(line,flush=True)
        if fp:fp.write(line+"\n");fp.flush()
    engine=Gestures(emit,args.long_ms,args.double_ms)
    try:
        if args.mode=="simulate":
            simulate(engine,json.loads(args.script.read_text()))
        else:
            print("CueWire H2 log-only foreground; Ctrl+C to stop",file=sys.stderr)
            listen(engine,socket_path())
    except KeyboardInterrupt:
        engine.reset(engine.now,"ctrl_c")
    finally:
        if fp:fp.close()


if __name__=="__main__":
    main()
