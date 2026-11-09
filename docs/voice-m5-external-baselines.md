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
