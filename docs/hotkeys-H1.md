# CueWire hotkeys H1 — safe NumPad soundboard and scope-only Super+C

**Date:** 2026-11-08. **State:** code and hardware-free CI checks; NOT
yet accepted on real Arch/Hyprland or with hung PipeWire clients.

This is a different module from the frozen voice M1/M4 research. Audio
signal processing and keyboard command routing must not depend on each
other. The hotkey module sends commands to the EXISTING Unix socket
`$XDG_RUNTIME_DIR/cuewire-v0.1.sock`, mode 0600. It does not record
raw keyboard input, use privileged `/dev/input/event*`, steal focus,
start a resident hotkey daemon or alter default system audio devices.

## User-approved keyboard behavior

| Key | Command | Notes |
|---|---|---|
| NumPad **0..9** | `cuewire ctl play N` | 10 configurable cue slots, `--cue 0=... --cue 9=...`; one cue at a time, replaces earlier cue |
| NumPad **decimal** | `cuewire ctl stop` | Cancels all active cue-player children; **voice stream continues unchanged** |
| **Super+C** | `tools/cuewire_scope.sh emergency` | Immediate forced SIGKILL of **only** the marked CueWire transient cgroup; game/audio defaults left alone |
| **W/A/Shift/Ctrl held** | Hotkeys still respond | Hyprland's `ignore_mods` flag; no global evdev hook and no keyboard polling |

Cue IDs 0..9 are supported in the Rust CLI and the same control
protocol. You can assign just a subset: hitting an unconfigured cue
returns a bounded error without altering the voice stream.

**Num Lock and key names** can vary by hardware/layout. Check with
`wev`; `KP_Decimal` might map differently when Num Lock is off.
Do not assume synthetic test fixture equals actual keyboard behavior.

## Security/lifecycle contract

- No automatic modification of `~/.config/hypr/`, no `hyprctl eval`,
  no keybind registration by launching CueWire and no global daemon.
- `tools/cuewire_hotkeys.py` is **read-only**: inspect existing binds
  with `hyprctl -j binds`, flag conflicts, and render manually reviewable
  snippets for both **Lua-based Hyprland** and **older hyprlang**.
- We do NOT silently unbind occupied keys. Matching key conflicts
  cause `check` to fail (exit 3), without any system changes.
- Normal Ctrl+C, SIGTERM, `ctl quit` use Rust supervisor's existing
  cleanup, retaining the virtual mic's safe lifecycle.
- Super+C requires starting CueWire **inside its own transient
  systemd user scope** via `tools/cuewire_scope.sh run`. If launched
  via bare `cuewire run`, the emergency handler deliberately REFUSES
  to use `pkill`, guess the parent PID or terminate other apps.
- The scope manager is existing `systemd --user`. The named
  `cuewire-hotkeys.scope` is temporary, NOT an installed/enabled
  service. On Super+C, `systemctl --user kill --kill-whom=cgroup
  --signal=SIGKILL cuewire-hotkeys.scope` kills owned CueWire clients
  even when the application's main event loop is hung.
- The target must have the exact private scope `Description` marker
  and report `Transient=yes`; otherwise emergency refuses to kill
  anything. This avoids acting on an unrelated existing scope with
  the same name.
- After the scoped command exits, the foreground launcher confirms
  that the scope is not active and uses a **socket owner / type /
  non-listening / inode check** before unlinking a stale CueWire socket.
  It never deletes an active or foreign socket or unrelated node.
- **SIGKILL is never graceful:** it cannot run Rust cleanup handlers.
  Kernel closes owned pw-loopback/pw-play clients; if systemd or the
  compositor is inaccessible, guarantees cannot be absolute. Run real
  process-leak fault tests before enabling Super+C. No privileged
  process-group owner or auto-recovery service is created.

## What has already been implemented and what remains opt-in

Implemented:
1. Rust cue slots 0..9 and validated `ctl play 0`.
2. `cuewire_hotkeys.py check` — read-only conflict inspection.
3. `cuewire_hotkeys.py render` — static Lua or legacy Hyprland
   keybinding text; prints no commands to execute automatically.
4. `cuewire_scope.sh check/run/emergency` — launch/forced shutdown
   with explicit, unique, marked user scope.
5. Offline unit tests for 12 generated keys, conflict detection,
   malformed JSON, and fake systemctl safe kill/refusal.

**Not yet implemented:** automatic/temporary compositor key
registration/removal. This is deliberate: a process terminated by
SIGKILL cannot safely undo a dynamically added binding, and on modern
Hyprland the Lua provider does not accept old `hyprctl keyword bind`
commands. It would violate the no-leftovers rule to claim otherwise.

The printed snippets are examples for **explicit user-controlled**
registration only. If you save them to your Hyprland startup config,
they become persistent user-managed desktop bindings, which is
distinct from CueWire's automatic behavior and requires your
independent approval. CueWire itself edits no desktop configuration.

Relevant official Hyprland documentation:
- https://wiki.hypr.land/configuring/core/binds/
- https://wiki.hypr.land/configuring/core/binds/flags/
- https://wiki.hypr.land/configuring/core/advanced-configuration/using-hyprctl/
- https://wiki.hypr.land/0.54.0/Configuring/Binds/
Relevant transient-scope documentation:
- https://www.freedesktop.org/software/systemd/man/latest/systemd.scope.html
- https://www.freedesktop.org/software/systemd/man/latest/systemctl.html

## Step A — inspect without changing the current desktop

In the foreground terminal:

    cd /data/cue-wire
    git fetch origin
    git switch feature/pcm-mixer-playground
    git pull --ff-only
    cargo test --locked --offline --all-targets
    cargo build --release --locked --offline

    bash tools/cuewire_scope.sh check
    python3 tools/cuewire_hotkeys.py check

If `check` reports a NumPad/Super+C conflict, do NOT override it.
Investigate the existing Hyprland bind using `hyprctl -j binds`,
then decide on a conflict-free layout. We never reassign it ourselves.

Render both formats **only to inspect**:

    python3 tools/cuewire_hotkeys.py render --format lua
    python3 tools/cuewire_hotkeys.py render --format legacy

For Lua-based Hyprland, a bind resembles:

    hl.bind("KP_1", hl.dsp.exec_cmd("/data/cue-wire/target/release/cuewire ctl play 1"), { ignore_mods = true })

For legacy hyprlang the equivalent is:

    bindi = , KP_1, exec, /data/cue-wire/target/release/cuewire ctl play 1

You must select the correct provider for your local version/config.
The NumPad keybindings do not repeat on hold, do not activate on
lockscreen and are not non-consuming/transparent. In an open game,
NumPad triggers are normally consumed by Hyprland; held W/A/Shift/Ctrl
remain unaffected. We still need to test actual fullscreen games.

## Step B — isolated foreground runtime (no compositor hooks yet)

Only after your real PipeWire physical mic and headset nodes are
known and the release binary is built:

    bash tools/cuewire_scope.sh run -- \
      --mic 'YOUR_PHYSICAL_NODE_NAME' \
      --monitor 'YOUR_HEADSET_NODE_NAME' \
      --cue 0=/absolute/path/zero.wav \
      --cue 1=/absolute/path/one.wav

In a second terminal, test the same control protocol manually:

    ./target/release/cuewire ctl play 0
    ./target/release/cuewire ctl play 1
    ./target/release/cuewire ctl stop

These do not alter microphone DSP or system defaults. Stop via
`./target/release/cuewire ctl quit` or Ctrl+C and verify socket and
child processes are gone.

## Step C — forced emergency fault test BEFORE binding Super+C

This is a deliberate TERMINATION of the app and its owned audio
clients. Save or stop any CueWire work first.

With the scoped foreground run active, from a second terminal:

    bash tools/cuewire_scope.sh emergency

Verify:
- all CueWire-owned pw-loopback and pw-play PIDs have exited;
- no CueWire virtual mic/sink remains in `wpctl status`;
- default source/sink unchanged; system PipeWire still works;
- no stale `$XDG_RUNTIME_DIR/cuewire-v0.1.sock` once wrapper exits;
- no unrelated process, window or active game was terminated;
- no persistent scope, daemon or new keyboard setting.

Then fault-inject a deliberately hung CueWire OWNED subprocess in a
safe test harness and repeat. Finally trial the manual Hyprland bind
while holding W/A/Shift/Ctrl and in a fullscreen game. Do not announce
Super+C as production-ready until the hardware tests pass.

## Out-of-scope for H1

No soundboard GUI, raw keyboard hook, secret key reader, auto-binding
daemon, auto-starting PipeWire virtual microphone, default audio device
switch or per-app configuration mutation. Voice AI model engineering
remains independently frozen in M1; long-form testing waits in M4.
