# M5 — prospective acoustic-echo guard validation on a NEW physical take

## Purpose and limits

This protocol is written BEFORE seeing a new physical capture after the
2026-11-09 M5 known-stem and guarded-FIR exploratory results.

Question: Does the existing, unmodified **echo-estimate-only 3800Hz guard**
retain meaningful physical speaker-only echo reduction while avoiding the
negative high-frequency energy effects observed with the unguarded FIR,
on a fresh continuous doubletalk take?

This experiment does NOT independently certify intelligible/undistorted
near-end speech: the single microphone does not supply the simultaneous
clean near-end speech truth. It can validate reproducibility of speaker-
only suppression and conservative upper-band interference behavior
in a separate physical capture with operator voice after calibration.

It is not a production rollout and must not change PipeWire/ALSA defaults,
play back the mic, spawn any background service or enable the CueWire
virtual microphone. M4 LEVEL-001 stays immutable.

## Pre-registered configuration — no tuning after this document

- Hardware: existing explicit USB physical MICAudio-97i source, physically
  unchanged position, powered USB hub; explicit physical analogue speaker
  sink \`alsa_output.pci-0000_00_1f.3.analog-stereo\`.
- Sample rate: 48kHz mono signed PCM16.
- Reference: existing local music file
  \`/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav\`,
  converted to **exactly 32s** by \`tools/voice_m5_capture.py\`.
  Reusing the same digital song tests *new take reproducibility*, not
  generalization to different music, rooms or playback chains.
- Playback volume: \`0.30\`; do not change other audio during this take.
- Silence and speech: **first 12s of playback** must contain speaker
  only, with operator silent; **next ~20s** operator speaks normally
  over music when explicitly prompted. Fixed microphone/speaker position.
- Alignment: existing \`voice_m5_doubletalk_gate.lag_estimate\` on early
  speaker-only samples; this is not hardware time sync.
- FIR: frozen **120ms, 5760 taps, 8192 Welch/FFT, ridge 0.02**,
  10ms pre-peak shift, 8ms tail fade; gain is set from training only.
- Train: digital reference **3–9s**.
- Speaker-only validation (unused in fit): digital reference **10–11.5s**;
  ends before 12s voice prompt.
- Mixed speech/music diagnostic: reference time **13–28s**;
  NOT a clean-voice quality measurement.
- Original benchmark: subtract whole-band echo estimate.
- Guarded variant: same prediction, then **513-tap symmetric Hann
  zero-phase FIR at 3800Hz** before subtraction; the physical
  microphone is not filtered. This is an OFFLINE NONCAUSAL
  research filter, not a ready-to-run real-time implementation.

Scripts: \`tools/voice_m5_capture.py\` and
\`tools/voice_m5_guarded_fir.py\`. Do not change numerical settings
or code in response to results from this prospective take. Changes
required to fix a code bug must be disclosed, tested, and may require
a separate fresh take for independent evaluation.

## Acceptance and interpretation — fixed BEFORE observing new data

### Data-integrity prerequisites (otherwise INVALID)

- Capture \`status=captured_for_offline_aec\`, 12s calibration, 32s
  reference, full-length mic, zero clipped samples.
- Explicit physical microphone and sink, no route changes, no other
  playback during the experiment.
- Digital reference and physical mic SHA256 hashes must match the
  new capture manifest and remain unchanged through offline analysis.
- **New mic raw SHA256 MUST differ** from the earlier doubletalk-001
  recording \`f6ce01ba66b9e68121c535ff1ddd735001e4381f9028e9fcb7032da420ed24c8\`.
- Operator must actually remain silent during first 12s; the manifest
  alone cannot prove this. Any known interruption invalidates the
  trial; disclose rather than filtering away a bad portion.

### Quantitative research gate (NOT a voice-quality PASS)

Apply thresholds to the **new** speaker-only 10–11.5s holdout
from the guarded variant:

1. Guarded overall heldout energy reduction (ERLE) **>=1.0 dB**.
2. Guarded 500–1500Hz speaker-only ERLE **>=3.0 dB**.
3. Guarded 1500–4000Hz speaker-only ERLE **>=3.0 dB**.
4. Guarded 4000–8000Hz speaker-only ERLE **>=-0.25 dB**.
5. Guarded 8000–16000Hz speaker-only ERLE **>=-0.25 dB**.
6. On the 13–28s speech+music mixture, guarded 4000–8000Hz
   and 8000–16000Hz energy changes each **>=-0.25 dB**.
   These two checks prevent strong high-band amplification;
   they do NOT prove near-end speech was preserved.

The earlier fullband model's existing 1dB speaker-only gate
must pass for \`voice_m5_guarded_fir.py\` to emit mixed-speech
diagnostics. If it fails, declare the trial inconclusive/rejected
and do not bypass the gate to obtain speech numbers.

These are engineering diagnostics chosen from prior
experiments, **not scientifically validated thresholds** for
human voice quality or echo audibility. The 3800Hz guard
mathematically removes most predicted echo above ~5kHz,
so passing high-band non-amplification is useful mainly as
an implementation/integrity check. Do not mistake it for
independent acoustic speech-preservation evidence.

Also report, without gate-based selective omission, all
six-band ERLE and coherence changes for both original and
guarded estimates, and the mixing total. Do not discard
negative results; no post-hoc optimization on the holdout.

## Procedure (new session; manually initiated only)

1. Verify \`M5-doubletalk-002\` is unused. If already used,
   pick a fresh identifier and keep that same name below.
2. Run local tests and abort if red:

~~~bash
cd /data/cue-wire
git pull --ff-only
~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

3. Record one fresh 32s capture:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_capture.py \
  --music '/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav' \
  --capture-backend pipewire \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --seconds 32 \
  --calibration-seconds 12 \
  --volume 0.30 \
  --out .audit/voice-aec/M5-doubletalk-002
~~~

4. Run the already-frozen, offline guard comparison to a NEW output:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_guarded_fir.py \
  --capture .audit/voice-aec/M5-doubletalk-002 \
  --out .audit/voice-aec/M5-guarded-fir-prospective-002

cd /data/cue-wire/.audit/voice-aec/M5-guarded-fir-prospective-002
sha256sum -c SHA256SUMS
~~~

5. Share the new \`manifest.json\` and \`guarded-fir.json\`
plus \`SHA256SUMS\` only. Do NOT upload private RAW music
or mic WAV unless needed for a separate explicitly approved
inspection. Evaluation can be derived from the numeric report.

## What it will NOT establish

- Clean-voice preservation in real doubletalk (requires a different
  physical reference technique or carefully qualified listening test).
- End-to-end game/Steam/CueWire latency or low-latency realizability
  of the offline noncausal filter.
- Generalization to another song, placement, speaker, environment,
  or microphone gain.
- AEC production readiness; production remains explicitly not approved.
