# CueWire — licenses and independent speech-restoration models (2026-11-08)

The **source license**, **checkpoint license**, **third-party dependencies**
and **training dataset license** are distinct. Local A/B tests do not
establish any right to redistribute all model files as one commercial build.

| Project | Code license | Verified model status |
|---|---|---|
| DeepFilterNet | MIT OR Apache-2.0 | README explicitly dual-licenses code AND model weights in repository |
| RNNoise | BSD-3-Clause | Bundled and downloaded models: inspect exact release before redistribution |
| ClearerVoice-Studio | Apache-2.0 | Official HF cards MossFormer2_SE_48K, FRCRN_SE_16K, MossFormerGAN_SE_16K: Apache-2.0 |
| Resemble Enhance | MIT | ResembleAI/resemble-enhance HF model card: MIT |
| VoiceFixer | MIT | Checkpoint/vocoder rights require separate verification |
| Audio Separator | MIT | Individual UVR, RoFormer, Demucs checkpoints may differ |
| Demucs | MIT | Third-party training data and weight terms need review |

Primary references:
- https://github.com/Rikorose/DeepFilterNet/blob/main/README.md
- https://github.com/xiph/rnnoise/blob/main/COPYING
- https://github.com/modelscope/ClearerVoice-Studio/blob/main/LICENSE
- https://huggingface.co/alibabasglab/MossFormer2_SE_48K
- https://huggingface.co/alibabasglab/FRCRN_SE_16K
- https://huggingface.co/alibabasglab/MossFormerGAN_SE_16K
- https://github.com/resemble-ai/resemble-enhance/blob/main/LICENSE
- https://huggingface.co/ResembleAI/resemble-enhance
- https://github.com/haoheliu/voicefixer/blob/main/LICENSE
- https://github.com/nomadkaraoke/python-audio-separator
- https://github.com/facebookresearch/demucs

## Explicit isolated installs

From /data/cue-wire, use:

    bash tools/voice_bench_install.sh --help
    bash tools/voice_bench_install.sh --check

Every --install command downloads/executess third-party code locally under
.audit/voice-tools, without sudo, systemd services, audio routing changes,
or installing to system Python. RNNoise requires the autotools C compiler
toolchain; its autogen.sh also downloads a model.

    bash tools/voice_bench_install.sh --install rnnoise

For neural projects, install uv separately if unavailable. The installer
uses separate Python 3.10/3.11 virtual environments (these can consume
significant disk space, and download model weights at first inference):

    bash tools/voice_bench_install.sh --install clearvoice
    bash tools/voice_bench_install.sh --install resemble
    bash tools/voice_bench_install.sh --install voicefixer

Legacy VoiceFixer may not resolve with modern Torch and needs its own
compatibility experiment; a failure is NOT a reason to touch system Python.

Use local model paths in THIS terminal ONLY:

    source tools/voice_bench_env.sh

The existing standalone DeepFilterNet binary and any existing
CUEWIRE_BENCH_DEEPFILTER override are preserved.

## Model variants

- deepfilter: DeepFilterNet default model, no postfilter
- deepfilter_pf: SAME model with optional --pf, not another independent network
- clearvoice: MossFormer2_SE_48K
- clearvoice_frcrn: FRCRN_SE_16K (16kHz internal inference)
- clearvoice_gan: MossFormerGAN_SE_16K (16kHz internal inference)
- resemble_denoise: denoise-only
- resemble_full: denoise + generative enhancement; risk of timbre change
- voicefixer: mode 0
- voicefixer_mode1: SAME VoiceFixer weights, alternative processing mode 1
- rnnoise: dedicated RNN realtime noise model
- cplus: our strong classic DSP baseline
- roformer: music vocal separation, not ideal for ordinary microphone noise

## Next run

Run models independently, then REAL sequential chains. Clean reference
is never sent into a model, only to the metrics evaluator. Every output
is kept as an immutable WAV with version/weights metadata where available.

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench.py \
      --clean /data/cue-wire/test.wav \
      --noisy /data/cue-wire/voice_before.wav \
      --out /data/cue-wire/bench-run-004 \
      --timeout-sec 1800 \
      --models identity,cplus,deepfilter,deepfilter_pf,rnnoise,clearvoice,clearvoice_frcrn,clearvoice_gan,resemble_denoise,resemble_full,voicefixer,voicefixer_mode1 \
      --chains deepfilter+cplus,cplus+deepfilter,deepfilter+clearvoice,deepfilter+clearvoice_frcrn,deepfilter+resemble_denoise,deepfilter+resemble_full,rnnoise+clearvoice,clearvoice+resemble_full,resemble_denoise+clearvoice

Start with a smaller --models/--chains set if installation or GPU/CPU
performance is uncertain. The benchmark reports missing dependencies
honestly; never count such models as failed quality comparisons.

More noise suppression is NOT necessarily better. In addition to SI-SDR,
do level-matched blind listening against test.wav for voice timbre,
consonants, musical noise and perceived closeness to clean source.
Publish results only to the existing PRIVATE GitHub release repository
after reviewing the archive and explicitly confirming UPLOAD.
