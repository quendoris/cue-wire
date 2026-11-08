#!/usr/bin/env python3
"""Read-only Hyprland hotkey preflight and opt-in binding snippet generator.

This module NEVER modifies Hyprland configuration, registers keys, starts
CueWire, records input, or changes system audio devices. No evdev access.
Works with game keys W/A/Shift/Ctrl held: ignore_mods on the NumPad.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SLOTS=tuple(range(10))
BIND_KEYS=tuple("KP_"+str(n) for n in SLOTS)+("KP_Decimal","C")


def project_root():
    return Path(__file__).resolve().parent.parent


def safe_path(path):
    real=Path(path).expanduser().resolve()
    if not real.is_absolute() or not re.fullmatch(r"/[A-Za-z0-9._/-]+",str(real)):
        raise ValueError("Path contains characters unsafe for Hyprland binding syntax: "+str(real))
    return real


def normalize_bindings(payload):
    if not isinstance(payload,list):
        raise ValueError("Expected Hyprland binds JSON list")
    hits=[]
    for entry in payload:
        if not isinstance(entry,dict):
            continue
        key=str(entry.get("key",""))
        norm=key.replace("KP_Decimal","KP_Decimal").upper()
        # The NumPad bindings have ignore_mods; ANY existing bind to that
        # keysym can collide in at least one held-modifier state.
        if norm in {x.upper() for x in BIND_KEYS}:
            hits.append({
                "key":key,
                "modmask":entry.get("modmask"),
                "dispatcher":entry.get("dispatcher"),
                "arg":str(entry.get("arg",""))[:180],
                "description":str(entry.get("description",""))[:180]
            })
    return hits


def load_hyprctl():
    result=subprocess.run(["hyprctl","-j","binds"],text=True,
                          capture_output=True,timeout=4,check=False)
    if result.returncode!=0:
        raise RuntimeError("hyprctl binds failed: "+result.stderr[-500:])
    return json.loads(result.stdout)


def commands(bin_path,scope_path):
    result=[]
    for n in SLOTS:
        result.append((f"KP_{n}",f"{bin_path} ctl play {n}",f"CueWire cue {n}"))
    result.append(("KP_Decimal",f"{bin_path} ctl stop",
                   "CueWire stop cues (voice stays on)"))
    result.append(("SUPER+C",f"/usr/bin/bash {scope_path} emergency",
                   "CueWire emergency terminate owned cgroup"))
    return result


def render_legacy(items):
    lines=[
        "# CueWire LEGACY hyprlang snippet, generated read-only.",
        "# This is NOT installed by CueWire. Review before explicitly adding.",
        "# WARNING: Super+C requires launching via tools/cuewire_scope.sh run.",
        "# The i flag ignores held Shift/Ctrl/Alt modifiers for NumPad.",
        "# No repeat/locked/transparent/non-consuming bindings are used.",
    ]
    for key,cmd,description in items:
        if key=="SUPER+C":
            # p bypasses game-requested keybind inhibition. No lockscreen bind.
            lines.append(f"bindip = SUPER, C, exec, {cmd} # {description}")
        else:
            lines.append(f"bindi = , {key}, exec, {cmd} # {description}")
    return "\n".join(lines)+"\n"


def render_lua(items):
    lines=[
        "-- CueWire LUA binding fragment, generated read-only.",
        "-- The tool does NOT call hyprctl eval or modify the user config.",
        "-- Super+C requires tools/cuewire_scope.sh run.",
        "-- ignore_mods accepts held movement/modifier keys.",
        "-- Game keys are NOT observed by CueWire; Hyprland dispatches binds.",
    ]
    for key,cmd,description in items:
        keys="SUPER + C" if key=="SUPER+C" else key
        flags="{ ignore_mods = true, dont_inhibit = true }" if key=="SUPER+C" \
               else "{ ignore_mods = true }"
        # All command paths were validated to contain only safe ASCII
        # punctuation; no Lua string injection is possible.
        lines.append(
            f'hl.bind("{keys}", hl.dsp.exec_cmd("{cmd}"), {flags})'
            f' -- {description}')
    return "\n".join(lines)+"\n"


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    for cmd in ("check","render"):
        item=sub.add_parser(cmd)
        item.add_argument("--repo",type=Path,default=project_root())
        item.add_argument("--binds-json",type=Path,
                          help="Synthetic fixture for offline preflight (check only)")
    sub.choices["render"].add_argument("--format",choices=("lua","legacy"),
                                     default="lua")
    args=p.parse_args()
    repo=safe_path(args.repo)
    exe=safe_path(repo/"target/release/cuewire")
    scoped=safe_path(repo/"tools/cuewire_scope.sh")
    if args.command=="check":
        if args.binds_json:
            payload=json.loads(args.binds_json.read_text())
            origin="fixture"
        else:
            payload=load_hyprctl()
            origin="live Hyprland"
        hits=normalize_bindings(payload)
        print("CueWire hotkeys preflight from",origin)
        print("Requested keys:",", ".join(BIND_KEYS[:-1]),"and SUPER+C")
        if hits:
            print("CONFLICT/POSSIBLE SHADOWED KEYBINDS (no changes made):")
            print(json.dumps(hits,indent=2,ensure_ascii=False))
            sys.exit(3)
        print("PASS: no matching keys in reported Hyprland binding list.")
        print("NOTE: not a guarantee all game/keyboard layouts will deliver these keysyms.")
    elif args.command=="render":
        if args.binds_json:
            p.error("--binds-json applies to check only")
        plans=commands(exe,scoped)
        result=render_lua(plans) if args.format=="lua" else render_legacy(plans)
        print(result,end="")
    if args.command=="check":
        print("CueWire did not modify keyboard bindings, input devices or services.")


if __name__=="__main__":
    main()
