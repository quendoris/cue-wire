# Open-source voice restoration study — 2026-11-08

This is a research survey, not an endorsement or independent benchmark.
It distinguishes background-noise suppression, reconstruction of degraded
human voice, and extraction of singing from music. A traditional
spectral gate cannot reliably solve all three.

## Candidates

| Project | Role | CueWire path |
|---|---|---|
| DeepFilterNet (Rikorose) | 48 kHz speech noise suppression, Rust and LADSPA/PipeWire | First real-time candidate. LADSPA: 20 ms minimal STFT latency + host latency. Source MIT/Apache-2.0; check checkpoint terms separately. |
| ClearerVoice-Studio (modelscope) | MossFormer2_SE_48K speech enhancement, plus separation/super-resolution | First offline real-voice A/B candidate. Python/PyTorch. Weights may download at first use. Repo Apache-2.0; verify checkpoint terms. |
| Resemble Enhance | Two-stage denoiser and enhancer/restorer | Compare denoise-only against full enhancement; vocoder can change timbre. MIT code. |
| VoiceFixer | Archival speech noise/echo/bandwidth/clipping correction | Offline quality baseline; can synthesize artifacts. |
| RNNoise (xiph) | Small real-time speech noise model | Lightweight streaming baseline. BSD-3 code; pretrained weight terms need checking. |
| Demucs (Meta) | Music separation, including vocals | For rock/music source separation first, NOT for ordinary mic noise. Archived project; MIT code. |
| Facebook Speech Denoiser | Research causal Demucs speech denoising | Historical baseline only; archived, CC-BY-NC 4.0. |

References:
- https://github.com/Rikorose/DeepFilterNet
- https://github.com/Rikorose/DeepFilterNet/blob/main/ladspa/README.md
- https://github.com/Rikorose/DeepFilterNet/issues/712
- https://github.com/modelscope/ClearerVoice-Studio/blob/main/clearvoice/README.md
- https://github.com/resemble-ai/resemble-enhance
- https://github.com/haoheliu/voicefixer
- https://github.com/xiph/rnnoise
- https://github.com/facebookresearch/demucs
- https://github.com/facebookresearch/denoiser

## Reproducible evaluation

Use the same original mono 48 kHz WAV, align timing, hold headphone
volume constant and compare at matched output loudness too.
Preserve exact command, git SHA, model SHA-256, dependencies, operating
system, device, output SHA-256, sample peak, RMS and runtime/latency.
With a known clean reference, compare SI-SDR (delay-aligned) and
intelligibility (WER, with real transcripts) and listen. Without a
clean reference, claims about restored intelligibility must rely on
listening, not SI-SDR inferred from thin air.

Use dedicated opt-in environments to test models. First clearvoice
test can use this published Python interface:

    from clearvoice import ClearVoice
    model = ClearVoice(task="speech_enhancement",
                       model_names=["MossFormer2_SE_48K"])
    model(input_path="input.wav", online_write=True, output_path="cv-results")

The model may download weights automatically at first inference. Never
trigger model downloads, persistent background processing or changes to
default audio devices from normal CueWire startup. Do not commit
private user speech to this public repo.

DeepFilterNet also supports a standalone 48-kHz WAV CLI:

    deep-filter --out-dir df-results input.wav

For rock/singing, test Demucs vocal extraction first, then enhancement.
For clean microphone speech, do not add music separation at all.
Noise-bed is an optional aesthetic mask, not speech reconstruction.

Models may have different checkpoint distribution rights than source
licenses. In particular, DeepFilterNet checkpoint redistribution
scope remains the subject of a 2026 public clarification request.
