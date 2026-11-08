# CueWire: bounded RMS leveler playground

This is a **second independent offline module**, built on the PCM mixer.
It does not start virtual devices or change the current v0.1 foreground
PipeWire graph, microphone, monitor or system output settings.

## Listening comparison

    cargo test --locked --offline --all-targets
    cargo run --locked --offline --example dynamics_preview -- --out dynamics-preview
    pw-play dynamics-preview/music_raw.wav
    pw-play dynamics-preview/music_leveled.wav

The demo creates ONE 16-second mono track with drums, bass, high-hat and
a plucked melody. Four successive sections intentionally use relative gains
of -12, -4, 0 and -9 dB. The two WAVs contain the identical composition:
the first is unprocessed, the second passes through an RMS leveler.
A third file, levels.csv, contains independent one-second RMS readings.

Use the SAME comfortable headphone volume for both passes (e.g. the
user's 60% setting). Begin at a safe low level if uncertain. **60% is not
an acoustic calibration or a dBFS target.** The program never changes
the system or headphone volume.

Try a broader band and slower adaptation:

    cargo run --locked --offline --example dynamics_preview -- \
      --out dynamics-preview --target-dbfs -28 --range-db 5 \
      --attack-ms 70 --release-ms 650 --max-boost-db 7

## Mathematical/implementation contract

- Detector: sliding unweighted 350 ms RMS, NOT a perceptual EBU R128/BS.1770
  or LUFS measurement. Configurable target = -26 dBFS and tolerance
  = +/-3 dB by default. The range argument is its HALF-WIDTH.
- Desired gain moves only if the current detected output estimate is outside
  the band. Gain changes are smoothed in dB; gain reduction is faster than
  boost (attack 45 ms, release 450 ms).
- Boost capped at +9 dB and reduction at -18 dB by default. Input levels
  below -55 dBFS HOLD gain instead of boosting every silence/background.
  This is a gain-hold rule, not an actual noise gate or voice activity detector.
- The processor does not clip, peak-limit or resample. Overloads are counted,
  and the preview refuses to silently quantize values beyond PCM full-scale.
- A fixed-size history buffer is allocated on construction. During
  process_block: no allocation, no I/O, no threads, no PipeWire calls.
  Buffers and state remain unchanged if invalid input is detected.
- Band compliance is a target, NOT an absolute guarantee for every
  instantaneous/one-second level: silence, dynamics, transient peaks,
  detection lag, gain caps and headroom can push signal outside the band.
- Output is 48 kHz mono, signed PCM16. No automatic playback.

## Next experiments

- Compare music transients and audible pumping at several attack/release
  settings and use levels.csv for objective RMS comparisons.
- For a real microphone, independently tune silence/voice detection and
  test with recorded speech; silence must not be boosted into hiss.
- Later benchmark perceptually weighted/gated LUFS and implement a separate,
  measured true-peak limiter; avoid confusing RMS steadiness with safety.
- DSP cannot compensate for changes in headphone fit or external acoustics
  when the user moves their head. Those require physical/acoustic measures.
- Actual live PipeWire callback timing, reconnect behavior, microphone and
  headset routing, and Super+C lifecycle have not been verified by this demo.
