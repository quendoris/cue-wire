# CueWire — architectural boundaries and hardware acceptance

CueWire is a single foreground terminal program. It uses explicitly named nodes on Arch Linux PipeWire / WirePlumber, never touches default devices and never intercepts system playback.

## Target signal graph

Input: MICAudio-97i, mono, nominal 48 kHz. Voice is processed once and emitted into one CueWire virtual microphone for Steam/games. Cues from local files (later streams and synthesis) are mixed into that same virtual source. Soundboard cues are also routed explicitly to HOCO EQ24 headphones; raw mic is not monitored into the headset to avoid feedback.

v0.1 is a temporary PipeWire bridge made of native pw-loopback clients, with pw-play cue instances supervised by a foreground Rust binary. The Rust crate has zero third-party dependencies. It is an infrastructure prototype, not yet a real-time DSP engine.

## Implemented in v0.1

- Strict CLI flags, exact physical mic and monitor node identifiers, 1..9 cue slots.
- One runtime virtual sink and one virtual source; explicit mic route.
- One active sound cue at a time, replaced on next play.
- Two explicitly targeted copies of a cue: game mic and monitor.
- Foreground ownership, local Unix control socket mode 0600, commands from second terminal.
- SIGINT/SIGTERM/quit cleanly stops spawned children and removes socket.
- Unit checks and a hardware-free mock PipeWire lifecycle integration test.

No actual PipeWire hardware graph, acoustic quality or latency is certified yet.

## Stage v0.2 (not yet implemented)

Native callback-based unified DSP: optional voice high-pass, RNNoise or independently evaluated suppressor, voice gain and limiter, separate cue gain, final mix ceiling, 48 kHz channel/rate handling. Cue files decoded off the real-time callback; callback does not allocate or block. A bounded ring buffer handles rapid cue switching. Acoustic echo cancellation is different from dereverberation and requires a far-end reference; physical USB monitor/loopback routing problems must be diagnosed before DSP.

## Stage v0.3 (opt-in helper implemented, live activation not yet accepted)

The separate H1 module emits Hyprland Lua/legacy keybind previews for
NumPad 0..9, KP_Decimal stop and Super+C. Read-only preflight reports
existing matching keybind conflicts via `hyprctl -j binds` and
refuses to modify any compositor setting. Core cue slot 0 and the
local socket protocol are now implemented.

Super+C requires the CueWire foreground process to be launched inside a
transient user systemd scope (collectible, no resident service). The
emergency command targets that scope's CGroup with SIGKILL; it refuses
unmarked or foreign scopes, and the foreground launcher verifies a
non-listening, owned socket before unlinking a stale socket after exit.
This does **not** rely on the compositor's `forcekillactive`, which
would miss detached audio subprocesses and could kill the wrong window.

Binding installation is intentionally NOT automated: a SIGKILLed
process cannot run unbind handlers, and Hyprland now supports both
legacy hyprlang and Lua configuration. Preflight, runtime scope,
modifiers-held behavior, actual keysyms, fullscreen games, hung
subprocesses and safe cleanup all need on-device acceptance. Do not
read raw evdev devices with privileged input-group access.

See [hotkeys H1](hotkeys-H1.md).

## Acceptance checks on the real machine

1. Compare wpctl status default source/sink before and after. They must not change.
2. Confirm exact MICAudio-97i source and HOCO EQ24 sink names, including after Bluetooth reconnect.
3. Start CueWire with a known-good WAV. Verify one virtual source visible to a game.
4. Verify voice reaches the virtual source and is not echoed in headphones.
5. Play two cues quickly; only intended samples appear in the virtual mic and headset. Confirm other apps' audio never enters the mix.
6. Stop, quit, Ctrl+C, reconnect/disconnect devices, check no CueWire clients remain.
7. Measure actual latency and dropouts instead of trusting a latency request flag.

Never silently broaden access, add persistent services, modify defaults, or promote a synthetic CI pass to hardware certification. SIGKILL cannot run cleanup code; no application can guarantee cleanup of all crash modes.
