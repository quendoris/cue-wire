#!/usr/bin/env python3
"""M7.6 transient Lua hotkeys, explicitly opt-in; no config edits or daemon.

Inside the existing marked scope: install per-session Lua handles.
Outside the scope: release those handles even after scope SIGKILL.
Every dispatcher is tied to the systemd InvocationID, not a reusable
socket name. No keyboard events, keycodes from input, or audio are read.
Hyprland 0.56.2 reports Lua handlers as __lua with opaque registry IDs.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
import sys
import time

import cuewire_keymap as keymap

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=Path(__file__).resolve()
SCOPE="cuewire-hotkeys.scope"
LABEL="CueWire Hotkeys Scoped Foreground"
GLOBAL="__cuewire_dynamic_m76"
PREFIX="CueWire M7.6 "
HEX=re.compile(r"[0-9a-f]{32}\Z")
BITS={"SHIFT":1,"CTRL":4,"ALT":8,"SUPER":64}
NUMPAD_ALIASES={
    "KP_0":"KP_INSERT","KP_1":"KP_END","KP_2":"KP_DOWN",
    "KP_3":"KP_NEXT","KP_4":"KP_LEFT","KP_5":"KP_BEGIN",
    "KP_6":"KP_RIGHT","KP_7":"KP_HOME","KP_8":"KP_UP",
    "KP_9":"KP_PRIOR","KP_DECIMAL":"KP_DELETE",
}


class HotkeyError(RuntimeError):
    pass


def call(argv:list[str],timeout:float=2.5)->str:
    try:
        result=subprocess.run(argv,stdin=subprocess.DEVNULL,
                              capture_output=True,text=True,
                              timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired,UnicodeError) as exc:
        raise HotkeyError("Command unavailable or timed out: "+argv[0]) from exc
    if result.returncode or len(result.stdout)>512*1024:
        raise HotkeyError("Command failed or result too large: "+argv[0])
    return result.stdout.strip()


def bindings()->list[dict]:
    try:
        rows=json.loads(call(["hyprctl","-j","binds"]))
    except ValueError as exc:
        raise HotkeyError("Invalid Hyprland bind JSON") from exc
    if not isinstance(rows,list) or not all(isinstance(x,dict) for x in rows):
        raise HotkeyError("Unrecognized Hyprland bind list")
    return rows


def lua_eval(code:str)->None:
    answer=call(["hyprctl","eval",code])
    if answer!="ok":
        raise HotkeyError("Hyprland Lua eval was not acknowledged: "+answer[:160])


def require_lua()->None:
    info=call(["hyprctl","status"])
    if not re.search(r"(?m)^\s*configProvider:\s*lua\s*$",info):
        raise HotkeyError("Active Hyprland config provider is not Lua")


def invocation()->str:
    data=call(["systemctl","--user","show",SCOPE,
               "--property=Description,Transient,ActiveState,InvocationID"])
    rows=dict(line.split("=",1) for line in data.splitlines() if "=" in line)
    ident=rows.get("InvocationID","")
    if (rows.get("Description")!=LABEL or rows.get("Transient")!="yes"
            or rows.get("ActiveState")!="active" or not HEX.fullmatch(ident)
            or ident=="0"*32):
        raise HotkeyError("No verified active CueWire invocation")
    return ident


def require_inside()->None:
    # Unit name alone does not establish that the caller belongs to it.
    actual=Path("/proc/self/cgroup").read_text()
    if not any(line.rstrip().endswith("/"+SCOPE)
               for line in actual.splitlines()):
        raise HotkeyError("Hotkey install must run INSIDE the managed scope")


def desired(mapping:dict[str,str])->dict[str,str]:
    return {**keymap.validate(mapping),"panic":"SUPER + C"}


# Hyprland can resolve keypad binds through the XKB translation state
# rather than the actual NumLock state of the keyboard. Both symbolic
# forms are therefore registered to the SAME action, never to two actions.
# No physical key events are observed by CueWire.
ALIAS_SPELLINGS={
    "KP_INSERT":"KP_Insert","KP_END":"KP_End","KP_DOWN":"KP_Down",
    "KP_NEXT":"KP_Next","KP_LEFT":"KP_Left","KP_BEGIN":"KP_Begin",
    "KP_RIGHT":"KP_Right","KP_HOME":"KP_Home","KP_UP":"KP_Up",
    "KP_PRIOR":"KP_Prior","KP_DELETE":"KP_Delete",
}


def registrations(mapping:dict[str,str])->list[tuple[str,str,str]]:
    """(action, actual keysym chord, description suffix)."""
    result=[]
    entries=desired(mapping)
    for action in ("panic",*keymap.ACTIONS):
        if action not in entries:
            continue
        chord=entries[action]
        result.append((action,chord,action))
        parts=chord.split(" + ")
        alias=NUMPAD_ALIASES.get(parts[-1].upper())
        if alias:
            alternative=" + ".join([*parts[:-1],ALIAS_SPELLINGS[alias]])
            result.append((action,alternative,action+" alias"))
    return result


def split(chord:str)->tuple[str,int,bool]:
    parts=chord.split(" + ")
    base=parts[-1].upper()
    return base,sum(BITS[x] for x in parts[:-1]),chord in keymap.KEYS


def base_of(row:dict)->str:
    return str(row.get("key","")).split("+")[-1].strip().upper()


def conflicts(mapping:dict[str,str],existing:list[dict])->list[str]:
    """Do not exempt old static CueWire binds. Shift+Super+C is distinct.

    The 0.56.2 JSON omits ignore_mods, so same-base custom chords are
    conservatively refused, except the known migrated Super+Shift+C
    window-close chord. For that case a reported ignore_mods is rejected.
    Numeric-only / multi-key chords cannot be certified by this scanner.
    """
    result=[]
    for row in existing:
        if not isinstance(row,dict):
            raise HotkeyError("Malformed bind row")
        key=base_of(row)
        desc=str(row.get("description",""))
        if desc.startswith(PREFIX):
            result.append("Previous dynamic CueWire bindings remain; use recover")
        if row.get("mouse") or key.startswith(("MOUSE:","SWITCH:","MOUSE_")):
            continue
        for action,chord in desired(mapping).items():
            base,mask,ignore=split(chord)
            if key not in (base,NUMPAD_ALIASES.get(base,base)):
                # Opaque code: binds may overlap the intended physical
                # keypad; refuse rather than claiming exact clearance.
                if key.startswith("CODE:") or (
                    not key and isinstance(row.get("keycode"),int)
                    and row["keycode"]>0):
                    result.append("Opaque physical-key bind: cannot certify conflicts")
                continue
            foreign_mask=row.get("modmask")
            foreign_ignore=bool(row.get("ignore_mods") or row.get("ignoreMods"))
            if (action=="panic" and foreign_mask==65 and not foreign_ignore
                    and key=="C"):
                # Migrated user window-force-close is Super+Shift+C.
                continue
            result.append(f"{chord}: existing {key} bind (mask {foreign_mask})")
    return sorted(set(result))


def validate_token(token:str)->str:
    if not HEX.fullmatch(token):
        raise HotkeyError("Invalid session token")
    return token


def description(token:str,action:str)->str:
    return PREFIX+validate_token(token)+" "+action


def lua_string(value:str)->str:
    if not value.isascii() or any(ord(c)<32 for c in value):
        raise HotkeyError("Unsafe non-ASCII control string")
    return json.dumps(value,ensure_ascii=True)


def render_install(mapping:dict[str,str],token:str,ident:str)->str:
    validate_token(token)
    validate_token(ident)
    entries=desired(mapping)
    lines=[
        f"assert(_G.{GLOBAL} == nil, 'CueWire dynamic owner already exists')",
        f"local owner = {{ token = {lua_string(token)}, handles = {{}} }}",
        f"_G.{GLOBAL} = owner",
    ]
    # Panic FIRST, so startup is interruptible as early as possible.
    for action,chord,suffix in registrations(mapping):
        _,_,ignore=split(entries[action])
        command=shlex.join([sys.executable,str(SCRIPT),"dispatch",ident,action])
        flags=("{ dont_inhibit = true, repeating = false, "
               +("ignore_mods = true, " if ignore else "")
               +"description = "+lua_string(description(token,suffix))+" }")
        lines.append("owner.handles[#owner.handles + 1] = assert(hl.bind("
                     +lua_string(chord)+", hl.dsp.exec_cmd("
                     +lua_string(command)+"), "+flags+"))")
    return "; ".join(lines)


def marked(rows:list[dict],token:str)->list[dict]:
    prefix=PREFIX+validate_token(token)+" "
    return [row for row in rows
            if str(row.get("description","")).startswith(prefix)]


def verification(mapping:dict[str,str],token:str,rows:list[dict])->list[str]:
    owned=marked(rows,token)
    problems=[]
    expected=registrations(mapping)
    if len(owned)!=len(expected):
        problems.append(f"Expected {len(expected)} registrations, found {len(owned)}")
    for action,chord,suffix in expected:
        base,mask,_=split(chord)
        matches=[b for b in owned if (
            b.get("description")==description(token,suffix)
            and b.get("dispatcher")=="__lua"
            and re.fullmatch(r"[0-9]+",str(b.get("arg","")))
            and base_of(b)==base and b.get("modmask")==mask
        )]
        if len(matches)!=1:
            problems.append(chord+" not uniquely registered")
    return problems


def install(mapping:dict[str,str],token:str)->None:
    require_inside()
    require_lua()
    ident=invocation()
    hazards=conflicts(mapping,bindings())
    if hazards:
        raise HotkeyError("Conflicting bindings: "+"; ".join(hazards))
    # The outer guardian knows token BEFORE this call, including partial
    # Lua registration failures and SIGKILL during registration.
    lua_eval(render_install(mapping,token,ident))
    errors=verification(mapping,token,bindings())
    if errors:
        raise HotkeyError("Registration not verified: "+"; ".join(errors))


def render_release(token:str,disable_only:bool=False)->str:
    token=validate_token(token)
    method="h:set_enabled(false)" if disable_only else "h:unbind()"
    return (
        f"local owner = _G.{GLOBAL}; "
        f"if owner and owner.token == {lua_string(token)} then "
        f"for _, h in ipairs(owner.handles) do {method} end; "
        +("" if disable_only else f"_G.{GLOBAL} = nil; ")
        +"end"
    )


def release(token:str)->None:
    """Remove only our remembered handles; never use global hl.unbind.

    Older handle implementations may remove by chord. If someone added
    a foreign matching chord mid-session, disable the own handles and
    report a cleanup failure rather than risk deleting foreign binds.
    """
    rows=bindings()
    owned=marked(rows,token)
    foreign=[b for b in rows if b not in owned]
    overlap=any(base_of(a)==base_of(b) and a.get("modmask")==b.get("modmask")
                for a in owned for b in foreign)
    if overlap:
        lua_eval(render_release(token,disable_only=True))
        raise HotkeyError("Foreign duplicate appeared; own binds disabled, "
                          "not removed. Review and reload config manually.")
    lua_eval(render_release(token))
    if marked(bindings(),token):
        raise HotkeyError("Temporary binds still registered; run recover")


def dispatch(ident:str,action:str)->int:
    """Short-lived helper from Hyprland, not a resident keyboard process."""
    validate_token(ident)
    if action not in (*keymap.ACTIONS,"panic"):
        raise HotkeyError("Invalid control action")
    # A stale callback must not control another session after restart.
    if invocation()!=ident:
        raise HotkeyError("Stale CueWire invocation; action refused")
    if action=="panic":
        call(["bash",str(ROOT/"tools/cuewire_scope.sh"),"emergency"],timeout=3)
    else:
        import cuewire_console as protocol
        command="stop" if action=="stop" else "toggle "+action[-1]
        result=protocol.request(command,timeout_seconds=0.5)
        if not result.startswith("OK "):
            raise HotkeyError("CueWire refused control action")
    return 0


def require_inactive()->None:
    # The scope can remain in deactivating briefly after emergency kill.
    # Never infer inactivity merely from an unsuccessful systemctl call.
    deadline=time.monotonic()+2.0
    while True:
        raw=call(["systemctl","--user","show",SCOPE,"--property=ActiveState"])
        if raw in ("ActiveState=inactive","ActiveState=failed"):
            return
        if raw!="ActiveState=deactivating" or time.monotonic()>=deadline:
            raise HotkeyError("Cannot prove CueWire scope inactive: "+raw[:80])
        time.sleep(.08)


def runtime_directory()->Path:
    raw=os.environ.get("XDG_RUNTIME_DIR","")
    p=Path(raw)
    if not p.is_absolute():
        raise HotkeyError("Missing absolute XDG_RUNTIME_DIR")
    meta=p.lstat()
    if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=os.getuid()
            or meta.st_mode&0o022):
        raise HotkeyError("Unsafe runtime directory")
    return p


def clean_stale_socket()->None:
    """Only after scope is gone; never unlink an active or foreign socket."""
    require_inactive()
    p=runtime_directory()/"cuewire-v0.1.sock"
    try:before=p.lstat()
    except FileNotFoundError:return
    if (not stat.S_ISSOCK(before.st_mode) or before.st_uid!=os.getuid()
            or before.st_mode&0o777!=0o600):
        raise HotkeyError("Unexpected runtime endpoint; left untouched")
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:
        probe.settimeout(.15)
        try:probe.connect(str(p))
        except OSError as exc:
            if exc.errno not in (errno.ECONNREFUSED,errno.ENOENT):
                raise HotkeyError("Socket liveness uncertain; left untouched") from exc
        else:
            raise HotkeyError("Socket still listening; left untouched")
    try:now=p.lstat()
    except FileNotFoundError:return
    if (now.st_dev,now.st_ino)!=(before.st_dev,before.st_ino):
        raise HotkeyError("Socket changed during verification")
    p.unlink()


def recover()->None:
    """Manual recovery if the OUTER guardian itself was killed."""
    fd=os.open(runtime_directory()/"cuewire-dynamic.lock",
               os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        meta=os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.getuid()
                or meta.st_mode&0o777!=0o600):
            raise HotkeyError("Unsafe runtime lock")
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HotkeyError("Foreground guardian still owns this session") from exc
        require_inactive()
        rows=bindings()
        tokens=set()
        for row in rows:
            desc=str(row.get("description",""))
            match=re.fullmatch(re.escape(PREFIX)+r"([0-9a-f]{32}) (?:cue:[0-9]|stop|panic)(?: alias)?",desc)
            if match:
                tokens.add(match.group(1))
        for token in tokens:
            release(token)
        # If an install failed before creating a first handle, clear just
        # our empty owner table. Never remove arbitrary binds by keysym.
        lua_eval(f"local o = _G.{GLOBAL}; if o and #o.handles == 0 then _G.{GLOBAL} = nil end")
        if any(str(row.get("description","")).startswith(PREFIX) for row in bindings()):
            raise HotkeyError("Unidentified dynamic registrations remain")
        clean_stale_socket()
        print("PASS: no remaining identified temporary CueWire bindings")
    finally:
        os.close(fd)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("check","status","recover","dispatch","probe"))
    p.add_argument("args",nargs="*")
    a=p.parse_args(argv)
    try:
        if a.action=="dispatch":
            if len(a.args)!=2:raise HotkeyError("Bad dispatch arguments")
            return dispatch(*a.args)
        if a.args:raise HotkeyError("Unexpected arguments")
        require_lua()
        if a.action=="probe":
            ident=invocation()
            import cuewire_console as protocol
            status=protocol.request("status",timeout_seconds=.5)
            protocol.parse_status(status)
            print("PASS: marked scope and authenticated Rust status")
            print("Current invocation:",ident)
            print("To test dispatcher directly (toggles cue 4):")
            print(shlex.join([sys.executable,str(SCRIPT),"dispatch",ident,"cue:4"]))
            return 0
        if a.action=="recover":
            recover()
        elif a.action=="check":
            mapping=keymap.read_profile()
            issues=conflicts(mapping,bindings())
            if issues:raise HotkeyError("; ".join(issues))
            print("PASS: no detected key conflicts; no bindings installed")
        else:
            rows=[b for b in bindings()
                  if str(b.get("description","")).startswith(PREFIX)]
            print(f"Temporary CueWire registrations: {len(rows)}")
            for b in rows:
                print(b.get("description"),b.get("key"),"mask",b.get("modmask"))
        return 0
    except (HotkeyError,keymap.KeymapError,OSError,RuntimeError) as exc:
        # Do not print keypress history; only configuration/command errors.
        print("REFUSED: "+str(exc),file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
