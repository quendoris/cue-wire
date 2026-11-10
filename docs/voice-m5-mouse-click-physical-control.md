# M5 — controlled physical mouse/keyboard source identification (2026-11-10)

User recognized earlier short 12–14kHz anomalies as likely
mouse-button clicks during starting/stopping physical
recordings. Instead of inferring cause from spectral
fingerprints alone, we preregistered a simple 43s
five-phase physical microphone recording using
\`tools/voice_m5_mouse_click_control.py\`:
silence; mouse movement with NO clicks;
repeated deliberate left/right mouse clicks;
keyboard keystrokes; silence.

The user supplied the **actual raw physical WAV**
(48kHz, mono PCM16, 42.944s), the numerical report
and the export's \`SHA256SUMS\`. We independently
rehash BOTH uploaded WAV and JSON and match
their checksum manifest:
- \`microphone-mouse-control-raw.wav\`:
  \`7f2d2cf3f5fc072c03560b6eecc0fc48b9680b243acf1b45c4c0ca33648490d2\`;
- \`mouse-control.json\`:
  \`203be8c95de42f1410911a51bc97c505b8adee2ff97c0935180332b072778775\`;
- \`pw-record.log\`: SHA256 of empty file
  \`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\`;
  this log was not separately uploaded but digest
  was recorded in the supplied SHA256SUMS.

The WAV's actual metadata and 2,061,312 samples
match the report, PCM amplitude peak about 0.1826.
We recomputed independent Welch PSDs on several
highest-energy half-second regions using
4096-sample windows at 48kHz, directly from WAV.
The actual peak frequencies matched the
report's rounded entries.

## Phase comparison (existing Welch 0.5-second estimator)

\`\`\`text
Input                       Median 8-16kHz    Max relative    Half-second
control                     power (dBFS)      to quiet (dB)  > +6dB events
-----------------------------------------------------------------------
Silence before                 -80.83           -0.32           0/11
Mouse movement (no clicks)     -80.68           +0.62           0/11
Deliberate mouse clicks        -65.37          +21.96           9/11
Keyboard clicks                -61.34          +31.34          10/11
Silence after                  -79.95           +0.60           0/11
\`\`\`

The median fraction of total 8–16kHz spectral
power in the 12–14kHz band is:
- quiet before: 19.1%;
- movement without clicks: 19.2%;
- **mouse clicks: 74.5%**;
- **keyboard typing: 24.3%**;
- quiet after: 17.6%.

Under deliberate mouse clicks, the top 8–16kHz
Welch PSD bin is usually **13089.8438Hz**,
occasionally 13101.5625 or 13312.5Hz.
The mouse-click band is spectrally colored and
appears in distinct short bursts rather than
as a continuously present 13kHz tone.
For highest-power mouse frame (20.5–21s),
top Welch bins lie near 13.09–13.18kHz.
For strongest keyboard frame (32.0–32.5s)
top Welch bins lie near **8.38kHz**;
many keyboard windows peak about 8.3–8.5kHz.
The keyboard can be overall *stronger*
8–16kHz energy than mouse clicks while
having a clearly different band distribution.
Thus "highband burst" alone is not a
reliable mouse classifier; spectral features
are needed, and the full waveform should
be preserved for later review.

**Comparison to prior 2026-11-09 unsupervised
physical M5 speaker-OFF anomalies:** six earlier
anomalous windows had median highband power
-62.12619dBFS vs -79.74776dBFS in 34 ordinary
windows, averaged **79.15%** of their 8–16kHz
power in 12–14kHz and spectral maxima at
~12.2–13.02kHz (many at ~13.0kHz).
The controlled mouse click signature
(~13.09kHz / median 74.5% in 12–14kHz)
is **highly consistent** with that earlier
12–14kHz pattern, whereas keyboard controls
are concentrated around ~8.4kHz.
The center frequencies aren't exactly the
same (~80Hz difference for 13.0 vs 13.09kHz),
so this is *strong physical-source evidence*,
not byte-for-byte identity of every earlier
event. No instrumented button-press timestamp
was recorded; mouse clicks can travel through
air or through desk mechanical vibration.
The current data cannot isolate that path or
prove no other sources of similar transients.

**Practical conclusion:** There is no evidence
from these transients alone of a defective USB
microphone, sporadic AGC, PipeWire dropout or
persistent 13kHz electronics oscillation.
The simplest now experimentally supported
explanation is user-origin **mouse clicks**
and separately **keyboard clicks**.
Old source-WAV data and older numerical
experiments remain unchanged.

## Engineering direction

This is **not an AEC task**: the mechanical
click signals have no digital far-end
speaker reference. Keep the existing
reference-conditioned dual Speex AEC
separate from any transient/noise
suppression stage.

Prefer evaluate **existing algorithms** first
(RNNoise, DeepFilterNet, temporal impulse
repair/declick approaches) on these identified
real-world controls, using untouched PCM:
- preserve high consonant speech plosives
  and sibilants, not just reduce 12–14kHz;
- distinguish mouse-click spectral profile
  from keyboard and natural voice dynamics;
- compare before/after with a known clean
  voice stem and random independent clicks,
  including false removals and audible
  artifacts, not only mean band power;
- measure real execution time, causal
  delay and CPU independently before
  any live integration.

No automatic recording, plugins, PipeWire
module, virtual mic, gain changes, playback
or external networking is proposed.
Private WAV remains local in user-owned
\`.audit\`; this committed document contains
**only numeric findings and hashes**, never
private voice or music waveforms.
