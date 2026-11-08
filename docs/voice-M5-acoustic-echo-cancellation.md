# CueWire M5 — acoustic echo cancellation for loudspeakers (2026-11-08)

Status: **architecture and acceptance protocol**. NOT implemented or
hardware-certified in CueWire. The operator is returning to VOICE after
the log-only NumPad H2 project. Keypad and audio remain isolated.

## 1. User-visible objective

The operator must be able to hear friends' in-game voice, the game,
and music through physical room loudspeakers while speaking into the
physical microphone. Friends should hear the operator, **not their
own voices or the loudspeaker music reverberating into the mic**.

This is **acoustic echo cancellation (AEC)**, not generic noise
suppression, a studio expander, noise gate, or dereverberation.
Far-end audio is available digitally *before* speaker reproduction and
therefore can be used as a reference for modeling the acoustic room
response. Absolute elimination is not guaranteed: nonlinear speaker
distortion, clipping, continuously changing paths, non-referenced
external sound, asynchronous device clocks and double-talk constrain
performance.

Design priorities: preserve voice timbre, don't duplicate friends,
protect timing/latency, no unrequested PipeWire default changes, no
feedback loop, and no persistent PipeWire/WirePlumber/Hyprland
configuration or automatic service.

## 2. Data flow — keep intentionally shared sounds distinct

            PC friends / game / music PCM
                       |
             explicitly identified
            physical speaker playback
                   /        \
                  /          \  monitor of SAME actual sink
            real speakers     \
                 |              v
                 |        FAR-END reference R(t) [stereo if available]
                 v              |
          room acoustic echo    |
                 |              |
        RAW mic M(t) ----------> AEC (WebRTC / measured alternative)
                                    |
                                    v
                         near-end speech after AEC
                                    |
                                    v
                    optional DeepFilterNet -> DPDFNet2 Soft
                                    |
                                    v
                           safe gain/peak ceiling
                                    |
            optional desired cue (DIGITAL, AFTER AEC only)
                                    |
                                    v
                         CueWire VIRTUAL MIC ---> game / chat

Do not route the final virtual microphone signal back to its own
far-end reference. Do not send raw mic to local speaker playback
(avoid feedback). A cue deliberately shared with friends belongs
AFTER AEC so it is not suppressed as echo. Independently playing the
same cue over speakers may still cause mic pickup; include that
speaker copy in the far-end AEC reference.

Use the PCM that ACTUALLY reaches the speakers (game + friends +
music). An arbitrary monitor of an unrelated sink is not a valid
AEC reference. If audio is emitted by a physically separate device
without a digital reference available to CueWire, normal AEC cannot
reliably cancel it. Capture only a named explicitly authorized sink;
do not blanket-capture private system playback without consent.

Stereo reference is preferable for separate speakers, especially
when L/R soundtracks differ. Do not assume that mono-summing the
playback reference is equivalent. Physical mic may be mono.
Exact channel topology, rate (target 48kHz), latency and clocks must
be measured rather than invented.

## 3. Relevant supported PipeWire implementation

Current PipeWire documentation describes:

- `libpipewire-module-echo-cancel` (native PipeWire module)
- `library.name = aec/libspa-aec-webrtc` (default WebRTC AEC plugin)
- `monitor.mode` which can capture monitor ports of the *default*
  output sink rather than creating a new playback sink
- Pulse-compatible `module-echo-cancel`, with `source_master`,
  `sink_master`, `aec_method`, `aec_args` (interfaces differ).

Primary current docs:
- https://pipewire.pages.freedesktop.org/pipewire/page_module_echo_cancel.html
- https://docs.pipewire.org/page_pulse_module_echo_cancel.html

**Important:** `monitor.mode` observes the DEFAULT sink. That may
be the wrong device when the operator's headset and speakers differ;
we MUST explicitly discover and verify the actual speaker sink.
Using the desktop default sink is NOT a valid assumption, and changing
the default to make AEC work violates CueWire's architecture.

The stock PipeWire/WebRTC module is a valuable reference baseline;
it is not mandatory for final production. Do NOT copy an example
into `~/.config/pipewire/pipewire.conf.d/`, restart PipeWire, or
auto-load a module. First prototype with a wholly foreground-owned
graph or an isolated offline experiment and verify unload/cleanup.
Never leave a module resident after CueWire exits.

The old `aec.method = webrtc` native configuration syntax is
obsolete in recent PipeWire; modern native modules use
`library.name = aec/libspa-aec-webrtc`. Do not conflate it with
the Pulse-compatible `aec_method` option.

## 4. Reuse the frozen restoration chain — only AFTER AEC

Exactly preserve the approved M1 baseline:

  DeepFilterNet (deepfilter) -> DPDFNet2 Soft (dpdfnet2_soft; limit 12dB)

The local M1 result won on a short 3-second recording. The original
benchmark model identity, order and source SHA were independently
verified in the private M1 report, but exact third-party checkpoint
hashes and real-time CPU/GPU performance have not all been pinned.

Compare audio quality with and without that chain. Offline success
does NOT establish real-time suitability or acceptable delay.
For a live Steam/game mic, AEC may require 10-ms-block processing;
the heavy models may require additional buffering and GPU memory.
Report actual end-to-end latency, discontinuities and CPU/GPU budget
rather than inserting unmeasured buffers.

## 5. M4+M5 controlled recording / AEC acceptance

**Never overwrite the original M4 RAW take**. For M5 collect a
second, explicitly consented, simultaneous stereo far-end reference
and raw mic (different streams), plus a synchronized or measured
timing offset and manifest. Full WAVs are PRIVATE and local.
Do not turn on a microphone monitor to the speaker.

Four essential conditions:

1. **Far-end only:** speaker plays friends/music/test reference;
   the operator is silent. Measure raw mic leaked far-end energy
   versus post-AEC residual; assess echo audibly.
2. **Near-end only:** speaker is muted; operator reads M4 script.
   Verify AEC does not gate or distort ordinary speech.
3. **Double-talk:** speakers play while operator reads, softly and
   emphatically, turns head and changes distances. Speech must
   remain intelligible while far-end remains suppressed.
4. **Non-stationary conditions:** move head 45 degrees, move near/far
   from mic, vary speaker volume at fixed mic gain, change music/game
   content, and repeat after the playback output device reconnects
   (an explicit new test, not a hidden config change).

Use the exact same raw mic and playback reference for ALL OFFLINE
AEC algorithms and output variants. Do not compare different game
sessions or different spoken words using pointwise SDR.

Initial alternatives, with independent output folders:

A0 = raw microphone, no AEC or denoising
A1 = M1 cleanup only, no AEC
A2 = stock WebRTC AEC only, supplied with exact far-end reference
A3 = WebRTC AEC -> M1 cleanup
A4 = alternate AEC implementation -> M1 (only if independently tested)
A5 = optional tested studio postprocess AFTER A3

The long 80-100-second M4 speech script remains the spoken-text and
head/distance ground truth. The extra far-end reference captures
what the speakers were actually playing while this script was read.
Keep pre-conversion streams and their SHA256, signal formats, graph
node IDs, software versions, DSP flags and synchronization metadata.

Useful metrics / failure indicators:
- **Far-end-only** ERLE estimate = 10 log10(energy of leaked far-end
  in raw mic / residual after AEC), measured on aligned equal windows.
  It is meaningful only when there is actual reference echo and
  conditions are comparable, and is bounded by ambient noise.
- Exact delay and drift relative to far-end PCM; after sudden volume
  change, measure how long AEC re-converges.
- Double-talk voice preservation by blind listener scores, level,
  consonant quality, unexpected voice coloration and clipping.
- End-to-end live latency (mic to virtual mic), jitter/underruns,
  audible artifacts at pauses, and speaker playback continuity.
- Confirm remote self-echo is not audible. Prevent virtual mic or
  chat capture feedback loops and duplicated voice paths.
- Monitor far-end signal only for the explicitly selected speaker
  sink (not any unrelated desktop output). Access may be privacy
  sensitive; save/upload only by explicit user action.

Test both relatively quiet and louder speaker listening levels,
keeping input safely below mic ADC clipping. Once acoustic echo has
clipped at the microphone input, true restoration is not assured.
Never claim 100% suppression in arbitrary rooms.

## 6. Operator-safe next commands (READ ONLY)

From the normal terminal, not a background service:

    wpctl status
    pactl list short sinks
    pactl list short sources
    pw-cli info 0

These inspect the PipeWire/Pulse device graph, including monitor
sources if available; they DO NOT load an AEC module, change
default sink/source, create a loopback or modify settings.
`pw-cli info 0` is optional; specific IDs from wpctl status
can be queried later.

On first implementation, provide a dedicated
`cuewire aec inspect` subcommand that prints named physical
mic, physical speakers, reference monitor and intended virtual
source, and exits without modifying the graph. Then make a
separate opt-in foreground AEC scope with a strict cleanup and
crash/leak test. Do not start with a permanent config file.

## 7. NumPad H2 transition gate (separate from voice)

The keypad is a general command adapter, NOT audio-bound. Any
NumPad key may be a held modifier. The confirmed hyprlang
transparent t flag prevents shadowing by other binds, but does
not itself guarantee exclusive capture during games.

On legacy hyprlang, **n** is the non-consuming flag (passes keys
to the active window). The current H2 temporary legacy binds use
i/r/t but NOT n. A true foreground-game exclusive test is still
required: a game that uses the keypad must not receive a duplicate
event if CueWire is intended to capture it exclusively.
If a fullscreen game requests a keybind inhibitor, the dedicated
p/dont_inhibit option requires explicit opt-in and testing.
Do not automatically rewrite game/app/system bindings.

This is NOT prerequisite to the offline echo-cancellation research.

## 8. Hard safety boundaries

One manually started foreground session. No autostart, resident
daemons, aggressive default-device switching, audio config editing,
or automatic upload. Ctrl+C / scoped Super+C may stop all CueWire
children, but emergency SIGKILL cannot execute Python finally.
We must verify no stale temporary virtual nodes/modules after
termination and detect an unexpected transport change (Bluetooth
reconnect, speaker sink rename, default device switch) as a stop
condition, not silently select a different audio device.

Design requirement: if AEC loses the playback reference, do not
claim to be canceling echo. Log the fault, and explicitly select
fail-closed behavior for the virtual mic rather than leaking friends'
voices back into their call. The fallback policy itself needs user
approval before production.
