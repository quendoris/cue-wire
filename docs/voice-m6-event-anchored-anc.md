# M6: event-anchored anti-noise — first OFFLINE implementation

**Status: experimental DSP and synthetic unit/CLI verification. NOT live,
NOT speech-quality-certified, NOT a replacement for approved M4/M5 processing.**

## Principle

Keep physical microphone samples unchanged until the final numerical mixer.

    mic = voice + acoustic_music + physical_clicks + other_noise
    anti_music = -(causal FIR(aligned_known_playback_reference) * held_gain)
    anti_clicks = -sum(calibrated_class_template at mic_sample_event)
    result = mic + anti_music + anti_clicks

The parallel inverted tracks are ordinary digital subtraction. They do
NOT create anti-phase waves in the user's room. This design is similar
to a two-source adaptive acoustic echo canceller plus a mechanical
event-conditioned acoustic predictor, not a new physical ANC device.

**Amplitude alone is not enough.** The reference must have the correct
sample delay, polarity and frequency-dependent phase/room response.
A *measured*, nonnegative --acoustic-lag-samples first shifts the
sample-aligned digital farend; then M6 fits a short causal 128-tap FIR
(2.67ms at 48kHz) to labeled speaker-only calibration. Default lag
is zero for pre-aligned references. The earlier M5 10ms-advanced
reference would require an explicitly justified 480-sample shift
before this filter, rather than incorrectly trying to fit 10ms
with 128 taps. Long reverberation tails, nonlinearities,
clock drift, and physical speaker movement are NOT solved by 128 taps.
A common gain can adapt over time but ONLY after explicitly marked
speech-free, click-free intervals; it freezes over voice/doubletalk.
This avoids silently minimizing the user's actual near-end speech.

The input timestamps for mechanical events must be corrected into
**microphone PCM sample indices**. Keyboard event delivery can occur
after the physical key sound. Templates therefore begin 5ms BEFORE
the event marker and last 75ms after. This remains an experimentally
chosen bound, not a measured hardware latency guarantee. Per-kind
template uses a median over >=3 **isolated no-music no-speech**
calibration events (mouse/keyboard press or release). Differences in
key force, key position, mouse button and simultaneous events may
remain. No model may train click shape on speech-containing audio.

## First run: complete synthetic pipeline

No audio device is accessed, no playback is performed and no real
voice/keystrokes are saved. This is a safe deterministic smoke test
using generated 48kHz mono PCM16 WAV and anonymized per-class event
markers, all under ignored private .audit.

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p 'test_voice_m6_*.py' -v

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m6_fixture.py \
  --out .audit/voice-aec/M6-synthetic-001

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m6_event_anc.py \
  --cal-mic .audit/voice-aec/M6-synthetic-001/cal-mic.wav \
  --cal-reference .audit/voice-aec/M6-synthetic-001/cal-reference.wav \
  --cal-events .audit/voice-aec/M6-synthetic-001/cal-events.jsonl \
  --music-only 0.35:1.05 \
  --acoustic-lag-samples 0 \
  --speech-free-calibration-confirmed \
  --reference-sample-clock-confirmed \
  --mic .audit/voice-aec/M6-synthetic-001/eval-mic.wav \
  --reference .audit/voice-aec/M6-synthetic-001/eval-reference.wav \
  --events .audit/voice-aec/M6-synthetic-001/eval-events.jsonl \
  --out .audit/voice-aec/M6-synthetic-result-001

cd .audit/voice-aec/M6-synthetic-result-001
sha256sum -c SHA256SUMS
~~~

All resulting filenames are private and NOT to be committed:
- 01_physical_raw.wav — unmodified input re-encoded at unity
- 02_inverted_music_estimate.wav — separately viewable musical anti-track
- 03_inverted_click_estimate.wav — separately viewable mechanical anti-track
- 04_mic_minus_music.wav — only the music reference correction
- 05_combined_compensation.wav — raw + anti_music + anti_clicks
- m6-report.json — input/output hashes, FIR fitting windows, timing
  limitations, number of modeled clicks and causal gain metadata.

The fixture also contains synthetic-clean-voice-truth.wav. **This truth
exists only in the synthetic fixture**, never in mixed physical M5
doubletalk. Compare numeric deviation to known voice before real use.

All gain 1 unless --speech-free-update is explicitly supplied.
A CLI gain update window is a no-voice/no-click interval on the
**evaluation recording**, used to tune only the common music estimate.
No dynamic fitting is done against speech. An actual physical input
requires one shared 48kHz sample clock for the microphone and reference;
merely starting pw-record and a music player at about the same time
does NOT qualify as synchronization.

## Physical keyboard/mouse events, optional future calibration

The optical/USB event time itself is NOT automatically audio-frame time.
A separate foreground-only tool can gather **timing** (not key
identity, typed text, mouse coordinates or device names in its output).
Keyboard timing patterns are still potentially sensitive metadata.
Only opt in during a deliberately isolated 30s calibration; do not
use while entering any password or private text.

A user with the optional python-evdev package and read permission on
their explicitly chosen device can run:

~~~bash
cd /data/cue-wire
~/.venvs/cuewire-voice-lab/bin/python tools/voice_m6_event_clock.py \
  collect --keyboard-device /dev/input/eventN \
  --mouse-device /dev/input/eventM \
  --seconds 30 --acknowledge-input-timing-privacy \
  --out .audit/voice-aec/M6-input-timing-001.jsonl
~~~

Replace N/M with **real hardware device identifiers** after checking
locally. No device autodiscovery, grabs, global hotkeys or always-on
process are used. Ctrl+C ends it, or it stops at the chosen duration.

That timing JSONL is NOT directly accepted by M6 because the precise
audio-frame-zero timestamp is not yet known. The explicit clock bridge
requires a **verified audio sample-zero CLOCK_MONOTONIC ns anchor** and
an **independently measured event-to-acoustic sample offset**:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python tools/voice_m6_event_clock.py \
  convert \
  --input .audit/voice-aec/M6-input-timing-001.jsonl \
  --mic .audit/voice-aec/RECORDER/MIC.wav \
  --sample-zero-monotonic-ns VERIFIED_NUMBER \
  --acoustic-offset-samples MEASURED_NUMBER \
  --sample-zero-verified --offset-measured \
  --out .audit/voice-aec/M6-synced-events-001.jsonl
~~~

This is a **contract**, not an immediately runnable command: both
uppercase placeholders require real measured calibration data, and
the clock bridge does not magically measure latency or drift.
No source M5 Chaos-001 event stream exists, so M5 cannot certify
the click part retroactively. Next hardware pilot must capture mic
and events together with a shared trace and derive the offset from
isolated mechanical impulses. The current 90s M5 audio and music
reference remain useful for a separate FAR-END ONLY AEC pilot.

## Hard safety and validity gates

- Keep every physical WAV, event JSONL and output under .audit (gitignored).
- Foreground-only tools; no default sink/source changes, no monitoring,
  routing, system config, hotkeys, daemons or autostart.
- No keycode, typed characters, passwords, positions in the event file;
  reject any extraneous JSON key; only kind and mic sample.
- Never fit the music FIR or adjust its gain during speech.
- Never fit click templates during music or human speech.
- Fail if predicted + recorded waveform exceeds 0.98 absolute;
  NO compressor, limiter, normalizer or invisible clipping correction.
- Never overwrite output directories or existing WAVs; include hashes.
- Untested physical latency, overlapping clicks, nonstationary acoustic
  paths, physical voice quality and subjective sound remain OPEN.
- For a future LIVE implementation, the current offline FFT FIR
  preview and large arrays must be replaced with a bounded block DSP
  engine and explicitly tested end-to-end latency/cleanup.

## Regression scope

Tests exercise a known synthetic ground-truth voice simultaneously
with loudspeaker reference and mouse/keyboard impulses, check restored
voice and exact anti-track summation, detect nearend contamination by
unapproved update intervals, reject incorrectly aligned/private input
markers, enforce clipping refusal and verify synthetic WAV roundtrips.
A real user's listening preference is the independent final gate.
