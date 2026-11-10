# CueWire / M6 Physical-007 — research acceptance and frozen checkpoint

**Decision date:** 2026-11-10  
**Status:** **RESEARCH-ACCEPTED / OFFLINE-REFERENCE-FROZEN**  
**NOT:** production release, certified real-time mic processor, speech-integrity
proof, unbiased generalization, or causal latency validation.

## Operator decision

After listening to the physical R5 (voice plus keyboard/mouse) A/B/C
outputs of M6 Physical-007, the operator stated:

> "007 ощутимо лучше, и я бы предложил зафиксировать эту модель
> и считать ветку исследования достаточноу спешной чтобы идти
> к следующей разработке)"

Interpretation: M6-007 achieved a noticeable subjective improvement
relative to earlier physical experiments, sufficient to **conclude this
research iteration** and proceed to a new development effort.

**Important specificity limit:** this statement names "007" as the
preferred *generation*, not explicitly B_007 versus C_007. Therefore
the **entire model family and all 3 A/B/C outputs** are preserved;
do not claim that C_007 was expressly selected over B_007. Previous
operator's explicitly chosen baseline was C_006_percussive_strong.

## Models, preserved unchanged

1. A_006_percussive_strong.wav — byte-identical reference reconstructed
   with the old accepted Physical-006 pipeline.
2. B_007_attack.wav — extra anonymous keyboard-event conditioned short
   STFT percussive correction on the exact Physical-006 C baseline.
3. C_007_attack_and_tail.wav — short attack + additional long decay,
   guarded near identified voice harmonics.

Main source modules:
- tools/voice_m6_residual_comb.py
- tools/voice_m6_physical_residual_ab.py

Upstream source modules from 001 through 006 remain required because
the Physical-007 exporter recomputes its previous reference and masks
from the original immutable private physical capture.

Exact Git blob identifiers at research acceptance:
- Physical-007 DSP core: e5d67410d96d6447484266c9fb0d04476d8d00c9
- Physical-007 exporter: 14459e8a306dd9b8eb538c1cf415a92d9615faca
- Residual synthetic tests: 5284b0519912d70803c3b9bc5b6a599bec1d8f80
- Residual integration tests: c3539c2f9592ae53f275a83439705408deb57537

These are *Git blob* SHA1 identifiers, NOT the SHA256 of private WAVs
and NOT a Git release tag. Preserve a frozen Git branch/ref at this
acceptance checkpoint; avoid modifying these files in place.

## What was established

**Established experimentally:**
- Real physical microphone and anonymous input events can be aligned
  in time across mouse/keyboard press/release classes.
- Multiple offline variations were compared using the same physical
  recording; the operator found M6-007 noticeably better on hearing
  than previous generations.
- The M6-004 broad aggressive approach damaged the end of prolonged
  vowels, demonstrating that lower spectral RMS alone is not enough.
- M6-005 vowel protection and M6-006 harmonic/percussive separation
  were subjectively much better for voice integrity; M6-007 builds on
  their frozen processing model.
- The implemented export code checks original SHA256 input evidence,
  restricts extra processing to anonymous keyboard-class event masks,
  compares off-mask PCM16 exactly, and produces output SHA256SUMS.

**Not established / explicit constraints:**
- No independent unseen physical session was used for model selection
  or rigorous generalization; all numerical spectral improvements on
  the current 44s source are descriptive.
- No clean-speech plus separate keyboard noise ground truth exists
  for R5; total highband RMS is NOT click-source SNR.
- Operator said M6-007 was noticeably better; no fresh assertion of
  complete speech fidelity in M6-007 is added beyond what was heard.
- STFT, median filtering, zero-phase filters and voiced guards require
  future samples; real-time causal performance, latency, CPU budgets,
  and under-load reliability remain UNVERIFIED.
- Voice consonants, whispering, silence transitions, different
  keyboards/acoustics and unseen voices need independent evaluation.

## Security / data-handling contract

- All actual physical WAVs, anonymous event records, timestamps,
  original user mic and runtime reports remain ONLY in local ignored
  .audit; never upload personal voice or input rhythms into GitHub.
- Only kind plus monotonic timestamp may be read from event records.
  NO key IDs, mouse-button IDs, text, pointer coordinates or motion.
- No user input monitoring, mic capture, autostart, daemons,
  automatic playback or system audio settings changes are introduced.
- No live virtual-microphone production switch is authorized by
  this research acceptance.
- Previous private physical experiments remain untouched and retain
  SHA256SUMS audit files.

## Reproducibility checklist (operator Arch only)

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover \
  -s tests -p 'test_voice_m6_*residual*.py' -v

# Expected: 10 synthetic tests; do not assume they passed without
# seeing local output. No new capture is required.
~~~

The private physical exporter and its SHA256 checks are documented in
docs/voice-m6-physical-residual.md. The operator already listened
to the produced physical variants. Do not fabricate a new SHA256 for
private M6-007 WAVs in GitHub, since they were not supplied here.

## Decision to move on

**M6 physical click reduction: closed as successful offline research.**

Freeze this model family as an immutable reference. Subsequent work,
if it targets live CueWire microphone operation, should start in a
**new development branch** with a different acceptance contract:

1. Causal streaming block DSP with explicit ring buffers, deterministic
   event-clock/audio-clock alignment and measured end-to-end latency.
2. A truly unseen physical capture for voice and keyboard, including
   sustained vowels, unvoiced consonants, simultaneous keyboard/mouse,
   room reflections, microphone volume changes and stop/restart.
3. Voice safety + bounded resource use + fail-closed termination.
4. Existing global Super+C terminate-app-and-children requirement;
   no lingering services, autostart or persistent PipeWire changes.

This **does not choose** the next unrelated DIY subsystem; it closes
M6 and records prerequisites if the next project later returns to
real-time CueWire.
