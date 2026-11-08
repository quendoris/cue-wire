#!/usr/bin/env python3
"""CueWire H2: hyprlang legacy live keypad adapter. Foreground; log-only.

Hyprland's legacy (hyprlang) config provider does NOT support hyprctl eval.
Use temporary "hyprctl keyword bindi / bindri" registrations, forwarding
fixed key events through a tiny on-demand UNIX datagram bridge.

No config file edits, no audio processing, no daemon. Every owned bind is
verified before removal; if an unrelated bind appears, refuse destructive
unbind. A crash may leave runtime binds: run "restore".
"""
import argparse
import errno
import json
import os
from pathlib import Path
import re
import select
import shlex
import socket
import stat
import subprocess
import sys
import time

import cuewire_keypad as core
import cuewire_keypad_emit as emitter


def runctl(*args, timeout=6):
    p=subprocess.run(["hyprctl",*map(str,args)],text=True,
                     capture_output=True,timeout=timeout)
    if p.returncode or p.stdout.strip()!="ok":
        raise RuntimeError("hyprctl "+str(args[:2])+" failed: "+
                           (p.stderr or p.stdout)[-700:])


def binds():
    p=subprocess.run(["hyprctl","-j","binds"],capture_output=True,
                     text=True,timeout=6)
    if p.returncode:
        raise RuntimeError("Cannot examine keybinds: "+p.stderr[-600:])
    value=json.loads(p.stdout)
    if not isinstance(value,list):
        raise RuntimeError("Malformed binding inspection output")
    return value


def code_for_row(row):
    if not isinstance(row,dict):
        return None
    try:
        raw=row.get("keycode",0)
        num=int(str(raw),0) if isinstance(raw,str) else int(raw)
        if num in core.KEYS.values():
            return num
    except (TypeError,ValueError):
        pass
    key=str(row.get("key",""))
    if key.lower().startswith("code:"):
        try:
            num=int(key[5:])
            return num if num in core.KEYS.values() else None
        except ValueError:
            return None
    return None


def is_our_arg(row):
    arg=str(row.get("arg",""))
    return (row.get("dispatcher")=="exec"
            and "cuewire_keypad_emit.py" in arg
            and re.search(r"\b(?:K[0-9]|DOT|ENTER|PLUS|MINUS|STAR|SLASH) (?:down|up) [a-zA-Z0-9-]{4,96}$",arg) is not None)


def key_arg(key,phase,owner):
    helper=Path(emitter.__file__).resolve()
    return " ".join(shlex.quote(item) for item in
                    (sys.executable,str(helper),key,phase,owner))


def keyword(key,phase,owner,game=False,transparent=False):
    code=core.KEYS[key]
    # "i": ignore currently held modifiers; "r": release only
    # "p": bypass inhibitor, explicit opt-in for fullscreen applications.
    suffix=("r" if phase=="up" else "")+"i"+("p" if game else "")+("t" if transparent else "")
    runctl("keyword","bind"+suffix,
           f", code:{code}, exec, {key_arg(key,phase,owner)}")


def expected_args(code,owner):
    key=next(name for name,c in core.KEYS.items() if c==code)
    return {key_arg(key,phase,owner) for phase in ("down","up")}


def owned_codes(snapshot,owner=None):
    groups={}
    for row in snapshot:
        code=code_for_row(row)
        if code is not None:groups.setdefault(code,[]).append(row)
    result={}
    blocked=[]
    for code,entries in groups.items():
        known=[r for r in entries if is_our_arg(r)]
        if not known:
            continue
        expected=expected_args(code,owner) if owner else None
        if owner and not any(str(row.get("arg","")) in expected for row in known):
            continue
        # Unbind is key-scoped on legacy Hyprland, not per-binding.
        # Prevent unbinding any other person's potentially concurrent binds.
        if len(known)!=len(entries) or (owner and
                any(str(row.get("arg","")) not in expected for row in entries)):
            blocked.append(code)
            continue
        result[code]=known
    return result,blocked


def remove_owned(owner=None):
    available,blocked=owned_codes(binds(),owner)
    if blocked:
        raise RuntimeError("REFUSED to unbind codes with unrelated/mixed binds: "+
                           str(blocked)+". Inspect hyprctl -j binds.")
    removed=[]
    for code in sorted(available):
        # Legacy keyword unbind removes both press/release bindings
        # for exactly the scanned keypad code with no foreign occupants.
        runctl("keyword","unbind",f", code:{code}")
        removed.append(code)
    return removed


def remove_stale_socket(sockpath):
    """Delete only an owned, unserved UNIX datagram socket from our namespace."""
    try:
        info=os.lstat(sockpath)
    except FileNotFoundError:
        return False
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError("Refusing to delete a non-owned / non-socket path")
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as probe:
        probe.settimeout(.10)
        try:
            probe.sendto(b"",str(sockpath))
        except OSError as exc:
            if exc.errno not in (errno.ECONNREFUSED,errno.ENOENT):
                raise RuntimeError("Socket status uncertain; refusing deletion") from exc
        else:
            raise RuntimeError("Socket is alive; refusing deletion")
    now=os.lstat(sockpath)
    if (info.st_dev,info.st_ino)!=(now.st_dev,now.st_ino):
        raise RuntimeError("Socket changed during probe; refusing deletion")
    os.unlink(sockpath)
    return True


def listen_dgram(engine,server,owner,open_log):
    open_log()
    engine.log("system","connected",[],source="hyprlang_datagram_bridge")
    started=time.monotonic()
    while True:
        engine.tick(int((time.monotonic()-started)*1000))
        ready,_,_=select.select([server],[],[],.025)
        if not ready:
            continue
        data=server.recv(256)
        if len(data)>128:
            continue
        try:
            session,key,phase=data.decode("ascii").split("/")
        except (UnicodeError,ValueError):
            continue
        if session==owner and key in core.KEYS and phase in ("down","up"):
            engine.input(key,phase,int((time.monotonic()-started)*1000))


def begin_session(logfile,long_ms,double_ms,game=False,device=None,transparent=False):
    if device:
        raise RuntimeError(
            "--device is NOT supported with hyprlang legacy binds. "
            "It can isolate keypad physical KEYCODES from the number row, "
            "but not two separate keyboards with identical keypad keys.")
    if logfile is not None and logfile.exists():
        raise RuntimeError("Log already exists; pick H2-002.jsonl or another new path")
    # Verify a clean namespace before any bind is registered.
    core.ensure_no_key_conflicts()
    address=emitter.socket_path()
    if address.exists() or address.is_symlink():
        raise RuntimeError("A keypad socket already exists: "+str(address)+
                           ". Run restore only if the older process has stopped.")
    owner=str(os.getpid())+"-"+str(time.monotonic_ns())
    fp=None
    attached=False
    all_registered=False
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as server:
            server.bind(str(address))
            os.chmod(address,0o600)
            attached=True
            for key in core.KEYS:
                for phase in ("down","up"):
                    keyword(key,phase,owner,game,transparent)
            all_registered=True
            # Confirm the compositor really registered the expected
            # 32 binds before creating a log or claiming readiness.
            registered,blocked=owned_codes(binds(),owner)
            if blocked or len(registered)!=len(core.KEYS) or any(
                    len(registered.get(c,[]))!=2 for c in core.KEYS.values()):
                raise RuntimeError("Legacy Hyprland did not expose all 32 "
                                   "expected binds; aborting activation")
            def open_log():
                nonlocal fp
                if logfile:
                    logfile.parent.mkdir(parents=True,exist_ok=True)
                    fp=logfile.open("x",encoding="utf-8",buffering=1)
            def emit(event):
                line=json.dumps(event,sort_keys=True,ensure_ascii=False,
                                separators=(",",":"))
                print(line,flush=True)
                if fp:fp.write(line+"\n");fp.flush()
            engine=core.Gestures(emit,long_ms,double_ms)
            old_handler=__import__("signal").signal(
                __import__("signal").SIGTERM,
                lambda _s,_f: (_ for _ in ()).throw(KeyboardInterrupt()))
            try:
                print("CueWire H2: hyprlang legacy keyboard events, LOG ONLY; "
                      "Ctrl+C to stop.",file=sys.stderr)
                listen_dgram(engine,server,owner,open_log)
            except KeyboardInterrupt:
                engine.reset(engine.now,"ctrl_c_or_sigterm")
            finally:
                __import__("signal").signal(__import__("signal").SIGTERM,old_handler)
    finally:
        # Even if the Nth keyword call fails, clean up the preceding
        # verified binds. Do not let a cleanup failure hide the main error.
        try:
            removed=remove_owned(owner)
            if removed: print("Removed owned legacy NumPad keys:",removed,file=sys.stderr)
        except Exception as exc:
            print("WARNING: legacy keypad bindings may remain. "
                  "Run: python3 tools/cuewire_keypad.py restore. "
                  +str(exc),file=sys.stderr)
        if attached:
            try:
                # This process owns the bound socket; it is closed when
                # the with-block unwinds, so only unlink after verification.
                remove_stale_socket(address)
            except Exception as exc:
                print("WARNING: keypad IPC socket may remain: "+str(exc),
                      file=sys.stderr)
        if fp:fp.close()


def restore():
    removed=remove_owned()
    if removed:
        print("Removed only verified CueWire H2 legacy key codes:",removed)
    else:
        print("No owned CueWire H2 legacy binds to remove")
    address=emitter.socket_path()
    if address.exists() and remove_stale_socket(address):
        print("Removed unserved CueWire H2 datagram socket")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=("run","restore"))
    p.add_argument("--log-jsonl",type=Path)
    p.add_argument("--long-ms",type=int,default=core.LONG_MS)
    p.add_argument("--double-ms",type=int,default=core.DOUBLE_MS)
    p.add_argument("--game",action="store_true")
    p.add_argument("--transparent",action="store_true",
                   help="EXPERIMENTAL transparent press+release binds to probe key shadowing")
    p.add_argument("--device")
    args=p.parse_args()
    if args.mode=="restore":
        restore()
    else:
        begin_session(args.log_jsonl,args.long_ms,args.double_ms,
                      game=args.game,device=args.device,transparent=args.transparent)


if __name__=="__main__":
    main()
