# CueWire voice benchmark v1 — reference-based, local, reproducible

The main quendoris/cue-wire repository is PUBLIC. Real voice recordings
must NOT be committed to it. The optional publisher explicitly verifies
a SEPARATE GitHub results repository is PRIVATE before uploading audio.

## Input contract

- CLEAN reference: /data/cue-wire/test.wav (not used by any enhancement model).
- NOISY input: /data/cue-wire/voice_before.wav, assumed to be the SAME
  utterance mixed with noise. Every model sees this same noisy input.
- Existing originals are not overwritten. FFmpeg creates normalized mono
  48 kHz PCM16 copies under the run's inputs/ directory.
- Preflight estimates lag, correlation and >=90% coverage. If correlation
  is too low (<0.12), by default the run stops BEFORE expensive inference.
  The heuristic cannot prove utterance or speaker identity. Listen to
  the pair; if they are unrelated, SI-SDR is meaningless.
- --force-unverified-pair lets you run a subjective-only comparison, but
  the report marks reference-based ranking ineligible.

No GUI, audio playback, WirePlumber modification, background daemon or
autostart is created. Jobs run sequentially in owned child process groups;
Ctrl+C / SIGTERM stops active subprocesses.

## Candidate models

The optional model IDs are:

    identity
    cplus
    deepfilter
    rnnoise
    clearvoice
    resemble_denoise
    resemble_full
    voicefixer
    roformer

The default independent models include everything except RoFormer (a
music-vocal separator; generally wrong for ordinary noisy microphone).
Default chained experiments are:

    deepfilter+clearvoice
    rnnoise+clearvoice
    deepfilter+resemble_denoise
    cplus+clearvoice

A chained result means a GENUINE series of model inferences. Models are
not secretly replaced by proxies. Missing tools are recorded as
missing_dependency; failed processes as failed. If you only installed two
models, specify a smaller list for an easier first run.

Verified upstream interfaces:

- DeepFilterNet standalone Rust deep-filter: deep-filter -D -o DIR input.wav
- RNNoise demo: rnnoise_demo input.raw output.raw, 48 kHz MONO s16le;
  the wrapper explicitly converts WAV/RAW/WAV via FFmpeg.
- ClearerVoice-Studio: MossFormer2_SE_48K, through the dedicated adapter.
- Resemble Enhance: resemble-enhance INDIR OUTDIR --denoise_only,
  or the full enhancer without that option. Device can be CPU or CUDA.
- VoiceFixer: voicefixer --infile INPUT --outfile OUTPUT --mode 0.
- RoFormer: audio-separator INPUT --model_filename MODEL
  --output_format WAV --output_dir DIR; only an unambiguous vocal stem
  is accepted. It is opt-in and more suitable for musical mixtures.
- C+: existing CueWire non-neural speech-focus test.

Reference docs:
https://github.com/Rikorose/DeepFilterNet
https://github.com/xiph/rnnoise
https://github.com/modelscope/ClearerVoice-Studio/blob/main/clearvoice/README.md
https://github.com/resemble-ai/resemble-enhance
https://github.com/haoheliu/voicefixer
https://github.com/nomadkaraoke/python-audio-separator

## First lightweight execution (no large ML models required)

From the root of the user's /data/cue-wire Git checkout:

    git fetch origin
    git switch feature/pcm-mixer-playground
    git pull --ff-only

A dedicated Python environment is required; the existing voice-lab
environment already includes NumPy and SciPy. If missing:

    python3 -m venv ~/.venvs/cuewire-voice-lab
    ~/.venvs/cuewire-voice-lab/bin/python -m pip install \
      -r tools/voice_ab_requirements.txt

Run the reference preflight plus identity and C+ in one foreground job:

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench.py \
      --clean /data/cue-wire/test.wav \
      --noisy /data/cue-wire/voice_before.wav \
      --out /data/cue-wire/bench-run-001 \
      --models identity,cplus \
      --chains ''

Review:

    cat bench-run-001/manifest.json
    column -s, -t bench-run-001/scores.csv
    pw-play bench-run-001/results/cplus.wav

No WAV is played automatically.

## Full comparison (models installed SEPARATELY, by operator)

Create dedicated environments for each model to prevent PyTorch/version
conflicts with CueWire and Arch's system Python. Many older packages
expect Python 3.11/3.12 and a compatible GPU Torch build rather than
Arch Python 3.13. Verify the specific model release first. Check licenses
of model weights separately from source-code licenses. Some projects
download model checkpoints during FIRST explicit run. This orchestrator
never installs packages or downloads checkpoints itself.

Possible overrides (export them only in the current shell/session):

    export CUEWIRE_BENCH_AB_PYTHON=$HOME/.venvs/cuewire-voice-lab/bin/python
    export CUEWIRE_BENCH_DEEPFILTER=deep-filter
    export CUEWIRE_BENCH_RNNOISE=/absolute/path/to/rnnoise/examples/rnnoise_demo
    export CUEWIRE_BENCH_CLEARVOICE_PYTHON=$HOME/.venvs/cuewire-clearvoice/bin/python
    export CUEWIRE_BENCH_RESEMBLE=$HOME/.venvs/cuewire-resemble/bin/resemble-enhance
    export CUEWIRE_BENCH_VOICEFIXER=$HOME/.venvs/cuewire-voicefixer/bin/voicefixer
    export CUEWIRE_BENCH_SEPARATOR=$HOME/.venvs/cuewire-separator/bin/audio-separator

Do not set a path until the corresponding tool is installed and checked
with its own --help. The actual model environments and checkpoints remain
independent. Then run:

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench.py \
      --clean /data/cue-wire/test.wav \
      --noisy /data/cue-wire/voice_before.wav \
      --out /data/cue-wire/bench-run-002 \
      --models identity,cplus,deepfilter,rnnoise,clearvoice,resemble_denoise,resemble_full,voicefixer \
      --chains deepfilter+clearvoice,rnnoise+clearvoice,deepfilter+resemble_denoise,cplus+clearvoice \
      --timeout-sec 1800

To test just one available model:

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench.py \
      --clean /data/cue-wire/test.wav \
      --noisy /data/cue-wire/voice_before.wav \
      --out /data/cue-wire/bench-run-deepfilter \
      --models identity,deepfilter --chains ''

Always use a FRESH --out directory so benchmark runs stay immutable.

## Metrics and integrity

Scores include delay-aligned scale-invariant SDR (SI-SDR), delta relative
to the noisy input, aligned correlation, coverage, sample peak, RMS,
clipping count and elapsed runtime for each model. These do NOT capture
subjective intelligibility, speaker identity, metallic artifacts or
naturalness. Avoid declaring a best model based only on SI-SDR.

The run folder includes input references, normalized clean/noisy copies,
individual model WAVs, logs, full model sequence metadata, hashes,
scores.csv, scores.json, manifest.json, and SHA256SUMS.
For complete reproducibility, provide explicit --weights FILE PATHS to
hash checkpoint files and record the exact model package versions.
The manifest truthfully marks weights provenance incomplete otherwise.

## Explicit private GitHub publication

Create a PRIVATE separate results repository in your GitHub account:

    gh repo create quendoris/cue-wire-voice-bench --private

After reviewing its visibility, deliberately upload one full experiment
as a private GitHub Release archive:

    bash tools/voice_bench_publish.sh \
      /data/cue-wire/bench-run-002 \
      quendoris/cue-wire-voice-bench \
      voice-bench-run-002

The script checks PRIVATE visibility, verifies SHA256SUMS, asks for
interactive confirmation and uploads the full archive (including the
CLEAN REFERENCE and NOISY VOICE) as a release attachment.

At the interactive prompt, enter the literal uppercase Latin word
`UPLOAD` and press Enter. It means 'I authorize this private upload',
NOT a transport selection (SSH/HTTPS). Any other input cancels the
operation without sending files. The prompt is also explained in Russian.
The private results repository must have an initial commit (README.md)
before its first GitHub Release is created. No public
repository/audio commit is created. The connected GitHub integration
must ALSO be authorized for the private results repo if ChatGPT is to
fetch those files for independent review.

If you prefer not to upload private audio anywhere, keep WAVs locally
and share only scores.json, manifest.json and harmless sanitized graphs.
