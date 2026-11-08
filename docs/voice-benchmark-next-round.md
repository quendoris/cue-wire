# CueWire: next blind voice-restoration benchmark (2026-11-08)

Our goal is first to approach the real clean test.wav in timbre,
intelligibility and consonants. Studio mastering (EQ, compressor, limiter,
optional noise bed) is a SEPARATE, later stage.

## Blind observations, do not reveal model identities

The user's prior blind observations:
- track_01 sounds similar to a clean, un-noised reference.
- track_05 sounds best overall.

These are listening impressions, NOT algorithm identities. In this
benchmark the actual clean source is reference_clean.wav, while track_XX
files are generated from model outputs. Keep the old answer_key.json
closed until the user explicitly chooses to unblind the previous test.

## New true model families

- GTCRN: lightweight ONNX model; outputs speech at native 16 kHz.
- DPDFNet2 and DPDFNet8 48 kHz HR: full-band neural models.
- DPDFNet2_soft and DPDFNet8_soft: SAME model checkpoints as above,
  but using sherpa-onnx's official 12 dB attenuation limit. These
  are modes, not different neural networks.
- SpeechBrain MetricGAN+ VoiceBank: 16 kHz enhancement aimed at
  perceptual speech quality. It is NOT full-band studio restoration.

The benchmark also retains ClearerVoice MossFormer2/FRCRN/MossFormerGAN,
Resemble, RNNoise, DeepFilterNet, VoiceFixer, C+ and existing combinations.

Official sources:
https://github.com/Xiaobin-Rong/gtcrn
https://k2-fsa.github.io/sherpa/onnx/speech-enhancement/models.html
https://k2-fsa.github.io/sherpa/onnx/speech-enhancement/dpdfnet.html
https://github.com/ceva-ip/DPDFNet
https://huggingface.co/speechbrain/metricgan-plus-voicebank
https://github.com/k2-fsa/sherpa-onnx

DPDFNet and sherpa-onnx source are Apache-2.0; SpeechBrain is Apache-2.0,
the published MetricGAN+ model card says Apache-2.0. Verify the
specific GTCRN checkpoint's terms before redistributing model files.
Keep all personal voice recordings PRIVATE.

## Installation (manual, no services, no system audio changes)

Run from /data/cue-wire:

    bash tools/voice_bench_extra_install.sh --check
    bash tools/voice_bench_extra_install.sh --install sherpa
    bash tools/voice_bench_extra_install.sh --download sherpa-models

The two ONNX models total about 11 MB, avoiding the problematic 56 MB
RNNoise source-model download. DPDFNet2 checks its pinned asset SHA256.
Optional DPDFNet8 from its official Hugging Face:
    
    bash tools/voice_bench_extra_install.sh --download dpdfnet8

Optional SpeechBrain in its own Python 3.11 virtual environment:

    bash tools/voice_bench_extra_install.sh --install speechbrain

SpeechBrain downloads weights on first explicit inference.
These installs may download packages/cache files; no automatic
background service or startup scripts are created.

## First new blind run

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench.py \
      --clean /data/cue-wire/test.wav \
      --noisy /data/cue-wire/voice_before.wav \
      --out /data/cue-wire/bench-run-005 \
      --models identity,cplus,deepfilter,rnnoise_lib,gtcrn,dpdfnet2,dpdfnet2_soft \
      --chains deepfilter+dpdfnet2,dpdfnet2+deepfilter,rnnoise_lib+gtcrn,gtcrn+cplus,deepfilter+dpdfnet2_soft

For the expanded models, use a FRESH output directory:

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench.py \
      --clean /data/cue-wire/test.wav \
      --noisy /data/cue-wire/voice_before.wav \
      --out /data/cue-wire/bench-run-006 \
      --timeout-sec 1800 \
      --models identity,gtcrn,dpdfnet2,dpdfnet2_soft,dpdfnet8,dpdfnet8_soft,metricgan_plus,clearvoice,clearvoice_frcrn,clearvoice_gan,resemble_denoise,resemble_full \
      --chains deepfilter+dpdfnet8_soft,dpdfnet8_soft+resemble_full,dpdfnet2_soft+clearvoice,gtcrn+clearvoice,metricgan_plus+deepfilter

Missing tools are honestly recorded as missing_dependency. No
results exist until the local recording is actually processed.

## Keep the old hidden 01 and 05 as anchors

The new blind listening script can mix prior samples into the new
randomized listening roster WITHOUT revealing their model labels:

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench_listen.py \
      /data/cue-wire/bench-run-005 \
      --out /data/cue-wire/voice-audition-005 \
      --anchor /data/cue-wire/voice-audition-rnnoise-001/track_01.wav \
      --anchor /data/cue-wire/voice-audition-rnnoise-001/track_05.wav \
      --seed 20261109

Then run manually:

    for f in /data/cue-wire/voice-audition-005/track_*.wav; do
      echo "$f"
      pw-play "$f"
    done

DO NOT OPEN answer_key.json until after rating. The two prior
references are only opaque anchors; their old identity remains masked.
Do not let a gain-normalization difference determine the winner.


## Next phase: preserve clean voices, then test gentle studio processing

The 56 MB RNNoise hang was a mirror/connection failure, NOT a maximum
model-size limitation. Model selection must be driven by audio quality,
license, runtime and reproducibility, not mere download size. Large and
full-band models (MossFormer2, Resemble, VoiceFixer, DPDFNet8) remain
high-priority experiments.

On the next blind listening batch, the user rated 01 as sounding like
the unnoised original, 04 as notably reducing background noise, and
14 as similarly strong. These are subjective evaluations. The actual
algorithm identities are intentionally not unblinded, and track 01
cannot be certified as literally the clean reference without checking
its input hashes.

For a SEPARATE studio-stage preview using the already listened-to
blind 04 and 14, install no new neural networks. Run the deterministic
and intentionally conservative EQ/compression sandbox:

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench_studio.py \
      --input 04=/data/cue-wire/voice-audition-005/track_04.wav \
      --input 14=/data/cue-wire/voice-audition-005/track_14.wav \
      --clean /data/cue-wire/test.wav \
      --noisy /data/cue-wire/voice_before.wav \
      --out /data/cue-wire/bench-studio-001

This yields twelve WAVs: unprocessed controls and five gentle tonal/
dynamics changes per source. Neither the clean reference nor an
external model label influences the processing settings. There is NO
noise bed, gate, voice synthesis, or system audio configuration change.
The strongest EQ band is only +1 dB; max gain reduction is 2 dB.

Blind the studio output as well:

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench_listen.py \
      /data/cue-wire/bench-studio-001 \
      --out /data/cue-wire/voice-audition-studio-001 \
      --seed 20261108

    pw-play /data/cue-wire/voice-audition-studio-001/reference_clean.wav
    for f in /data/cue-wire/voice-audition-studio-001/track_*.wav; do
      echo "$f"; pw-play "$f"
    done

Leave answer_key.json closed while listening. If no processed variant
beats its unmodified control, do NOT merge studio processing into the
runtime. Audition, source fidelity, and absence of metallic artifacts
matter more than a numerical SI-SDR gain. This offline stage does not
imply studio-quality restoration has been achieved.

The studio benchmark includes a manifest, SHA256SUMS and scoring files,
and can be uploaded to the separate PRIVATE benchmark repository using
the existing explicit confirmation script after checking the contents.
