# CueWire M3 — freeze the M1 restoration chain and benchmark STUDIO voice processing

**Research status:** M1 restored voice is frozen; M3 studio postprocessing
is a NEW experiment. Nothing here establishes studio quality until the
operator listens and tests on multiple real recordings.

## 1. Stop replacing a good voice

The user's M2 trials of LavaSR / FlowHigh / AP-BWE and further restoration
were clearly inferior to the previously preferred M1 blind track 01.
We therefore keep:

- **B1 (restored baseline):**
  /data/cue-wire/voice-audition-studio-001/track_01.wav
- **B0 (original good clean voice):**
  /data/cue-wire/test.wav
- **B2 (noisy pre-cleaning voice):**
  /data/cue-wire/voice_before.wav

M3 processes **B1 and B0 independently**. The studio processing code never
uses B0 as its target/input while transforming B1. B0 is used only for an
independent mastering pass and a separate evaluation metric.

Previous M1 integrity checks were both TRUE on the operator's machine.
The frozen source before gain matching has SHA-256:

    357dc4303b4e1f9bbe4c4ddc374c6695b2160f1a183e0daab500fa949798bdf3

The detailed audit and original WAVs are retained in PRIVATE
quendoris/cue-wire-voice-bench. No new model may silently replace B1.

## 2. Resolve the *actual original* M1 processing stages

The studio-round track 01 was the control \`blind04__control\` from the
PREVIOUS blind 04. The exact upstream model label is contained in the
operator's local prior listening \`answer_key.json\`; it is NOT in the
private studio archive. Do not guess the chain based on SI-SDR.

The provenance resolver verifies:
- studio track_01 WAV matches its local listening answer key;
- track_01 source SHA matches the frozen M1 source;
- original \`bench-studio-001\` control and its manifest match;
- prior audition track_04 matches the recorded studio source;
- if prior 04 is a previous-session anchor, follows that old key;
- records true ordered model names and upstream WAV hashes;
- optionally verifies original model-run manifest and original output
  WAV hash (use --source-run with the ACTUAL benchmark folder).

From /data/cue-wire:

    python tools/voice_m1_freeze.py \
      --studio /data/cue-wire/voice-audition-studio-001 \
      --studio-run /data/cue-wire/bench-studio-001 \
      --prior /data/cue-wire/voice-audition-005 \
      --previous /data/cue-wire/voice-audition-rnnoise-001 \
      --out /data/cue-wire/.audit/voice-chain/M1-ordered-stages.json

This prints the EXACT "Original ordered model stages" without inspecting
or modifying any audio. It refuses to write an unverified model label.
If the original source-run directory still exists, run a SECOND time
into a FRESH JSON path with \`--source-run /data/cue-wire/<actual benchmark>\`;
this pins its output, commit and model-stage details. Even then, bit-exact
future replay requires original dependency/weight hashes, which may not
have been completely recorded in old experiments. Report such gaps;
never silently invent checkpoints.

Keep JSON in .audit (git-ignored). The original M1 WAV stays frozen.

## 3. Why studio postproduction is different

The new objective is **natural recorded voice**: presence, warmth,
articulation, controlled sibilance, consistent intelligible level and
non-fatiguing peaks. No echo/reverb added, no deliberate white-noise bed,
no aggressive gating and no new denoising or generative resynthesis.

The M3 signal path is:

    input
      -> (optional 65-85 Hz high-pass)
      -> conservative parametric EQ (small tonal changes)
      -> adaptive dynamic de-essing (max 1-2 dB)
      -> downward compressor (ratio 1.3-2.0, max 4 dB reduction)
      -> bounded loudness trim (target -20 LUFS by default)
      -> 4x oversampled estimated true-peak protection (-1.5 dBFS)
      -> PCM16 48k WAV

**Do not call the last stage a certified true-peak limiter.**
It applies a GLOBAL gain trim using a 4x oversampled estimated peak,
so it preserves shape instead of clipping. The short 3 s recordings
support useful rough integrated loudness readings, but not a reliable
full-programme loudness range (LRA) or mic performance verdict.

The built-in SciPy engine creates 8 candidate WAVs per source:
\`control\` (bit-identical PCM16), \`transparent\`, \`intimate\`,
\`articulate\`, \`soft\`, \`radio\`, \`deess_only\`, \`compress_only\`.
These are genuinely DIFFERENT DSP profiles, **not eight ML models**.

## 4. Independent toolkit/algorithm comparison

| Engine | License | Role / limitations |
|---|---|---|
| CueWire native SciPy DSP | Our implementation + SciPy BSD-3-Clause | Main, fully reproducible low-dependency benchmark; no persistent processes |
| [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) | MIT | ITU-R BS.1770-based loudness meter, not a voice-enhancing neural net |
| [Spotify Pedalboard](https://github.com/spotify/pedalboard) | **GPL-3.0** | High-quality, independent JUCE-based EQ/compressor and 4x-sidechain BrickwallLimiter; optional and not bundled into CueWire core |
| [FFmpeg](https://ffmpeg.org/ffmpeg-filters.html) | Build-dependent LGPL/GPL | Reference CLI DSP and EBU R128 loudnorm/deesser/compressor; future direct comparison |
| [Matchering](https://github.com/sergree/matchering) | GPL-3.0 | Reference-guided automatic mastering, mostly designed for music; test only with a genuine mastered-VOICE reference, not the same B0 clean file used to score B1 |

The optional Pedalboard engine is already implemented as
\`--include-pedalboard\` with two independent profiles, not enabled by
default. The GPLv3 license must be respected for any redistribution.
Do not confuse an optional locally installed processor with mandatory
CueWire runtime dependencies.

Neural Resemble/VoiceFixer/full-band restoration is no longer in the
production M1 chain. Such models may still be compared as exploratory
optional follow-up paths, with caution about altered voice identity.

## 5. Run locally, preserving all prior results

Use a completely separate environment. Do NOT install into system
Python, replace the audio engine, or start any daemon.

    cd /data/cue-wire
    git fetch origin
    git switch feature/pcm-mixer-playground
    git pull --ff-only

    uv venv --python 3.11 .audit/voice-tools/studio
    uv pip install --python .audit/voice-tools/studio/bin/python \
      numpy scipy soundfile pyloudnorm

\`uv\` may download Python/packages, only when explicitly invoked.
An existing venv should NOT be silently overwritten.

Start independent studio profiles using both good input recordings:

    STUDIO_PY="$PWD/.audit/voice-tools/studio/bin/python"

    "$STUDIO_PY" tools/voice_master_lab.py \
      --input B1=/data/cue-wire/voice-audition-studio-001/track_01.wav \
      --input B0=/data/cue-wire/test.wav \
      --out /data/cue-wire/bench-master-M3-001 \
      --target-lufs -20

This creates 16 WAVs and independent per-file profiles, output
checksums, \`scores.json\`, \`manifest.json\` and \`SHA256SUMS\`.
Nothing is replayed automatically, inputs are never rewritten.
Only the B1 and B0 sources from these local paths are processed.

## 6. Blind listening correctly, in TWO source-specific groups

    "$STUDIO_PY" tools/voice_master_listen.py \
      /data/cue-wire/bench-master-M3-001 \
      --out /data/cue-wire/voice-audition-master-M3-001 \
      --seed 20261108

Listen to B1 mastering candidates first, maintaining the same physical
headphone volume; DO NOT open the answer keys:

    pw-play /data/cue-wire/voice-audition-studio-001/track_01.wav
    for f in /data/cue-wire/voice-audition-master-M3-001/B1/track_*.wav; do
      echo "$f"
      pw-play "$f"
    done

Then listen to B0 candidates as a SECOND group:

    pw-play /data/cue-wire/test.wav
    for f in /data/cue-wire/voice-audition-master-M3-001/B0/track_*.wav; do
      echo "$f"
      pw-play "$f"
    done

The listening kit separately matches playback level (RMS) across
presets and verifies all input hashes. This avoids a 'louder is
better' effect and prevents accidentally scoring the clean-source
processing as restored-source processing.

Only AFTER writing subjective ranked IDs and specific notes about
sibilance/naturalness/rumble/dynamics, reveal the mapping in the
appropriate B0 or B1 \`answer_key.json\`.

### Optional GPLv3 studio processor comparison

Install the additional dependency only deliberately, into this
EXPERIMENTAL separate Python environment:

    uv pip install --python "$STUDIO_PY" pedalboard

Run a NEW, separate benchmark folder:

    "$STUDIO_PY" tools/voice_master_lab.py \
      --input B1=/data/cue-wire/voice-audition-studio-001/track_01.wav \
      --input B0=/data/cue-wire/test.wav \
      --out /data/cue-wire/bench-master-M3-002 \
      --include-pedalboard --target-lufs -20

Compare the existing eight profiles against two independent
JUCE/Pedalboard profiles. Log exact Python/plugin versions.

## 7. Private GitHub Release publication

The existing publisher will verify the PRIVATE repo, WAV checksums,
and ask for explicit confirmation:

    bash tools/voice_bench_publish.sh \
      /data/cue-wire/bench-master-M3-001 \
      quendoris/cue-wire-voice-bench \
      voice-master-M3-001

At the Russian-language prompt, type exactly \`UPLOAD\` in uppercase
Latin characters and press Enter. This uploads **personal voice**
(B0/B1) and mastering result WAVs into a PRIVATE GitHub Release, never
the public CueWire repo. The private CI has been updated to recognize
\`voice-master-*\` release tags, verify archive integrity and save
non-audio studio metrics.

## 8. Before claiming 'real studio voice'

Our input is currently about 3 seconds long. It is valuable for
reproducible A/B tests, but not representative of a real long-form
recording. The next serious assessment should include 20-60 seconds
of continuous speech with variable loudness, quiet pauses, plosives,
sibilants ('с', 'ш', 'щ'), varied pitch and distance, and a known
input path. Do NOT include a clean reference in model inference.

Measurable requirements: no unintended clipping/overs, no obvious
tail echo, no pumping or hiss from downward processing, stable level,
intelligible consonants and believable timbre. Listening judgments
outrank tiny SI-SDR gains. The pristine B0 and frozen B1 must remain
unaltered and independently usable.
