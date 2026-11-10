# M6 PHYSICAL-EVENT-001 — real event/audio alignment

Status: evidence-based PHYSICAL alignment probe, NOT an anti-click
processor or a sample-zero clock certification.

The M6 physical event capture already contains all required inputs:
44.330667 s original PCM16 mono 48 kHz mic, and 364 anonymous class
press/release kernel-clock event markers (132/132 keyboard, 50/50 mouse).
SHA256 of the uploaded WAV, manifest, and events matched the source.

No key identity, mouse button code, typed text, or mouse movement was
stored. No new audio capture is needed for this alignment experiment.

## Independent training versus heldout

The new tool calculates a causal 1300–9000 Hz microphone highpass/bandpass
transient strength in 1ms bins (local 251ms median baseline, log scaling
and 1ms Gaussian smoothing). For each anonymous press/release kind,
it searches -50..+100ms EFFECTIVE offset using ONLY the separate
physical mouse-only/keyboard-only calibration stage, with no local voice.

Then it freezes the offset and scores the 7-second both-device heldout
stage. It compares the true timing against 2000 circular time-shifted
surrogates WITHIN that same heldout stage, preserving event rhythm
and density. Each surrogate shifts by at least 250ms. No heldout time
is used to optimize offsets.

Exploratory independent measurements on the real supplied physical
source show:

| Event | Training-derived effective offset | Heldout event strength | Circular null mean | P-value upper |
|---|---:|---:|---:|---:|
| Mouse press | 19 ms | 3.268 | 1.420 | 0.0005 |
| Mouse release | 17 ms | 3.503 | 1.406 | 0.0005 |
| Keyboard press | 24 ms | 3.688 | 1.405 | 0.0005 |
| Keyboard release | 10 ms | 3.574 | 1.396 | 0.0005 |

Scores are unitless engineered spectral-envelope features, not decibels
and NOT noise-reduction values. P-values are estimated from 2000 null
shifts with (1 + exceedances)/(2001). The new fixed-seed GitHub tool
can generate slightly different random null averages from the first
exploration, but MUST reproduce heldout association, or fail.

These offsets MIX two unknowns: PCM audio-clock offset relative to
the recorder's monotonic launch time AND the physical timing between
acoustic click and kernel-reported press/release. They CANNOT be
interpreted as independent true mic_sample_zero timestamps.
The tool explicitly reports sample_zero as unmeasured.

## Run on the original local private capture

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

timeout --signal=TERM --kill-after=3s 90s \
  ~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover \
  -s tests -p 'test_voice_m6_physical_event_align.py' -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m6_physical_event_align.py \
  --capture .audit/voice-aec/M6-physical-events-001 \
  --out .audit/voice-aec/M6-physical-event-alignment-001

cd .audit/voice-aec/M6-physical-event-alignment-001
sha256sum -c SHA256SUMS
~~~

The output consists ONLY of:
- m6-physical-event-alignment.json: four class offsets, heldout
  validation, circular controls and explicit uncertainty flags.
- SHA256SUMS: report integrity.

No actual audio or event timestamp stream is duplicated in the output
directory. All source hashes are checked before and after computation;
no source files are rewritten, no system state or audio devices altered.

An acceptance=true only establishes strong causal correlation between
event timestamps and acoustic transients in THIS recording at
fixed effective offsets. It does NOT show that waveform subtraction
preserves voice or removes clicks. Before the actual anti-click
experiment we must learn class-conditioned sound templates on isolated
stages only, freeze them and measure residuals on the heldout scenes.
The 6-second voice-only stage contains 11 actual input events and is
not a wholly clean voice reference; mask event neighborhoods.

Physical recorder return code is 1 although the WAV is complete and
SHA256-valid. Do not speculate about the exit status without stderr.
