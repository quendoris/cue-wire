# CueWire H2 — independent NumPad event adapter (logging only)

Status: implemented, mock tests passed. Actual Hyprland release/press
bindings and keyboard scan codes still require physical acceptance.

## H2.1 — compatibility with the active hyprlang provider

Current user's Hyprland uses legacy hyprlang, which deliberately refuses
hyprctl eval. No conversion to a Lua configuration is required.

H2 detects the active provider before it creates any JSONL:
- Lua config manager: temporary hl.bind handles and direct socket2 events
- hyprlang manager: dynamic bindi for press and bindri for release,
  forwarded to one foreground NumPad gesture interpreter through
  a short-lived cuewire_keypad_emit.py Unix datagram sender per transition

Legacy dynamic keys are checked for conflicts before registering. The
adapter never writes to hyprland.conf or hyprland.lua. On Ctrl+C and
SIGTERM, cleanup verifies that only our own bindings occupy each key
before calling hyprctl keyword unbind. Foreign bindings are NOT deleted.
After SIGKILL, cleanup cannot be guaranteed: run the H2 restore command
when the foreground session has ended. It verifies owned markers.

The earlier H2-001.jsonl exists because the Lua-only attempt opened
it before registration failed. H2.1 opens the log only after success,
and still refuses to overwrite any prior file. Use a new H2-002 name:

    cd /data/cue-wire
    git fetch origin
    git switch feature/pcm-mixer-playground
    git pull --ff-only
    python3 tests/test_keypad.py
    python3 tests/test_keypad_legacy.py
    python3 tools/cuewire_keypad.py check
    mkdir -p .audit/keypad
    python3 tools/cuewire_keypad.py run --temporary-bindings \
      --log-jsonl .audit/keypad/H2-002.jsonl

Press the physical NumPad 1 once, twice, and hold it for a second;
then test a 1+2 chord and normal number-row 1. Stop with Ctrl+C.

Legacy limitation: --device is deliberately rejected. The physical
NumPad is distinguished from number-row 0..9, but cannot be isolated
from another full keyboard with an identical physical keypad. Such
per-device filtering needs a different provider/back-end.

Test coverage is synthetic: actual keyboard key-up delivery and
Hyprland bindr behavior must still be verified locally.
## Core architecture

H2 intentionally REPLACES the old H1 audio-triggering NumPad bindings.
Never install H1 and H2 keymaps simultaneously. H2 does not launch
music, touch the microphone, switch devices, register keybinds, install
a service, or start any process at login. It logs EVENTS ONLY.

A full 16-position NumPad is monitored using physical XKB code:N values
rather than keysym KP_0 or characters from the number row. NumLock is
NOT taken over; it keeps working as an ordinary OS modifier.

| Button | Internal ID | XKB code |
|---|---|---|
| Numpad 0 | K0 | 90 |
| Numpad 1 | K1 | 87 |
| Numpad 2 | K2 | 88 |
| Numpad 3 | K3 | 89 |
| Numpad 4 | K4 | 83 |
| Numpad 5 | K5 | 84 |
| Numpad 6 | K6 | 85 |
| Numpad 7 | K7 | 79 |
| Numpad 8 | K8 | 80 |
| Numpad 9 | K9 | 81 |
| Numpad decimal/delete | DOT | 91 |
| Numpad Enter | ENTER | 104 |
| Numpad plus | PLUS | 86 |
| Numpad minus | MINUS | 82 |
| Numpad multiply | STAR | 63 |
| Numpad divide | SLASH | 106 |

Codes are standard Linux evdev KEY_KP codes + 8 for XKB; verify on
the actual physical keyboard with wev. The main number row has
different codes and is deliberately excluded.

A physically SEPARATE USB NumPad can be isolated further with
the optional --device "EXACT_NAME" flag in the renderer. Identify
the exact Hyprland name with hyprctl -j devices. This uses the
official Lua per-device binding flag. Without --device the adapter
separates keypad keys from the number row but does not distinguish
two keyboards containing identical physical NumPad codes.

## Direct Hyprland socket2 event transport

Bindings use hl.dsp.event, not exec_cmd. No subprocess per key press.
On down and up, the compositor emits:

    custom>>cuewire_keypad_v1/K1/down
    custom>>cuewire_keypad_v1/K1/up

One foreground Python stdlib-only process subscribes to Hyprland
socket2, validates the namespace and known key/state names, ignores
other compositor and keyboard events, and logs only NumPad gestures.
It does NOT inspect raw evdev, read normal keyboard events or collect
passwords. On configreload/disconnect, pressed state is invalidated,
not fabricated.

References (current Hyprland official documentation):
https://wiki.hypr.land/configuring/core/binds/
https://wiki.hypr.land/configuring/core/binds/flags/
https://wiki.hypr.land/configuring/core/binds/devices/per-device/
https://wiki.hypr.land/configuring/core/dispatchers/
https://wiki.hypr.land/ipc/

## Gesture semantics

| Event | Meaning | Default time |
|---|---|---|
| raw down / up | Actual press/release from compositor | Immediate |
| single | One short press that was not followed by same-key second press | Emitted 290 ms after first release |
| double | Two short presses of the same key | Second down <=290 ms after first up |
| hold_start | Key held continuously | At >=550 ms |
| hold_end | Release after hold_start | Contains measured held_ms |
| chord_start | A second or third keypad key is down while another is held | Immediately when overlap begins |
| chord_end | Chord breaks when any member releases | Contains held_ms; if changed, includes changed marker |
| repeat_ignored | Duplicate down while key is held | Prevents autorepeat from faking a tap |
| orphan_up | Release after lost down | Diagnostic only |
| reset | Session end, compositor reload or socket disconnect | Drop held state safely |

A chord suppresses single/double for participating keys.
A long key can become part of a chord; both observations are logged
independently. The events are descriptive, not attached to any
external actions yet. An action layer will be designed separately.

Timestamp is adapter-arrival monotonic time, not kernel scan time.
Missed or queued compositor events can affect threshold edge cases.
All logs use JSON Lines with fields at_ms, kind, event, keys and
additional timing fields.

## Test without changing the desktop

    cd /data/cue-wire
    python3 tests/test_keypad.py
    python3 tools/cuewire_keypad.py --help

The tests use fake JSON and a mock Hyprland Unix socket. They validate
16 key names, physical code rendering, double/single/hold/chord,
orphan/duplicate input, namespace filtering and no unintended
spawning or audio. This does NOT certify actual hardware key signals.

## Preview-only live setup

1. Inspect the actual binds and keyboard devices; do NOT override
   any existing shortcut automatically:

       python3 tools/cuewire_keypad.py check
       hyprctl -j devices

2. Generate a Lua binding PREVIEW:

       python3 tools/cuewire_keypad.py render > /tmp/cuewire-H2-preview.lua

   For a physically separate USB NumPad, render with the exact device
   name found above:

       python3 tools/cuewire_keypad.py render --device "EXACT_NAME" > /tmp/cuewire-H2-device.lua

   If a full-screen game inhibits keyboard shortcuts, an explicit
   opt-in flag adds dont_inhibit to keypad-only binds:

       python3 tools/cuewire_keypad.py render --game > /tmp/cuewire-H2-game.lua

3. H2 does NOT activate the generated binds. After confirming no
   conflict and verifying codes with wev, manually add the selected
   32 hl.bind statements to the user-controlled Hyprland Lua config.
   Both down/up events are registered per key. If a particular
   compositor build does not deliver both, that is an experimental
   failure; do not claim single/double/hold works without both.

4. Launch one foreground LOG-ONLY process:

       mkdir -p /data/cue-wire/.audit/keypad
       python3 tools/cuewire_keypad.py run --log-jsonl /data/cue-wire/.audit/keypad/H2-001.jsonl

   An existing log path is rejected; nothing gets overwritten.
   Log output appears in the terminal and the JSONL file.
   Ctrl+C ends it; no service or background logger remains.

5. Test 1-tap, 2-tap, >1s hold, 1+2, 1+2+3, NumLock on/off,
   keypad arithmetic keys, W/Shift held while pressing NumPad,
   and number-row 1 (which MUST NOT appear in the NumPad log).

There is no automatic removal of manually entered Hyprland binds;
they are owned and maintained by the user. Running H2 itself
makes no persistent keyboard configuration change. Super+C remains
an unrelated H1 emergency-control experiment, never registered by H2.

## Preferred opt-in TEMPORARY mode (no configuration-file edits)

With the Lua config provider, the adapter can perform a **read-only
conflict preflight**, connect its foreground event listener, then
register all 32 required press/release binds using ONE
hyprctl eval invocation. Returned binding handles are retained in
a uniquely namespaced Lua table. On Ctrl+C or SIGTERM, the adapter
removes ONLY those 32 handles using handle:remove() (not an
indiscriminate hl.unbind). It never edits hyprland.lua/hyprland.conf
and never installs an autostart/daemon.

This *temporarily mutates the running compositor's bindings*, but
only after the operator deliberately requests --temporary-bindings.
If binds conflict with existing NumPad shortcuts, activation refuses
to proceed. This does not take over Super+C or ordinary key rows.

Start (new file path per run):

    cd /data/cue-wire
    python3 tools/cuewire_keypad.py check
    python3 tools/cuewire_keypad.py run --temporary-bindings \
      --log-jsonl /data/cue-wire/.audit/keypad/H2-temporary-001.jsonl

For a physically separate USB keypad, identify the name first:

    hyprctl -j devices
    python3 tools/cuewire_keypad.py run \
      --temporary-bindings --device "EXACT_KEYPAD_DEVICE_NAME" \
      --log-jsonl /data/cue-wire/.audit/keypad/H2-device-001.jsonl

If a game inhibits binds, --game enables the more intrusive
dont_inhibit option, but only when explicitly requested.

If SIGKILL occurs, Python finally does NOT execute. The Lua binds
may therefore survive until the compositor reloads. The rescue
command removes ONLY the H2 namespaced handle table:

    python3 tools/cuewire_keypad.py restore

There is NO claim of automatic SIGKILL cleanup. Before treating this
as production, test Hyprland eval/handle removal on the real machine
and ensure the emergency control scope has an OUTSIDE parent that
cleans ephemeral bindings after an emergency cgroup termination.

IMPORTANT: Unlike the manual Lua example earlier in this document,
the preferred temporary mode does **not** require copying binds to
the persistent desktop configuration. Never install both variants
at the same time.

## Next gate

Do not assign any audio commands until the real key event log
demonstrates reliable press AND release events, correct physical
key mapping and simultaneous key handling. Then define a distinct
action mapping with versioned profiles and audit logs.
