# CueWire — M6-to-M7 integration handoff and first terminal interface

**2026-11-10 • Scope:** research summary, implementation boundary and
a working operator-console preview. This is not a new M6 experiment.

## What is frozen

The accepted research checkpoint is the separate branch:
archive/m6-physical-007-research-accepted-20261110
at commit 05b371debfbd6fb404c0fee6e3ff7f40440ada73.

All physical recordings, anonymous input events and generated WAVs stay
ONLY in the local ignored .audit directories. GitHub stores code,
synthetic tests, methodology and source-independent summaries.

M6 Physical-007 was preferred as a GENERATION in real operator
listening. The operator did not explicitly identify B_007 vs C_007
when accepting the final generation, so freeze the whole three-way
reference group rather than fabricating an individual winner.

Prior subjective results:
- M6-002: highband change measured, little audible click attenuation.
- M6-003: adaptive tails gave barely audible improvement; voice sounded
  unchanged during the listening test.
- M6-004: clicks quieter, but an extended 'Аааа' voice ending was cut;
  that approach failed the speech-quality criterion.
- M6-005 C: natural voice and considerably quieter keyboard,
  though clicks remained clearly audible.
- M6-006 C: preferred over B; keyboard appreciably quieter, natural voice.
- M6-007: operator stated it was noticeably better and requested freeze
  and transition to the next development.

This is a SUCCESSFUL OFFLINE physical pilot, not a shipping DSP module.
The test data were repeatedly examined during parameter selection,
so reported full-stage bandpower differences are descriptive and
cannot establish unseen-session or clean-source noise reduction.

## What code already exists

| Layer | Existing implementation | Readiness |
|---|---|---|
| M4 voice clean/level | offline Python experimentation and listening | OFFLINE, not in live run |
| M5 echo reference | physical offline captures / analytic FIR work | EXPERIMENTAL, no live AEC |
| M6 event-time alignment | kernel monotonic class events + physical WAV | validated on this capture only |
| M6 accepted click cancellation | Physical-007 residual STFT on M6-006 C | OFFLINE, noncausal |
| PCM mixer | Rust src/mixer modules and synthetic offline preview | offline; not wired into live runtime |
| Existing v0.1 engine | Rust cuewire run/ctl via pw-loopback and pw-play | exists; physical acceptance incomplete |
| Cue triggering | 0..9, stop and status over user-owned Unix socket | supported by engine |
| Hyprland Super+C scope | separate opt-in tools/cuewire_scope.sh | do not silently install binds |
| M7 console | tools/cuewire_console.py | initial operator interface only |

The engine's current status response contains mic, mix input, virtual
source, monitor and active cue. It does NOT expose current processing
modules, DSP CPU usage, latency, XRUN counts or real device quality.
The UI therefore does not draw fake DSP toggles or false metrics.

## Integration architecture (target, NOT implemented yet)

1. Owned physical microphone -> explicit input, never change system default.
2. Rust-owned bounded PCM ring buffers and deterministic clock mapping.
3. Optional causal M4/M5/M6 DSP in separately testable processors.
4. Sample-aligned voice and cue mixer with overload guard.
5. Owned PipeWire virtual source manually selected by Steam/game.
6. Foreground owner and opt-in scoped emergency shutdown for ALL
   owned children; no background process or autostart after exit.
7. M7 user interface reads state via control protocol and requests
   actions, not by directly altering audio graph.

M6-007 uses noncausal STFT, medians and zero-phase filters, so simply
calling it on a live callback is INCORRECT. A separately designed
causal/limited-lookahead version must pass new independent recordings,
CPU, memory, glitch and end-to-end latency audits. No latency target
has yet been measured or promised. Do not enable untested modes.

## M7.0 console — first executable interface

New development branch: feature/m7-terminal-interface.
The archived M6 branch remains unchanged.

This slice intentionally uses only Python stdlib to talk to the
EXISTING Rust v0.1 local control socket. It is not a second audio
engine and not a GUI. It runs in foreground without writing config.

Safe preview without a socket, PipeWire or microphone:

~~~bash
cd /data/cue-wire
git switch feature/m7-terminal-interface
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_cuewire_console.py' -v
python3 tools/cuewire_console.py --preview
~~~

Read-only check of an already running CueWire:

~~~bash
python3 tools/cuewire_console.py --once
~~~

Interactive interface, in a SEPARATE foreground terminal, when
the existing CueWire run process is already active:

~~~bash
python3 tools/cuewire_console.py
~~~

Supported commands: status, play 0..9, stop, research, quit, exit, help.

- status: request existing runtime snapshot, including active cue.
- play 0..9: trigger ONLY a cue already configured by engine run.
- stop: stop cue playback ONLY, not the microphone or engine.
- research: show OFFLINE module readiness; cannot enable M6.
- quit: must type QUIT to ask the engine to shut itself down.
- exit or Ctrl+C: close ONLY the operator console, engine is untouched.
- Super+C: existing separately configured opt-in scope emergency
  policy; this console neither installs nor impersonates it.

The UI checks XDG_RUNTIME_DIR and the owner/mode of the socket
before sending commands. It refuses non-sockets and symlinks,
malformed input and unsafe terminal control sequences. It sends a
strictly bounded allowlist over the existing Unix socket, not
through a shell. No microphone or input event timestamps are logged.

The core engine must continue to be started manually with known,
explicit PipeWire nodes and cue WAV paths as documented in README.
The console does NOT automatically start the engine, select a
default source, configure a headset or create a virtual microphone.

## M7.0 validation checklist

- Offline preview succeeds with no PipeWire and no existing socket.
- 10 synthetic Python unit tests pass.
- Runtime panel is accurately OFFLINE when engine not running.
- With an existing Rust engine running: status reflects its real
  microphone, virtual source, monitor and active cue.
- play N and stop operate via the existing same-user socket.
- exit/Ctrl+C leaves running engine alive by design.
- quit requires explicit operator confirmation, and relies on
  the owner's normal cleanup; no unverified killall fallback.
- Pre-existing M6 source and any private audio remain unchanged.
- No filesystem configuration, Hyprland keybinds or PipeWire defaults
  modified by this console.

Until live hardware tests actually pass, do not claim them green.
The next development milestone is a Rust streaming DSP scheduler
and a richer, versioned telemetry protocol for the console.
