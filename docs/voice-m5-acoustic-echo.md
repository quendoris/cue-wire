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
