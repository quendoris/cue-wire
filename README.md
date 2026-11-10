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
explicitly chosen physical microphone (ALSA hw: or PipeWire alsa_input.*).
After the M5-001..003 ALSA pcm_read EIO failures the pilot adds an
opt-in, explicit PipeWire capture backend, with no automatic fallback.
First 5 seconds speaker only (including
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


## M6 — predictive event-anchored anti-noise (EXPERIMENTAL, offline)

New research-only M6 builds two separately inspectable *inverted*
digital audio tracks from **known causes** of unwanted sound:
(1) an externally sample-aligned loudspeaker PCM reference, modeled
with a short causal FIR and speech-free-only dynamic gain;
(2) explicitly time-stamped keyboard/mouse event classes, modeled as
isolated acoustic click templates from no-voice calibration.
The mic is summed with these anti-tracks at the end; no neural
voice-processing model is trained from doubletalk.

Not a real-time AEC, no physical anti-sound in the room, no certified
sample-zero input event timestamps yet. The public repository contains
only code and synthetic fixtures, never the user's private voice/music.

See [M6 design, controls and runnable synthetic end-to-end test](docs/voice-m6-event-anchored-anc.md).

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p 'test_voice_m6_*.py' -v
~~~

No background listener, no autostart, no PipeWire changes. Optional,
explicit evdev timing collection is foreground-only and stores no
key identities or text. Real audio-clock anchoring still requires
independent hardware calibration; never guess it from process launch.


## M6 PHYSICAL-001 — real recorded microphone, NO new recording

New private offline replay reuses the previously SHA256-pinned M5 Chaos
90-second mic, original local music and frozen two-restart alignment.
It fits a speaker-only 120ms FIR in 12–18s (music + clicks, no voice),
adjusts music estimate gain causally only at 20–26s (no voice, some
clicks), freezes before heldout 28–34s and real speech+music 53–83s,
and compares RAW, native Speex, frozen M6, adapted M6 (four variants).
The physical 37–43s voice-only stage has mandatory exact PCM16
unchanged-speech checks before AND after common attenuation. Outputs
private 48kHz WAVs and a pinned report. No sound is played automatically.

**Mechanical event capture was absent in old M5.** This physical
pilot tests known-PCM anti-music only and DOES NOT suppress mouse or
keyboard events. Event-conditioned anti-click needs a separate short
synchronized calibration at a later stage.

See [M6 PHYSICAL-001 reproducible commands](docs/voice-m6-physical-replay.md).

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p 'test_voice_m6_physical_replay.py' -v
~~~


## M6 PHYSICAL-EVENT-001 — anonymous mouse/keyboard event capture

The existing M5 music+microphone recording is adequate for physical
anti-music replay, but LACKS exact press/release event timestamps.
For a reproducible mechanical-click experiment, an optional
**foreground-only ~44-second** M6 capture is available:
one explicitly chosen real microphone + two explicitly approved
Linux evdev event sources. Written event records store ONLY coarse
class (keyboard/mouse press/release) and kernel monotonic event
timestamp; **no key identity, button code, text, device ID, mouse
position or movement**. No background capture, automatic playback,
routing changes, re-run or event grab. PCM sample-zero alignment
MUST be validated later from real calibration clicks.

See [short physical M6 anonymous event recording protocol](docs/voice-m6-physical-event-capture.md).

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover \
  -s tests -p 'test_voice_m6_physical_event_capture.py' -v
~~~


## M6 PHYSICAL-EVENT-ALIGN — independent heldout timestamp/acoustic audit

The original 44.33s microphone and 364 anonymized input events were
SHA256-verified. The new offline tool fits per-class effective temporal
offsets ONLY on speaker-off/no-voice isolated mouse and keyboard stages,
then validates on a heldout phase with both devices, testing against
2000 phase-preserving circular timing permutations. It writes only a
private audit JSON; no real audio or event timestamp series to GitHub.
This verifies a physical **correlation**, not yet click cancellation,
human speech preservation or absolute PCM sample-zero anchoring.

See [M6 physical acoustic/event alignment reproducible audit](docs/voice-m6-physical-event-align.md).

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover \
  -s tests -p 'test_voice_m6_physical_event_align.py' -v
~~~


## M6 PHYSICAL-002 — event-triggered anti-click waveform A/B (OFFLINE)

Following successful independently verified mouse/keyboard event-to-acoustic
alignment, M6 can now build three private candidate WAVs per scene:
A original, B conservative (.40) and C stronger (.75). Event markers
serve only as time anchors; the compensating in-phase high-frequency
wave is built from the ACTUAL physical microphone transient. Outside
short triggered event masks, the original PCM16 samples are bit-exact
preserved. The non-causal zero-phase filter is OFFLINE-only.

This is a **prototype, not proved echo/noise removal or safe speech
processing**. Initial parameter selection used this same recording, so
its numerical heldout results are descriptive, not unbiased validation.
No private WAV/event stream or key/button identity is published.

See [M6 Physical-002 local commands and validity limits](docs/voice-m6-physical-click-ab.md).

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover \
  -s tests -p 'test_voice_m6_physical_click_ab.py' -v
~~~


## M6 Physical-003 — duration-adaptive click anti-wave (OFFLINE)

Physical-002 preserved speech outside event windows but audible keyboard
clicks remained. Physical isolated-event analysis found median keyboard
acoustic decay around 40ms (versus ~5.5ms for mouse); the previous
~8ms suppression window missed much of the keyboard tail.

New M6 Physical-003 learns per-device-class decay bounds ONLY from
isolated speaker-off, speech-free training events, then predicts an
event-limited, dynamically decaying anti-wave from current microphone
highband. B = dynamic tail; C = same anti-wave with lowband voice guard.
A = unmodified mic. Exactly THREE candidates; 5 scene exports.
Privacy remains strict: no key/button identity, text or coordinates.
Off-event PCM16 is exact. All processing is NONCAUSAL, OFFLINE,
research-only; subjective voice safety is NOT proven by energy metrics.

See [M6 physical adaptive-tail test and Arch commands](docs/voice-m6-physical-tail.md).

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover \
  -s tests -p 'test_voice_m6_physical_tail_ab.py' -v
~~~


## M6 Physical-004 — aggressively event-gated multi-band click reduction (OFFLINE)

The operator confirmed Physical-003 preserved voice on two replayed
scenes, but audible keyboard suppression remained minimal. The new
M6 Physical-004 expands keyboard transient/tail coverage, lowers
event confirmation thresholds and adds wideband 650–12000 Hz plus
low-mid 260–650 Hz inverted event-local corrections. Two NEW variants:
B aggressive, C similar with speech-aware guard; the third A
regenerates the previous M6-003 stronger processing for direct A/B.

Exploratory real WAV measurements show a larger RMS decrease but
speech protection MUST be rechecked by listening. Because these
choices were explored on the same recording, the reported changes
are NOT unbiased heldout speech/click SNR estimates. The processing
is still zero-phase NONCAUSAL and OFFLINE ONLY; no new audio or input
capture, keycode, button identity, defaults or background processes.

See [M6 Physical-004 listening and regression audit](docs/voice-m6-physical-aggressive.md).

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover \
  -s tests -p 'test_voice_m6_physical_aggressive_ab.py' -v
~~~


## M6 Physical-005 — speech-locked vowel-end protection (OFFLINE)

Physical-004 was the first perceptually effective keyboard attenuation,
but the operator reported that aggressive B chopped the final part
of sustained vowels such as "Аааа"; C was less aggressive but still
damaged voice. This is a quality failure even with lower spectral RMS.

M6 Physical-005 explicitly recognizes voiced periodicity using a 90–490Hz
acoustic pitch cue calibrated ONLY on speech-free isolated phases.
It maintains an exact PCM16 source bypass during voiced regions, holds
120ms before/220ms after detected voice and gently releases over 50ms.
Three local options: A former 004 guarded, B new exact vowel bypass,
C new 90% vowel bypass (10% residual compensation). Speech-free
keyboard attenuation remains strong; when clicks overlap vocal tones,
their suppression will be deliberately WEAKER to protect speech.
Speech not recognized by the detector is NOT certified preserved.

See [M6 Physical-005 protected vowel tails and listening commands](docs/voice-m6-physical-vowel.md).
Synthetic regression: 11 tests across two files with vowel in event
overlap and strict off-event PCM16 invariants. No private WAV or input
event timestamp is published. Processing remains OFFLINE/noncausal.

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover \
  -s tests -p 'test_voice_m6_*vowel*.py' -v
~~~


## M6 Physical-006 — stronger keyboard separation while preserving C_005

The user's preferred M6 Physical-005 C sounded like natural uninterrupted
speech and clearly quieter keyboard clicks, but remaining clicks were
still audible. M6-006 keeps that exact C_005_vowel_soft as A and adds
a noncausal harmonic-percussive spectral correction **only** where an
anonymous key/button event overlaps recognized voiced speech. Two
strengths (B .65, C 1.0) permit an explicit maximum-three-candidate
comparison. All already validated speech-free click processing remains
exactly equal to M6-005; all physical PCM16 outside events is exact.

Exploratory real physical 44s pilot yielded additional ~1.49 and
~2.12dB attenuation of total 1.3–9kHz band RMS during R5 voice+
clicks, with only very small 90–650Hz level changes. These are NOT
clean speech/click-source measurements or perceptual guarantees;
higher-frequency consonants may still change. Parameters were
explored on the same physical recording. No key/button ID is read,
nothing is recorded or auto-played, and no live PipeWire processing.

See [M6-006 physical harmonic/percussive listening audit](docs/voice-m6-physical-percussive.md).

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover \
  -s tests -p 'test_voice_m6_physical_percussive_ab.py' -v
~~~


## M6 Physical-007 — residual keyboard click reduction, C006 FROZEN

User selected **C_006_percussive_strong** as best real physical
speech-preserving click attenuator after comparing B/C from M6-006.
M6-007 preserves that exact C006 as A, then tests two *additional*
anonymous keyboard-event-gated corrections: B short STFT residual
attack; C stronger short attack plus protected ringing tail using
pitch-harmonic notches. No key/button identity, new capture, live
PipeWire processing, background services or autoplay.

Preliminary repeated-on-same-physical-capture 1300–9000Hz full-R5
reductions relative to C006 are about 0.45dB for B and 0.66dB for C;
these are only descriptive energy changes, NOT new unbiased validation
or guaranteed preservation of consonants. Off-keyboard-event PCM16
must be EXACTLY C006, and A output should SHA256-match prior C006
output. Operator listening is the next mandatory quality gate.

See [M6 physical keyboard residual A/B and integrity audit](docs/voice-m6-physical-residual.md).

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover \
  -s tests -p 'test_voice_m6_*residual*.py' -v
~~~


## M6 Physical-007 — RESEARCH ACCEPTED (2026-11-10)

Physical-007 is the accepted *offline research checkpoint* for
anonymous event-driven keyboard-click attenuation while preserving
voice as judged by the operator. Physical-007 sounded noticeably
better in user listening; its family of A=C006 reference, B=short
attack, and C=short attack+harmonic-protected tail is frozen together.
The user did not explicitly specify B vs C as the individually
selected 007 winner, so do not silently assign that preference.

This does NOT certify objective clean voice quality, unseen-session
generalization, causality or live PipeWire readiness. No further
unsupervised changes to the accepted model. Physical source WAVs and
anonymous event timestamps remain exclusively in private ignored
.audit.

See [M6-007 signed-off research freeze and next-stage gates](docs/voice-m6-physical-007-research-freeze.md).
