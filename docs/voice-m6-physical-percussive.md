# M6 Physical-006 — harmonic-aware EXTRA click suppression during voice

**Goal:** User heard that Physical-005 C_005_vowel_soft considerably reduced
keyboard clicks while maintaining their natural prolonged vowel. Keyboard
still remained audible, so Physical-006 preserves **C_005 as A reference**
and adds attenuation only in *existing voiced keyboard/mouse event windows*.
No new capture is required; the original 44.33s source and 364 anonymous
input press/release markers are reused.

## Crucial lesson from Physical-004 and 005

Increasing all-frequency suppression around keys damaged the END of "Аааа".
The periodic-vowel lock in 005 helped, at the cost of letting some clicks
through when the user was talking. Instead of lowering the user's entire
speech under those event masks, Physical-006 splits the acoustic signal
into relatively persistent harmonic material and broadband, short-lived
percussive material using offline harmonic-percussive source separation
(HPSS), then applies a separate anti-percussive waveform. An imperfect
separation can still damage consonants: NO speech-fidelity claim.

The 005 user-preferred 90%-bypass is frozen as the A reference.
All additional corrections are gated by BOTH existing physical M6
keyboard/mouse event windows AND the existing periodic-vowel protection.
This does not touch the successful speech-FREE keyboard filtering.

## Model / integrity

- STFT: Hann 2048 samples @48 kHz, hop 256 samples.
- Harmonic temporal median: 19 STFT frames (~101 ms).
- Percussive frequency median: 21 STFT bins (~492 Hz).
- Quadratic spectral soft mask estimates nonharmonic material.
- Reference is weighted to zero up to 650 Hz, smoothly increasing
  between 650 and 1450 Hz, full to 11.5 kHz, then fading to zero
  by 13.5 kHz. This protects the low voice fundamental and lower
  harmonics, not every speech overtone or unvoiced consonant.
- An additive inverted highband reference is applied ONLY where
  the previous M6-004 event mask and M6-005 voice-protection overlap.
  The additional correction has a gain no larger than 1.
- Outside those overlaps, B and C must equal preferred A exactly
  in PCM16. Also outside any input event, all must equal original
  physical microphone PCM16, exactly.
- R1 mouse, R2 keyboard and R3 mouse+keyboard must be bit-identical
  between A, B and C: no new "speech" should be hallucinated there.
- Reject clipping, missing source hashes, inconsistent anonymous
  events, symlink evidence, overwrite, and lowband divergence.
- Original source WAV, events and manifest are SHA256 verified
  before and after analysis; they are never edited. All outputs stay
  private in ignored .audit, not the public GitHub repository.
- This method requires future PCM samples for STFT medians,
  zero-phase source processing and the prior vowel gate. NOT a
  real-time or causal microphone DSP yet.

Three options, with no more than three variants per comparison:

| Role | File | What changes |
|---|---|---|
| A | A_005_vowel_soft.wav | Exact previous user-preferred Physical-005 C |
| B | B_006_percussive.wav | A plus event-local percussive antiwave at gain 0.65 during protected voice |
| C | C_006_percussive_strong.wav | A plus event-local percussive antiwave at gain 1.00 during protected voice |

## Exploratory physical WAV experiment

The initial independent algorithm development probe on the original
44-second physical capture found the following total 1300–9000 Hz RMS
changes in R5 voice+clicks versus ORIGINAL unprocessed mic:

| Role | R5 band RMS change | Relative to previous A |
|---|---:|---:|
| A preferred C_005 | about -1.04 dB | 0 |
| B HPSS 0.65 | about -2.54 dB | about -1.49 dB |
| C HPSS 1.00 | about -3.16 dB | about -2.12 dB |

In the protected voice+event overlap of R5, the 90–650 Hz RMS
difference compared with A was about -62.5 dB (B) and -58.9 dB (C).
That is a narrow LOWBAND difference proxy, NOT proof that spoken
consonants/higher harmonics are unchanged or that click noise alone
is reduced. Broad highband energy also contains parts of natural speech.
R1–R4 had zero additional corrections in this exploratory source.

**The same physical recording has been inspected repeatedly while
choosing M6 parameters. These numbers are descriptive.**
Only a novel physical recording with genuinely unseen speech/click
timing can provide an unbiased out-of-sample efficacy claim later.

## Arch commands, tests first, original unchanged

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

timeout --signal=TERM --kill-after=3s 90s \
  ~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests \
  -p 'test_voice_m6_physical_percussive_ab.py' -v
~~~

Expect six synthetic tests OK. If any test fails, do NOT export yet.

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m6_physical_percussive_ab.py \
  --capture .audit/voice-aec/M6-physical-events-001 \
  --alignment .audit/voice-aec/M6-physical-event-alignment-001/m6-physical-event-alignment.json \
  --out .audit/voice-aec/M6-physical-percussive-001

cd .audit/voice-aec/M6-physical-percussive-001
sha256sum -c SHA256SUMS
cat m6-physical-006-report.json
~~~

The private output consists of 15 PCM16 WAVs (five physical scenes,
three modes), a numerical report and SHA256SUMS. The original capture
is never recorded again; nothing auto-plays or modifies PipeWire.

## Listening: ONLY R5 matters for this change

~~~bash
pw-play R5_voice_clicks_holdout/A_005_vowel_soft.wav
pw-play R5_voice_clicks_holdout/B_006_percussive.wav
pw-play R5_voice_clicks_holdout/C_006_percussive_strong.wav
~~~

Compare:
- Keyboard click audibility DURING voice, especially key releases and
  ringing tails. Does C sound clearly quieter than A and B?
- Entire elongated "Ааааа" from onset through final decay; can the
  last vowel or consonants sound cut, hissy or underwater?
- Silence between clicks. New modes should not pump noise when NO
  anonymous event is present.

If C makes speech unpleasant, prefer B. If B and C are not better than
A on hearing, keep A: engineering RMS is NOT a subjective success.
If the click remains too loud under actual simultaneous speech, a
second independent mechanical reference (for example an optional
contact microphone/accelerometer) may be needed; do NOT compromise
the strict privacy boundary by identifying individual keys.


## Human listening result — November 10, 2026

The operator explicitly compared the new Physical-006 listening WAVs
with previous generations and reported in Russian:

> "6 намного сильнее убрал звуки клавиш (они различимы, но уже
> существенно тише), полностью сохранив голос, это определённо
> нужное направление"

Interpretation with strict limits:
- The keyboard clicks are still perceptible, but **substantially quieter**
  than earlier M6 physical versions.
- In the operator's hearing, the voice was **fully preserved**;
  unlike the elongated-vowel truncation reported for Physical-004.
- This is a **positive subjective result on the current recording**,
  not objective proof of voice source separation or generalization.
- The operator did NOT specify whether B_006 or C_006 was their favored
  new candidate. Do not record a winner or select C unilaterally.

Decision:
1. Preserve C_005 as the known successful earlier voice baseline, and
   preserve both M6-006 spectral candidates byte-for-byte.
2. Continue the *event-gated harmonic/percussive separation* approach,
   rather than indiscriminately extending broadband click suppression.
3. The next research focus should be residual clicks and any transient
   speech consonant artifacts, measured on a wholly independent
   recording when making efficacy claims.
4. Do not alter live PipeWire routes, install services, collect key
   identities, overwrite previous private WAVs, or silently deploy
   the noncausal STFT processor.
