#!/usr/bin/env python3
"""Tiny one-shot bridge for legacy hyprlang keybinds; NO sound or side effects.

Legacy Hyprland has no Lua event dispatcher in its config provider. A
hyprctl keyword bind must therefore use exec. Each invocation sends one
small UNIX datagram to the foreground CueWire keypad adapter, then exits.
"""
import hashlib
import os
from pathlib import Path
import re
import socket
import sys

KEYS = frozenset(tuple("K"+str(i) for i in range(10)) +
                 ("DOT","ENTER","PLUS","MINUS","STAR","SLASH"))
PHASES = frozenset(("down","up"))


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    instance = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not runtime or not instance or not re.fullmatch(r"[a-zA-Z0-9._-]{1,128}",instance):
        raise ValueError("Missing/invalid Hyprland session")
    path = Path(runtime)
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ValueError("Runtime directory is not owned by this user")
    suffix = hashlib.sha256(instance.encode()).hexdigest()[:12]
    return path / ("cuewire-keypad-h2-"+suffix+".sock")


def main():
    if len(sys.argv)!=4:
        raise SystemExit(2)
    _,key,phase,owner=sys.argv
    if key not in KEYS or phase not in PHASES or not re.fullmatch(r"[a-zA-Z0-9-]{4,96}",owner):
        raise SystemExit(2)
    payload=(owner+"/"+key+"/"+phase).encode("ascii")
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
        sock.settimeout(.15)
        try: sock.sendto(payload,str(socket_path()))
        except (OSError,ValueError): raise SystemExit(3)


if __name__=="__main__":
    main()
