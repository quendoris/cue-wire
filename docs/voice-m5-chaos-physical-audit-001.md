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
