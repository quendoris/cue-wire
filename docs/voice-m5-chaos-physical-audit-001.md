# M5 Chaos-001 — actual user-supplied full physical WAV audit (2026-11-10)

**This is an audit of the actual first captured chaotic audio**, not
a proposed experiment or synthetic substitute. The user uploaded
\`microphone-chaos-raw.wav\`, \`manifest.json\` (upload alias
\`manifest(7).json\`), 2x \`pw-play\` logs, \`pw-record\` log,
and \`SHA256SUMS\`. Independent SHA256 over the actual uploaded
bytes matches every supplied digest:

\`\`\`
63322a193843100e87124fb47fd1445604fbc6118accbc1fe489e772326980fb  microphone-chaos-raw.wav
20d52a781d33c6df05d9bc551afb8fea5899dbd070a6690e5bfaabb3fd6b7c66  manifest.json
c9da22f434ff327e9b8d8834d7ba1fe51faa466104d9942ba3dc6bbb76ea849a  pw-record.log
3f35b502386f44a59a3ba36f5229b91de5a94af31d3fbfbd79f3b7f59fffcc6e  pw-play-001.log
3f35b502386f44a59a3ba36f5229b91de5a94af31d3fbfbd79f3b7f59fffcc6e  pw-play-002.log
\`\`\`

The actual file was decoded as 48kHz, mono, signed PCM16,
4,318,208 frames, **89.96267 seconds**, amplitude peak
18,806 / 32,768 = 0.5739136,
maximum **-4.82307dBFS**, **zero clipped samples**.
The user's Ctrl+C finalized the foreground capture.
The console reports \`captured_ctrl_c_at_free_stress\`
and \`failure: null\`.

**Source music provenance:** selected local stereo PCM16
48kHz \`/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav\`,
duration 292.931333s, stored digital SHA256
\`495d4fb1845a369129df4a659f62f18c087c7de1bff4f895dcf60a1c38351e6a\`
(as recorded in the original capture manifest).
This music WAV was **NOT uploaded**; we
cannot independently rehash or analyze the exact
digital far-end reference in this review.
\`pw-play\` uses an explicitly selected physical sink
and stream gain **0.60**, versus 0.30 previously.
The physical sink gain/default route was not
modified by our software, per manifest.
The first player starts ~11.008s and stops
~35.012s, second starts ~44.018s and stops
~90.016s. Both players are explicitly terminated
with exit code 1 as recorded; the logs contain
only argv, no detailed errors. The recording is
intact, but this exit code should not be
reinterpreted as a naturally completed successful
playback. The file was long enough that these
two segments were not expected to finish naturally.

### Recalculated by decoding the WAV, NOT copying only the manifest

All stage RMS values below were recalculated
directly using decoded PCM and independently
agree with the user's manifest (trim 0.75s
from stage boundaries).

| Stage | Approx stage start-end (s) | RMS dBFS | Peak dBFS |
|---|---:|---:|---:|
| Quiet before | 3.00–11.00 | -60.266 | -38.268 |
| Mouse click + music | 11.01–19.01 | -53.504 | -20.857 |
| Keyboard + music | 19.01–27.01 | -48.821 | -17.881 |
| Mouse+keyboard+music | 27.01–35.01 | -46.704 | -19.603 |
| Clean live near-end voice; music OFF | 36.01–44.01 | -40.469 | -21.100 |
| Full stress: song, human speech, clicks, possible laughter | 44.02–90.01 | -32.344 | -4.823 |

The highest waveform peak occurs at
**~86.6872s in the open-ended full-stress phase**.
This is not evidence of clipping (absolute
amplitude is <1.0). "Clean voice" describes
the **absence of our played music**, NOT an
anechoic or noise-free microphone voice waveform.
The actual free-stress physical mic includes
real simultaneous stimuli; it differs from prior
digital M4-stem injection studies.

Independent spectral windows (48kHz, half-second,
Welch 4096) show median **8–16kHz** energy:
- quiet before: approx **-80.1dBFS**;
- mouse+music: **-60.3dBFS**;
- keyboard+music: **-59.9dBFS**;
- both+music: **-63.1dBFS**;
- clean live voice: **-71.6dBFS**;
- free stress: **-59.2dBFS**.

Note these spectral distributions are **mixtures
during speaker-ON stages**; 12–14kHz and 13kHz
power alone cannot isolate mouse clicks once
song/music is also playing. The earlier dedicated
mouse-only physical control proved much more
specific click signatures. Avoid extrapolating
mouse-band source identification to the full
chaotic music+typing recording without comparison
to its actual digital music input and careful
source separation.

### Research implications and next source prerequisites

This WAV is a useful realistic adverse dataset:
isolated physical music+click phases, an
8-second live voice-only stage, and a long
simultaneous music/vocal/near-end voice,
keyboard/mouse, natural laughter attempt.
It is not itself a clean speech ground truth
for doubletalk. No ground-truth duplicate of
the **same live speech** is available when
music and clicks occur simultaneously, so
PESQ/true voice-distortion SNR is not measurable
without another controlled protocol.

**NEXT:** run a purely offline reference-
integrity/alignment probe on the user's Arch
against the exact local stereo song WAV identified
and hashed in the manifest. Treat \`pw-play\`
restarts and the 1s music-off gap as separate
far-end reference segments; estimate lag
and correlation independently for the first
and second playback windows. A Wall clock
\`pw-play\` process launch timestamp is not
sample-synchronous with the actual audio
playback, and the 0.60 stream volume does
not guarantee a physical acoustic amplitude
ratio of exactly 2 due to hardware, sink
gain and mic firmware.

Only after verified local far-end alignment
and stage controls do offline comparisons
of already available **SpeexDSP**, **WebRTC
AEC3**, and existing CueWire guarded
differential, plus separate click/noise
suppression baselines, become scientifically
interpretable. Library/provider algorithms
should be studied before more custom model
architecture is proposed.
Do not run live virtual mic or change default
audio routing to perform offline comparison.

Raw music+voice audio should remain private
and must NOT be committed to the repo.
This committed audit contains only
measurements, SHA256 provenance and
interpretation limits.


## Read-only local music reference alignment diagnostic

\`tools/voice_m5_chaos_reference_probe.py\` was subsequently added.
It pins all THREE independently verified real evidence hashes:
the full uploaded M5 chaos capture manifest, microphone WAV,
and the exact privately held local stereo music WAV. It uses
the historical CueWire \`band_lag\` 1500–4000Hz normalized
cross-correlation on song seconds 1.0–3.7 for EACH of the
two \`pw-play\` process launches individually.
It tries L, R and arithmetic-mean stereo reference.
Correlations below 0.06 are INCONCLUSIVE, and even
higher correlations require further checks because
music loops/repeating riffs can create false peaks.
This is an alignment diagnostic, **not echo cancellation**.

Two additional synthetic regression tests, including
two different exact known acoustic delays and source
SHA256 protection, bring the M5 test suite to
**57 methods**. These new tests have not been executed
on the user's Arch in this turn.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

Only after green tests, with the **original**
\`/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav\`
unchanged and still available locally:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_chaos_reference_probe.py \
  --capture .audit/voice-aec/M5-chaos-001 \
  --out .audit/voice-aec/M5-chaos-alignment-001

cd /data/cue-wire/.audit/voice-aec/M5-chaos-alignment-001
sha256sum -c SHA256SUMS
~~~

Send \`music-alignment.json\` and \`SHA256SUMS\`
(or error text if an input digest is missing).
No actual music, microphone or voice WAV
needs to be uploaded, and no sound will play.
All old source files are read-only.


## Uploaded onset-lag probe: actual two-restart results (2026-11-10)

User uploaded \`music-alignment.json\` and \`SHA256SUMS\`.
Independently rehashed JSON bytes:
\`ea1855721441b4a859a4409af707f0ff554953184b5bd634519a9349cc67b243\`;
the user's SHA256SUMS matches exactly.
The report also confirms locally checked SHA256 for the
physical 89.96267s microphone WAV, original manifest and
locally held stereo AC/DC song source. The source WAV itself
was not uploaded for this later diagnostic.

Same 1.5–4kHz filtered normalized cross-correlation,
original song source seconds 1.0–3.7, launch-relative
search -0.3 to +0.9 seconds:

| Restart | Process starts at mic wall s | Best reference | Peak correlation | Lag reference-origin sample from mic start | Relative to process launch |
|---|---:|---|---:|---:|---:|
| First, speaker-only clicking + music | 11.00758 | arithmetic L/R mean | 0.449889 | 541898 (11.2895417s) | +281.96167ms |
| Second, near-end voice + music + clicks | 44.01799 | L | 0.139972 | 2117222 (44.1087917s) | +90.80167ms |

The difference in process-launch-relative lags is
**191.16ms**. This is NOT evidence of a changed
sound propagation distance or physical hardware
latency, because \`pw-play\` startup and PipeWire
buffering are part of the measurement.
These are **candidate offsets to acoustic
recorded waveforms**, not production AEC
sample-clock synchronization.

The second peak is about 3.2× weaker in
magnitude than the first. This may be expected
because the second stage contains live
near-end speech and clicks, but an ambiguous
musical correlation match remains possible.
The "best" L/R selection differs by only
~0.0015 absolute correlation in the second
recording. Treat this as **inconclusive
channel preference**; do not infer which
physical loudspeaker is better represented
from those small differences alone.
Both candidates exceed the preexisting
0.06 minimum of the first-stage probe,
which is not an independent reliability
test or physical hardware gate.

A next offline-only step must **validate**
each previously found lag in later, disjoint
song windows, rather than choosing a
different globally optimal offset after
inspection. The proposed validation computes
both (a) signed normalized reference-mic
correlation at the original pinned lag
and (b) local ±120ms search for possible
drift/ambiguity; it NEVER silently replaces
the original timing observations.

Implemented in \`tools/voice_m5_chaos_reference_validate.py\`,
which SHA256-pins the uploaded prior alignment report,
actual mic WAV, original manifest and on-disk
stereo music source. One new synthetic test with
a known 330ms lag, three held-out windows and a
deliberately wrong 47ms prior was added;
**58 test methods** now exist, not yet
independently executed on the user's Arch.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

If green, and local original song and
Chaos-001 WAV are still in the same exact
locations with unchanged SHA256:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_chaos_reference_validate.py \
  --capture .audit/voice-aec/M5-chaos-001 \
  --prior-alignment .audit/voice-aec/M5-chaos-alignment-001/music-alignment.json \
  --out .audit/voice-aec/M5-chaos-alignment-consistency-001

cd .audit/voice-aec/M5-chaos-alignment-consistency-001
sha256sum -c SHA256SUMS
~~~

Send \`alignment-consistency.json\` and its
\`SHA256SUMS\`. Do not upload private voice/music
WAVs or alter existing capture/manifest files.
No audio playback or PipeWire changes occur.


## Actual uploaded multiwindow lag consistency (2026-11-10)

The user supplied the original numeric-only
\`alignment-consistency.json\` and its \`SHA256SUMS\`;
SHA256 of uploaded file verified independently:
\`c3d89762d5fc3147876ab45af1546f117ab342960396cd41df3be5d98d13f3e7\`.
The report confirms original physical mic/manifest,
stereo song, and prior alignment report hashes.
Previous offsets (541898 and 2117222 samples)
were **NOT refitted** to the later music windows.

Previously selected initial channel was
L/R average for playback #1 and left channel
for #2. Absolute normalized correlation at
these fixed lags on distinct 2.7s song windows:

| Digital song time (s) | First L/R mean | Second left |
| --- | ---: | ---: |
| 1–3.7 (initial fit, not later holdout) | .449889 | .139972 |
| 5–7.7 | .269492 | .092906 |
| 10–12.7 | .322385 | .104390 |
| 18–20.7 | .087669 | .142579 |
| 27–29.7 | — | .023940 |
| 38–40.7 | — | .011873 |

In separately fitted local +/-120ms descriptive
search, the first L/R mean reference's
candidate shifts are approximately
0, -0.250, -0.042 and -0.104ms.
Second, initial left channel:
0, -0.229, -0.063, -0.104, -0.146 and
-0.229ms.
But these are NOT independently measured
true sampling-clock drift! Several local
maxima change SIGN compared with the
original lag due to the use of *absolute*
correlation; narrowband/periodic music
makes nearby positive and negative peaks
compete on ~0.2–0.3ms scales.
The second start has substantially more
real physical near-end speech and clicking.

Crucial **false drift caution:** at second
song time 38–40.7s the L/R-average (and R)
local search picks -96.27083ms with
absolute normalized correlation
ONLY 0.0350. This is under the original
0.06 minimum and is **not a reliable
timing correction**. The original left
channel has maximum ~0.0284 near
-0.229ms, likewise too weak for
a confident end-of-record alignment.
Do not auto-adjust adaptive AEC delays
based on those late "peaks".
A strongly correlated second-window
example remains 18–20.7s, with left
local 0.4700 at -0.10417ms.
This proves the digital song remains
statistically recognizable amid some
real doubletalk, but does not establish
reliable high-frequency clean speech
transfer.

**Next physical comparison** is a new
offline NUMERIC experiment reusing
native SpeexDSP and our previously tested
dual Speex real-minus-zero cancellation
on the exact same real chaotic recording.
Inputs are the physical WAV + locally
SHA-verified stereo music with a separate
fixed lag for each recorded playback.
The fixed 10ms digital reference prelead
follows the earlier Speex experiment and
is NOT a newly optimized causal delay.
Reference variants: L/R arithmetic mean
on both restarts and first-peaks selected
mean for #1/left for #2. The latter
channel-selection experiment is
post-hoc and exploratory. For every
original physical stage, report output
RMS/changes from input. Where the music
reference is active, compare 500–4000Hz
reference-linked coherence proxies.
Where music is silent, compare direct
changes to the original unprocessed
mic/clean-speech recording. True acoustic
same-utterance clean voice during doubletalk
does not exist; do not claim exact speech
quality or perceptual SNR there.

Code:
\`tools/voice_m5_chaos_speex_real.py\`.
Two synthetic controls were added:
exact physical two-restart music windows
with zero source during 8s clean speech,
and no misleading ground-truth speech
claim in numeric stage comparison.
**60 M5 unit test methods total**,
yet to run on user's local Arch.

~~~bash
cd /data/cue-wire
git pull --ff-only
~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

If green, use the existing local immutable
\`M5-chaos-001\` raw and the exact previous
local numerical reports:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_chaos_speex_real.py \
  --capture .audit/voice-aec/M5-chaos-001 \
  --initial-alignment .audit/voice-aec/M5-chaos-alignment-001/music-alignment.json \
  --consistency .audit/voice-aec/M5-chaos-alignment-consistency-001/alignment-consistency.json \
  --out .audit/voice-aec/M5-chaos-speex-real-001

cd .audit/voice-aec/M5-chaos-speex-real-001
sha256sum -c SHA256SUMS
~~~

Share \`physical-chaos-speex.json\` and
\`SHA256SUMS\`, not RAW voice or commercial
music WAV. No audio capture, playback,
export or PipeWire changes, only native
SpeexDSP offline analysis. This does not
deploy a live microphone; any next step
will be grounded in actual results.

## User-supplied real physical native Speex vs dual-Speex report (2026-11-10)

The operator supplied \`physical-chaos-speex.json\` and
\`SHA256SUMS\`; SHA256 calculated independently for the
actual uploaded JSON and matched the manifest:

\`\`\`text
3fdb522dbbcbe456709c11987d1e795c9b9fc9f086b91718dff374cd6919ad58  physical-chaos-speex.json
\`\`\`

This is \`cuewire-M5-chaos-native-speex-vs-dual-delta-real-physical-v1\`
from native \`libspeexdsp.so.1\`, 48kHz, 10ms frames,
120ms acoustic tail. This first real doubletalk report
preserves source hashes for the exact actual recorded
M5 chaos mic WAV, initial two-lag alignment, frozen
multiwindow lag checks, and privately held stereo song.
No WAV was exported. No live microphone was created.

### Real physical-stage reference-linked power suppression

Values below are dB reductions in the **uncalibrated
500–4000Hz finite-sample coherence-linked power proxy**
relative to exactly the same raw mic section.
This is NOT a measured voice-to-echo SNR,
nor a direct isolated echo power measurement
while near-end speech/keyboard clicks occur.

| Real physical stage | Native Speex, both L/R avg | Raw dual delta, both L/R avg | Guarded dual delta, both L/R avg | Zero-ref native Speex |
| --- | ---: | ---: | ---: | ---: |
| Mouse clicks + music | 11.36445 | 10.84853 | 10.65616 | 0.01144 |
| Keyboard + music | 13.11296 | 12.29805 | 11.94908 | 0.01113 |
| Mouse + keyboard + music | 14.80536 | 13.82245 | 13.47140 | 0.01132 |
| Live speech + clicks + vocal music, open final | 6.40248 | 6.36581 | 6.34529 | 0.01402 |

**Interpretation:** Native Speex is somewhat
stronger by ~0.7–1.3dB on the first three
speech-off physical stages. Under actual
near-end speech + music + clicking, the
guarded dual delta preserves nearly all
its ref-linked suppression (6.345dB
vs native 6.402dB, only 0.05719dB less).
This is a positive trial for the originally
proposed differential reference-dependent
AEC mechanism under *real simultaneous sound*,
not a near-end voice-quality certificate.

### Clean live speech speaker-OFF is EXACTLY untouched

The real user's voice-only physical mic
interval (approx 36.76–43.26s) has
raw total RMS **-40.46861dBFS**.
Both raw delta and guarded delta produced
**sample-exact identical output** to the
raw original in this window:
max abs diff 0, difference RMS sentinel
-240dBFS, same total RMS.
Native Speex with actual far-end (which
is zero in this stage) and the zero-ref
Speex independently produce the same
nonzero change: output RMS -40.60407dBFS,
difference waveform RMS -49.45508dBFS,
max absolute waveform difference 0.02575684.
This does **not** mean native Speex ruined
the voice or delta preserved voice under
speaker-ON doubletalk; it directly demonstrates
that the reference-conditioned dual-delta
avoids reference-independent signal changes
during the **observed speaker-OFF clean speech**.

### Real open-ended chaotic doubletalk details

On approx 44.77–89.26s with mean L/R
source, input RMS is -32.34388dBFS.
Native Speex output -32.42889dBFS,
guarded dual-delta output -32.36474dBFS.
That is only **0.08501dB** and
**0.02086dB** reduction of TOTAL
wideband energy, respectively.
A 6.35dB suppression of the music-linked
proxy can coexist with almost unchanged
overall RMS: other inputs (live speech,
typing/clicks, noise and microphone DSP)
remain audible and unclassified.

Native Speex output-minus-raw error RMS
was -43.00331dBFS whereas guarded
differential's was -53.01475dBFS
(**10.01144dB weaker waveform change**).
This is not an objectively calibrated
speech distortion comparison because
some of that correction is actual echo
removal, and a clean copy of the identical
spoken utterance during doubletalk
does not exist. The report does not
establish subjective voice quality.

### Critical stereo reference choice — do not overfit 2.7s

Only the second song restart changes
between two channel-strategy cases.
The first restart always uses mean(L,R).

For the open 45s simultaneous doubletalk:

| Reference for second restart | Native Speex | Raw delta | Guarded delta |
| --- | ---: | ---: | ---: |
| Stereo mean(L,R) | 6.40248 | 6.36581 | 6.34529 |
| Left alone (early peak winner) | 4.74491 | 4.67728 | 4.66584 |
| Mean minus left | +1.65757 | +1.68853 | +1.67945 |

These are same *uncalibrated linked-power
proxy* dB units, not total speech
improvements. The early 1–3.7s song
winner "left" beat mean by only
**0.001526** in normalized correlation
(0.139972 vs 0.138446). That tiny
short-probe advantage does not support
a persistent left-channel choice.
On this recording the long-stage
posthoc descriptive evidence favors
stereo mean as a more robust mono
reference. No claim of production
optimality or general cross-song result.

### Performance and next step

Native Speex audio processing of
the complete 89.963s recorded mic took
0.790928s (mean case) and 0.801720s
(left-second case) on the operator's
machine. The zero-reference branch
took 0.737857s, performed once and
reused. The two native branches
total ~1.53s for 90s mic, excluding
the offline noncausal FIR, I/O and
real-time scheduling. A 10ms frame
API is not a measured end-to-end
real-time delay.

**Next experimental priority:** an explicit
private, matched-level A/B listening
export on the original 90s physical
doubletalk, not another numeric
tuning loop or new AEC design.
Choose the mean(L,R) fixed source
for both restarts as this recording's
candidate, compare raw, native
Speex and guarded dual delta on
equal actual mic samples. Preserve
matched gain and useful phases
(e.g. clean voice, speech+music,
mouse+music). Avoid commercial music
or private voice uploads to public GitHub.
For assessing speech intelligibility under
the final combined phase, this must remain
a subjective A/B, not a true same-utterance
clean reference. Then explore mature
WebRTC AEC3 and existing click suppressors
as independent baselines.

