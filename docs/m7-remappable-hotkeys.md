# CueWire M7.4 — safe, remappable hotkey assignments

**Status:** local profile compiler implemented; no automatic Hyprland
installation or modifications. Physical custom-key tests pending.

## Design

Three independent layers:

1. Hyprland owns physical keyboard events. hl.bind maps an XKB key
   **name** (not a logged key event) to one CueWire command. CueWire
   never reads evdev or the keyboard's pressed keys.
2. Rust owns audio. New backwards-compatible cuewire ctl toggle N:
   when N is active, stop cue only (mic remains); when a different
   cue is active, replace it; otherwise start N. Existing play N
   remains explicit start/restart.
3. A local owner-only .audit/cuewire-keymap-v1.json stores action
   to symbolic keysym strings. A separate compiler creates owner-only
   .audit/cuewire-keymap-v1.lua. Lua source contains only validated
   fixed dispatch commands, never input history or user text.

Super+C remains **unconditionally reserved** for the existing
marked CueWire scope emergency: not assignable to a sound cue.

dont_inhibit supports games that inhibit shortcuts; unmodified
NumPad keys use ignore_mods to work while movement modifiers are held.
Custom chords do NOT ignore their modifiers. No locked flag is set.

**Global binds remain installed/consuming in Hyprland even while
CueWire is offline.** Config installation is explicit, reversible,
and never performed by CueWire. The goal of temporary active-session
bindings is an independently validated later feature.

## Commands, from /data/cue-wire

~~~bash
python3 tools/cuewire_keymap.py show
python3 tools/cuewire_keymap.py set cue:4 F8 --dry-run
python3 tools/cuewire_keymap.py set cue:4 F8
python3 tools/cuewire_keymap.py set stop 'CTRL+ALT+0'
python3 tools/cuewire_keymap.py unset cue:9
python3 tools/cuewire_keymap.py reset
python3 tools/cuewire_keymap.py preview
python3 tools/cuewire_keymap.py check
python3 tools/cuewire_keymap.py compile
~~~

Supported key families: KP_0..KP_9, KP_Decimal, F1..F24;
A..Z, digits and Space WITH an explicit modifier CTRL/ALT/SHIFT/SUPER.
Invalid Lua, shell injection, unmodified game letters, duplicate
physical combos and SUPER+C assigned to a cue are rejected.

Current preflight deliberately flags ANY existing bind to the same
base key, even with another modifier mask. This errs on the side of
false positives rather than stealing other applications' shortcuts.
For changing an existing CueWire mapping, first remove the previously
opted-in CueWire Lua include and manually reload before re-check.

After check succeeds, compile writes the local Lua and prints its
manual include line:

~~~lua
dofile("/data/cue-wire/.audit/cuewire-keymap-v1.lua")
~~~

**This is LUA code for the active Hyprland config, NOT a Bash
command**. Typical path: ~/.config/hypr/hyprland.lua. Check the
actual active configuration before adding it. Do not load the old
hard-coded tools/cuewire_hyprland.lua and the new .audit generator
simultaneously. Remove the dofile and reload to disable.

There is NO automatic compositor reload, key capture, default
device change, service, keypress history or audio recording. Only
the local owner-only profile (0600) and compiled Lua (0600) are
written in ignored .audit. No personal key profile is sent to GitHub.

## Verification gates

~~~bash
cd /data/cue-wire
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_cuewire_keymap.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_hotkeys.py' -v
cargo test --locked --offline
cargo build --release --locked --offline
~~~

14 new synthetic keymap tests check normalized safe chords, injection
resistance, reserved panic, duplicate prevention, rendering,
read-only preview, 0600 files, symlinks, no overwrite, and conflict
refusal. They are NOT proof of global hotkeys in real games.

Physical test gates (all pending):
- [ ] tests pass on user's Arch
- [ ] keymap and generated Lua remain local 0600
- [ ] no old static CueWire Lua included at same time
- [ ] collision scan passes; no foreign binds displaced
- [ ] key F8/chosen chord triggers the correct CueWire cue
- [ ] repeat same key toggles only cue off, other cue replaces it
- [ ] game held Ctrl/Shift/Alt do not block DEFAULT NumPad keys
- [ ] Super+C destroys ONLY verified owned scope, TTY recovers
- [ ] default input/output devices remain unchanged
- [ ] no audio or real keyboard events are logged

Future interface extension: an inspectable keymap panel and staged
editing from the existing M7 command dock without key capture.
