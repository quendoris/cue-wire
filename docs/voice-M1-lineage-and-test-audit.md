# CueWire M1 forensic chain and CLEAN reference audit

**Status:** the only baseline approved by the listener is the frozen
\`voice-audition-studio-001/track_01.wav\`. The later M2 bandwidth
reconstruction and M3 studio B0 auditions did NOT improve voice quality;
they are **REJECTED AS QUALITY WINNERS**, not promoted into production.
Old files remain untouched for audit.

The operator reported two local SHA-256 checks as **True**, validating
that the recorded M1 blind 01 matches the archived source.

From the PRIVATE archive \`voice-bench-studio-001\`, its source was:

    studio blind 01
      <- blind04__control (no studio EQ, compression or new model)
      <- prior blind 04 (voice-audition-005/track_04.wav)
      <- exact processing stages encoded in the PRIOR answer_key.json
      <- possibly an even earlier RNNoise blind 01/05 anchor
      <- model output of an original bench-*/manifest.json

We explicitly UNBLIND the original algorithm here because the user
wants to preserve it as a future production chain.

## Baseline and sample meanings

- **B0**: \`/data/cue-wire/test.wav\`. Original good un-noised microphone
  recording, used for comparison only (never for model input).
- **B1**: \`/data/cue-wire/voice-audition-studio-001/track_01.wav\`.
  The verified, user-preferred cleaned recording.
- **B2**: \`/data/cue-wire/voice_before.wav\`. Original noisy input.

Do not reverse B0 and B1; do not substitute an M3 loudness-processed
\`B0__*.wav\` for B1. M3 sound-quality results were rejected and its
16 audio derivatives are NOT a new reference. No files are deleted.

## Step 0: git state

    cd /data/cue-wire
    git fetch origin
    git switch feature/pcm-mixer-playground
    git pull --ff-only
    mkdir -p /data/cue-wire/.audit/voice-chain

## Step 1: unblind, verify and identify the original ordered model stages

    python tools/voice_m1_freeze.py \
      --studio /data/cue-wire/voice-audition-studio-001 \
      --studio-run /data/cue-wire/bench-studio-001 \
      --prior /data/cue-wire/voice-audition-005 \
      --previous /data/cue-wire/voice-audition-rnnoise-001 \
      --scan-root /data/cue-wire \
      --out /data/cue-wire/.audit/voice-chain/M1-chain-verified-v2.json

This validates original blind track SHA, studio control and old key
links. It then scans \`/data/cue-wire/bench-*/manifest.json\` for an
ORIGINAL model output with the same verified SHA. If exactly one match
exists, records its \`repo_commit\`, ordered \`steps\`, \`stage_details\`,
the source input WAV hash and any checkpoint hashes recorded by the run.

If the source run has gone missing, it prints
\`listening_key_chain_verified_source_run_unavailable\`. This is a
legitimate **partial result**: model names/order come from exact keys,
but a bit-identical replay still needs original model versions and
weights. NEVER replace this with a guess. If several equivalent source
runs exist, supply one explicitly with \`--source-run DIR\` instead.

\`playback_level_normalization_history\` records listening-kit gain
matching separately; it is NOT part of the denoising/voice enhancement
algorithm. Original B1 audio is not overwritten or reprocessed.

## Step 2: compare *exact* B1 with B0 and noisy B2

Use the previously prepared voice-bench venv with numpy/scipy and FFmpeg:

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_m1_compare.py \
      --clean /data/cue-wire/test.wav \
      --restored /data/cue-wire/voice-audition-studio-001/track_01.wav \
      --noisy /data/cue-wire/voice_before.wav \
      --chain-report /data/cue-wire/.audit/voice-chain/M1-chain-verified-v2.json \
      --out /data/cue-wire/.audit/voice-chain/M1-vs-test-v2.json

It requires that the exact B1 SHA matches the frozen listening key,
canonically decodes all three ONLY in a temporary folder, and reports:

- actual sample counts/durations (old B1 is 2.97 s; B0 is 3.00 s);
- lag alignment and clean/reference overlap;
- waveform correlation and SI-SDR (plus baseline change from raw B2);
- spectral energy bands, short tail RMS and peak/clipping;
- exact source WAV and normalized comparison SHA-256 hashes.

**The score cannot certify naturalness, voice identity, or whether
a few consonants are hallucinated.** These remain audio-listening tasks.
A tiny 3 s clip cannot establish mastered loudness statistics.

Previous audit of the archived B1 source control found ~0.790
reference correlation and +2.20 dB SI-SDR (versus -11.86 dB for B2);
the newly generated report recomputes metrics from the exact locally
listened file and may differ slightly due to listening gain matching.

## Step 3: publish verified NON-AUDIO evidence to our PRIVATE GitHub

First run a **no-write check**:

    python tools/voice_m1_publish_report.py \
      --chain /data/cue-wire/.audit/voice-chain/M1-chain-verified-v2.json \
      --comparison /data/cue-wire/.audit/voice-chain/M1-vs-test-v2.json \
      --check-only

Then, after inspecting any issues, publish:

    python tools/voice_m1_publish_report.py \
      --chain /data/cue-wire/.audit/voice-chain/M1-chain-verified-v2.json \
      --comparison /data/cue-wire/.audit/voice-chain/M1-vs-test-v2.json

**Consent prompt:** Enter precisely the uppercase English word \`UPLOAD\`
and press Enter to authorize the upload.

This is NOT a public GitHub commit or a voice recording release.
It checks that \`quendoris/cue-wire-voice-bench\` remains PRIVATE,
refuses to overwrite previous evidence, sanitizes local absolute
paths, and publishes ONLY one JSON with original model stages,
checkpoint-hash coverage, SHA-256 lineage and clean-vs-restored metrics:

https://github.com/quendoris/cue-wire-voice-bench/blob/main/reports/M1-exact-chain-and-test-comparison.json

NO WAV, original voice, private audio, authentication token or full
local absolute path is included. Once that private JSON exists, the
assistant can analyze and choose how to turn the exact sequence into
a dedicated stable CueWire production processor. Do not write any
guessed model sequence into the production configuration.

## Next experiment (NOT started)

Record a new 20-60 s clip only AFTER the verified production chain
has been fully identified and reproducibly pinned. The new capture
can include head turns, mic approach/withdrawal, varied speaking
levels, sibilants, plosives and pauses; it will be used to test gain,
presence and dynamic stability. No new recording is required to finish
the present M1 provenance audit.
