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

## M5 echo-floor-001: real numeric audit, SHA256 verified (2026-11-09)

User supplied \`echo-floor.json\` and \`SHA256SUMS\`. Independently
recomputed report SHA256:
\`13b1211682f9c96b1be51f3824154fea0b79ff88e3837682050e42302a231a07\`.
The report's physical WAV/reference/manifests SHA256 IDs match
the previously audited volume-control captures; original WAV
bytes were not uploaded for independent rehashing here.

Both studies use the same 120ms/ridge 0.02 frozen FIR,
513-tap/3800Hz echo-estimate-only noncausal guard,
and the 10–16s heldout reference window. No near-end voice
was recorded, no model was refit on the heldout, no audio
was exported or played. These are retrospective
diagnostics, NOT independent blind acceptance tests.

| Metric | Per-stream volume 0.15 | Per-stream volume 0.30 |
| --- | ---: | ---: |
| Training ERLE | +0.31158dB | +0.92427dB |
| Whole-band heldout full-FIR ERLE | +0.22198dB | +0.87119dB |
| Whole-band heldout guarded ERLE | **+0.23316dB** | **+0.88581dB** |
| 500–4000Hz raw mic band PSD power | -62.313dBFS | -60.231dBFS |
| 500–4000Hz guarded residual PSD power | **-63.088dBFS** | **-63.129dBFS** |
| 500–4000Hz mean OFF background band PSD power | -64.338dBFS | -64.769dBFS |
| Guarded residual above mean OFF background | +1.250dB | +1.640dB |
| 500–4000Hz original reference-linked power PROXY | -69.038dBFS | -63.055dBFS |
| 500–4000Hz guarded residual linked PROXY | **-73.830dBFS** | **-69.612dBFS** |
| 500–4000Hz unrelated-reference OFF null PROXY | -85.031dBFS | -85.236dBFS |
| Residual linked proxy above unrelated OFF null | **+11.201dB** | **+15.624dB** |
| Linked proxy suppression from guarded FIR | **+4.79193dB** | **+6.55686dB** |

These combined 500–4000Hz values were recomputed
by **adding linear band powers first**, then converting
to dB. The protected AEC residual is remarkably
similar (~-63.1dBFS) at both playback levels,
and within 1.3–1.6dB of an OFF power estimate.
**BUT** reference-linked residual power remains
11–16dB above the unrelated-reference null estimate.
Total residual proximity to background is therefore
NOT evidence that audible echo is gone.
The linked proxy reduction is ~66.8% at 0.15 and
~77.9% at 0.30, **of the coherence proxy only**,
not independently measured physical echo/SPL.
Magnitude-squared coherence estimates have positive
finite-sample bias and cannot be converted into
a certified clean near-end speech result.

**Highband instability:** during genuine speaker-OFF
microphone intervals 2–7s and 30–35s,
8–16kHz band power changed **-11.46258dB**
in the 0.15 take and **+13.56910dB**
in the 0.30 take (opposite directions).
The OFF floor is emphatically NOT stationary
over the full recordings. In 500–1500Hz,
the same before→after changes were -1.53543dB
and -1.08801dB; in 1500–4000Hz,
-1.77797dB and -0.02740dB.
So the mean OFF power is a *descriptive*
baseline and not a statistically validated
noise floor during ON. These findings do not
identify whether mic DSP/AGC, electronics,
environmental transients or other factors caused
the fluctuations.

The echo-only guard suppresses negligible upper-band
reference power, leaving original microphone high
frequencies almost unchanged. This avoids known
highband energy regressions at the price of not
cancelling those frequencies. Voice preservation,
latency and real physical doubletalk remain UNTESTED.

**Status:** research-only; the historical
M5-doubletalk-002 guarded **0.67881dB prospective FAIL**
is unchanged. Next diagnostic: inspect 0.5s short-window
OFF floor variation to distinguish brief spikes from
persistent level changes, with no new audio recording.

## Next fixed OFF-window diagnostic — detect highband transients without new recordings

The real \`echo-floor-001\` analysis revealed **11–14dB**
before/after changes of the 8–16kHz physical speaker-OFF
mic power, while combined 500–4000Hz AEC residual power
was near the descriptive mean OFF power.

Before asking for any new recording, the new tool
\`tools/voice_m5_off_floor_windows.py\` will divide
each old mic WAV's 2–7s and 30–35s truly OFF segments
into **ten 0.5-second windows per segment**. It computes,
for each of the six fixed bands, median, P10, P90, maximum,
arithmetic mean POWER, and the fraction of the phase's
total band energy concentrated in its single loudest
half-second window. It also records any window more
than 6dB above its own phase's median. Those thresholds
are solely descriptive indicators of bursts,
**not new AEC or voice acceptance gates**.

This answers a testable diagnostic question:
do the historical OFF differences come from one
or two isolated mic/background bursts, or from
a more sustained shift of the distribution?
The tool does not determine whether such changes
were caused by the USB microphone, AGC/noise gating,
software, or environmental sounds.

The original physical source SHA256s of both volume
takes and M5-006 digital reference/manifest are
checked. Only the two existing OFF raw microphone
intervals are analyzed. A 4096-bin Welch power
estimate is **NOT** reused for this short-window
check: a 4th-order offline Butterworth bandpass is
applied to each 5s OFF phase, and 0.5s mean
squared band samples are measured. Therefore the
short-window band energies need not match the earlier
Welch PSD estimate to the last decibel.

New source code adds two synthetic tests:
a single strong highband burst that dominates
mean power while the median stays stable, and a
full immutable-source/JSON-only integration test.
**39 tests total**, requiring a local run before
we trust the new code.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_off_floor_windows.py \
  --low-capture .audit/voice-aec/M5-level-015-001 \
  --high-capture .audit/voice-aec/M5-level-030-001 \
  --out .audit/voice-aec/M5-off-floor-windows-001

cd /data/cue-wire/.audit/voice-aec/M5-off-floor-windows-001
sha256sum -c SHA256SUMS
~~~

Use a NEW output folder if that name already exists,
never overwrite evidence. Send
\`off-floor-windows.json\` and \`SHA256SUMS\` only.
No physical audio WAVs, playback or microphone
capture are required. The original 0.67881dB
prospective FAIL and M4 speech master remain unchanged.

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
