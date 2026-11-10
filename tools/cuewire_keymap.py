#!/usr/bin/env python3
"""CueWire M7.4 safe local keymap editor / Lua-Hyprland code generator.

Never reads keyboard events, changes the compositor, reloads config, starts
CueWire, captures audio or writes outside gitignored .audit. User chooses
keysym strings explicitly (not a keylogger). The permanent Super+C emergency
is intentionally fixed and cannot be overwritten by this profile.

Usage:
    python3 tools/cuewire_keymap.py show
    python3 tools/cuewire_keymap.py set cue:4 F8 --dry-run
    python3 tools/cuewire_keymap.py set cue:4 F8
    python3 tools/cuewire_keymap.py check
    python3 tools/cuewire_keymap.py compile
    # Then add dofile("/data/cue-wire/.audit/cuewire-keymap-v1.lua")
    # to your active Hyprland Lua config and manually reload it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

import cuewire_hotkeys as previous

ROOT=Path(__file__).resolve().parents[1]
DIRECTORY=ROOT/".audit"
PROFILE=DIRECTORY/"cuewire-keymap-v1.json"
GENERATED=DIRECTORY/"cuewire-keymap-v1.lua"
SCHEMA="cuewire-local-keymap-v1"
MOD_ORDER=("CTRL","ALT","SHIFT","SUPER")
ALLOWED_MODS=set(MOD_ORDER)
KEYS=tuple(f"KP_{i}" for i in range(10))+("KP_Decimal",)
ACTIONS=tuple(f"cue:{i}" for i in range(10))+("stop",)
SUPPORTED_KEYS=set(KEYS)|{f"F{i}" for i in range(1,25)}|set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")|{"Space"}
PROTECTED="SUPER + C"
MAX_FILE_SIZE=16*1024


class KeymapError(RuntimeError):
    pass


def defaults()->dict[str,str]:
    return {**{f"cue:{n}":f"KP_{n}" for n in range(10)},
            "stop":"KP_Decimal"}


def parse_key(value:str)->str:
    if not isinstance(value,str) or len(value)>80:
        raise KeymapError("Invalid or overly long key name")
    # No string is inserted into Lua except this closed, ASCII-only grammar.
    chunks=[p.strip().upper() for p in value.split("+")]
    if not chunks or any(not p or not re.fullmatch(r"[A-Z0-9_]+",p) for p in chunks):
        raise KeymapError("Expected a keysym such as KP_4, F8, CTRL+ALT+4")
    if len(chunks)!=len(set(chunks)):
        raise KeymapError("Repeated modifier/key")
    mods=set(chunks[:-1])
    key=chunks[-1]
    if not mods.issubset(ALLOWED_MODS):
        raise KeymapError("Unsupported modifier")
    canonical=("Space" if key=="SPACE" else "KP_Decimal" if key=="KP_DECIMAL" else key)
    if canonical not in SUPPORTED_KEYS:
        raise KeymapError("Unsupported key (use NumPad, F1..F24, or modified A..Z / 0..9 / Space)")
    if not mods and canonical not in KEYS and not re.fullmatch(r"F(?:[1-9]|1[0-9]|2[0-4])",canonical):
        raise KeymapError("Ordinary letters/numbers/Space require a modifier (no typing interference)")
    if key=="C" and "SUPER" in mods:
        raise KeymapError("Super+C emergency is protected and cannot be reassigned")
    ordered=[m for m in MOD_ORDER if m in mods]
    return " + ".join([*ordered,canonical])


def validate(mapping:dict[str,str])->dict[str,str]:
    if not isinstance(mapping,dict) or not set(mapping).issubset(ACTIONS):
        raise KeymapError("Unknown action or malformed keymap")
    canonical={}
    taken={}
    for action,key in mapping.items():
        if not isinstance(key,str):
            raise KeymapError("Key must be a string")
        combo=parse_key(key)
        base=combo.split(" + ")[-1]
        for previous,owner in taken.items():
            prev_base=previous.split(" + ")[-1]
            # An unmodified NumPad bind uses ignore_mods=true.
            # Therefore it also captures combinations with Shift/Ctrl/Alt,
            # even though those chord strings are different.
            if (combo==previous or
                (base==prev_base and
                 (previous in KEYS or combo in KEYS))):
                raise KeymapError(
                    f"Conflict: {action} and {owner} overlap on {base}")
        taken[combo]=action
        canonical[action]=combo
    return canonical


def private_directory()->Path:
    if DIRECTORY.is_symlink():
        raise KeymapError("Refuse symlink .audit")
    if DIRECTORY.exists():
        meta=DIRECTORY.lstat()
        if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=os.getuid()
                or meta.st_mode&0o022):
            raise KeymapError("Unsafe private directory ownership/permissions")
    return DIRECTORY


def read_profile()->dict[str,str]:
    private_directory()
    try:metadata=PROFILE.lstat()
    except FileNotFoundError:return defaults()
    if (not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid!=os.getuid()
            or metadata.st_mode&0o777!=0o600
            or metadata.st_size>MAX_FILE_SIZE):
        raise KeymapError("Profile must be a regular user-owned 0600 file")
    try: payload=json.loads(PROFILE.read_text(encoding="utf-8"))
    except (OSError,UnicodeError,ValueError) as exc:
        raise KeymapError("Corrupt keymap profile") from exc
    if (not isinstance(payload,dict) or set(payload)!={"schema","bindings"}
            or payload["schema"]!=SCHEMA):
        raise KeymapError("Unknown keymap schema")
    return validate(payload["bindings"])


def atomic_private(destination:Path,data:str)->None:
    directory=private_directory()
    directory.mkdir(mode=0o700,exist_ok=True)
    if destination.is_symlink():
        raise KeymapError("Refuse symlink destination")
    if destination.exists():
        meta=destination.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.getuid():
            raise KeymapError("Refuse unowned/non-regular destination")
    fd,path=tempfile.mkstemp(prefix=".cuewire-map-",dir=directory)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(path,destination)
    finally:
        if os.path.exists(path):os.unlink(path)


def save_profile(mapping:dict[str,str])->None:
    canonical=validate(mapping)
    payload={"schema":SCHEMA,"bindings":canonical}
    atomic_private(PROFILE,json.dumps(payload,indent=2,ensure_ascii=True,
                                      sort_keys=True)+"\n")


def digest(mapping:dict[str,str])->str:
    return hashlib.sha256(json.dumps(
        validate(mapping),sort_keys=True,separators=(",",":")
    ).encode("ascii")).hexdigest()


def render_lua(mapping:dict[str,str])->str:
    """Safe codegen from restricted keys/actions; no io or input capture in Lua."""
    canonical=validate(mapping)
    bin_path=ROOT/"target/release/cuewire"
    emergency=ROOT/"tools/cuewire_scope.sh"
    for p in (bin_path,emergency):
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+",str(p)):
            raise KeymapError("Unsafe repository path for Lua code generation")
    lines=[
        "-- CueWire M7.4 locally generated Lua-Hyprland bindings.",
        "-- User-selected profile SHA256: "+digest(mapping),
        "-- No automatic installation. No audio input/keyboard capture.",
        "-- Include this file via dofile(...) in your active Lua config.",
        "-- Remove the dofile line and reload to disable bindings.",
        "if _G.__cuewire_keymap_handles then",
        "    for _, handle in ipairs(_G.__cuewire_keymap_handles) do",
        "        handle:unbind()",
        "    end",
        "end",
        "local handles = {}",
    ]
    for action in ACTIONS:
        combo=canonical.get(action)
        if combo is None:
            continue
        # The server handles all playback semantics, including toggling.
        ctl=("stop" if action=="stop" else
             "toggle "+action.split(":")[1])
        desc="CueWire M7.4 "+action
        flags=(
            '{ ignore_mods = true, dont_inhibit = true, description = "'+desc+'" }'
            if " + " not in combo and combo in KEYS else
            '{ dont_inhibit = true, description = "'+desc+'" }'
        )
        lines.append(
            f'handles[#handles + 1] = hl.bind("{combo}", '
            f'hl.dsp.exec_cmd("{bin_path} ctl {ctl}"), {flags})'
            f' -- {action}'
        )
    lines.extend([
        'handles[#handles + 1] = hl.bind("SUPER + C",',
        f'    hl.dsp.exec_cmd("/usr/bin/bash {emergency} emergency"),',
        '    { dont_inhibit = true, description = "CueWire M7.4 emergency" })',
        "_G.__cuewire_keymap_handles = handles",
        "",
    ])
    return "\n".join(lines)


def is_our_active_binding(binding:dict)->bool:
    """Ignore ONLY authenticated self-identifying old M7.4 dispatchers.

    A matching description alone is insufficient: exact dispatcher and
    exact fixed shell-free command must also match this implementation.
    Foreign or old static CueWire binds are never silently exempted.
    """
    if str(binding.get("dispatcher","")).lower()!="exec":
        return False
    descriptor=binding.get("description")
    arg=str(binding.get("arg","")).strip()
    bin_path=ROOT/"target/release/cuewire"
    emergency=ROOT/"tools/cuewire_scope.sh"
    if descriptor=="CueWire M7.4 emergency":
        return arg==f"/usr/bin/bash {emergency} emergency"
    if not isinstance(descriptor,str) or not descriptor.startswith("CueWire M7.4 "):
        return False
    action=descriptor[len("CueWire M7.4 "):]
    if action not in ACTIONS:
        return False
    cmd="stop" if action=="stop" else "toggle "+action.split(":")[1]
    return arg==f"{bin_path} ctl {cmd}"


def conflicts(mapping:dict[str,str],existing:list[dict])->list[str]:
    """Conservative scan: a conflicting keysym can shadow a custom binding.

    For keypad keys using ignore_mods, ANY other modifier is a conflict.
    For custom chords we conservatively flag any same base keysym, even
    if another binding has different modifiers; this errs on safety.
    """
    desired=validate(mapping)
    key_bases={}
    for action,combo in desired.items():
        key_bases.setdefault(combo.split(" + ")[-1].upper(),[]).append(action)
    key_bases.setdefault("C",[]).append("emergency Super+C")
    hazards=[]
    for b in existing:
        if not isinstance(b,dict):
            raise KeymapError("Unexpected Hyprland bind schema")
        key=b.get("key")
        if not isinstance(key,str):
            continue
        upper=key.strip().upper()
        if upper not in key_bases:
            continue
        if is_our_active_binding(b):
            # Existing handles belong to our previous M7.4 compiled
            # map. The generated Lua only unbinds these own handles.
            continue
        hazards.append(
            f"{upper}: existing {str(b.get('dispatcher','unknown'))[:32]} "
            f"bind conflicts with {', '.join(key_bases[upper])}"
        )
    return sorted(set(hazards))


def view(mapping:dict[str,str])->str:
    rows=["CueWire M7.4 / local keymap (not automatically applied)"]
    for a in ACTIONS:
        rows.append(f"  {a:<7}  {mapping.get(a,'—')}")
    rows.extend(["  PANIC    SUPER + C (protected, fixed)",
                 "Changes require conflict check + manual Hyprland config reload."])
    return "\n".join(rows)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    sub.add_parser("show",help="display locally configured keys; no hardware")
    setter=sub.add_parser("set",help="assign an explicit keysym to a cue or stop")
    setter.add_argument("target",choices=ACTIONS)
    setter.add_argument("key",help="KP_4, F8, CTRL+ALT+4 etc.")
    unset=sub.add_parser("unset",help="remove a cue/stop key")
    unset.add_argument("target",choices=ACTIONS)
    sub.add_parser("reset",help="restore initial NumPad map locally")
    sub.add_parser("preview",help="print Lua generated from local map; no write")
    sub.add_parser("check",help="read-only live Hyprland conflict preflight")
    sub.add_parser("compile",help="after preflight, write ignored owner-only Lua")
    for name in ("set","unset","reset"):
        sub.choices[name].add_argument(
            "--dry-run",action="store_true",help="preview change without disk write")
    a=p.parse_args(argv)
    try:
        mapping=read_profile()
        if a.action=="show":
            print(view(mapping))
        elif a.action in ("set","unset","reset"):
            if a.action=="reset":
                changed=defaults()
            else:
                changed=dict(mapping)
                if a.action=="set":
                    changed[a.target]=parse_key(a.key)
                else:changed.pop(a.target,None)
            changed=validate(changed)
            print(view(changed))
            if a.dry_run:
                print("DRY RUN: no file changed")
            else:
                save_profile(changed)
                print("Saved local 0600 JSON only; existing Lua bindings are NOT reloaded")
                print("Next: keymap check, then keymap compile, then manual Lua include/reload")
        elif a.action=="preview":
            print(render_lua(mapping),end="")
        else:
            try:existing=previous.load_hyprctl()
            except (RuntimeError,ValueError,OSError) as exc:
                raise KeymapError("Cannot inspect Hyprland binds; refuse apply") from exc
            if not isinstance(existing,list):
                raise KeymapError("Unknown Hyprland bind response")
            hazards=conflicts(mapping,existing)
            if hazards:
                raise KeymapError("Bindings conflict:\n"+"\n".join(hazards))
            print("PASS: no matching keysym conflicts in current Hyprland binds")
            if a.action=="compile":
                atomic_private(GENERATED,render_lua(mapping))
                print("Generated owner-only Lua:",GENERATED)
                print("Manual include: dofile("+json.dumps(str(GENERATED))+")")
                print("No config was edited or reloaded automatically")
    except KeymapError as exc:
        print("REFUSED: "+str(exc),file=sys.stderr)
        return 2
    return 0


if __name__=="__main__":
    raise SystemExit(main())
