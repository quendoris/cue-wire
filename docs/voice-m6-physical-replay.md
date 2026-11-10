# CueWire M6 PHYSICAL-001 — reuse existing 90-second real microphone M5

**This is an OFFLINE physical-microphone anti-music pilot.** No new
recording needed. It reuses the exact earlier M5 Chaos-001 physical mic
recording, original copyrighted stereo song *locally*, two measured
playback lags and the pinned native-Speex numeric report. Source
SHA256 and experiment phase annotations must match. This is an
investigation, **not** a verified production echo canceller.

## What existing audio supports

M5 recorded these distinct phases:
- 12–18s: music + mouse clicks; NO near-end speech (train FIR only)
- 20–26s: music + keyboard; NO near-end speech (conservative gain updates)
- 28–34s: music + both devices; NO near-end speech (**held out**)
- 37–43s: real human near-end voice, loudspeaker OFF and no clicks
  (**hard exact PCM16 preservation gate** for both M6 estimates)
- 53–83s: REAL simultaneous voice, music, clicks
  (**held-out subjective doubletalk evaluation**)

**Physical input-event timestamps were NOT captured in M5.** This
experiment performs **zero** mouse/keyboard cancellation. It cannot
recover source-event times or validate event-conditioned anti-click
templates from the music/mic alone. There is no pretend click model,
no guessed key events, and no relabeling of residual noise as removed.
For that second part of M6 a short, controlled synchronized new capture
will be needed later, NOT a repeat of 90-second chaotic recording.

## Four candidates (within requested max five)

1. ORIGINAL RAW mic
2. Native SpeexDSP AEC, exactly as M5
3. M6 frozen 120ms causal acoustic anti-music FIR (fit on 12–18s)
4. M6 FIR with causally adjusted scalar gain based ONLY on 20–26s
   no-voice interval; gain frozen from 26s onward, before holdout/doubletalk

The musical reference is generated using the same *two previously
measured* M5 playback lags and the same average stereo channel and
music volume as Speex, without fitting lag from doubletalk. The FIR
is regularized CSD with a truncated 120ms impulse tail and 8ms fade.
The training music+mouse stage contains some clicks, so FIR fitting is
not perfect or speech-quality-certified. In the later no-voice
adaptation stage, robustly trimmed 100ms blocks can still be
contaminated by keyboard impacts. This is a real limitation explicitly
reported, not a guaranteed click exclusion.

All four candidates within ALL scenes share a single linear
attenuation-only gain if needed to enforce -3dBFS sample peaks.
No volume boosting, clipping, compression, limiting or EQ is inserted.
Output tracks remain PCM16 mono 48kHz, private inside .audit.
The raw 90s original, M5 reports, old A/B tests and all prior
sources are immutable.

## Run on Arch, from the existing feature branch

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover \
  -s tests -p 'test_voice_m6_physical_replay.py' -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m6_physical_replay.py \
  --capture .audit/voice-aec/M5-chaos-001 \
  --initial-alignment \
    .audit/voice-aec/M5-chaos-alignment-001/music-alignment.json \
  --consistency \
    .audit/voice-aec/M5-chaos-alignment-consistency-001/alignment-consistency.json \
  --previous-report \
    .audit/voice-aec/M5-chaos-speex-real-001/physical-chaos-speex.json \
  --out .audit/voice-aec/M6-physical-replay-001
~~~

If any test fails, do NOT proceed to audio. If the script refuses a
wrong SHA, acoustic onset, stage bound, or voice-only control, that
is a valuable negative result; do not bypass the gate.

Then independently validate generated checksums:

~~~bash
cd /data/cue-wire/.audit/voice-aec/M6-physical-replay-001
sha256sum -c SHA256SUMS
~~~

Outputs:
- R1_clean_voice: RAW, Speex, M6 frozen, M6 adaptive (6s)
- R2_keyboard_mouse_music: four options (6s)
- R3_real_doubletalk: four options (30s)
- physical-m6-report.json, SHA256SUMS

Do NOT assume every WAV is safe at a previous headset volume. Start
quiet, then adjust hardware volume manually; do not modify files.
For early checking, first replay ONLY the four R2 tracks, then R3.
Never auto-loop or auto-play speech. Use pw-play manually.

~~~bash
pw-play R2_keyboard_mouse_music/01_original_raw.wav
pw-play R2_keyboard_mouse_music/02_speex_baseline.wav
pw-play R2_keyboard_mouse_music/03_m6_frozen_anti_music.wav
pw-play R2_keyboard_mouse_music/04_m6_adaptive_anti_music.wav
~~~

The R2 window is heldout even though the R1 and amplitude-update stages
contain some click activity. RMS energy in R2/R3 is a MIXTURE energy
proxy and **NOT** proof of isolated echo cancellation, intelligibility,
click removal or preserved clean doubletalk speech. If M6 wins in a
sighted listen, follow with a random blinded comparison before choosing
a new algorithm.

## Afterwards: minimum new physical data for mouse/keyboard

Only if the above music-only physical pilot is useful: capture a short
speaker-OFF no-speech isolated sequence of three or more mouse presses,
keyboard presses and releases, recording mic samples and synchronized
event class timestamps. Capture an extra microphone-with-speech section
only as a holdout, NEVER for acoustic-template learning. The precise
CLOCK_MONOTONIC / PCM sample-zero mapping and event-to-mic delay must
be measured; event read/receive time is NOT itself guaranteed to mark
acoustic onset. No normal typing, key identity, passwords or text
is required or wanted; the collector stays opt-in, foreground-only.
