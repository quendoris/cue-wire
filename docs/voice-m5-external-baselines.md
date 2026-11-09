# CueWire M5 — audited 13 kHz bursts and fair external DSP/AEC baselines

Date: 2026-11-09.
Status: exploratory architecture research; no third-party engine has yet
been executed on the private M5 physical WAVs. **NO benchmark winner
or production approval is claimed.** The previously preregistered
new-take guarded 0.67881dB wholeband AEC result remains FAIL.

## Fresh physical M5 off-spectral-fingerprint result

The user provided the actual \`off-spectral-fingerprint.json\` and
\`SHA256SUMS\`, independently SHA256-verified:
\`81f4ed5b9e2e6e7df713cd74c4bd85cff174169dd962fa0346f6415f07260f54\`.

All 40 prespecified microphone-only 0.5s OFF windows were evaluated,
using unchanged private originals from the 0.15 and 0.30 volume
experiments. Six exceeded their own 5s OFF-phase median
by >6dB at 8–16kHz; the other 34 did not.

| Run/phase | 0.5s window in physical microphone WAV | 8–16kHz Welch band power | Dominant-bin Hz | Fraction of 8–16kHz energy in 12–14kHz |
| --- | --- | ---: | ---: | ---: |
| 0.15 OFF before | 5.5–6.0 | -62.82925dBFS | 13007.8125 | 0.778253 |
| 0.15 OFF before | 6.0–6.5 | -60.04714dBFS | 13007.8125 | 0.824496 |
| 0.30 OFF after | 30.0–30.5 | -64.33657dBFS | 12796.875 | 0.661278 |
| 0.30 OFF after | 30.5–31.0 | -59.13965dBFS | 12222.6562 | 0.849403 |
| 0.30 OFF after | 31.0–31.5 | -61.92968dBFS | 13007.8125 | 0.825205 |
| 0.30 OFF after | 34.0–34.5 | -62.32270dBFS | 13019.5312 | 0.810645 |

Across all **six** flagged windows:
- Median 8–16kHz band power is **-62.12619dBFS**;
  for the other 34 windows the median is
  **-79.74776dBFS** (17.62157dB difference
  between selected-group medians; descriptive, not
  a statistically independent confidence estimate).
- Mean share of 8–16kHz power in 12–14kHz is
  **79.15%** for flagged windows vs **17.16%**
  for unflagged windows.
- Median spectral flatness is **0.21316** for
  flagged vs **0.74757** for unflagged windows;
  entropy medians **0.83834** vs **0.94124**.
- Yet the top 1% of frequency bins contains
  only ~**10.0%** of spectral power in both
  flagged and unflagged groups. These are
  **12–14kHz-concentrated, spectrally structured
  bursts, NOT demonstrated pure 13kHz sine tones**.
  A static needle-narrow notch at 13kHz alone
  is **not justified** by the observed spectrum.
- At least four spike maxima fall near 13.01kHz;
  one is at 12.22kHz and another at 12.80kHz.
  No observed peak is by itself proof of device
  electronics, USB glitches, driver resampling,
  microphone DSP, room sound, or other mechanism.

Because the affected recordings have no near-end
voice, these samples cannot certify voice
preservation after a new denoiser.

## External implementations: what the source actually does

**WebRTC AEC3 / WebRTC AudioProcessing (APM).**
A reference-aware acoustic echo canceller,
with linear subtraction, residual echo estimation
and frequency-domain suppression, plus optional
noise suppression and AGC at the APM level.
The relevant code is not a direct drop-in offline
WAV filter: correct render/capture chronology,
clocking, process frames and AEC internal state
must be controlled before comparative metrics.
Official code:
https://webrtc.googlesource.com/src/+/3bdce3dc1798e8733450d5f56070f8b830571278/modules/audio_processing/aec3/echo_remover.cc
Packaged for Arch as \`webrtc-audio-processing\`
(current package listing 2.1-9):
https://archlinux.org/packages/extra/x86_64/webrtc-audio-processing/
A standalone third-party extraction also exists, but
its documented WAV test operates on 16kHz mono,
which would require resampling our 48kHz material:
https://github.com/Enaium/webrtc-aec3

**SpeexDSP** classical adaptive acoustic echo canceller,
operates on synchronized rec/play frames. Exposes
\`speex_echo_cancellation\`, state frame size,
filter tail and optional separate residual echo
suppression via Speex preprocessor. Documented
recommended frame size ~10–20ms and tail
roughly 100–500ms. Do not combine AEC+postprocessor
and compare to CueWire linear FIR as though both
were the same class of model.
https://github.com/xiph/speexdsp/blob/master/include/speex/speex_echo.h
https://github.com/xiph/speexdsp/blob/master/include/speex/speex_preprocess.h
https://www.speex.org/docs/manual/speex-manual/node7.html

**RNNoise**, reference-FREE recurrent speech
denoiser. Official command-line example expects
raw mono signed PCM16 48kHz, NOT WAV:
https://github.com/xiph/rnnoise
Appropriate as a speech/noise stage; not
an independent acoustic echo canceller
with known speaker reference.

**DeepFilterNet2/3**, reference-FREE, full-band
48kHz neural speech enhancement. The user's
existing M4 voice processing chain already tested
DeepFilterNet2; retain its recorded setting
as a candidate separate denoising baseline.
Official project:
https://github.com/Rikorose/DeepFilterNet

**PipeWire echo-cancel module**, built around
WebRTC, supplies additional virtual source/sink
nodes. **Do not load this module in the user's
active PipeWire session** for these initial
comparisons: the user's invariants forbid modifying
system defaults/routes or leaving daemons.
Use an isolated library/offline harness instead.
https://pipewire.pages.freedesktop.org/pipewire/page_module_echo_cancel.html

## Strict two-track benchmark: DO NOT MIX task labels

Track A: **acoustic echo cancellation with speaker reference**.
Candidates: unprocessed raw mic, original CueWire
frozen 120ms/.02 FIR, CueWire guarded 3800Hz
echo-estimate-only FIR, SpeexDSP AEC only,
WebRTC AEC3 only, plus OPTIONAL separately labeled
full AEC+residual suppressor configurations.

Samples: identical SHA256-pinned 48kHz physical
microphone + digital speaker-reference pairs from
M5-006 and the M5 ABA/control takes. Record actual
render/capture offset per take using training-only
data and reuse that offset for all candidate engines.
Do not leak heldout future samples into adaptive
state (no looking ahead). Explicitly mark/measure
per-engine convergence periods and real startup.
Freeze settings before scoring new independent runs.

Metrics: whole- and per-band ERLE on speaker-only
intervals; residual absolute PSD band power
compared against mic OFF phase distributions;
reference-linked linear coherence proxy before/after;
compute cost (RTF or CPU time) and algorithmic
latency, memory and reset state. A high ERLE can
occur because raw echo was loud; do not rank
engines solely by ERLE. On real doubletalk,
speech-preservation must remain UNKNOWN until
an independent clean near-end reference or
carefully validated protocol is available.

Track B: **speaker-OFF 12–14kHz bursts / general
microphone noise suppression**. Candidates:
unprocessed raw, current previously used
DeepFilterNet2, RNNoise and an explicit conservative
band-selective, activity-gated DSP baseline.
Do NOT evaluate WebRTC AEC3 or SpeexDSP AEC
as if they were reference-free impulse denoisers.
The 13kHz region contains a broadened bump
and shifting peak frequencies, not a certified
constant-frequency sine tone. Do NOT assume
a fixed 13kHz notch will work; use it only as a
carefully labeled simplistic control.

Metrics: level of existing six flagged OFF windows;
false attenuation of the other 34 OFF windows;
change in 500–4000Hz and 4–8kHz mic bands;
presence of musical/tonal artifacts; CPU/RTF,
causality and algorithmic latency.
Optional synthetic known clean M4 voice injection
provides exact algebraic fidelity checks but
**CANNOT** prove real acoustic doubletalk
speech preservation, firmware AGC or subjective
voice quality.

## Environment and safety constraints

- Arch Linux/Hyprland, terminal-only, manually
  launched child processes, Super+C emergency
  termination of all owned processes; no GUI,
  autostart, system default changes, or persistent
  audio daemons.
- Physical microphone and speaker remain untouched.
  All original WAVs under private \`.audit\` are
  SHA256-checked, never rewritten; any generated
  processed outputs stay in fresh private dirs.
  Export numeric reports and hashes only.
- No \`pactl set-\`, \`wpctl set-\`, module loading,
  changing PipeWire routes or using live mic
  loopback simply to get a baseline.
- **Do not assert comparative wins until actual
  external implementations are installed or
  built in an isolated environment and exercised
  on identical physical files.**
- Initial read-only local inventory (NO installs):
  \`pacman -Q speexdsp webrtc-audio-processing webrtc-audio-processing-1 2>&1\`
  and
  \`ldconfig -p | grep -E 'speexdsp|webrtc_audio_processing' || true\`.
  Absence of any package is normal and should
  not cause a system modification.
- Capturing same real near-end voice and far-end
  echo in matched doubletalk conditions is a
  separate later prospective trial; old FAIL
  remains \`0.67881dB\`.

## Offline Native SpeexDSP AEC-only baseline: code ready for first physical run

The user's read-only Arch package inventory confirmed:
- \`speexdsp 1.2.1-2\` installed; \`ldconfig\` found
  \`/usr/lib/libspeexdsp.so.1\`;
- legacy \`webrtc-audio-processing-1 1.3-5\` installed;
- current \`webrtc-audio-processing\` not installed,
  and no WebRTC shared library was visible under
  the earlier \`ldconfig\` grep. No missing packages
  were installed.

Added \`tools/voice_m5_speex_baseline.py\` using
Python ctypes to call **the locally installed native
libspeexdsp**:
\`speex_echo_state_init\`,
\`speex_echo_ctl\`,
\`speex_echo_cancellation\` and
\`speex_echo_state_destroy\`, following the official
\`include/speex/speex_echo.h\` API. No compilation,
download, audio device or subprocess is necessary.
The native functions run in the manually launched,
foreground Python process and are not persisted.

**Locked first-pass configuration before actual Speex results:**

| Parameter | Value | Why |
| --- | --- | --- |
| Sampling | 48000Hz mono PCM16 | Physical capture/digital source native format |
| Speex frame | 480 samples (10ms) | Official native library API recommends 10–20ms |
| Speex filter tail | 5760 samples (120ms) | Match CueWire linear FIR nominal memory |
| 0.15 fixed capture lag | 386952 samples / 8061.5ms | Previously published 1500–4000Hz reference coherence, NO retiming on Speex output |
| 0.30 fixed capture lag | 387967 samples / 8082.64583ms | Same |
| Render pre-peak shift | -480 samples (-10ms) | Exactly CueWire frozen-FIR pre-peak convention |
| Digital speaker PCM source | Original identical SHA256-pinned 20s M5-006 reference | No sink-monitor or hardware playback |
| Per-stream playback gain | 0.15 / 0.30 respectively | Matches original \`pw-play --volume\`; sink/room gain NOT fabricated |
| Processing | Continuous sequential frame-by-frame including OFF | Standard Speex native AEC, adapting through evaluation |
| Separate Speex preprocessor | **DISABLED** | Isolates AEC linear baseline from NS/AGC/residual postprocessing |
| Eval | 10–16s of music-reference time | Exactly CueWire M5 heldout reference interval |
| OFF negative controls | Original mic 2–7s, 30–35s | Catch undesired background changes |
| Outputs | \`speex-aec-only.json\`, \`SHA256SUMS\` only | No residual audio exported |

The native ABI uses \`SPEEX_ECHO_SET_SAMPLING_RATE=24\`,
\`GET_SAMPLING_RATE=25\` and
\`GET_FRAME_SIZE=3\`; library responses are
checked before streaming. Input is verified
exact PCM16 recovered from the audited WAVs.
The final partial 480-sample block is zero-padded
and output is cut back to the original sample
count. The process records measured CPU-wall
duration/RTF but **does not** measure
device-to-device or perceptual latency.
No processed audio is stored or played.

**Fairness caveat:** CueWire fixed FIR learns only on
reference 3–9s and is frozen; Speex adapts continuously
including 10–16s evaluation. Hence this is an
**external sequential-adaptive engineering baseline**,
not a matched frozen model or a rigorously blind
prospective A/B comparison. The test reuses
previously examined M5 recordings.
All metrics and mode labels remain explicit.
Do NOT call a better score a formal production PASS
and do not rescue the historical 0.67881dB failure.

Three new regression tests cover (i) immutable
reference alignment and exact PCM16 conversion,
(ii) 480-frame/native ABI order including partial
tail with a controlled fake library, and
(iii) two SHA-pinned synthetic physical A/B/A
captures with numerical-only output, no WAV,
no overwritten source and tamper refusal.
**44 M5 regression methods** now in the file;
these were not yet run on the user's Arch.
The fake-library tests do NOT establish that the
native Speex algorithm performs well.

### First physical Speex benchmark — opt-in, read-only inputs

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

Only when all tests pass:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_speex_baseline.py \
  --low-capture .audit/voice-aec/M5-level-015-001 \
  --high-capture .audit/voice-aec/M5-level-030-001 \
  --out .audit/voice-aec/M5-speex-aec-only-001

cd .audit/voice-aec/M5-speex-aec-only-001
sha256sum -c SHA256SUMS
~~~

If source names differ, point to actual existing files
whose SHA256 hashes match the pinned evidence.
If output dir exists, use fresh \`-002\` etc: never
overwrite anything in private \`.audit\`.

Send **only** \`speex-aec-only.json\` and \`SHA256SUMS\`.
The pre-existing CueWire
\`M5-echo-floor-001/echo-floor.json\` is the
frozen baseline for numerical comparison.
No new microphone capture, public upload of WAV
or any PipeWire sound setting change is needed.

If an invocation aborts because the local native
library or a source hash does not match,
**do not install/fix packages in haste**: send
the exact error, then review the adapter.
The latest native AEC run has not yet happened.

## Actual first native SpeexDSP result and mandatory zero-farend control (2026-11-09)

The user supplied actual \`M5-speex-aec-only-001/speex-aec-only.json\`
and \`SHA256SUMS\`. The complete report independently
SHA256-checks as
\`75ca166a0439bf5e5345a567a569549aaf81ef1f95affdc9aedcb7e929adfc01\`,
matching their checksum. The report's six input
SHA256 identifiers are identical to the existing
0.15 and 0.30 physical WAV/manifest and M5-006
music source digests. We rehashed the report, not
unuploaded private physical WAV bytes.
The native library was **libspeexdsp.so.1**,
using real 10ms frames at 48kHz and 120ms tail.
The Speex preprocessor/noise suppressor and AGC
were not enabled, but the native AEC has internal
signal processing as noted below.

### Same 10–16s music-only holdout, source 500–4000Hz

| Metric | 0.15: CueWire guarded FIR | 0.15: Speex native AEC | 0.30: CueWire guarded FIR | 0.30: Speex native AEC |
| --- | ---: | ---: | ---: | ---: |
| Wholeband speaker-only ERLE | +0.23316dB | **+1.79157dB** | +0.88581dB | **+3.79385dB** |
| 500–4000Hz linked proxy reduction | +4.79193dB | **+5.88278dB** | +6.55686dB | **+11.10253dB** |
| Linked proxy residual absolute level | -73.830dBFS | **-74.92077dBFS** | -69.612dBFS | **-74.15756dBFS** |
| Source-linked raw mic proxy | -69.03799dBFS | same | -63.05503dBFS | same |

Per-band Speex native speaker-only ERLE in 0.15/0.30:
70–180Hz **+2.96565/+2.10144dB**,
180–500 **+0.21362/+0.89443dB**,
500–1500 **+0.50251/+2.50042dB**,
1500–4000 **+1.25096/+4.89939dB**,
4000–8000 **+0.00936/+0.20243dB**,
8000–16000 **-0.02262/+0.00187dB**.

Native performance in the report:
- Volume 0.15: 3606 frames, **0.303661s**
  processing 36.053333s audio, **RTF=0.008423**;
- Volume 0.30: 3608 frames, **0.514640s**
  processing 36.074667s audio, **RTF=0.014266**.
This is processing time ONLY, not end-to-end
acoustic latency, deterministic worst-case load,
or a certified real-time scheduling guarantee.

**CRITICAL OFF control:** Speex changed microphone
signal even when the loudspeaker was silent and
the supplied far-end reference was all zeros
for those periods. Whole-spectrum raw-to-Speex
RMS reduction during original mic OFF segments:
- 0.15, before/after: **1.4370/1.89397dB**;
- 0.30, before/after: **3.52640/1.55913dB**.
Most prominent band is **70–180Hz**, reduced by
2.54–2.73dB across runs, while 500–4000Hz
OFF energy changes are only hundredths of a dB.
Thus the high wholeband ERLE cannot be interpreted
as pure echo cancellation without qualification.

Looking at the official SpeexDSP source
\`libspeexdsp/mdf.c\`, within
\`speex_echo_cancellation\` lines 671–697,
the MIC passes through \`filter_dc_notch16\`
and pre-emphasis even before the adaptive MDF
echo processing:
https://raw.githubusercontent.com/xiph/speexdsp/master/libspeexdsp/mdf.c
The rate-dependent \`notch_radius\` is .992
at 48kHz (see same file's sample-rate ctl
lines 1150–1168). This is source-code
evidence of input front-end conditioning,
but its exact contribution to measured
70–180Hz attenuation needs a controlled
experiment. The local distro binary was
not disassembled or independently mapped
to the exact upstream commit.

**Mandatory next falsifiable control**:
\`tools/voice_m5_speex_zero_reference.py\`.
Run actual native Speex on precisely the same
two physical mic WAVs, but feed PCM16 **zero**
throughout as far-end. Compare the numerical
speaker-ON/OFF outputs with the exact
SHA256-pinned first real native Speex report.
This will separate how much of the apparent
improvement exists independently of knowing
the speaker signal from the additional change
caused by supplying it. The normal vs zero
difference still isn't ground-truth isolated
physical echo SPL, due to nonlinear/adaptive
states, different background, and absence of
clean near-end speech. No production pass
or voice-preservation claim follows.

The new control produces only
\`speex-zero-reference.json\` and
\`SHA256SUMS\`. It does NOT save any residual
waveform, play music, use live audio devices,
change PipeWire state, or modify inputs.
The original first real Speex report digest
is an immutable constant checked along with
all six original source SHA256s.
It should be a fresh sibling of the existing
\`M5-speex-aec-only-001\` report.

Two new synthetic tests verify that even
a fake native implementation sees ONLY
zero far-end samples in every 10ms frame,
and that source integrity, exact report
digests, refusing overwrite, and numeric-only
output hold end-to-end. **46 M5 test methods**
now require local execution.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

Only if 46 tests are green:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_speex_zero_reference.py \
  --low-capture .audit/voice-aec/M5-level-015-001 \
  --high-capture .audit/voice-aec/M5-level-030-001 \
  --original-speex-report .audit/voice-aec/M5-speex-aec-only-001/speex-aec-only.json \
  --out .audit/voice-aec/M5-speex-zero-reference-001

cd /data/cue-wire/.audit/voice-aec/M5-speex-zero-reference-001
sha256sum -c SHA256SUMS
~~~

Send only \`speex-zero-reference.json\`
and \`SHA256SUMS\`. If the output folder
already exists, use another fresh suffix;
never overwrite old evidence. No recordings
or system/audio-setting modifications needed.

**This original M5 prospective speaker-only
guarded fullband ERLE 0.67881dB is still FAIL.**
No real-doubletalk quality, voice preservation,
or end-to-end streaming latency has yet been proven.

## Next engineering actions

Implement two **offline adapters**, pinned to
specific library versions and SHA256s:
SpeexDSP AEC with explicit rec/play alignment
and separate AEC-only + preprocessor modes;
WebRTC AEC3 native AudioProcessing with
no default-node PipeWire integration and
separately controlled NS/AGC states.
A reusable evaluator will compare frame-by-frame
output, wave integrity, event-level logs, and
absolute metrics. Parallel reference-free
RNNoise/DeepFilterNet2 noise experiments should
use a *different benchmark track*.
