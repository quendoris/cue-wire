# M6 Physical-004 — deliberately aggressive click removal pilot

Status: OFFLINE, ZERO-PHASE, NONCAUSAL physical research. No live
virtual microphone changes, capture, daemon, audio routing or playback.

## Why this experiment exists

User A/B hearing result from Physical-003 on BOTH mouse+keyboard and
voice+clicks: keyboard attenuation was only BARELY audible in the more
aggressive version; voice sounded unchanged. This is useful subjective
feedback but does NOT certify preservation at all overlapping syllables.

Old acoustic evidence:
- M6 Physical-001 original 44.330667s 48 kHz mono PCM16 WAV,
  364 anonymous press/release events, and certified independent
  physical event association.
- Physical-003 per-class tail calibration: 36 isolated mouse events,
  14 isolated keyboard events in a stricter >65ms nonoverlap filter;
  keyboard trained cap about 59ms, mouse about 41ms.
- At most ~17.5% of samples in R3 were inside old event masks,
  explaining why actual click energy could remain.

## What's different

No hard-coded button identities, keycodes, text or motion are even read.
The source event reader rejects JSONL rows with extra fields.

- Same independently validated timing offsets; NEVER fit from heldout
  voice+clicks.
- Broader zero-phase acoustic components: 650–12,000 Hz (gain .98)
  and 260–650 Hz (gain .55). Low-mid processing is a deliberate
  higher SPEECH DISTORTION RISK; C separately protects it.
- Trigger on a local highband spike >1.7x recent baseline and at
  least .0002 RMS. Search +/-12ms for keyboard, +/-7ms for mouse.
- Extend each event's tail from the **speech-free training cap**;
  allow up to 55ms extra for keyboard and 23ms extra for mouse.
  Minimum tail lengths: 35ms keyboard, 8ms mouse.
- Suppress 85% (plus variable measured excess) of the filtered
  components within the event mask, with cosine attack and fade.
  Overlapping masks use pointwise max, NOT summed anti-sound.
- An optional speech guard attenuates the antiwave where sustained
  lowband 150–850Hz activity suggests voice. Its threshold derives
  only from speech-free isolated phases; it is a heuristic, not
  a validated speech detector.
- The original PCM16 microphone samples OUTSIDE the event mask
  must remain EXACTLY unchanged, or the exporter refuses to write.
  Clip protection rejects amplitudes >= 0.98 rather than limiting.

Three deliberately labeled (not blinded) candidates:
- A_003_adaptive.wav = EXACT old M6 Physical-003 B algorithm
  reconstructed from verified original capture
- B_004_aggressive.wav = new stronger wideband + low-mid antiwave
- C_004_voice_guard.wav = same new model, attenuated during likely voice

The reported total 1300–9000Hz band RMS decrease is descriptive ONLY.
Parameters were chosen AFTER exploratory inspection of this recording,
including the combined-device scene, so that scene is NOT an unbiased
generalization test. Low highband energy does not prove individual
keyboard-click removal or voice quality. Noncausal zero-phase filtfilt
requires future samples and must not be deployed as live DSP directly.

## Expected exploratory numeric changes (physical WAV)

These values come from the initial development probe, not from a
new user listening test or independent unseen data. New GitHub code
must reproduce them before the result can be treated as replicated.

| Scene | Old A_003 | New B_004 | New C_004 |
|---|---:|---:|---:|
| Mouse only | -1.75 dB | -2.52 dB | -2.52 dB |
| Keyboard only | -3.00 dB | -6.61 dB | -6.36 dB |
| R3 both holdout | -3.66 dB | -7.57 dB | -7.57 dB |
| R4 voice-only* | -0.09 dB | -0.12 dB | -0.12 dB |
| R5 voice+clicks | -1.94 dB | -4.41 dB | -3.14 dB |

*R4 contains 11 anonymous input events; not a clean speech reference.
Greater attenuation in dB may damage some consonants despite the
earlier Physical-003 voice being perceptually unchanged.

## Reproduce on Arch: tests FIRST, then local export

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

timeout --signal=TERM --kill-after=3s 90s \
  ~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover \
  -s tests -p 'test_voice_m6_physical_aggressive_ab.py' -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m6_physical_aggressive_ab.py \
  --capture .audit/voice-aec/M6-physical-events-001 \
  --alignment .audit/voice-aec/M6-physical-event-alignment-001/m6-physical-event-alignment.json \
  --out .audit/voice-aec/M6-physical-aggressive-001

cd .audit/voice-aec/M6-physical-aggressive-001
sha256sum -c SHA256SUMS
cat m6-physical-004-report.json
~~~

Do not run exporter if any test fails. Never overwrite a previous
private .audit directory. The exporter outputs 15 private WAVs
(5 phases x 3 versions), a JSON audit and SHA256SUMS.

## Subjective listening, low comfortable volume

First R3 (mouse and keyboard, no intentional speech):

~~~bash
pw-play R3_both_holdout/A_003_adaptive.wav
pw-play R3_both_holdout/B_004_aggressive.wav
pw-play R3_both_holdout/C_004_voice_guard.wav
~~~

Then R5 (speech with input events):

~~~bash
pw-play R5_voice_clicks_holdout/A_003_adaptive.wav
pw-play R5_voice_clicks_holdout/B_004_aggressive.wav
pw-play R5_voice_clicks_holdout/C_004_voice_guard.wav
~~~

Choose on ACTUAL CLICK AUDIBILITY and ANY voice damage or unnatural
consonants. Record 'no difference' if appropriate. Listen especially
for transient release ticks, resonances and fading tails. B is
expected to be stronger, but distortion may outweigh quieter clicks.
C is a higher-priority candidate for a voice application if B
makes the voice sound unnatural.

No private raw WAV, JSONL timing list, speech or device identity
may be added to the public repository. Future research: compare
event-conditioned STFT denoising vs broad time-domain attenuation;
a new truly unseen physical segment will be needed for claims of
generalization, followed by a separately timed CAUSAL DSP variant.
