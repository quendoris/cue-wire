# CueWire: isolated PCM mixer playground (first slice)

The first slice deliberately does NOT create a virtual microphone or touch
PipeWire, WirePlumber, system defaults, audio devices, services, or hotkeys.
It introduces an independent 48 kHz **mono, f32 PCM** mixer and a reproducible
offline hearing test. The current run / ctl code remains unchanged.

## Listen (on Arch Linux)

    cargo test --locked --offline --all-targets
    cargo run --locked --offline --example mixer_preview -- --out mixer-preview
    pw-play mixer-preview/noise.wav
    pw-play mixer-preview/beat.wav
    pw-play mixer-preview/mix.wav

The preview synthesizes 12 s of two independent sources: seeded white noise
and a 120 BPM kick/snare rhythm. It writes mono PCM16 WAV at 48 kHz. The two
stem WAVs have their individual gain already applied, so their linear sum
approximates the mixed WAV to PCM16 rounding precision.

To vary the two source levels independently:

    cargo run --locked --offline --example mixer_preview -- \
      --out mixer-preview --noise-db -24 --beat-db -3 --seconds 12 --seed 42

The output path is explicit; output WAV files are ignored by Git. No audio
is played automatically. The user invokes pw-play manually, and can stop it
with Ctrl+C. The test does not select or modify any default audio device.

## Engineering contracts

- mix_block() is pure sample-aligned linear summation with independent
  **amplitude** gains; -6.0206 dB is approximately 0.5 amplitude.
- Input signals must already use the SAME sample rate, channel layout and
  timing. Resampling, channel conversion and clock reconciliation are NOT
  implemented here.
- No hidden clipping, averaging, loudness normalization, limiter or makeup
  gain occurs. Stats report pre-quantization peak and samples exceeding 1.0.
- Before writing PCM16 the WAV encoder rejects NaN, Inf and amplitudes
  outside [-1.0, 1.0]. The preview fails loudly if mixed audio overloads.
- A two-pass preflight rejects invalid input without partially writing the
  caller-owned output buffer; processing is allocation-free inside mix_block.
- Noise generation is deterministic but NOT suitable for cryptography.
- The sample sources and WAV encoder are **offline tooling**, not proposed
  work for the future real-time callback.

## Future integration gates

First compare generated individual stems and mixed WAV by listening and by
independent sample analysis. Next introduce a live buffer scheduler and
sample-clock alignment, then test a single owned PipeWire output path.
Only after those checks connect physical microphone capture and a CueWire
virtual source; test cleanup, unplug/replug and device changes separately.

This change neither replaces nor certifies the existing v0.1 cue routing.
No latency, glitch rate, synchronization with monitor playback, or real
hardware functionality is claimed by these offline unit checks.
