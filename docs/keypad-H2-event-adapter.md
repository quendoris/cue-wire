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

## H2 field audit — first real NumPad trace, FAIL for gesture reliability

The operator ran the hyprlang backend and privately supplied H2-002.jsonl.
The raw user log is NOT committed to the public repository. A separate
read-only diagnostic was added:

    python3 tools/cuewire_keypad_audit.py .audit/keypad/H2-002.jsonl

Reproducible aggregate observations (2026-11-08):

- 181 valid JSONL records over 24.562 seconds; all 16 physical keypad
  identifiers occurred in actual raw down events.
- 54 raw down, 37 raw up; 11 down events were repeats while an earlier
  down was still considered active. Of 43 unique presses, 37 had
  matching release events. Six remained in a pressed state at Ctrl+C.
- The final six unresolved keys were DOT, K0, K3, K6, SLASH and STAR.
- Six accepted single gestures, **zero double**, **zero hold**;
  35 chord_start and 35 chord_end events.
- **28 of 35** chord_start events included at least one key that
  was already held for over 2 seconds in the received event stream.
  These chords are flagged as ambiguous, NOT proven physically false.
- K3 received down at 9153 ms and no corresponding raw up before
  terminal shutdown at 24562 ms. Had it actually been released
  earlier, this would explain the subsequent enormous artificial chords.
- Several matching pairs of short presses for the same button occurred
  73–114 ms apart (e.g. K7/K8/K9); the gesture layer did not classify
  them as double taps because it believed an earlier key remained held.

**Current acceptance: FAIL.** Physical key identification and datagram
transport work, but reliable release/cross-key chord correctness are
NOT established. Never attach audio, scripts, Super+C or other real
actions to these untrusted gesture events.

Investigate two competing explanations: (a) the user really held the
keys through these intervals, or (b) the compositor/bindr/one-shot
bridge missed release events. The log alone cannot decide which.
The backend receives only dispatched bind events, not kernel events.

Next controlled experiment: with a fresh log, test K1 single press
and release 5 times, K1 double 3 times, one K1 1-second hold, then
K1+K2 chord with explicit release of BOTH buttons. Do not press other
keys during the test. Independently cross-check release delivery
on the selected physical keyboard if a mismatch remains.

External Hyprland release-bind regressions have also been reported
(not proof that this same issue affected the current session):
https://github.com/hyprwm/Hyprland/discussions/16275
https://github.com/hyprwm/Hyprland/discussions/16878


## H2.2: real-log integrity audit and follow-up (November 2026)

The privately provided H2-002.jsonl contains 181 events over 24.562 s,
including all 16 physical keypad names. It has 54 raw down events,
37 raw up, 11 repeated downs while believed already pressed, and 6
unresolved buttons at shutdown (DOT, K0, K3, K6, SLASH, STAR).
28 of 35 chord_start events reference at least one key held for over
two seconds according to the received stream. These chords are NOT
proven physically genuine. **Release/event fidelity FAILED**:
do not attach audio, macros, or emergency actions to these gestures.

The original user keystroke log is private and must not be copied
into the public CueWire repo. Audit locally, using the existing
offline, read-only health script:

    python3 tools/cuewire_keypad_audit.py .audit/keypad/H2-002.jsonl \
      --out .audit/keypad/H2-002-health.json

The trace cannot distinguish actually held buttons from lost UP
events. It also cannot determine whether Hyprland keybind dispatch,
the one-shot helper or the underlying keyboard caused the loss.
Never invent UP after a timeout or accept phantom chords as commands.

### H2-003: controlled baseline

    cd /data/cue-wire
    git switch feature/pcm-mixer-playground
    git pull --ff-only
    hyprctl version
    python3 tools/cuewire_keypad.py check
    python3 tools/cuewire_keypad.py run --temporary-bindings \
      --log-jsonl .audit/keypad/H2-003.jsonl

Press K1 alone five times (around 0.5 s apart); make three pairs of
short K1 double taps (~0.1 s intra-pair); hold K1 for one second and
release; test K1+K2 releasing K1 first; test K1+K2 releasing K2 first;
finally press number-row 1 (which must NOT appear in the H2 log).
End with Ctrl+C. Run:

    python3 tools/cuewire_keypad_audit.py .audit/keypad/H2-003.jsonl

Minimum acceptance: zero unresolved presses, no orphan releases,
and clear single/double/hold/chord events for each corresponding
controlled action. Any unexpected ambiguity is a failed gate.

### H2-004: optional transparent-bind A/B test

Only if the baseline still drops release events, repeat the SAME
sequence with explicitly opted-in legacy transparent bindings:

    python3 tools/cuewire_keypad.py run --temporary-bindings \
      --legacy-transparent \
      --log-jsonl .audit/keypad/H2-004-transparent.jsonl

This produces hyprlang bindit / bindrit (the transparent t flag
prevents shadowing by other binds). It is an EXPERIMENT, NOT an
established fix. It may change interaction with other shortcuts.
It is not enabled by default. The Lua provider does not support
this flag in CueWire. No desktop config or audio settings are edited.

If both fail, investigate an event transport with reliable physical
key-up/down rather than using timing heuristics. Hyprland release
events have version-dependent reports:
https://github.com/hyprwm/Hyprland/discussions/16275
https://github.com/hyprwm/Hyprland/discussions/16878


## H2.3: real H2-003 vs H2-004-transparent result

Both private JSONL logs were audited; the raw keyboard histories stay local.

| Metric | Ordinary hyprlang H2-003 | Transparent H2-004 |
|---|---:|---:|
| Duration | 21.901 s | 15.342 s |
| JSONL records | 218 | 344 |
| Physical NumPad key IDs observed | 12 | 12 |
| Raw DOWN | 73 | 105 |
| Raw UP | 32 | 105 |
| Repeated DOWN without an UP | 34 | 0 |
| Orphan UP | 0 | 0 |
| Unclosed at shutdown | 7 | 0 |
| Recognized singles | 0 | 4 |
| Recognized doubles | 0 | 4 |
| Recognized holds | 0 | 0 |
| Chord starts | 38 | 62 |
| Maximum chord size | 10 | 4 |
| Chord intervals <= 5 ms | 4 | 22 |

Seven unresolved H2-003 keys: DOT, K3, K5, K7, K8, K9 and PLUS.
All 105 H2-004 presses had matched releases; no duplicates or orphans.
This is strong comparative evidence for the legacy transparent
binding option, but the sequences and durations were not identical,
so it is not a strict controlled causal proof.

IMPORTANT: 22 out of 62 transparent chord intervals were <=5 ms.
Even when raw key events balance, short positive overlap can be
genuine or caused by separate one-shot helper scheduling. The
gesture engine currently emits chord_start on ANY overlap.
Do not attach irreversible application actions to such chords
without confirmation of minimum overlap duration and game testing.

One K1 physical press lasted approximately 1295 ms but coincided
with other key presses, and no individual hold_start was recognized.
This is consistent with the existing chord suppression policy; test
an isolated one-second K1 hold before declaring long-press success.

Next H2-005 isolated acceptance: use the transparent mode, press K1
five times individually with >400 ms gaps; do three double taps
(100 ms between each pair and >500 ms between pairs); hold K1 ALONE
for one second; hold K1+K2 for >200 ms and release in both orders;
verify top-row 1 does not register. Produce a NEW file:

    python3 tools/cuewire_keypad.py run --temporary-bindings \
      --legacy-transparent \
      --log-jsonl .audit/keypad/H2-005-transparent.jsonl

    python3 tools/cuewire_keypad_audit.py \
      .audit/keypad/H2-005-transparent.jsonl --require-balanced

The auditor now reports micro-chord durations and exits code 3
if raw press/release integrity fails under --require-balanced.
The transparent flag remains opt-in until fullscreen/game and
modifier-held behavior has been independently tested on hardware.
