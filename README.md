# CueWire

**Foreground-only, terminal-first PipeWire virtual microphone.** Written in Rust, using the PipeWire command-line tools on Arch Linux / Hyprland. No GUI, no daemon, no autostart, no permanent system configuration.

**Current status:** v0.1 engineering preview, automated compilation and unit checks only. Real microphone/headphone audio has not yet been verified on the user's Arch machine. No denoising, reverb reduction, DSP gain/limiter or automatically registered Hyprland hotkeys in the foreground runtime. An opt-in NumPad/Super+C hotkey helper and isolated cgroup launcher now exist but require live hardware acceptance.

## Graph

~~~text
MICAudio-97i -> explicitly targeted pw-loopback -> CueWire Input (virtual sink)
                                                |       ^
CUE WAV file -> pw-play --target CueWire Input --+       |
                                                +-- CueWire Microphone (virtual source)
                                                            |
                                             manually select in Steam / game

SAME CUE file -> pw-play --target HOCO EQ24 -> headphones (cues ONLY)
~~~

Default system audio is not intercepted. The microphone is not automatically monitored into headphones to avoid feedback. The cue is played to the virtual mic and the chosen monitor as two separate player processes, so these two outputs are not guaranteed sample-synchronous.

## Install and verify

Dependencies: Arch Linux PipeWire and WirePlumber already active, pw-loopback, pw-play, pw-dump, Rust cargo/rustc. There are no Rust third-party crates.

~~~bash
git clone https://github.com/quendoris/cue-wire.git
cd cue-wire
cargo test --locked --offline
cargo build --release --locked --offline
./target/release/cuewire doctor
~~~

Obtain exact PipeWire node names (hardware identifiers may change after Bluetooth reconnection):

~~~bash
wpctl status
pw-dump | grep -E '"node.name"|media.class'
~~~

### Start in the terminal

~~~bash
./target/release/cuewire run \
  --mic 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --monitor 'bluez_output.28_7C_0F_6D_57_51.1' \
  --cue 1="$HOME/Sounds/test.wav" \
  --cue 2="$HOME/Sounds/meme.wav" \
  --latency-ms 25
~~~

CueWire creates an Audio/Sink named cuewire.mix.PID and a virtual Audio/Source named cuewire.microphone.PID. **Manually select CueWire Microphone inside Steam/game settings.** No default audio devices are changed.

In the foreground terminal:

~~~text
status
play 1
play 2
stop
quit
~~~

In a second terminal, including while a game is focused:

~~~bash
./target/release/cuewire ctl status
./target/release/cuewire ctl play 1
./target/release/cuewire ctl stop
./target/release/cuewire ctl quit
~~~

Control uses only a local Unix domain socket under XDG_RUNTIME_DIR/cuewire-v0.1.sock with mode 0600. The instance is foreground and owns its children. SIGINT (Ctrl+C), SIGTERM or quit terminate the owned players and PipeWire bridge, and remove the socket.

SIGKILL, abrupt machine shutdown or a crash cannot invoke cleanup handlers; verify orphaned state in that case. The program does not delete an existing socket without checking whether another instance may be running.

## Stages and guarantees

| Stage | Status |
|---|---|
| CLI parser, node and cue validation, safe argv-only child calls | Implemented; unit checks |
| Virtual source + input sink via pw-loopback | Implemented; hardware test still needed |
| Physical mic routed only to CueWire input | Implemented; hardware test still needed |
| Play/replace/stop one cue in both mic and monitor | Implemented; hardware test still needed |
| Foreground process, local control socket, cleanup | Implemented; live fault injection still needed |
| Independent gains, noise suppression (RNNoise), HPF, limiter, reverberation reduction | Not yet implemented |
| NumPad 0..9 and decimal helper, supervised Super+C | Opt-in Hyprland snippet/preflight and transient scope available; no automatic binding registration; hardware test pending |
| Confirmed latency, glitches, real Steam/game integration | Not measured |

The requested latency value is not a measured end-to-end latency guarantee. No sound system defaults, services, persistent PipeWire configuration, system audio monitoring, or Hyprland keyboard settings are changed.

## Engineering checks on the real machine

Before starting, note the default sink/source and check the physical mic and headset nodes exist. After launch, inspect PipeWire source/sink graph and manually select CueWire Microphone in the app. Verify raw speech and cue, verify the monitor plays cues without echoing speech, verify unrelated system audio is absent, and confirm normal quit and microphone/headset disconnect do not leave owned running processes. Any failure is a valid development finding; do not silently weaken these requirements.

References: [PipeWire loopback](https://pipewire.pages.freedesktop.org/pipewire/page_module_loopback.html), [pw-loopback Arch manual](https://man.archlinux.org/man/pw-loopback.1), [WirePlumber linking](https://pipewire.pages.freedesktop.org/wireplumber/policies/linking.html).

## PCM mixer experiment (separate from v0.1 live runtime)

The feature branch adds an **offline, deterministic mixer playground** with
seeded white noise and a synthetic kick/snare beat. It does not alter the
existing foreground PipeWire runtime or default audio routing.

    cargo test --locked --offline --all-targets
    cargo run --locked --offline --example mixer_preview -- --out mixer-preview
    pw-play mixer-preview/noise.wav
    pw-play mixer-preview/beat.wav
    pw-play mixer-preview/mix.wav

See [mixer playground design](docs/mixer-playground.md) for safety and DSP contracts.

## M4 real-microphone chain: CLEAN -> speech leveling (offline)

The accepted M4/R0 chain now has an auditable independent leveling stage:
**physical ALSA RAW -> DeepFilterNet (-D, 30 ms aligned) -> DPDFNet2 Soft
(12 dB limit) -> optional speech-aware leveler**. No live processing or
system audio changes are implied. The user's listening approval now covers both CLEAN-003 and
LEVEL-001/03-voice-leveled.wav (especially quiet speech). LEVEL-001 stays
frozen as a known-good source; LEVEL-002 candidates need a new listening review.

See [M4 clean protocol](docs/voice-m4-clean.md) and
[M4 speech level protocol](docs/voice-m4-level.md).

~~~bash
python3 -m unittest discover -s tests -p 'test_voice_m4_level.py' -v
python3 tools/voice_m4_level.py \
  --in .audit/voice-captures/M4-R0-ALSA-007/clean-003/02-dpdfnet2-soft.wav \
  --out .audit/voice-captures/M4-R0-ALSA-007/level-001
~~~

Files remain local and private in `.audit`; RAW and completed CLEAN
runs are never overwritten. Leveling estimates speech RMS, not LUFS,
and uses a sample-peak guard rather than a certified true-peak limiter.

For an A/B experiment with *independent* soft/dense compression of
loud speech, see [M4 LEVEL-002 dynamics refinement](docs/voice-m4-refinement.md):

~~~bash
python3 -m unittest discover -s tests -p 'test_voice_m4_refine.py' -v
python3 tools/voice_m4_refine.py \
  --in .audit/voice-captures/M4-R0-ALSA-007/level-001/03-voice-leveled.wav \
  --out .audit/voice-captures/M4-R0-ALSA-007/level-002
~~~

## M5 controlled acoustic echo pilot: speaker reference + physical mic

A new opt-in offline experiment identifies how an **explicit digital reference**
sounds when emitted by one real speaker and recorded through the user's
physical ALSA microphone. First 5 seconds speaker only (including
pre-recorded vocals), then operator reads a passage while music continues.
After recording, a separate offline analyzer finds clock offset, trains
a frozen linear FIR from speaker-only samples and validates held-out
echo reduction before producing a research-only subtraction candidate.

This is **not** automatic voice separation or live AEC. It does not
touch existing M4 masters, PipeWire defaults/routes, hotkeys, services
or any permanent system configuration.

See [M5 step-by-step pilot](docs/voice-m5-acoustic-echo.md),
[read-only output discovery](tools/voice_aec_inspect.py),
[paired capture](tools/voice_m5_capture.py), and
[offline AEC analysis](tools/voice_m5_aec.py).

~~~bash
python3 tools/voice_aec_inspect.py
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover -s tests -p 'test_voice_m5_aec.py' -v
~~~

The test playback and microphone WAV remain in the private `.audit`
directory. **No sound is played** by the AEC analyzer or the tests.

## Offline voice restoration lab (not yet live)

An opt-in, deterministic voice enhancement candidate lives in
[src/mixer/voice.rs](src/mixer/voice.rs). It does not modify audio devices
or the live Ctrl+C / ctl lifecycle. To compare a deliberately degraded
vocal-like fixture to its processed output:

    cargo run --locked --offline --example voice_restore -- --out voice-preview
    pw-play voice-preview/voice_before.wav
    pw-play voice-preview/voice_after.wav

For real recordings and a deterministic real-speech damage fixture, see
[voice restoration playground](docs/voice-restoration.md). This is a
limited DSP experiment, NOT certified full studio restoration or
separation of vocals from full-band music.


## NumPad H2 — standalone keypad event interpreter (preferred over H1 for keys)

**H2 supersedes H1's direct soundboard NumPad bindings.** It assigns
no audio/action to buttons. Instead it logs raw physical NumPad down/up
and single, double, hold_start/hold_end and multi-key chord gestures.

    python3 tests/test_keypad.py
    python3 tools/cuewire_keypad.py check
    python3 tools/cuewire_keypad.py render
    python3 tools/cuewire_keypad.py render --device "SEPARATE_NUMPAD_DEVICE"
    python3 tools/cuewire_keypad.py run --temporary-bindings --log-jsonl .audit/keypad/H2-001.jsonl

H2.1 autodetects legacy hyprlang and Lua providers before creating a
log. In legacy mode it temporarily installs bindi/bindri at runtime
and forwards NumPad events with a short-lived Unix datagram helper.
Lua mode uses direct custom socket2 events. Both use preflight and
cleanup on Ctrl+C. Legacy --device is unsupported and rejected;
the old H2-001.jsonl is never overwritten.

The generated Lua uses code:NN physical keypad keys and
hl.dsp.event messages to Hyprland socket2. No child process per
keypress, no keyboard evdev privileges, no recording other keys and no
audio controls. It is foreground-only; Ctrl+C normally removes its H2 bindings.
Using the explicit --temporary-bindings flag temporarily registers
Hyprland Lua binds for this terminal session via hyprctl eval; it does
not edit configuration files. Without this flag it registers NO binds.
SIGKILL cannot run cleanup; use python3 tools/cuewire_keypad.py restore
to remove only the H2 named handles if the process is forcibly killed. The optional --device allows physical
isolation of a **separate** keypad from the main keyboard. See
[NumPad H2](docs/keypad-H2-event-adapter.md).

Do NOT install the older H1 NumPad audio-bind snippet in parallel;
that older snippet is only kept for historical reference and the
independent future Super+C emergency design.

## Hotkeys H1 (opt-in only; no automatic desktop modifications)

The Rust CLI now accepts cue IDs 0..9. A separate, foreground-only
Hyprland helper can inspect existing bindings and render **manual** Lua
or legacy hyprlang bindings without making changes:

    python3 tools/cuewire_hotkeys.py check
    python3 tools/cuewire_hotkeys.py render --format lua
    python3 tools/cuewire_hotkeys.py render --format legacy

The historical H1 design used NumPad 0..9 -> cue 0..9, KP_Decimal -> stop
the current cue **without stopping voice**, and Super+C -> terminate
the explicitly isolated CueWire cgroup. Modifier-insensitive NumPad
bindings work while holding W/A/Shift/Ctrl when supported by the
keyboard/compositor. No privileged key reader or persistent daemon.

To make the emergency action exact and not kill other apps, launch the
foreground instance in a transient user scope:

    bash tools/cuewire_scope.sh check
    bash tools/cuewire_scope.sh run -- --mic NODE --monitor SINK --cue 0=FILE

Only when such a session is active, `bash tools/cuewire_scope.sh emergency`
can terminate the cgroup. This is a **forceful SIGKILL**, so the
foreground launcher additionally tries to remove a verified stale
socket after scope exit. No automatic config changes or key injection.
Do not activate bindings until on-device collision and force-kill tests
are passed.

See [hotkeys H1](docs/hotkeys-H1.md). The frozen research plan for
long-form controlled voice recording is [M4](docs/voice-M4-long-capture-protocol.md).
