# CueWire M2: broader voice restoration, beyond noise suppression

Date: 2026-11-08. Status: **research plus experimental adapters**, NOT
a claim that new models improve the user's voice.

## Frozen baseline M1

The PRIVATE checkpoint [M1](https://github.com/quendoris/cue-wire-voice-bench/blob/main/reports/milestone-M1-frozen-blind-01-2026-11-08.md)
records the user's two successful local True/True integrity tests,
the verified blind-01 recording SHA-256, listening judgement, and objective
audit. The best studio-round 01 was an *unchanged studio-stage control of
previously restored audio*, not the pristine reference or a newly proven
model. The raw model identity remains blinded. It is 2.97 s vs the 3.00 s
clean reference; correlation on common samples ~0.790. Freeze it.

B0 = /data/cue-wire/test.wav (clean, only for evaluation)
B1 = /data/cue-wire/voice-audition-studio-001/track_01.wav (best)
B2 = /data/cue-wire/voice_before.wav (original noisy input)

Future models must NEVER use B0 to infer B1 or repair B2.

## New researched approaches

Do not confuse the **code license** with model/checkpoint license and
data training rights. The model-specific rights listed below were
checked against repository LICENSE or official HF cards as noted.

| Project | Task | Code / model license | Readiness |
|---|---|---|---|
| [SGMSE+](https://github.com/sp-uhh/sgmse) | Diffusion-based speech enhancement and **separate 48 kHz dereverberation**, EARS-WHAM / EARS-Reverb checkpoints | MIT code / HF card MIT | Research-ready; PyTorch + checkpoint, new isolated adapter required |
| [LavaSR v2](https://github.com/ysharma3501/LavaSR) | Bandwidth restoration, optional denoising, 48 kHz output | Apache-2.0 source / [Apache-2.0 weights](https://huggingface.co/YatharthS/LavaSR) | Integrated through audiosronnx |
| [MossFormer2_SR_48K](https://huggingface.co/alibabasglab/MossFormer2_SR_48K) | Full-band speech super-resolution (not denoising) | Apache-2.0 model and ClearerVoice source | Published HF repository ~2.18 GB, dedicated task adapter not yet complete |
| [FlowHigh](https://github.com/jjunak-yun/FLowHigh_code) | Flow-matching high-band synthesis | audiosronnx lists MIT for engine | Integrated via audiosronnx, confirm exact weights |
| [AP-BWE](https://github.com/yxlu-0102/AP-BWE) | Amplitude/phase bandwidth extension | audiosronnx lists MIT | Integrated via audiosronnx |
| [Sidon](https://github.com/sarulab-speech/Sidon) | Fully generative, SSL-based speech resynthesis | audiosronnx lists MIT | Integrated via audiosronnx, possible voice/word drift |
| [GAP-URGENet](https://github.com/Xiaobin-Rong/gap-urgenet) | 2026 predictive/generative fusion, speech restoration | MIT code; check all checkpoint terms | Published inference scripts and multiple checkpoints, integration pending |

[audiosronnx](https://github.com/TigreGotico/audiosronnx) itself
is an Apache-2.0 **inference framework**, not another neural network.
It wraps models with their individual weights and licenses. Some other
models it supports are **non-commercial**; we do NOT include them by
default.

Muffling of a 48 kHz PCM file does not mean changing its sample rate
will restore missing harmonics. Model IDs `sr_lavasr_6k` and
`sr_lavasr_9k` explicitly reconstruct the high band above 6 or 9
kHz. These are **settings of the same LavaSR model**, not different
independent networks; invented detail is plausible, not guaranteed true.

All models and training/checkpoint downloads are OPTIONAL; no size
cap, no automatic services, no audio routing changes, no system Python
dependency modifications.

## First practical M2: improve frozen B1

Run explicitly (installed uv required):

    cd /data/cue-wire
    git fetch origin
    git switch feature/pcm-mixer-playground
    git pull --ff-only
    bash tools/voice_bench_extra_install.sh --install audiosronnx

When installed, the third-party package downloads model weights on the
FIRST EXPLICIT model invocation. Larger engines may take longer.

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench.py \
      --clean /data/cue-wire/test.wav \
      --noisy /data/cue-wire/voice-audition-studio-001/track_01.wav \
      --out /data/cue-wire/bench-restore-M2-B1-001 \
      --timeout-sec 3600 \
      --models identity,sr_lavasr,sr_lavasr_6k,sr_lavasr_9k,sr_flowhigh,sr_apbwe \
      --chains ''

Here `--noisy` is the B1 *model input*, NOT the raw noisy B2.
The pristine B0 stays exclusively in the scoring stage. Run a
separate B2 experiment (different output directory) to test the
effect of restoration *before* noise removal:

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench.py \
      --clean /data/cue-wire/test.wav \
      --noisy /data/cue-wire/voice_before.wav \
      --out /data/cue-wire/bench-restore-M2-B2-001 \
      --timeout-sec 3600 \
      --models identity,deepfilter,sr_lavasr_6k \
      --chains deepfilter+sr_lavasr_6k,sr_lavasr_6k+deepfilter

Both experiments are private, offline, produce separate immutable
manifests/WAVs/metrics/hashes and run no resident processes. No model
has been declared better until real user A/B listening.

## Measurement

Use original B0, frozen B1 and raw B2 as distinct comparators.
Evaluate naturalness, consonants, speaker identity, high-band spectra,
room tails, boundary truncation, perceived echo, noise-only intervals,
timing/peak and SI-SDR. For generative models, word hallucination and
identity drift are primary failure modes. Blind tests at equalized
playback level always come before algorithm name reveals.

Next true experimental research path: dedicated 48 kHz SGMSE+
denoise vs **dereverb** checkpoints, then heavyweight
MossFormer2_SR_48K and GAP-URGENet. Those require new pinned
checkpoint adapters and their own tests before they can be claimed
plug-and-play.
