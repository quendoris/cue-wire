# CueWire M7.3 — integrated foreground session, defaults, hotkeys

**State:** implemented on feature/m7-terminal-interface; physical acceptance
of this NEW session mode is still pending.

M7.3 unites the existing Rust CueWire engine and the six-pane Quendoris
terminal interface, with a single owner lifecycle and an optional
read-only check of WirePlumber default capture/playback devices.
Existing M6-007 remains a FROZEN OFFLINE acoustic reference, NOT a
real-time DSP processor. No daemon, autostart, captured speech or
automatic change of system audio defaults.

## Architecture and lifecycle

1. One-time explicit local cue-profile creation with mode 0600 under
   gitignored .audit/cuewire-session-v1.json. Path and file never
   appear in public GitHub commits.
2. A read-only default-device check uses wpctl inspect
   @DEFAULT_AUDIO_SOURCE@ and @DEFAULT_AUDIO_SINK@, then pw-dump
   matching exact node.name and required media.class.
   Reject any virtual CueWire microphone as a physical source,
   non-unique names, mismatched class or absent devices.
   Explicit --mic / --monitor remain supported if the defaults
   aren't the intended capture/output. NO wpctl set-default call.
3. --run refuses an already active CueWire scope or control socket.
   It creates one marked transient systemd user scope with the same
   label as the existing cuewire_scope.sh emergency verifier.
   All potentially slow auto-detection, Rust startup, terminal UI
   and owned PipeWire subprocesses run INSIDE that killable cgroup.
4. Internal Rust start is accepted only after the existing v0.1 socket
   responds with the exact requested mic and monitor. Source file
   validation is delegated to Rust / PipeWire; launcher just checks
   an existing non-symlink WAV header before spawning.
5. MANAGED TUI session: exit/Ctrl+C requests graceful closure of both
   UI and Rust engine. Lost owned engine automatically closes the
   UI. Separately launched standalone --live still closes only UI.
6. Emergency Super+C (when manually bound) uses the existing marked
   cgroup SIGKILL so it kills the complete managed CueWire session,
   not session PipeWire or unrelated processes. A foreground parent
   outside that scope restores original TTY termios/alternate screen
   even if the interior UI was abruptly SIGKILLed. This needs
   physical verification.
7. All startup runtime diagnostics are ephemeral anonymous Linux
   memfd bytes, never written to disk. The local profile contains
   paths only; NO keyboard codes, real audio or credentials.

## 1. Initial synthetic regression gates

~~~bash
cd /data/cue-wire
git switch feature/m7-terminal-interface
git pull --ff-only

python3 -m unittest discover -s tests -p 'test_cuewire_session.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_tui_live.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_tui.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_console.py' -v
cargo test --locked --offline
cargo build --release --locked --offline
~~~

Unit tests use synthetic graph data and mock processes, no real
microphone or key input. A green synthetic suite is necessary, but
NOT a substitute for hardware/scope and stream audits.

## 2. One-time local cue profile

Saving the profile is a separately EXPLICIT user action. It does not
start PipeWire, change default audio or modify Hyprland.

~~~bash
python3 tools/cuewire_session.py --configure \
  --cue '4=/absolute/path/to/test.wav'

python3 tools/cuewire_session.py --check
~~~

For additional slots, repeat --cue N=/absolute/path. Profile updates
will NOT overwrite the existing file unless the user explicitly uses:

~~~bash
python3 tools/cuewire_session.py --configure --replace-profile \
  --cue '4=/absolute/path/to/new.wav'
~~~

The source file is local-only; the profile is owner-owned 0600
and atomic. Explicit --cue on --run overrides saved cues for THAT run.

## 3. Stop the old standalone engine, then one-command launch

Before the new unified mode, close the existing M7.2 Rust engine via
quit and CONFIRM in its standalone TUI. Otherwise --run safely
refuses to seize its socket or reuse its scope.

~~~bash
bash tools/cuewire_scope.sh check
python3 tools/cuewire_session.py --check
python3 tools/cuewire_session.py --run
~~~

The new --run reads current defaults at session start and then opens
the same six-pane Quendoris interface in LIVE/MANAGED mode.
Active devices are NOT changed continuously behind the user's back.
Use exit to close BOTH engine and UI gracefully. No persistent service.

Optional explicit manual capture/playback overrides:

~~~bash
python3 tools/cuewire_session.py --run \
  --mic 'REAL_MIC_NODE_NAME' \
  --monitor 'REAL_OUTPUT_NODE_NAME'
~~~

## 4. Hotkeys: audit FIRST, opt in through own LUA configuration

This user runs the Lua Hyprland configuration provider. Current Lua
bind API uses hl.bind and hl.dsp.exec_cmd. Legacy hyprctl keyword
bind is not used to modify the running compositor.

The included (but NOT automatically loaded!) fragment is:

tools/cuewire_hyprland.lua

| Binding | Action |
|---|---|
| KP_0 … KP_9 | cuewire ctl play 0…9 |
| KP_Decimal | cuewire ctl stop (cue only; voice remains) |
| Super+C | bash tools/cuewire_scope.sh emergency |

NumPad mappings have ignore_mods and dont_inhibit to accept gameplay
modifier keys even with input inhibition; Super+C has dont_inhibit.
These are persistent optional compositor bindings, NOT a daemon or
physical keyboard event reader. They will consume those keys even
when CueWire is stopped; do not enable casually.

Before installing, run the existing READ-ONLY hotkey conflict checker:

~~~bash
python3 tools/cuewire_hotkeys.py check
python3 tools/cuewire_hotkeys.py render --format lua
~~~

If conflicts are reported, DO NOT install until the other bindings
are reviewed and explicitly resolved. The preflight does not prove
game/window-specific keys will pass through; later physical tests
are still required. No existing binds are silently replaced.

After explicit approval, add this one line to the ACTIVE user Lua
Hyprland configuration, and reload using the user's normal process:

~~~lua
dofile("/data/cue-wire/tools/cuewire_hyprland.lua")
~~~

To disable, remove this line and manually reload. CueWire does
NOT edit user Hyprland files, install binds, or reload compositor.
The source fragment releases its OWN prior bind handles on reimport,
but never unbinds unrelated user shortcuts.

## 5. Explicit acceptance — not yet verified on real hardware

- [ ] New session/managed TUI synthetic tests pass on Arch
- [ ] Profile stays local and 0600; no Git exposure
- [ ] Default mic/sink read correctly without switching OS defaults
- [ ] Unified engine and UI run inside ONE marked transient cgroup
- [ ] Live doctor identifies the physical mic/sink and virtual nodes
- [ ] NumPad keys trigger actual cues while game modifiers are held
- [ ] KP_Decimal stops cue only, leaving voice running
- [ ] exit/Ctrl+C closes managed engine and all owned children
- [ ] Super+C immediately kills scoped processes AND TTY is restored
- [ ] No orphan cue players, virtual sources or default changes
- [ ] New/removed USB device, crashes and reconnection fail closed
- [ ] Real virtual mic audio, latency and resource budgets measured
- [ ] M6 DSP stays offline until separately ported to causal streaming

The accepted M7.2 physical PASSes for socket and graph registration
remain valid prior evidence, but do not automatically prove M7.3
single-scope cleanup or live audio fidelity.
