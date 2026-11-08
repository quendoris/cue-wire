# CueWire

**Foreground-only, terminal-first PipeWire virtual microphone.** Written in Rust, using the PipeWire command-line tools on Arch Linux / Hyprland. No GUI, no daemon, no autostart, no permanent system configuration.

**Current status:** v0.1 engineering preview, automated compilation and unit checks only. Real microphone/headphone audio has not yet been verified on the user's Arch machine. No denoising, reverb reduction, DSP gain/limiter, global NumPad or Super+C key binding yet.

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
| Global NumPad triggers, Super+C forced exit | Not yet implemented; no automatic key binding takeover |
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
