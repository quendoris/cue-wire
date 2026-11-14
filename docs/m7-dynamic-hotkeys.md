# M7.6 — session-owned dynamic NumPad bindings

Status: implementation + synthetic regressions; real Hyprland 0.56.2
acceptance is pending. No edits to hyprland.lua, no automatic reload,
no persistent key registration, no evdev/input event reader, no daemon.

## Run

The operator has migrated to the Lua config provider. Keep the prior
static CueWire dofile commented out: never install both backends.
The migrated Super+Shift+C window-kill binding stays intact.

~~~bash
cd /data/cue-wire
git switch feature/m7-terminal-interface
git pull --ff-only

python3 -m unittest discover -s tests -p 'test_cuewire_dynamic_hotkeys.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_workspace.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_session.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_console.py' -v
cargo test --locked --offline
cargo build --release --locked --offline

python3 tools/cuewire_keymap.py show
python3 tools/cuewire_dynamic_hotkeys.py check
python3 tools/cuewire_dynamic_hotkeys.py status
~~~

If the local profile still has the F8 experiment, explicitly restore
the standard keypad map with cuewire_keymap.py reset ONLY if desired,
then repeat check. The default map is NumPad 0..9, Decimal, Super+C.
Check is read-only, and status before launch should report zero
temporary registrations. Do not apply a static Lua include.

After stopping any existing CueWire session:

~~~bash
python3 tools/cuewire_session.py --run --workspace --hotkeys
~~~

With NumLock enabled, NumPad digits toggle the corresponding configured
cue; another cue replaces it. Decimal stops the cue only. Holding a
movement modifier is intended to work via ignore_mods for bare keypad
bindings; physical keysym resolution and input inhibition in the actual
game must be tested. No repeat flag, no lockscreen registration.
An unconfigured slot is refused by the Rust engine. Custom symbolic
chords use the saved private map and respect their exact modifiers.
Changes to the saved map apply on the next launch, not silently live.

In another terminal:

~~~bash
python3 tools/cuewire_dynamic_hotkeys.py status
~~~

Expected for the default map: 23 temporary registrations (12 logical
commands, including 11 alternate navigation keysyms). A custom map
with unassigned actions or non-keypad keys may have fewer.
Status reports compositor registration metadata, not press history or
proof of physical key delivery. Switch TUI to Mixer; it polls the
existing Rust status twice per second (subject to socket timeout and
render interval). Global cue state is reflected by the active button,
not by recording which physical key was pressed.

## Ownership and cleanup

The opt-in outer foreground supervisor holds a 0600 runtime flock and
creates a random per-launch token. It passes the token to the existing
marked transient scope. The inner installer verifies its own cgroup,
active Lua provider, and exact scope InvocationID, then checks existing
Hyprland binds. All user-configured cues and panic are registered using
hyprctl eval and hl.bind with per-session descriptions and handles
retained in a dedicated Lua global. Panic is registered first, before
device discovery and Rust startup. The pre-scope preflight itself can
be interrupted with Ctrl+C; the global panic shortcut does not exist
until registration inside the scope.

Hyprland 0.56.2 exposes Lua dispatchers as __lua with opaque registry
IDs. Installation verifies registered keys, modifier masks and unique
token/action descriptions, not a fictitious exec command field.
The original Super+Shift+C is distinct from Super+C. The conflict scan
is conservative for keypad aliases and opaque physical code binds;
the 0.56.2 JSON does not expose every binding flag. No guarantee of
absence of every possible collision under all keyboard layouts,
future configuration edits or submaps.

Every bind executes a short-lived external command, not an input
listener. A dispatcher checks the marked scope's actual InvocationID
before sending only a fixed allowlisted toggle/stop or emergency
command. Old callbacks refuse a different invocation. The existing
scope emergency kills the engine, TUI and their scoped children.

After systemd-run returns, the surviving outer foreground guardian
restores TTY state, removes its own Lua handles, and verifies the
absence of its token-labelled binds. It checks scope inactivity and
only unlinks a stale owner-owned 0600 Unix socket after a failed
connection and inode recheck. A live socket or uncertainty is refused.
If a foreign duplicate chord appeared during runtime, it disables its
own handles and reports an incomplete cleanup instead of removing a
possibly foreign bind. No broad hl.unbind("all"), no config reload.

A normal exit, Ctrl+C, or scoped Super+C should leave zero dynamic
registrations. This requires physical confirmation. Short-lived
Hyprland-spawned dispatch helpers and the outer guardian are outside
the audio cgroup by design; their commands have bounded waits and
are not long-lived daemons. If the entire outer guardian is itself
SIGKILLed, or the compositor cannot respond, normal cleanup cannot
be guaranteed. If the scope remains active use the existing explicit
emergency command first; then:

~~~bash
bash tools/cuewire_scope.sh check
python3 tools/cuewire_dynamic_hotkeys.py recover
python3 tools/cuewire_dynamic_hotkeys.py status
~~~

Recover takes the same runtime lock, refuses active scope, removes
identified remaining handles and checks the stale socket. If cleanup
reports a foreign duplicate or unavailable compositor, review the
existing configuration and perform a deliberate manual reload after
ending the session. No automatic reload is attempted.

Do not reload or change Hyprland configuration during this experiment:
a reload can discard dynamically registered bindings (including panic);
the old shell emergency remains available from another terminal.
Do not claim verified cleanup or latency until the actual machine
demonstrates them. Live gain stages and M6-007 DSP remain unimplemented.

## Physical acceptance

- Read-only preflight succeeds on user's active Lua configuration.
- Default map: 0 registrations before, 23 during, 0 after.
- NumPad 4 toggles actual cue 4; repeated press stops; decimal stops
  music while the physical microphone stays alive.
- In a game or another window, actual assigned hotkeys work while
  modifiers are held, and the Mixer reflects Rust's active cue.
- Super+Shift+C stays an unrelated window shortcut.
- Normal exit, panic during playback and panic during scoped startup
  remove the temporary registrations, restore TTY, and leave no
  owned audio processes or stale socket.
- Relaunch after panic succeeds; no global key consumption while
  CueWire is stopped and no system audio default changes.

The 16 synthetic tests cover Lua registry metadata, conflicts,
partial/failed registration, cleanup and fallback, invocation
guard, stale socket and outer guardian. They do not replace
physical acceptance of this compositor API.


## M7.6.1 — keypad compatibility and diagnostic split

Operator confirmed playback/toggle inside the TUI but not in other
windows. This proves terminal-local control, not global dispatch.
The 0.56.2 KeybindManager can resolve keys through a separate XKB
translation state. A keypad key may resolve to KP_Left instead of
KP_4, regardless of the terminal's delivered character. Register
both numeric and navigation symbolic forms to the same logical
action. Decimal also supports KP_Delete. This is a plausible fix,
not yet a confirmed physical root cause. No keycode capture.

While the managed session is live, from another terminal:
~~~bash
python3 tools/cuewire_dynamic_hotkeys.py status
python3 tools/cuewire_dynamic_hotkeys.py probe
~~~

Probe checks the invocation marker and Rust status without playing
audio. It prints two explicit commands for the operator to run:
a direct invocation-guarded toggle of slot 4, then the equivalent
hyprctl eval/exec dispatcher. Test separately, stopping playback
between them. If direct works but compositor dispatch fails, the
problem is not the keysym; inspect compositor dispatch/environment.
If both work but physical keys fail, inspect NumLock/XKB/submap and
actual registration. No event history is stored or needed.
