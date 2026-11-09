# M5 — absolute reference-linked echo and physical microphone noise floor

## Why this measurement exists (2026-11-09)

M5 showed that a large full-band ERLE can simply reflect loud
reference-correlated echo, while a quiet echo plus almost
constant microphone background yields a small ERLE.
Specifically, the prospective physical doubletalk-002
speaker-only holdout **0.67881dB FAIL** is still a valid
recorded failure under its original engineering gate.
We do not rewrite that result because a later diagnostic
found weaker speaker leakage.

The two separately recorded level-control
\`M5-level-015-001\` and \`M5-level-030-001\` takes
show a near-linear **+5.87/+5.98dB** increase in
reference-linked power over 500–4000Hz when
per-stream \`pw-play --volume\` doubles.
That is evidence for level sensitivity, NOT historical
100% speaker volume.

This next numerical protocol was fixed before looking
at its new post-subtraction numeric report. It uses
**EXISTING files only** and keeps both frozen filter
and already selected 3800Hz echo-only guard unchanged.

## Immutable source contract

- Physical \`M5-level-015-001/microphone-aba-raw.wav\`
  with verified manifest, exact \`pw-play=0.15\`.
- Physical \`M5-level-030-001/microphone-aba-raw.wav\`
  with verified manifest, exact \`pw-play=0.30\`.
- Identical \`M5-006/reference-sent.wav\` digital 20s reference
  and verified M5-006 source manifest, reached through
  the two A/B/A source links.
- Exact SHA256s pre-pinned in
  \`voice_m5_playback_coherence.py\` and propagated into
  \`tools/voice_m5_echo_floor.py\`.
  Any altered or unexpected source makes the analysis
  reject, not silently accept replacement files.
- No mic/speaker hardware is opened; no new WAV, music
  playback, voice processing, system route/default change,
  autostart or daemon. Fresh private report directory only.

## Locked analysis windows

| Phase | Timebase and interval | Role |
| --- | --- | --- |
| Alignment | Digital reference 1.0–3.7s | 1500–4000Hz lag search; never used for test selection |
| Acoustic FIR training | Digital reference 3.0–9.0s | One fixed 120ms / 5760-tap FIR, ridge 0.02, training-only gain |
| Speaker-only evaluation | Digital reference **10.0–16.0s** | Numeric comparison on fixed FIR and fixed guarded echo |
| OFF noise before | **Physical mic WAV 2.0–7.0s** | Speaker OFF, raw microphone background |
| OFF noise after | **Physical mic WAV 30.0–35.0s** | Speaker OFF, raw microphone background |
| OFF reference negative control | Digital reference 10.0–15.0s paired separately with each OFF segment | Quantifies unrelated finite-sample coherence estimator floor |

The guarded comparison is the already fixed **513-tap
Hann zero-phase 3800Hz lowpass on the ECHO ESTIMATE**.
Original mic samples are never filtered. The guard is
noncausal and only suitable for this offline research.

The 10–16s sections were investigated by previous
M5 coherence diagnostics, so these are NOT newly blind
heldout data for the already selected model/guard.
The training procedure itself must not use that interval.
No subsequent parameter fitting, threshold modifications
or selection of a favorable window are allowed here.

## Metrics, interpretation and non-claims

For each source volume and every band (70–180, 180–500,
500–1500, 1500–4000, 4000–8000, 8000–16000Hz),
using a 4096-point Welch FFT with 50% overlap:

1. **Raw mic band power**: integration of \`S_yy(f)\`;
2. **Residual mic band power**: integration of \`S_rr(f)\`
   after subtracting the reference-only frozen echo,
   with and without the predeclared guard;
3. **Reference-linked power proxy**: integral of
   \`|S_xy(f)|² / S_xx(f)\`, equivalent to mic PSD
   times magnitude-squared coherence. Report absolute
   power (relative dBFS) **before AND after subtraction**;
4. **Absolute proxy suppression** in dB:
   \`10log10(P_link_before/P_link_after)\`;
5. **OFF-noise band floor**: original mic band PSD
   measured in each speaker-OFF interval and their
   arithmetic mean **power** (not arithmetic mean dB);
6. **OFF-null proxy**: correlation of an unrelated
   digital music segment with each physical OFF mic
   segment, to show the estimator's finite-sample bias;
7. **Floor-relative residual**: residual speaker-ON
   band power minus average speaker-OFF band power
   (dB); and linked proxy after AEC minus the unrelated
   speaker-OFF null proxy;
8. Whole-band ERLE on the speaker-only evaluation
   window, along with 500–4000Hz combined coherent
   proxy absolute before/after levels.

Neither \`P_link\` nor \`P_OFF\` is proven to be a
clean isolated echo stem or acoustic sound pressure.
Finite-sample coherence has positive bias, microphone
firmware/DSP is unobserved, and OFF before/after have
different backgrounds in prior tests. Even an excellent
filter can leave nonzero coherent estimates at the
OFF-null estimator floor.

**No universal voice-quality PASS is computed from these
numbers.** In particular we cannot infer preservation
of real simultaneous near-end human speech from this
speaker-only experiment. The previous
**prospective 0.67881dB FAIL remains immutable.**

A result is scientifically useful if it shows whether:
- source-linked power before subtracting follows the
  preexisting 0.15→0.30 gain response;
- source-linked power after subtracting approaches
  (or remains well above) the unrelated-reference
  OFF-null proxy in 500–4000Hz;
- residual raw band power approaches the observed
  OFF mic floor, even when fullband ERLE looks small;
- the highband echo-only guard avoids an amplification
  without being praised as speech-preserving.

These are exploratory diagnostic descriptions, not
production acceptance criteria.

## Run local tests first — NO new recording

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

The test file now has **37 tests**: two new tests
verify known synthetic echo removal in the coherence-
weighted power proxy, plus an end-to-end test
reusing exactly hashed old/new synthetic A/B/A
sources, rejecting tampered WAV and refusing to
write any WAV or overwrite existing report.
Local execution is required; do not assume green.

Only after all tests pass:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_echo_floor.py \
  --low-capture .audit/voice-aec/M5-level-015-001 \
  --high-capture .audit/voice-aec/M5-level-030-001 \
  --out .audit/voice-aec/M5-echo-floor-001

cd /data/cue-wire/.audit/voice-aec/M5-echo-floor-001
sha256sum -c SHA256SUMS
~~~

The two input names are example paths confirmed in
the earlier level-control protocol. If the actual
local directory differs, replace only the matching
input path; SHA256 checks still require the exact
historical files. If the output folder exists, choose
a fresh \`-002\` etc suffix. Never overwrite.

Share \`echo-floor.json\` and \`SHA256SUMS\`.
No raw mic, M4 voice, speaker audio, or new session is
needed for the present diagnostic.

## Future: true near-end speech criterion

Only after we understand the absolute residual vs
background on speaker-only samples, design an
independent real simultaneous doubletalk protocol.
It needs clean near-end truth if we want a numerical
speech-preservation metric, e.g. a separate close-talk
reference path with explicitly qualified leakage.
An unrelated earlier voice-only take digitally mixed
with speaker echo tests signal math, not human voice
preservation under real USB mic processing.
