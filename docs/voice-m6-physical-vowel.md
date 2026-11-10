# M6 Physical-005 — preserving prolonged vowel endings (OFFLINE)

## Human finding: suppression was stronger, but vowel ends were cut

After the first listenable real M6 Physical-004 result, the operator
reported:
- Keyboard clicks became noticeably quieter with aggressive B.
- B made the END of an elongated vowel like "Ааааа" sound
  chopped/partly removed.
- C (old sustained lowband energy guard) was calmer, but the
  vowel-end truncation was still audible.

This is a QUALITY FAILURE for physical M6-004. A numerically stronger
click reduction in dB is NOT an acceptable trade for damaged speech.
No new physical microphone capture is needed to investigate it.

## New hypothesis and limitations

A simple low-frequency ENERGY guard can drop below threshold late in a
long vowel, leaving a long event window still attenuating voice. This
is a plausible explanation, NOT yet a proven individual acoustic
cause, because no isolated clean voice ground truth exists.

M6-005 uses 90–490 Hz voice periodicity rather than only an energy
threshold. It checks 50ms frames every 5ms, measures normalized
autocorrelation over likely speech pitch periods, and checks that the
low-band level exceeds twice the 95th percentile of the initial
speaker-off, no-speech isolated mouse/keyboard phases. It then:
- bridges brief (<60ms) periodicity gaps,
- reserves 120ms BEFORE a detected voiced frame,
- continues protecting for 220ms AFTER it,
- crossfades 50ms outside protected voice intervals.

All these operations require FUTURE samples and are OFFLINE only.

Within a fully protected vowel region, B does an **exact PCM16 dry
bypass**. It cannot truncate the protected samples because NO
compensation reaches the output there. Other vowel/phoneme samples
not recognized by the detector are not certified as preserved.
Consonants, whispering and weak vowels remain open risks.

C is an experiment with just 10% of M6-004 aggressive correction
under the fully voiced region. It may still alter some vowel samples;
this is explicitly NOT a bit-exact speech-preserving model.

The original verified isolated click training, event timestamp
alignment, user privacy boundary, and event mask are unchanged.
No key/button codes, text, mouse coordinates or individual event
timestamps are exported.

## Existing private physical WAV: exploratory measurements

Same M6 Physical-001 recording, *not* new independent data.
The periodic-voice detector's strictly protected PCM fraction:

| Scene | Fraction locked |
|---|---:|
| Mouse isolated | 0% |
| Keyboard isolated | 0% |
| Mouse + keyboard holdout | 0% |
| Voice-only (contains 11 physical input events) | 51.4% |
| Voice + keyboard/mouse | 58.5% |

Physical pilot bandpower measurements versus ORIGINAL microphone
in the whole phase, 1300–9000Hz, rounded:

| Scene | A old M6-004 guarded | B new full vowel lock | C new 90% vowel bypass |
|---|---:|---:|---:|
| Keyboard only | -6.36 dB | -6.61 dB | -6.61 dB |
| Mouse + keyboard | -7.57 dB | -7.57 dB | -7.57 dB |
| Voice + clicks | -3.14 dB | -0.57 dB | -1.04 dB |

This is EXPECTED, not a regression bug. Voice protection reduces
the amount of aggressive suppression precisely when acoustic
events overlap sustained speech. Clicks may sound LOUDER during
speech in B/C than in M6-004. These metrics are combined voice+noise
energy, not isolated click SNR, a controlled hearing result or proof
of speech fidelity. M6-005's expected benefit is **speech integrity**.

Parameters and detector acceptance were explored on this SAME
physical recording. This is an engineering pilot, not an unbiased
generalization study.

## Run tests before local export

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

timeout --signal=TERM --kill-after=3s 90s \
  ~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests \
  -p 'test_voice_m6_*vowel*.py' -v
~~~

Expected: **11 synthetic unit tests OK** (7 detector and
4 signed-PCM16 mixing/export integration). Stop on red.

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m6_physical_vowel_ab.py \
  --capture .audit/voice-aec/M6-physical-events-001 \
  --alignment .audit/voice-aec/M6-physical-event-alignment-001/m6-physical-event-alignment.json \
  --out .audit/voice-aec/M6-physical-vowel-001

cd .audit/voice-aec/M6-physical-vowel-001
sha256sum -c SHA256SUMS
~~~

Output: 15 private 48kHz mono PCM16 WAVs, report
m6-physical-005-report.json, SHA256SUMS. Nothing is sent to GitHub.

## Listening experiment: ONLY the R5 speech + click section

In R3 there is no detected human voice, so B and C have the SAME
aggressive suppression: another R3 listen adds little information.
Start with comfortable low listening volume. No automatic playback.

~~~bash
pw-play R5_voice_clicks_holdout/A_004_voice_guard.wav
pw-play R5_voice_clicks_holdout/B_005_vowel_lock.wav
pw-play R5_voice_clicks_holdout/C_005_vowel_soft.wav
~~~

A = previous M6-004 C (the operator noticed truncated vowel ends).
B = strongest click suppression where NO voice is detected, FULLY
original PCM16 during detected prolonged vowels and extended tails.
C = similar, but 10% of the strong correction survives under vowels.

Judge two things separately:
1. Did the END of the prolonged vowel remain whole? Any pop/gate
   as voice tails off?
2. How much of keyboard noise remains WHEN SPEAKING versus between
   utterances? The full protected version B may leave some clicks.

If B cures chopping but clicks remain too loud in voiced regions,
the next research direction should be harmonic-aware spectral
source separation or an independent mechanical vibration reference,
NOT further indiscriminate broad-band gain. Real-time safety and
causal latency are unmeasured. Neither candidate is live-ready.
