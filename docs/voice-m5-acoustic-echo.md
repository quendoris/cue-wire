# CueWire M5 — controlled acoustic echo experiment

## Hypothesis

A known digital signal sent to a real loudspeaker can be used to estimate the loudspeaker → room → microphone acoustic path. First five seconds: music (including other people's vocals), **no local speech**. Then, while the music continues, the user reads a short passage. The calibrated echo-path FIR is frozen during the near-end speech to avoid double-talk contamination.

Model: mic[n] = local_voice[n] + sum_k h[k] * reference[n-k] + noise[n]. A pilot residual is mic[n] - estimated_echo[n].

This is **acoustic echo cancellation**, not semantic voice separation. The digital reference includes the foreign voices, so their acoustic leakage may be cancelled without identifying those voices.

## Boundaries

- **Offline research only**. Never routes the result to the game, speaker or virtual mic.
- Preserves RAW, CLEAN-003, LEVEL-001 and LEVEL-002. New files go only into private .audit/voice-aec.
- Reference is the PCM16 mono WAV *actually passed to pw-play*, not a hardware output monitor or sample-clock-locked stream. Playback latency must be estimated.
- Use an explicitly chosen physical speaker sink and ALSA mic; never CueWire's virtual sink or default/auto source.
- No other system sounds may use this speaker during the pilot; they would be missing from the reference.
- Keep speaker/mic stationary and volume constant. Comfortable low volume first.
- Five seconds of silence means **no local voice**; the speaker itself continues playing vocals/music.
- The analyzer trains on the first ~3.3 seconds of actual playback, reserves 3.45–4.40 seconds for held-out verification, and rejects a model that does not improve held-out echo by at least 1 dB.
- Only a frozen linear ~120-ms filter is fitted, no adaptation when the user speaks. No guarantees of perfect speech preservation, nonlinear/clock-drift compensation, or live AEC.
- The music recording and microphone WAV remain private; no upload.

## Step 1: inspect devices

Run on Arch:

~~~bash
cd /data/cue-wire
git pull --ff-only
python3 tools/voice_aec_inspect.py
arecord -l
~~~

From the inspect report take **the exact speaker output_sinks node.name**. The historical MICAudio97i microphone is likely at ALSA hw:0,0, but inspect will verify before each capture. If the actual card is on another ALSA index, use that observed index and expected card ID.

Choose an FFmpeg-readable local WAV/FLAC/MP3 song with other people's voices, at least 20 seconds in length. The code generates and plays an explicit mono 48-kHz PCM16 reference so the samples are known exactly.

## Playback startup policy (revision M5-002)

The original M5-001 attempt rejected the microphone **before** any sound
played because `arecord` exited during a three-second ALSA readiness
wait. That wait has been removed: after verifying devices and preparing
the known PCM reference, CueWire now launches physical microphone capture
and **immediately launches the speaker playback process**, back-to-back.
The first five seconds are timed from the player launch, not from the
shell command or FFmpeg preprocessing.

There is no guarantee that the physical DAC produces its first sample
at that exact moment; Pulse/PipeWire buffering and device latency remain
part of the later alignment estimate. Starting playback without waiting
also means the microphone *might* start too late or fail. The capture
checks for microphone PCM bytes within one second and aborts the playback
on any recorder error. Such a failed attempt is not used as an AEC
reference or calibration.

The previous `M5-001` directory is immutable failure evidence. Inspect
the actual cause before assuming the startup order alone was responsible:

~~~bash
cd /data/cue-wire
tail -n 35 .audit/voice-aec/M5-001/arecord.log
~~~

An error such as `Device or resource busy` could indicate an
exclusive ALSA-hardware conflict. Do not kill or disable PipeWire
services to work around it; identify the actual holder and decide
separately whether capture should use the PipeWire physical-source node.
The new capture prints the end of `arecord.log` on failure.

For this changed behavior, use **M5-002**, never overwrite M5-001.

## M5 frozen-FIR heldout: ACTUAL recorded evidence (2026-11-09)

The user supplied the real `M5-frozen-aec-heldout-001/frozen-aec-heldout.json`
plus its `SHA256SUMS`. The report's SHA256 **was independently computed
from the uploaded bytes and matched**:
`5b9981132551d569e3cc8c9264fcf734997d98fe5f11631e2ff141e8cd86dfe6`.
Input manifest and audio SHA256s match the previous M5-006
reference and M5-ABA-001 physical microphone.

**Actual fixed model and disjoint time windows:**

- Alignment reference time **1.0–3.7 s**, 1500–4000 Hz;
  estimated reference-origin/mic offset **8315.27083 ms**
  (~281.37083 ms after *software playback launch*, not
  literal acoustic propagation time).
- Fixed FIR: **5760 taps/120 ms**, regularization **0.02**,
  spectral estimate 8192 FFT, coefficient gain **1.1173878**
  from **training only**.
- Training time **3.0–9.0 s**, total ERLE **+1.11885 dB**
  (in-sample, optimistic).
- Held-out evaluation time **10.0–16.0 s**, total ERLE
  **+1.18578 dB**. Original microphone RMS **-58.30545 dBFS**,
  predicted echo RMS **-62.95045 dBFS**,
  residual RMS **-59.49123 dBFS**.
- The held-out total power is reduced by
  `1 - 10^(-1.18578/10) = 0.2389346`, **23.9%**.
- Same-session speaker-OFF baseline around
  **-59.8607 dBFS**; evaluated residual is about
  **+0.37 dB** above this earlier OFF power estimate.
  This comparison does not certify complete removal of
  actual loudspeaker echo.

**Heldout spectral evidence** (magnitude-squared coherence
weighted by the ORIGINAL mic PSD for both before and after):

| Frequency | Coherence before | Coherence after | ERLE |
| --- | ---: | ---: | ---: |
| 70–180 Hz | 0.103679 | 0.050759 | +1.49397 dB |
| 180–500 Hz | 0.177296 | 0.051445 | +0.91970 dB |
| 500–1500 Hz | 0.417548 | 0.160470 | +2.28760 dB |
| **1500–4000 Hz** | **0.553982** | **0.385542** | **+3.26274 dB** |
| 4000–8000 Hz | 0.114123 | **0.130685** | **-0.24140 dB** |
| 8000–16000 Hz | 0.069379 | **0.160249** | **-0.82629 dB** |

Thus, frozen subtraction **materially reduces microphone
correlation with the digital reference** and total power
in the useful 500–4000 Hz region. Above 4 kHz the
held-out residual power and coherence **increase**.
This is a failure mode, not something to suppress from
the report. The higher-band energy is small in absolute
terms, but quality and speech preservation have NOT
been assessed by listening or clean speech truth.

The existing minimal `>=1 dB` *speaker-only heldout
engineering gate* **PASSES** on this particular A/B/A
test (1.18578 dB), whereas the original M5-006 failed
at 0.7574 dB. **Neither result approves a voice
pipeline, music/game routing, or production AEC.**
This pass must not silently override high-frequency
regressions. The `speaker_off_control.echo_prediction_off_max_abs=0`
is true **by construction** because the reference model is zero
during OFF; it is not a clean-voice preservation test.

**Audit independence limitation:** 10–16s did not influence
the current FIR fit, but its spectral coherence was
already inspected earlier in M5-reference-coherence-002.
Thus this is a heldout evaluation from *model training*,
not a genuinely blind independent validation of the
entire iterative research project.

### Next quality gate: controlled double talk (not yet validated)

A suitable future test needs a *new* raw capture that
preserves (a) a clean speaker-only calibration and a
separate heldout speaker-only validation period before
(b) the operator talks while the same reference continues.
Keep the mic/speaker/volume and processing chain fixed.
A fresh recording avoids relying on the same inspected
heldout interval repeatedly. No model may update during
speech, and no cleanup output may feed a speaker or game.

For double talk, full-band ERLE alone is a poor metric:
explicitly report per-band reference-coherence improvement,
especially the **negative 4–16kHz bands** here, plus
voice audibility, preservation (using a valid reference or
careful listening), no accidental output monitoring,
and no background services. A clean speech-only control
may be useful, but separate spoken takes are not
sample-exact ground truth and cannot certify distortion
numerically on their own.

The accepted M4 `LEVEL-001` voice master stays unchanged.

## M5 new frozen FIR holdout: source-code readiness (not yet measured on real PCM)

New `tools/voice_m5_frozen_heldout.py` runs the first controlled
**train-once / heldout** test of the reference-conditioned echo
cancellation model on the already saved, immutable M5-ABA-001
microphone and verified M5-006 digital reference.

The prior M5 coherence report was **M5-reference-coherence-002**;
its non-overlapping second window has already been *inspected* in a
previous exploratory analysis. Therefore these new measurements are
**research heldout for parameter fitting**, not a fully blind or
independently replicated validation of the overall development
process. Avoid promoting this single heldout result to a production
AEC guarantee.

### Fixed protocol, no trial-and-error candidate search

- Reference source: `M5-006/reference-sent.wav` loaded through the
  immutable source link in the M5-ABA-001 manifest; all WAVs and
  both manifests must match their SHA256s.
- **Alignment:** initial 1.0–3.7 s of reference, bandpass
  1500–4000 Hz, via previously tested
  `voice_m5_refcoherence.band_lag`.
- **Training:** reference 3.0–9.0 s, physical microphone with
  measured fixed sample offset. Fit one 120-ms FIR using the
  same regularized Wiener/CSD model as the previous M5
  implementation (8192 FFT, ridge factor **0.02**, final
  8-ms impulse taper). FIR is causal starting about
  10 ms before the strongest alignment peak.
- **Gain:** one scalar determined on **training samples only**,
  never on the evaluation interval.
- **Heldout:** reference 10.0–16.0 s, evaluate the **frozen**
  model with no retiming, refitting, adaptation or gain adjustment.
  A full second separates training from heldout.
- **Outputs:** original vs residual mic RMS, heldout
  whole-band and band-specific ERLE; magnitude-squared coherence
  with the reference BEFORE and AFTER subtraction. Both
  coherence variants use **unchanged original microphone PSD
  weights** or unchanged reference PSD weights for a fair
  within-band comparison. Record pre/post median coherence too.
- **Controls:** physical speaker-OFF intervals, sample/peak
  levels, immutable input hashes, source/device/volume identity.
  Predicted echo in OFF should be zero **by construction**;
  that is not a test of speech preservation.
- **No processed audio is written**, and no speaker/microphone
  hardware is opened. This does **not** change the original
  AEC >=1 dB fail-closed gate, and success in a no-voice test
  never authorizes real-time processing.

Only one preset is tested, to avoid inflating significance
by choosing the best of many models after seeing heldout
results. Report potentially **negative ERLE**, higher
post-cancellation coherence, or unsafe levels as such;
never silently discard a failed candidate.

### Local run (no music playback and no new capture)

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_frozen_heldout.py \
  --capture .audit/voice-aec/M5-ABA-001 \
  --out .audit/voice-aec/M5-frozen-aec-heldout-001

cd .audit/voice-aec/M5-frozen-aec-heldout-001
sha256sum -c SHA256SUMS
~~~

Report: `frozen-aec-heldout.json` (JSON metrics only) and
`SHA256SUMS`. Choose a NEW output identifier if
`M5-frozen-aec-heldout-001` already exists; never overwrite
a prior study.

Two additional synthetic regression tests ensure that
(i) changing only the *future heldout mic samples* does not
change the predicted echo filter or training metrics and
(ii) the end-to-end offline report preserves audio hashes,
rejects source tampering and writes no WAV. There are
**21 tests** in `tests/test_voice_m5_aec.py` after
this change; local execution is still required.

User should send the resulting
`M5-frozen-aec-heldout-001/frozen-aec-heldout.json`
and optionally the console test summary. No private
raw music or voice upload is required.

If the measured heldout coherence drops substantially in
1500–4000 Hz and the residual stays near the observed
OFF-noise spectrum, investigate independent controlled
double-talk speech-preservation tests; if coherence remains
high or ERLE becomes negative, investigate time variation,
nonlinearity and acoustic-response fitting with new
independent calibration, not by lowering the safety gate.

## M5 reference-conditioned ABA report: actual run -002 (2026-11-08)

User corrected the output-directory identifier:
the real run was **`M5-reference-coherence-002`**,
not the planned/example `M5-reference-coherence-001`.
An attempted `cd .audit/voice-aec/M5-reference-coherence-001`
failed because that directory did not exist. The numerical
analyzer itself had **already succeeded** and written the
report under `-002`. Correct audit command:

~~~bash
cd /data/cue-wire/.audit/voice-aec/M5-reference-coherence-002
sha256sum -c SHA256SUMS
~~~

The user supplied the actual `reference-coherence.json` and
`SHA256SUMS`; the report SHA256 was **independently verified**
as `8fd671f53c7e1974b6fbfa5b1353bbcdfb00efc05f52ae80f4d5dcd4bd2816c7`.
Source hashes match the earlier M5-006 verified music file
and the previous A/B/A recording:

- A/B/A manifest: `508a94b28ef91d19b8e6ebbcdef679aff496558b9cecdc0ef2eee14859f69d8d`
- Digital reference: `afd3b0ea1141147d6df284bfab62d2a9c9adc579053cfbea8c35559f2e94aa90`
- Physical microphone: `47bb10cd4f79e8e9a84a753bf05898a875955b966c4d2550315b77327eb53aef`

The **mic-power-weighted magnitude-squared coherence** was:

| Hz | Speaker ON 3–9s | Speaker ON 10–16s | Speaker OFF negative control |
| --- | ---: | ---: | ---: |
| 70–180 | 0.035567 | 0.103679 | 0.007473 |
| 180–500 | 0.134160 | 0.177296 | 0.008939 |
| 500–1500 | 0.179121 | **0.417548** | 0.013841 |
| **1500–4000** | **0.495615** | **0.553982** | **0.011981** |
| 4000–8000 | 0.080595 | 0.114123 | 0.012856 |
| 8000–16000 | 0.064394 | 0.069379 | 0.008308 |

Thus the 1500–4000-Hz weighted spectral coherence on ON
windows is around **41.4x** and **46.2x** the corresponding
negative-control value. This is strong within-session evidence
of a linear association with the known played digital reference
during the music. The two time windows do not make independent
experimental replications, and neither a 0.554 coherence score
nor its ratio is a measured percentage of removed acoustic echo.

The algorithm estimates digital-reference-origin-to-microphone
alignment of **8315.27083 ms**, and the approximate software
`pw-play` launch was at **8033.9 ms** after recording start.
Their difference is **281.37083 ms**, with band-limited
alignment correlation **0.388004**. Do not equate this with
a hardware ADC/DAC latency. The earlier M5-006 report's
~101.29-ms reference-relative lag was from a different
recorder/player startup sequence; a difference between
them is NOT evidence of an altered physical room delay
or a 180-ms sound propagation change.

**Next scientifically meaningful step (not yet implemented):**
Fit one pre-specified frozen acoustic model strictly
on the first ON window, evaluate only the later ON
window, and report, **without exporting a voice WAV**,
held-out reference/mic coherence BEFORE and AFTER
the fitted echo subtraction, plus full-band and
band-specific ERLE and absolute noise floor. Do not
select among filters using the same later interval and
then present that interval as independent validation.
The speaker-OFF section stays a negative control.
This would test echo removal directly while leaving
the existing AEC >=1-dB fail-closed gate and the
working M4 voice chain unchanged.

## M5 actual A/B/A spectral audit — reproduced (2026-11-08)

The user supplied `M5-ABA-spectral-001/aba-spectral.json` and its
`SHA256SUMS`. Their actual SHA256 matches:
`27863368dc9cdc89bddb7eddc64c7c1bc330bd4d982f9cc36bd213ab30998931`.

The spectrum report reproduces the independently inspected
source WAV checksums and A/B/A measured values:

- OFF before **-59.78533 dBFS**; speaker ON **-58.43360 dBFS**;
  OFF after **-59.93715 dBFS**.
- Whole-band speaker ON minus average OFF power:
  **+1.42697 dB**, equivalent to a *conditional* stationary
  background power fraction **0.719951**.
- 20–70 Hz makes up **0.406871** of OFF-phase total mic
  power and changes **-0.07276 dB** between ON and OFF.
- The 1500–4000 Hz band changes **+5.92131 dB** and
  4000–8000 Hz changes **+0.13466 dB**.
- Welch low-frequency lines remain at **50 Hz and 64 Hz**
  in ALL phases; spectral line levels vary by only a few
  tenths of a dB. Their sources are not identified.
- Transient peak summary shows impulses near 28.75–30s and
  35.75s, outside measurement interiors.

The previous real M5-006 acoustic filter still has rejected
whole-band **0.7574 dB heldout ERLE**. The combined
observations support the need to separate constant mic
background from audio linearly linked to the digital speaker
reference. They **do not** establish a physically exact
"28% speaker signal" fraction, nor prove echo cancellation
is safe for concurrent near-end speech.

### Next M5 diagnostic: reference-conditioned speaker leakage

Added `tools/voice_m5_refcoherence.py` to run on the
already recorded `M5-ABA-001` physical WAV and the
verified original `M5-006/reference-sent.wav` digital
playback file. The A/B/A manifest already records the
linked M5-006 location, launch time, physical source/sink,
volume and SHA256 values; the diagnostic verifies all
of those before analysis.

The tool estimates global lag using **1500–4000 Hz**, where
the previous independent spectral experiment found the
most stable correlation. It then assesses two
non-overlapping speaker-ON windows (digital reference
time 3–9s and 10–16s) with:

- median squared spectral coherence by band;
- **mic-PSD-weighted coherence**, a frequency-averaged
  normalized linear relation (not a measured fraction
  of removable physical speaker energy);
- reference-PSD-weighted coherence;
- a negative control: a speaker-OFF physical microphone
  segment compared with reference samples that were NOT
  playing at that time.

This permits a direct test of whether the digital speaker
reference explains a stronger *linear spectral relation*
during music than during OFF. It does not fit an echo
filter, create a cleaned WAV, or change AEC policy.
The negative control is a sanity check, not a formal
statistical null distribution.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_refcoherence.py \
  --capture .audit/voice-aec/M5-ABA-001 \
  --out .audit/voice-aec/M5-reference-coherence-002

cd .audit/voice-aec/M5-reference-coherence-002
sha256sum -c SHA256SUMS
~~~

The new output is strictly `reference-coherence.json`
and `SHA256SUMS`. No voice or playback is generated.
Tests include an injected known 85-ms acoustic/digital
offset, independent 50Hz background and positive/negative
coherence discrimination.

The local test suite must be run before accepting the new
code. Successful reference-conditioned coherence would
motivate a subsequent carefully validated frozen AEC
model comparison with a genuinely unused speaker-only
holdout, followed by preservation tests on concurrent voice.
The real-time virtual-mic chain and approved M4 voice
master are **not touched**.

## M5 real continuous A/B/A: uploaded RAW independently verified

User uploaded `M5-ABA-001/manifest.json`, `SHA256SUMS`
and **actual private** `microphone-aba-raw.wav`.
The private mic WAV is not committed to this repository.

The following SHA256s were **independently recomputed** from both
uploaded files and match the provided `SHA256SUMS` exactly:

- WAV `47bb10cd4f79e8e9a84a753bf05898a875955b966c4d2550315b77327eb53aef`
- Manifest `508a94b28ef91d19b8e6ebbcdef679aff496558b9cecdc0ef2eee14859f69d8d`

The WAV is unprocessed **PCM16 mono 48 kHz**, 1,739,776
samples, **36.245333 s**, 0 clipped samples, actual full-record
peak -29.852 dBFS and RMS -58.9 dBFS. The program marked
`captured_aba_for_noise_diagnostics`. Controlled stop returned
recorder code 1, but WAV finalized correctly. Playback lasted
20.2548 s for a 20-second known reference (no premature exit).

**Measurements independently recalculated directly from uploaded
PCM samples using strictly interior windows (avoid transitions):**

| Phase | Approx WAV interval | RMS | Peak |
| --- | --- | ---: | ---: |
| speaker OFF before | 2.0–6.0 s | **-59.7853 dBFS** | -41.4281 dBFS |
| speaker ON | 12.0–24.0 s | **-58.4336 dBFS** | -43.1503 dBFS |
| speaker OFF after | 30.2453–34.2453 s | **-59.9374 dBFS** | -47.7023 dBFS |

The median half-second OFF-window RMS differs only **0.03799 dB**
between pre and post. Their combined mean power corresponds to
**-59.8607 dBFS**. The ON RMS increases only **1.4271 dB**.
The report's conditional stationary OFF power fraction is
**0.71993**, i.e. 72.0%; ON-related added power 28.0%.
This is a much better same-session estimate than the
non-contemporaneous baseline from separate WAVs, but
stationarity, independence and constant firmware gain are still
assumptions. A microphone on/off trial does not identify the
physical origin of all emitted audio.

**Independent bandpass analysis of the uploaded waveform**
(4th-order Butterworth, zero-phase, identical per phase):

| Frequency | OFF before | ON | OFF after | ON vs mean OFF power |
| --- | ---: | ---: | ---: | ---: |
| 20–70 Hz | -63.71 | -63.84 | -63.83 | **-0.07 dB** |
| 70–180 Hz | -74.55 | -73.04 | -74.62 | +1.55 dB |
| 180–500 Hz | -69.89 | -69.36 | -70.58 | +0.86 dB |
| 500–1500 Hz | -68.81 | -67.29 | -70.41 | +2.25 dB |
| **1500–4000 Hz** | **-69.80** | **-64.02** | **-70.09** | **+5.92 dB** |
| 4000–8000 Hz | -69.93 | -70.10 | -70.56 | +0.13 dB |
| 8000–16000 Hz | -78.14 | -80.17 | -81.14 | -0.78 dB |

The 20–70-Hz band is remarkably stable across phases and
accounts for roughly **40.6%** of power in the combined
silent-speaker segments. Welch PSD from the actual samples
has conspicuous narrow peaks around **50 Hz and 64 Hz** in all
three phases, with no attribution: they could be acoustic,
electrical, host/hub related or intrinsic USB mic/DSP features.
Do not assume mains hum alone without isolating sources.

Conversely the 1500–4000-Hz band gains **5.92 dB** when
speaker playback starts; this is congruent with the previous
M5-006 model producing **3.37 dB band-limited ERLE**, while
full-band ERLE remains **0.7574 dB (REJECT)**. The stable
low-frequency background can overwhelm a whole-band ERLE
even when music-correlated components are suppressed
meaningfully in a narrower band.

The biggest instantaneous PCM peaks occur around
**28.8–30.2 s and 36.0 s**, outside the selected
interior measurement windows. No clipping occurs.
Those peaks might be acoustic events, USB artifacts
or other transient sources; there is insufficient
evidence to label them.

**Interpretation and next measurement policy**

- One-session OFF/ON/OFF controls greatly strengthen the
  evidence for stationary overall background.
- `1.4271 dB` is NOT a proven upper bound on real
  echo cancellation; it is the conditional upper bound
  on removing additional independent ON power
  while leaving a stationary OFF baseline.
- AEC should also be evaluated by **removal of reference-
  correlated energy/coherence**, with independently
  measured speech preservation, rather than
  relying on full-band ERLE alone. Do not lower
  the existing fail-closed >=1 dB gate merely to
  force a candidate WAV.
- Retain the successful M4 microphone clean chain as-is.

### New reproducible OFF/ON/OFF spectrum tool (offline)

`tools/voice_m5_aba_spectrum.py` independently computes
the phase RMS, frequency-band RMS and ON/OFF power changes,
20–70Hz fraction, dominant ~50/64Hz spectral lines and
transient locations on **the existing immutable M5-ABA-001 WAV**.

The script rehashes both local source files against the
capture manifest, refuses an existing output folder, does not
write WAV or touch any audio device, and saves a JSON
audit plus SHA256SUMS.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aba_spectrum.py \
  --capture .audit/voice-aec/M5-ABA-001 \
  --out .audit/voice-aec/M5-ABA-spectral-001

cd .audit/voice-aec/M5-ABA-spectral-001
sha256sum -c SHA256SUMS
~~~

Only `aba-spectral.json` and `SHA256SUMS` should
appear in the new report directory. The newly added
regression test checks synthetic stationary 50Hz noise
and an independent 2kHz speaker component. New code
requires a local test run before acceptance.

## Actual M5 noise-probe result and one-session ABA control (2026-11-08)

The uploaded `M5-noise-probe-006/noise-probe.json` was independently
SHA256 verified against the supplied `SHA256SUMS`:
`035f056fc7e2f2d9e06838abe49aa804c2a8bc85f48963d091748b6967214b83`.
All the source hashes in this report match the previous
quiet-room M5 baseline and M5-006 paired capture manifests.

**Observed half-second RMS measurements (two independent takes):**

| Metric | Speaker OFF, baseline | Speaker ON, M5-006 calibration |
| --- | ---: | ---: |
| Windows | 21 | 8 |
| p10 | -60.38802 dBFS | -58.19258 dBFS |
| median | **-60.13671 dBFS** | **-57.78384 dBFS** |
| p90 | -59.74011 dBFS | -57.55171 dBFS |
| Window-power RMS | -59.84946 dBFS | -57.84339 dBFS |
| Lowest window | -60.60255 dBFS | -58.21556 dBFS |
| Highest window | -57.62427 dBFS | -57.48116 dBFS |

The baseline windows are relatively steady: 19/21
fall between -60.60255 and -59.74011 dBFS. Two windows,
indices 16 and 18 (0-based), rise to -57.62427 and
-57.83797 dBFS. These could be transient events, not
necessarily a stable elevated background.

The on/off median delta is +2.35217 dB, comparing
mean window powers yields +2.00607 dB, while
comparing speaker-only full-window to the full baseline WAV
yields +1.71714 dB (the tool's `difference_rms_db`).
The last value is not a contradiction: the whole quiet-room
WAV includes the start and end portions omitted by the
half-second windowed report, and the baseline contains
short level excursions.

The report's `conditional_background_power_fraction=0.673421`
uses the **full baseline RMS** (not the window medians).
The mean-window-power ratio implies about **0.6301** and
the ratio of median-window powers about **0.5818**.
These remain **conditional comparisons across non-simultaneous
recordings**, NOT a measurement of the true echo fraction
or a proven ceiling on achievable AEC.

The original AEC held-out ERLE remains **0.7574 dB**
and remains rejected; the baseline report provides no
independent evidence that near-end speech would survive.

### New controlled experiment: physical OFF → ON → OFF in ONE take

New CLI: `tools/voice_m5_aba.py`, which records the physical
USB microphone in one uninterrupted `pw-record` session
while the real analogue speaker:

1. is quiet for **8 seconds** (operator also quiet);
2. plays the **already verified local 20-second M5-006 digital
   reference** via `pw-play`, volume 0.30, on an explicitly
   selected physical sink;
3. returns to quiet for **8 seconds**.

The microphone and physical speaker remain stationary.
The tool uses known working process ownership (child process groups
and Linux parent-death signal) so Super+C or ordinary
interruption leaves **no owned playback or recorder processes**.
No system defaults, new virtual endpoints, settings, services,
automations or monitoring path are changed. A user should not
have another audio player running during this test.

The program writes only a NEW private
`.audit/voice-aec/M5-ABA-001` directory with the unprocessed
microphone WAV, recorder/player logs, manifest and SHA256SUMS.
It summarizes **interior** half-second windows in each phase,
avoiding approximate unsynchronized phase boundaries by at least
2–4 seconds. These offsets are approximate because the two
processes do not share hardware sample clocks. The result can
identify whether the background before and after is similar and
whether the speaker has a reproducible RMS effect in the
same take, but firmware AGC or changes in ambient noise
could still confound attribution.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aba.py \
  --capture .audit/voice-aec/M5-006 \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --pre-seconds 8 \
  --post-seconds 8 \
  --volume 0.30 \
  --out .audit/voice-aec/M5-ABA-001
~~~

One complete capture will last approximately **36 seconds**,
not counting process startup/finalization. The user does not
read text and must remain silent throughout. The script fails
closed on a short capture, an early process exit, invalid WAV,
clipping or changed source hash. Never overwrite an existing take.
New code is not accepted until the local tests pass.

**Next review:** user shares only
`M5-ABA-001/manifest.json` and the console test summary,
not private raw WAV. If the preceding and trailing quiet
levels match closely, the on/off comparison becomes materially
stronger than two recordings made at different times. This
still does not certify speaker echo reduction or speaker-to-mic
phase response; AEC acceptance remains a separate step.

## M5 real quiet-room baseline — 2026-11-08

The user supplied `M5-room-baseline-001/manifest.json` for the
separately powered-hub USB microphone, physical PipeWire source
`alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback`.

The new room capture is **valid**:

- Schema `cuewire-M4-raw-capture-v2`;
  status `recorded_unprocessed_raw`, stop reason `maximum_duration_reached`
- PCM16 mono, 48,000 Hz, **573,440 frames**, **11.946667 s**
- **RMS -59.563 dBFS**, peak **-29.624 dBFS**,
  **0 clipped samples**
- Recorder stopped by our existing lifecycle procedure
  (`pw-record` code 1 after forced/controlled stop), but the
  finalized WAV was parsed and passed integrity checks
- WAV SHA256 recorded by the local recorder:
  `845718e232570f6938848a06a7a8d9896d9f748e003d76a883a1ca68dc65449f`
- No audio processing, playback, route changes or virtual microphone.
  The baseline manifest alone does **not** allow independent
  rehashing/inspection of the RAW WAV from outside the user's machine.

Compare to the **previous** M5-006 speaker-only held-out
microphone segment (3.45–4.40 s in reference time):
**-57.516 dBFS RMS**. That is **+2.047 dB** above the
separately measured silent-room baseline. If microphone
gain/background conditions were identical and independent during
both takes, the hypothetical stationary noise component would
represent `10^(-2.047/10)=0.6242` (**62.4%** of speaker-on
mic power) and added uncorrelated speaker/other signal could
account for up to **37.6%**. In that strongly conditional
simple-noise model, the whole-band cancellation ceiling would
be about **2.05 dB** for those comparison levels.

**Do NOT promote the 2.05-dB figure to a measured physical upper
bound**; quiet and speaker-only sections were **not taken
simultaneously** and no independent proof of stationary noise,
gain or recording electronics exists. AEC's earlier 0.7574-dB
heldout improvement remains the real result on M5-006. Band-only
3.37-dB improvement does not contradict a lower full-band
noise-limited bound because band-specific noise powers differ.

The room baseline's peak -29.624 dBFS is ~29.94 dB above
its overall RMS. This may be a transient or an intermittent
event rather than typical room hiss; a single global RMS
and peak cannot identify the origin. Local, non-overwriting
windowed analysis is needed.

### Next gate — two immutable WAV pairs, no additional recording

New `tools/voice_m5_noise_probe.py` compares the separately
recorded quiet-room baseline and the established M5-006 speaker-only
calibration period, calculating **half-second RMS power windows**,
10th / 50th / 90th percentiles and their variability. Only the
first 4.4 seconds of M5-006 are used; user voice is excluded.
All three WAV files are SHA256-verified against their *respective*
original capture manifests, and an entirely new private report
directory is used.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_noise_probe.py \
  --baseline .audit/voice-captures/M5-room-baseline-001 \
  --capture .audit/voice-aec/M5-006 \
  --out .audit/voice-aec/M5-noise-probe-006

cd .audit/voice-aec/M5-noise-probe-006
sha256sum -c SHA256SUMS
~~~

Output: `noise-probe.json` and `SHA256SUMS`, no WAV.
The new test in `test_voice_m5_aec.py` checks source
immutability, JSON-only output and refusal to overwrite existing
diagnostics. New code/test must be run locally before acceptance.

If background is stable in short windows, the next *independent*
experiment should record a **single continuous mic session**
with explicit silent-speaker -> speaker-only -> silent-speaker
phases at fixed device position and gain, and an acoustically
matched digital reference. Avoid concluding anything about
the original source of the microphone's noise from
these two non-simultaneous captures alone.

## M5-006 FIR model ablation — all 15 candidates measured (2026-11-08)

The user provided the real `M5-model-probe-006/model-probe.json` and
its `SHA256SUMS`. The JSON's SHA256 was independently verified:
`efd9900cafd299bc8d0189bb060ad9f0ad42e26a0f035802d6f9750780f53c52`.

**Held-out (3.45–4.40 s) full-band ERLE, in dB:**

| FIR (ms) | Ridge .005 | Ridge .02 | Ridge .10 |
| --- | ---: | ---: | ---: |
| 30 | 0.57304 | 0.57608 | 0.58573 |
| 60 | 0.74869 | 0.75155 | 0.76239 |
| 90 | 0.74740 | 0.75347 | 0.76980 |
| 120 | 0.75180 | **0.75738** | **0.77396** |
| 150 | 0.73879 | 0.74300 | 0.75638 |

The 120-ms/.02 control reproduced the previous algorithm's
training ERLE 0.7200 and held-out 0.7574 dB. The best *exploratory*
held-out result is 120-ms/.10 at 0.77396 dB, only **+0.01658 dB**
above the unchanged control, and **none** of the 15 exceeds the
existing >=1.0 dB minimum gate. Choosing the maximum out of 15
using this shared holdout would invalidate a claim of independent
validation for that chosen setting.

For the 120-ms/.02 control, band-pass held-out ERLE is:

- 150–500 Hz: 0.9031 dB
- 500–1500 Hz: 0.7692 dB
- **1500–4000 Hz: 3.3701 dB** (~54.0% reduction in band energy)
- 4000–8000 Hz: -0.0092 dB (no measurable improvement in this band)

More FIR taps do not fix the overall model; 60–150 ms all cluster
between approximately 0.739 and 0.774 dB with ridge 0.02–0.10.
The fitted model captures speaker-related structure particularly
in high-middle frequencies but does not provide reliable whole-band
AEC. The frequency-specific ERLE is measured on the **speaker-only**
calibration holdout, not on the user's later speech. Do not claim
voice preservation or real-time processing success.

### Next discriminating gate: silent speaker baseline

Before making yet another music/AEC capture, collect **one short
standalone physical microphone take with the speaker silent**, using
the same working externally powered USB hub, microphone position,
PipeWire source node and recording gain. This lets us estimate an
ambient microphone RMS floor under similar conditions and compare it
with the speaker-only portion of M5-006. A comparison across separate
takes is only suggestive: room ambience, device state and sources may
change. It cannot by itself isolate the true speaker leakage.

Avoid playing audio through *any* nearby speaker during this baseline.
Remain silent for the complete take, stop unrelated audio playback,
and leave microphone and speaker stationary.

~~~bash
cd /data/cue-wire
pactl list short sources
python3 tools/voice_raw_capture.py record \
  --mic 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --out .audit/voice-captures/M5-room-baseline-001 \
  --max-seconds 12 \
  --startup-check-sec 7
~~~

Let it run its 12-second cap with complete silence; no Ctrl+C
required. The original `voice_raw_capture.py` writes
`raw.wav` and a private, SHA256-audited `manifest.json` to a **new
directory**. Its tool includes a cautious buffered-file readiness
check at 7 seconds; if that fails, retain the aborted folder as
evidence instead of reusing the name. The soundboard/virtual mic
should not be running during this baseline.

Share only its `manifest.json` first. A quiet mic WAV with finite
nonzero RMS, sane duration and zero clipping gives a first noise
floor estimate. Do not upload any raw private audio unless deeper
waveform analysis is explicitly needed. Once the baseline is known,
we can choose between (a) calibrating an independent low-noise test
source/near-speech double-talk protocol or (b) investigating model
mismatch despite a low noise floor.

## M5-006 v3 band-selected lag: stable high-mid band, weak AEC remains

The user supplied the real `M5-bandlag-006/spectral.json` report, which
matches the existing reference/mic/capture SHA256s and identifies schema
`cuewire-M5-spectral-diagnostics-v3`.

Measured best lag (ms) for four windows (0.30–1.20 / 1.35–2.25 /
2.40–3.30 / 3.40–4.30 s):

| Hz | Four lag estimates (ms) | Max-min (ms) |
| --- | --- | ---: |
| 70–180 | 102.125 / 105.458 / 95.375 / 109.146 | 13.771 |
| 180–500 | 110.813 / 106.063 / 110.667 / 104.583 | 6.229 |
| 500–1500 | 102.396 / 108.813 / 108.813 / 108.792 | 6.417 |
| **1500–4000** | **101.333 / 101.313 / 101.542 / 101.292** | **0.250** |
| 4000–8000 | 100.938 / 100.521 / 100.521 / 100.917 | 0.417 |

At 1500–4000 Hz the normalized correlation scores are
0.304735 / 0.350095 / 0.318457 / 0.367615. This band preserves
a near-101.3ms peak on all four windows, *including* the third window
where broadband correlation selected the 93.1875ms musical peak.
Thus this high-mid band is a useful candidate for **robust initial
lag detection**, subject to more independent recordings and speaker
excitation testing. It is not a certified sample-accurate DAC latency
or a guaranteed echo path.

The 4000–8000Hz band also has comparatively small lag variation,
but its best normalized correlations are only ~0.078–0.099,
making its apparent stability less trustworthy.
Below 1500Hz the strongest band-limited peaks are highly ambiguous.

**Critical distinction:** the M5 frozen AEC model uses the
global **101.2917ms** delay already, which is close to the reliable
1500–4000Hz lag. Therefore the broadband musical
correlation ambiguities alone cannot explain the poor
**0.7574dB held-out ERLE**. Fitting the linear acoustic path is
the next bottleneck to investigate, and the mic may also contain
substantial background unrelated to the played speaker.

### M5-006 FIR ablation: diagnostic model comparison, NO voice WAV

New tool: `tools/voice_m5_model_probe.py`. It reads the already
captured M5-006 pair and fits **15 independent fixed linear filters**
on the original speaker-only *training* section (0.25–3.30s):

- FIR lengths 30, 60, 90, 120, 150 ms
- Wiener spectral ridge factors 0.005, 0.02, 0.10
- same global delay, 8192 FFT size, **training-only** fitted gain
- records overall and band-specific ERLE on the held-out
  3.45–4.40s section
- the **120ms / 0.02** candidate is intentionally equivalent
  to the current AEC model, as a control
- no WAV is written; no devices/sinks/streams/configs are changed

This grid is *exploratory*. Comparing 15 candidates on one hold-out
makes that hold-out a **model-selection set**. Even if a candidate
scores well, it is NOT independently validated after selection.
Do not change the original ERLE gate or apply a selected model
to the near-end voice without a **new independent speaker-only
recording** with a genuinely unused validation interval.

~~~bash
cd /data/cue-wire
git pull --ff-only
~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_model_probe.py \
  --capture .audit/voice-aec/M5-006 \
  --out .audit/voice-aec/M5-model-probe-006

cd .audit/voice-aec/M5-model-probe-006
sha256sum -c SHA256SUMS
~~~

The only report files are `model-probe.json` and `SHA256SUMS`.
No new speaker playback and no microphone recording are needed.
Keep all prior M5 data immutable. Review the full train/holdout
matrix, not only the best value, before deciding whether a new
controlled calibration is worth running.

## M5-006 alternate-peak report: nearly periodic 8.1-ms ambiguity

From the real `M5-spectral-peaks-006/spectral.json`, the original
global lag remains **101.29167 ms**, with normalized correlation
**0.117837** and source SHA256s unchanged from the original
M5-006 reference and physical microphone WAVs.

The top separated correlation peaks form a repeated pattern near
**93.2 ms**, **101.3–101.5 ms**, and **109.4 ms** across all four
speaker-only windows. Adjacent separations around **8.08 ms**
correspond to a repetition frequency near **123.7 Hz**.
This is compatible with a quasi-periodic musical component or
harmonics: it is **not proof** of a 123.7-Hz acoustic oscillator,
nor a measurement of speaker-to-mic room delay alone.

| Reference window (s) | Highest peak (ms) | Best/second peak ratio |
| --- | ---: | ---: |
| 0.30–1.20 | 101.29167 | 1.38855 |
| 1.35–2.25 | 101.54167 | 1.12484 |
| 2.40–3.30 | **93.18750** | **1.04068** |
| 3.40–4.30 | 101.52083 | 1.11896 |

In the anomalous third window the best and second peaks
have correlations **0.153677** (93.1875 ms) and **0.147670**
(109.35417 ms), only 4.07% apart by score. The original global
delay is still a local candidate with **0.137056** correlation.
These competing peaks strongly support the **lag-estimation
ambiguity** explanation of the apparent instantaneous ~8-ms jump;
they do not support claiming steady clock drift from this report.

**Important: the production AEC algorithm has always used ONE
fixed global delay**, not independent per-window shifts. The
ambiguous local maxima therefore do **not** in themselves
explain the earlier weak **0.7574-dB held-out ERLE**; there may
still be substantial signal-to-noise/model mismatch.

### More discriminating on-the-same-audio diagnostic (v3)

`tools/voice_m5_spectral.py` now additionally applies identical
offline zero-phase Butterworth bandpass filters to reference and mic,
then computes the four local correlations **separately by frequency**,
in 70–180 Hz (contains the ~123.7-Hz candidate component),
180–500 Hz, 500–1500 Hz, 1500–4000 Hz and 4000–8000 Hz.
This may show whether the ~8-ms peak lattice disappears once
the low-frequency periodic component is excluded.
Low-energy bands are marked unreliable instead of inferring
a meaningful delay.

The bandpass results are exploratory; they are neither true
clock drift measurements nor a revised acoustic filter.
No sound is played, no voice is altered, and no audio WAV is generated.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_spectral.py \
  --capture .audit/voice-aec/M5-006 \
  --out .audit/voice-aec/M5-bandlag-006
~~~

Report: `.audit/voice-aec/M5-bandlag-006/spectral.json`;
the `frequency_selected_lag_diagnostics` field holds
each frequency band's four lag estimates. Keep previous
`M5-spectral-006` and `M5-spectral-peaks-006` immutable.
If the output folder already exists use a fresh ID.

## Actual M5-006 spectral.json review — low broadband coherence, one ambiguous lag

The user provided the real `M5-spectral-006/spectral.json`.
These are calibrated **speaker-only** measurements from the existing
M5-006 WAV pair (not synthetic tests). The report repeats the exact
SHA256s of the previous real microphone WAV, reference and capture
manifest. The spectral file's own checksum was not provided in this
turn, so integrity of that *report file* has not been separately
verified via its `SHA256SUMS`.

Band frequency / median magnitude-squared coherence / reference-energy
weighted coherence:

| Hz | Median | Weighted |
| --- | ---: | ---: |
| 150–500 | 0.041050 | 0.172791 |
| 500–1500 | 0.034935 | 0.359191 |
| 1500–4000 | 0.100132 | 0.331926 |
| 4000–8000 | 0.037990 | 0.109944 |

The energy-weighted values are substantially higher than the medians
in some bands. This indicates the energetic components of the music
have stronger linear coherence than typical frequencies **within each
band**, not that the speaker leakage fraction or AEC ERLE equals these
numbers. A short music-only interval and spectrally concentrated
excitation can bias the interpretation.

Measured global lag: **101.29167 ms**, normalized correlation 0.117837.
The four independent ~0.9-s local windows:

| Reference time (s) | Lag (ms) | Correlation |
| --- | ---: | ---: |
| 0.30–1.20 | 101.29167 | 0.122993 |
| 1.35–2.25 | 101.54167 | 0.139832 |
| 2.40–3.30 | **93.18750** | 0.153677 |
| 3.40–4.30 | 101.52083 | 0.151539 |

Three windows select ~101.3–101.5 ms; only the third selects ~93.2 ms,
then the estimate returns to ~101.5 ms. This **does not support
claiming monotonic clock drift**. Plausible causes include competing
music-correlation peaks, acoustic reflections and weak broadband
signal-to-background ratio. A short high correlation at the alternative
lag does not prove it is the physical direct path.

**Next test on the SAME frozen WAVs, no re-recording:**

The spectral tool is now version 2. It keeps existing first-best lag
estimates but additionally records up to five correlation-peak
candidates separated by ~0.8 ms, score at the original global lag,
and best/second-best separated-peak ratio for each window. This helps
distinguish a genuinely different dominant delay from two near-tied
competing peaks. The candidate search is **diagnostic only**, not an
AEC filter update or production acceptance.

~~~bash
cd /data/cue-wire
git pull --ff-only
~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_spectral.py \
  --capture .audit/voice-aec/M5-006 \
  --out .audit/voice-aec/M5-spectral-peaks-006
~~~

Keep `M5-spectral-006` immutable. If any output directory exists,
pick a new number. Inspect the v2 `spectral.json` before designing
another acoustic take. Do not increase compression or denoising
to mask an unverified echo cancellation model, and do not change the
1-dB ERLE fail-closed gate.

## Follow-up M5-006 spectral audit (no new recording)

The standalone `tools/voice_m5_spectral.py` provides additional
calibration-only **numerical diagnostics**, distinct from both the
regular AEC subtraction and the M5 shift/gain oracle audit.
It reads the same verified M5-006 reference and mic WAVs, and:

- estimates local delay in four ~0.9-s speaker-only windows, searching
  only +/-10 ms around the original global peak lag;
- computes magnitude-squared reference/mic coherence by frequency
  (150–500, 500–1500, 1500–4000, 4000–8000 Hz) over 0.30–4.35 s of
  the speaker-only calibration;
- saves only `spectral.json` and `SHA256SUMS` in a NEW private
  diagnostics directory; never emits audio or adjusts the AEC filter;
- cannot certify true speaker-to-mic energy fraction, absolute
  alignment, or preservation of the user's voice.

Run in the dedicated scientific Python venv (NumPy/SciPy already needed
by existing M5 AEC):

~~~bash
cd /data/cue-wire
git pull --ff-only
~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_spectral.py \
  --capture .audit/voice-aec/M5-006 \
  --out .audit/voice-aec/M5-spectral-006
~~~

Read `M5-spectral-006/spectral.json` after verifying its
`SHA256SUMS`. **Band coherence** is a normalized relationship
statistic and does not directly imply AEC ERLE. A high-coherence
frequency band can still have insufficient overall energy; a low
coherence band can be dominated by background, nonlinear speaker
distortion, unmodeled reflections, or weak excitation in the reference.

Local lag differences are exploratory: musical periodicity and
short-window noise can make apparent lag jump. Only a stable,
consistent pattern would motivate clock-drift study; do not update
the production filter using these exploratory measurements alone.
All earlier M4 and M5 recordings remain unchanged.

## M5-006 numerical diagnosis — verified report 2026-11-08

The independently generated private `M5-diagnostics-006/diagnostics.json`
passed its own SHA256SUMS check (digest
`1d25ca88ac2b5efc960d0c986b56c74c13efc33a6af6e8cc0395838c1ee34fc0`).
These are actual measurements from the M5-006 captured microphone and
speaker reference, **not** synthetic performance results.

Measured:

| Metric | Value |
| --- | ---: |
| Training ERLE | **0.7200 dB** |
| Held-out ERLE | **0.7574 dB** (FAIL, threshold 1.0 dB) |
| Reference/mic correlation | 0.117837 |
| Coarse acoustic peak lag | 101.2917 ms / 4862 samples |
| Fitted gain scalar | 1.121617 |
| FIR impulse length | 120 ms / 5760 samples |
| Validation mic RMS | -57.516 dBFS |
| Validation echo prediction RMS | -64.91889 dBFS |
| Holdout optimal **oracle** scale at 0 ms | 0.9400 |
| Holdout oracle ERLE at 0 ms | 0.7608 dB |

**Interpretation grounded in this diagnostic:**

1. The fit is weak *on both training and hold-out*: 0.72 versus 0.7574
   dB. Strong train-to-holdout overfitting is not indicated by these
   numbers; poor model adequacy / a weak echo component remain possible.
2. With unchanged fitted gain, small holdout timing shifts worsen
   performance: at -0.5 ms ERLE -0.0493 dB; +0.5 ms +0.0497 dB;
   at ±1 ms both values negative. The tested local optimum is near
   **zero relative shift**; this does not exclude independent clock
   drift over the full 20-s recording or errors smaller than 0.5 ms.
3. Optimizing only a scalar on the holdout makes **negligible**
   improvement (0.7574 -> 0.7608 dB), meaning a simple global gain
   mismatch is unlikely to explain the weak suppression.
4. The predicted echo RMS is 7.40289 dB below total mic RMS. The
   squared-RMS ratio is `10^(-7.40289/10) = ~0.18185`, or 18.2% of
   mic signal power. The achieved suppression corresponds to a
   residual-to-original energy ratio `10^(-0.7574/10) = ~0.83996`,
   a 16.0% energy reduction. **Those are energy ratios, not a
   rigorously measured proportion of actual speaker sound**: the echo
   predictor can contain error and may be correlated with ambient
   signals. Never label 18.2% as ground-truth leakage fraction.
5. No AEC voice WAV was generated, correctly. The production gate
   remains unchanged; the holdout-optimized "oracle" shifts/gains
   must never be reused for a purported independent validation.

**Next discriminating experiment:**
measure a quiet-mic/background-only baseline (speaker silent),
followed by speaker-only reference music (no near-end voice),
then simultaneous near-end speech and music. This would help
distinguish a genuinely weak speaker component from a mismatched
acoustic model. The baseline and speaker-only periods must be
separately time-marked in both the reference and microphone signals
and the code must use matching calibration indices; do **not**
apply the current first-5-seconds assumptions to such a recording
without explicitly updating the analyzer.

For existing M5-006 samples, before recollecting audio, we can
also compute frequency-resolved reference/mic coherence and
time-varying lag on the clean speaker-only section, provided we
report their statistical uncertainty. Neither requires loosening
ERLE acceptance or touching existing voice.

## M5-006 first REAL AEC failure: independent hold-out 0.76 dB

Using the successful, real M5-006 WAV pair, the original
`voice_m5_aec.py` fitted its acoustic model, but held-out
speaker-only ERLE was **0.76 dB**, strictly below the unchanged
**1.0 dB** minimum. The algorithm correctly **rejected** the model;
no speech-subtraction candidate was generated.
The `pw-record.log` output supplied for the attempt contained the
command argv but did not identify an additional runtime fault.
This is now a *model-validation* failure, **not** an audio-capture
failure.

Do **not** lower the ERLE threshold or call 0.76 dB adequate
cancellation. Even passing 1 dB would only clear an initial minimal
engineering gate, not prove that the voice remains natural.

### Offline diagnostics without changing the acceptance gate

A new opt-in `--diagnose` mode runs numerical analysis only.
It uses the immutable, verified M5-006 microphone/reference WAVs,
performs the same lag estimate and filter fit, and reports:

- normalized reference/mic cross-correlation and the candidate
  acoustic lag (milliseconds)
- training and held-out ERLE with the frozen fitted filter
- microphone/echo-prediction RMS on the held-out section
- a **diagnostic-only**, small temporal offset sweep
  (-10..+10 ms) with the existing gain
- an **oracle** gain for each shift, optimized on the held-out
  section, included solely to help identify whether a level mismatch
  could explain weak suppression

The oracle values are *not* independent test results and **must never
be applied** to the voice or promoted into production. If the
calibration window was contaminated with near-end speech, the
numbers cannot certify the echo model. No WAV, new recording,
audio playback, routing changes or hardware access occurs.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --diagnose \
  --capture .audit/voice-aec/M5-006 \
  --out .audit/voice-aec/M5-diagnostics-006
~~~

The resulting private `M5-diagnostics-006/diagnostics.json` and
`SHA256SUMS` record numerical evidence only. Do not rerun into
an existing directory; use a fresh identifier instead. Share the
printed summary or JSON for review (no need to upload private audio).

Next research decision: if a tiny delay shift markedly improves the
independent *fixed-gain* ERLE, investigate clock/synchronization;
if only the hold-out oracle gain helps, test a revised gain/path model
on **a new independent capture**; if neither helps, investigate acoustic
nonlinearity, room instability and limitations of the 120-ms
Wiener-filtered impulse response. These are hypotheses, not conclusions
from the current 0.76 dB alone.

## M5-006 actual paired capture results — FIRST SUCCESSFUL REAL TAKE

The first *real*, successfully completed paired speaker + physical
microphone capture is documented in the locally stored
`.audit/voice-aec/M5-006/manifest.json`. It reports
`status=captured_for_offline_aec`. This is an actual hardware capture,
not the earlier synthetic AEC unit-test fixture.

- Speaker stream: `alsa_output.pci-0000_00_1f.3.analog-stereo`;
  `pw-play` exit code 0, observed playback 20.074 s
- Digital reference: mono 48-kHz 16-bit PCM WAV, **960,000 frames = 20.0 s**;
  SHA256 `afd3b0ea1141147d6df284bfab62d2a9c9adc579053cfbea8c35559f2e94aa90`
  unchanged before/after recording
- Microphone capture backend: explicit `pipewire`, physical node
  `alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback`
- Microphone actual WAV: mono, 48 kHz, PCM16,
  **985,088 frames = 20.522667 s**; peak **-17.595 dBFS**, RMS
  **-40.926 dBFS**, **0 clipped samples**
- First file PCM bytes seen about 0.1606 s after playback launch
- Microphone SHA256 `606d0876896150399ee729e4a9eea8fc2d261af956aae6d5159f229903d73b66`,
  matching both manifest copy fields
- `pw-record` reports exit code **1** after CueWire's controlled stop,
  despite a verified valid WAV. Keep the `pw-record.log` for inspection;
  no definitive explanation of this code is asserted from the manifest alone.
- The microphone WAV is 0.522667 s longer than the digital reference.
  This is the capture envelope difference, **not** an acoustically
  measured playback delay. Estimate offset using AEC cross-correlation.

**Next command (no new capture, no system changes):**

~~~bash
cd /data/cue-wire
~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --capture .audit/voice-aec/M5-006 \
  --out .audit/voice-aec/M5-analysis-006
~~~

The offline analyzer still must prove a usable correlation and
held-out calibration ERLE before we can regard the AEC experiment as
technically successful. Success of the capture does not establish
that the local voice was preserved or that subtracting the speaker
audio worked in double-talk. Keep the original RAW and reference
unchanged, compare subjective sound only after numerical acceptance.

To examine the recorder termination without changing system state:

~~~bash
tail -n 35 .audit/voice-aec/M5-006/pw-record.log
~~~

## M5 hardware recovery: alternate powered hub (2026-11-08)

The user could not connect the microphone directly to the laptop because
its onboard USB ports were already occupied. Two external hubs were
available: an inexpensive hub and a higher-quality, separately powered hub.

Moving **only** the MICAudio-97i microphone to the other, separately
powered hub restored recording through the existing PipeWire
`pw-record` path. The completed `mic-isolated-002.wav` contains
**181,248 frames at 48,000 Hz = 3.776 seconds**, rather than zero
frames. This is an observed **real-hardware capture**, not a synthetic
unit test. The source remains unprocessed RAW; recording quality,
peak/RMS and the full 20-second stability have not yet been audited.

The evidence strongly suggests that USB-path conditions affect the
fault, but does **not** isolate the cause to hub power quality.
Unplug/replug also resets USB device state; alternative explanations
include cable/connector integrity, topology, driver state, and
firmware reinitialization. Keep the working device on the current
hub for the next experiment and do not disturb other devices.

**Next gate before AEC M5-006:** re-inspect the physical microphone
source after re-enumeration. The source name/ALSA card index may have
changed; never assume the old name is still valid.

~~~bash
cd /data/cue-wire
pactl list short sources
arecord -l
lsusb -t
~~~

The historical sink was
`alsa_output.pci-0000_00_1f.3.analog-stereo`.
If it remains present and the physical mic source is still exactly
`alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback`,
run fresh **M5-006** (not any prior directory):

~~~bash
python3 tools/voice_m5_capture.py \
  --music '/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --capture-backend pipewire \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --seconds 20 --calibration-seconds 5 --volume 0.30 \
  --out .audit/voice-aec/M5-006
~~~

Check for an explicit capture PASS and a finalized full-length microphone
WAV before any analysis. On successful capture:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --capture .audit/voice-aec/M5-006 \
  --out .audit/voice-aec/M5-analysis-006
~~~

A successful 3.776-second isolated take makes a paired experiment
reasonable, but does not itself demonstrate that the 5-second acoustic
calibration or 20-second simultaneous capture will succeed. Keep the
five-second speaker-only period free from local speech; read after
the console prompt. The resulting AEC WAV remains experimental until
verified against the microphone RAW by listening and measurement.

## USB topology finding: microphone behind cascaded hubs

The read-only check confirms the following **observed, current** device
state, without guessing from generic USB errors:

- Anlya.cn `MICAudio-97i`; USB VID:PID `0031:1133`, serial
  `08613544166503`
- USB bus path `3-3.1.4`: host xHCI → external/embedded USB hub
  `3-3` → downstream hub `3-3.1` → **port 4** microphone
- Device enumerates at **12 Mbps (Full Speed)**, uses kernel
  `snd-usb-audio`; normal USB 1.1 audio speed alone does not
  establish a bandwidth fault
- ALSA `card 0 [MICAudio97i], device 0`, capture subdevice present
- `/sys/bus/usb/devices/3-3.1.4/power/control=on` and
  `runtime_status=active` at inspection; it is **not currently in
  runtime suspend**, so do not apply autosuspend hacks without evidence
- Previous `pw-record` stream was correctly linked to the physical
  `capture_MONO` port but finalized with **zero audio frames**.
  PipeWire node RUNNING and ALSA card enumeration do not prove actual
  samples are streaming.

**Controlled physical isolation test (optional, explicit user action):**

1. Stop/finish *only* the microphone recording process; don't unplug
   anything with an active read or write.
2. Identify a **direct motherboard USB port** and move **only the
   USB microphone** from the cascaded hubs to that port. Do not reset
   or unplug the shared hubs containing printer, storage, mouse, etc.
3. Check new `lsusb -t`, `arecord -l`, and
   `pactl list short sources`. The ALSA card index and source name may
   change. Confirm matching VID:PID and serial before using a source.
4. Run a **fresh 5-second isolated microphone test** using the *new,
   explicitly observed* `alsa_input.*` source; gracefully Ctrl+C,
   then check WAV frame count and source peak.
5. If frames appear, this supports a USB path/port dependency, but
   doesn't yet prove the exact failing hub, connector, power issue, or
   driver behavior. Repeated stable captures and a later paired AEC
   run would be separate acceptance stages.
6. If still zero, collect targeted kernel/USB audio logs and
   `pw-top` diagnostics before any service restarts, driver reloads,
   default routing changes or permanent system configuration changes.

Example read-only pre/post checks:

~~~bash
lsusb -t
cat /proc/asound/cards
arecord -l
pactl list short sources
~~~

This recommendation leaves the approved M4 speech WAVs and all private
failed M5 attempts immutable; no AEC result may be claimed while the
microphone produces zero samples.

## M5 upstream USB / ALSA log diagnosis (2026-11-08)

Subsequent diagnostics from the actual Arch host:

- `pactl get-source-mute`: `Mute: no`; `get-source-volume`:
  **100%, 0.00 dB**. Neither mute nor zero source volume explains the
  silent capture.
- WirePlumber reports **`spa.alsa: poll fd error/hangup (card removed?)`**
  on at least two occasions. This describes a failed ALSA poll
  descriptor but does not by itself prove a physical unplug.
- The kernel log explicitly enumerates **`Anlya.cn MICAudio-97i`**
  on **USB path `3-3.1.4`**, behind a hub. Enumerated USB nodes can
  still fail to stream captured audio.
- Kernel `bogus dB values (-12800/-12700), disabling dB reporting`
  belongs to the USB audio mixer range sanity check. The kernel handles
  bad device-supplied mixer units; this line does not establish
  streaming failure.
- **Do not misattribute `usblp0: nonzero read bulk status ... -71` to
  the microphone.** That line names the USB printer interface.
  `-71` is USB EPROTO, but the named endpoint matters.
- The user's earlier M4 physical ALSA RAW capture succeeded, whereas
  current direct ALSA capture returns EIO and the PipeWire mic stream
  is physically linked but writes zero frames. A source/USB/ALSA
  failure is plausible; exact root cause is not established.

**Next diagnostic is read-only and isolated to the USB microphone:**

~~~bash
echo '=== USB TREE ==='
lsusb -t
echo '=== CURRENT SOUND CARDS ==='
cat /proc/asound/cards
arecord -l
echo '=== MIC USB SYSFS ==='
for key in idVendor idProduct manufacturer product serial speed; do
  file="/sys/bus/usb/devices/3-3.1.4/$key"
  if test -r "$file"; then printf '%s: ' "$key"; cat "$file"; fi
done
for key in control runtime_status; do
  file="/sys/bus/usb/devices/3-3.1.4/power/$key"
  if test -r "$file"; then printf 'power/%s: ' "$key"; cat "$file"; fi
done
echo '=== TARGETED KERNEL EVENTS ==='
journalctl -k -b --no-pager | grep -Ei \
  '3-3\.1\.4|MICAudio|snd.usb.audio|usb.*disconnect|usb.*reset' | tail -n 65
~~~

These commands do **not** change drivers, PipeWire, sysfs or system
settings. The sysfs path can change after USB re-enumeration; if files
are absent, consult `lsusb -t` again and do not write to sysfs.

Only after preserving the read-only evidence, consider a controlled
physical replug of **just the microphone** (with no recording in
progress), ideally testing a direct motherboard USB port rather than
the intervening hub. Re-check its new source/card identifiers and
perform a new 5-second mic-only test **before** another 20-second
speaker/music AEC calibration. Never indiscriminately reset a common
USB hub, which could disrupt keyboard, mouse, printer, storage and
other peripherals.

Reference context: Linux kernel USB error codes document -EPROTO
(-71) as possibly including bad signalling, no response, or device
disconnect, and caution that the host controller often cannot
disambiguate these causes. Kernel ALSA USB mixer code explicitly
detects implausible dB ranges.

## M5 standalone capture diagnostic: live graph linked, zero frames

After M5-005, a standalone explicit capture targeted the correct
physical USB source:

`alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback`

During `pw-record --verbose`, the source showed **RUNNING** in
`pactl list short sources` and `pw-link -l` showed the concrete port link
`...capture_MONO -> pw-record:input_MONO`. Nevertheless, after graceful
completion the standalone `mic-isolated-001.wav` also had **zero
frames**. The verbose recorder repeatedly printed `buffered:0
buffers:2 avail:0` (and `ticks:0` in the screenshot). This rules out
the M5 program's startup timing or final-file acceptance checks as the
sole explanation. Linking and node RUNNING status do **not** prove a
driver actually delivered PCM samples.

**Next phase is upstream hardware/graph diagnostics, not another AEC
recording.** Do not restart audio services, change defaults, or relax
the final zero-frame check to force apparent success.

Read-only checks:

~~~bash
MIC='alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback'
pactl get-source-mute "$MIC"
pactl get-source-volume "$MIC"
journalctl --user -b -u pipewire -u wireplumber --no-pager | tail -n 70
journalctl -k -b --no-pager | grep -Ei 'snd.usb.audio|usb|xhci|audio' | tail -n 70
~~~

For a live diagnosis when a short isolated `pw-record` is running,
use `pw-top` to inspect the *driver* and input stream's
`WAIT`, `BUSY`, `ERR`, `QUANT` and `RATE`. Note whether counters
change, not just whether the node says `RUNNING`. The source is
correctly linked but has not produced WAV frames.

The earlier M4 direct ALSA microphone recording did work in another
session. The later repeated `arecord: pcm_read:2285: read error:
Input/output error` and zero PipeWire frames suggest a possible
USB/ALSA source/driver or stalled PipeWire graph. Exact cause
**remains unproven**; gather logs and check the active device before
any corrective state changes.

## M5-005 actual-hardware outcome: zero microphone frames

The user confirmed that the speaker music completed all ~20 seconds,
the five-second speech prompt appeared, and the PipeWire mic recorder
survived through playback. However, the finalized microphone WAV
contained **0.0 seconds** of audio. This was a correctly rejected
capture; no valid real-world AEC measurement or candidate was made.
Successful AEC ERLE scores earlier in the terminal were synthetic only.

**Do not retry complete music-plus-voice takes until a mic-only
recording proves that the explicit physical source produces samples.**
Do not loosen the final zero-length WAV check. Investigate the source
link and USB audio path independently:

~~~bash
cd /data/cue-wire
pw-record --list-targets
pw-record --verbose \
  --target='alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --rate=48000 --channels=1 --channel-map=mono --format=s16 \
  .audit/voice-aec/mic-isolated-001.wav
~~~

In this foreground diagnostic, speak for 5 seconds and press Ctrl+C.
In a second terminal during capture, run:

~~~bash
pactl list short sources
pw-link -l
~~~

Determine whether the actual mic node changes from SUSPENDED to RUNNING
and whether the capture stream is linked to its output port. Check the
new file **after** recording has stopped and the WAV has finalized:

~~~bash
python3 - <<'PY'
from pathlib import Path
import wave
p = Path('.audit/voice-aec/mic-isolated-001.wav')
print('bytes:', p.stat().st_size)
with wave.open(str(p), 'rb') as w:
    print('rate:', w.getframerate(), 'channels:', w.getnchannels(),
          'frames:', w.getnframes(), 'seconds:', w.getnframes()/w.getframerate())
PY
~~~

If this mic-only WAV also has zero frames, diagnose USB/ALSA/PipeWire
ports before any more M5 paired takes. A zero-length WAV cannot be
fixed by AEC, denoising, timing or loosening acceptance conditions.

## M5-005: avoid false rejection from in-progress WAV buffering

M5-004 selected the correct physical PipeWire microphone and launched
music through the intended speaker. It stopped at one second with
`Microphone has not written PCM after 1 second`. The `pw-record.log`
tail did not report a process error: it showed only command arguments.

That observation does **not** distinguish a genuinely inactive input
from delayed/ buffered WAV writes. The one-second file-size gate was
premature and has been removed. The new protocol:

1. Launch microphone recorder and speaker back-to-back (same as M5-004).
2. While both processes run, watch for actual recorder **process exit**;
   any early exit aborts and stops playback. File size is advisory only.
3. Prompt for the local speech sample after five seconds of playback
   even if the live WAV has not visibly grown yet.
4. Stop the recorder gracefully at the end, allow WAV header finalization,
   then verify **48-kHz mono PCM16**, at least requested duration minus
   one second, non-negligible actual RMS (above -85 dBFS), and SHA256.
5. If the resulting file is absent, too short, effectively silent or
   corrupted, reject the entire experiment. No AEC model is trained.

This is **not** proof that the USB device is healthy: the final WAV
measurement decides. M5-001..M5-004 remain failed archives.

Before repeating, inspect whether M5-004 even produced a file:

~~~bash
cd /data/cue-wire
ls -lh .audit/voice-aec/M5-004/microphone-raw.wav
tail -n 20 .audit/voice-aec/M5-004/pw-record.log
~~~

Either command may legitimately report a missing file.

### Full retry with the verified physical mic (new M5-005)

~~~bash
cd /data/cue-wire
git pull --ff-only
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover -s tests -p 'test_voice_m5_aec.py' -v

python3 tools/voice_m5_capture.py \
  --music '/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --capture-backend pipewire \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --seconds 20 --calibration-seconds 5 --volume 0.30 \
  --out .audit/voice-aec/M5-005
~~~

After a successful recording:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --capture .audit/voice-aec/M5-005 \
  --out .audit/voice-aec/M5-analysis-005
~~~

This produces a *candidate* only after the held-out acoustic calibration
passes; the user's near-end voice still requires listening review.

## M5-004: direct ALSA read error and explicit PipeWire backend

The first real M5 attempts did **not** produce a valid paired recording:
`arecord -D hw:0,0` printed `pcm_read:2285: read error: Input/output error`.
M5-003 did successfully get as far as launching the speaker stream but
physical ALSA capture failed during playback. The green tests and an ERLE
of ~18.94 dB observed in the terminal were from **synthetic fixture
audio**, not a measurement of the real speaker.

The prior direct-ALSA capture worked in a different M4 session, so do
not infer permanent hardware failure. EIO alone does not identify whether
the cause is a transient USB/driver issue, a lost stream, a capture
configuration, an audio-session conflict, or another fault.

M5 capture now supports a **deliberate** choice of backend:
`--capture-backend alsa` retains the original verified `hw:0,0`;
`--capture-backend pipewire --source EXACT_ALSA_INPUT_NODE` uses
`pw-record` on a **specific** physical `alsa_input.*` node, never on
a default input, output monitor, or CueWire virtual microphone.
The program never silently falls back to another device/backend.

### Inspect actual mic sources

~~~bash
cd /data/cue-wire
python3 tools/voice_raw_capture.py inspect
pactl list short sources
~~~

Find the exact `alsa_input.*` source that corresponds to the physical
USB microphone (not any `.monitor` entry). Do not guess its name.

### Mic-only PipeWire probe, BEFORE playing music

Replace the sample placeholder with the source name you actually saw:

~~~bash
cd /data/cue-wire
pw-record \
  --target="EXACT_ALSA_INPUT_NODE" \
  --rate=48000 --channels=1 --channel-map=mono --format=s16 \
  .audit/voice-aec/pw-probe-001.wav
~~~

Allow about 3–5 seconds of recording; then press Ctrl+C so `pw-record`
closes the WAV. No system routes/defaults/settings are changed.

To check that the file contains PCM:

~~~bash
python3 -c 'import wave; w=wave.open(".audit/voice-aec/pw-probe-001.wav","rb"); print(w.getframerate(),w.getnchannels(),w.getsampwidth(),w.getnframes()); w.close()'
~~~

Do not run the 20-second acoustic experiment until the mic-only test
produces actual 48 kHz, mono PCM16 frames. If `pw-record` also fails,
keep its stderr and inspect the device/driver separately rather than
disabling PipeWire services.

### Full capture using PipeWire (after successful mic probe)

~~~bash
cd /data/cue-wire
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover -s tests -p test_voice_m5_aec.py -v

python3 tools/voice_m5_capture.py \
  --music '/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --capture-backend pipewire \
  --source 'EXACT_ALSA_INPUT_NODE' \
  --seconds 20 --calibration-seconds 5 --volume 0.30 \
  --out .audit/voice-aec/M5-004
~~~

The source name is intentionally left as a placeholder: user-specific
USB node IDs have not been observed. All actual device choices must be
verified from the local inspection output.

Playback is still launched immediately after the recorder process
(no three-second waiting). Failure before/during capture aborts the
paired experiment, stops the player and records the current
`pw-record.log` tail in the failed manifest. A successful result
records the selected capture backend/node in the manifest.

The first five seconds of the **speaker playback** must contain no local
speech; after the terminal prompt, read the test passage naturally.
M5-001/M5-002/M5-003 are failed evidence and must not be overwritten.

For a successful M5-004 capture, the subsequent analysis command is:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --capture .audit/voice-aec/M5-004 \
  --out .audit/voice-aec/M5-analysis-004
~~~

This is not a promise that PipeWire capture will work. It is a
controlled attempt to isolate whether direct-ALSA read failures can be
bypassed while keeping the microphone identity explicit.

## Legacy ALSA-only recording example (not recommended while EIO persists)

~~~bash
cd /data/cue-wire
mkdir -p .audit/voice-aec
python3 tools/voice_m5_capture.py \
  --music "/absolute/path/to/music-with-voices.wav" \
  --speaker "EXACT_PHYSICAL_SINK_NODE_NAME" \
  --mic hw:0,0 \
  --expect-card-id MICAudio97i \
  --seconds 20 \
  --calibration-seconds 5 \
  --volume 0.30 \
  --out .audit/voice-aec/M5-005
~~~

This example deliberately selects the direct ALSA backend. Run it only after independently fixing the confirmed `pcm_read` EIO condition. For the immediate follow-up use the explicit PipeWire M5-004 instructions above. The program verifies physical ALSA card identity, physical sink and the known reference file before trying the streams. It records the mic through arecord and sends the reference to the explicit speaker via pw-play; it does NOT change system defaults, routes, WirePlumber or create monitor streams.

During the first five seconds of playback: **do not speak**. When the terminal prints CALIBRATION COMPLETE — NOW READ YOUR TEXT!, start reading while the music continues:

> «Сегодня я проверяю, насколько точно микрофон отделяет мой голос от звука колонки. Сейчас я говорю обычным голосом, затем чуть тише, а потом немного громче. Колонка продолжает воспроизводить музыку, но моя речь должна оставаться разборчивой, естественной и полной.»

The playback finishes after ~20 seconds; the capture stops automatically. Results:

- .audit/voice-aec/M5-005/reference-sent.wav — exact prepared digital signal sent to the output stream
- .audit/voice-aec/M5-005/microphone-raw.wav — unprocessed real microphone capture
- .audit/voice-aec/M5-005/manifest.json — SHA256, devices and timing metadata
- local logs of ALSA, playback, and reference preparation

If interrupted, process children are terminated and prior evidence is kept. Every attempt uses a **new** output directory.

## Step 3: fit acoustic transfer, validate, then subtract

Use the dedicated voice-lab Python environment with NumPy/SciPy:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -c 'import numpy, scipy; print(numpy.__version__, scipy.__version__)'

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --capture .audit/voice-aec/M5-004 \
  --out .audit/voice-aec/M5-analysis-004
~~~

The analyzer estimates unknown mic-to-digital offset by FFT correlation, fits a regularized acoustic FIR on speaker-only training seconds, reports ERLE on a **separate held-out speaker-only interval**, then freezes the FIR for the entire double-talk interval. If correlation/validation fail, it does not create subtraction WAVs.

Outputs after accepted engineering preflight:
- M5-analysis-004/aec-candidate-float32.wav — experimental subtraction
- M5-analysis-004/speaker-echo-estimate-float32.wav — predicted speaker leakage
- M5-analysis-004/manifest.json and SHA256SUMS

The candidate is not automatically endorsed as usable voice: listen during simultaneous music and local speech for consonant loss and artifacts. Do not start DeepFilterNet/DPDF processing in front of this calibration, because that changes the physical echo waveform we are trying to identify.

## Step 4: check and listen

~~~bash
cd /data/cue-wire/.audit/voice-aec/M5-analysis-004
sha256sum -c SHA256SUMS
pw-play ../M5-004/microphone-raw.wav
pw-play aec-candidate-float32.wav
~~~

The previous accepted 03-voice-leveled.wav stays untouched. Look at calibration-correlation, estimated lag, training and held-out ERLE, and actual voice quality. A failed/weak result is valid scientific evidence; do not disable protective thresholds to force a pass.

## Synthetic tests

~~~bash
cd /data/cue-wire
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover -s tests -p 'test_voice_m5_aec.py' -v
~~~

These are **synthetic** tests only. Real audio synchronization, sink identity, device levels, system mixing, double-talk quality and hardware acceptance require the user's physical experiment.

## Future real-system output reference

The user specifically distinguishes the signal sent to the speaker from acoustic sound emitted by its drivers. In a later iteration the best available reference may be an explicit **post-mix PipeWire sink monitor**, including actual game/cue streams, not just a single test file. Existing voice_aec_inspect.py can discover monitor nodes read-only. The existence of a monitor does not prove clock synchronization or capture at the exact DAC boundary.

Do not alter default source/sink, monitor the mic into headphones, or install a persistent echo-cancellation service during this pilot.
