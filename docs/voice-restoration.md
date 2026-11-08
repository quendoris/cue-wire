# CueWire voice restoration playground: deliberately difficult audio

Status: an **offline, deterministic DSP prototype**. This is NOT a certified
studio-quality restoration system and NOT an isolated singing-vocal extractor.
The current live CueWire run/ctl infrastructure is unchanged; no new background
process, persistent PipeWire node, autostart or system volume change.

## 1. Self-contained first run (no external audio dependencies)

    cargo test --locked --offline --all-targets
    cargo run --locked --offline --example voice_restore -- --out voice-preview

This creates these files, without starting audio playback:

    voice-preview/voice_reference.wav  # original synthetic vocal-like sound
    voice-preview/voice_before.wav     # echo + hum + guitar-like bleed + noise + saturation
    voice-preview/voice_after.wav      # adaptive restoration
    voice-preview/report.json          # parameters, metrics, SHA-256 hashes

Listen to the three files with pw-play, manually, at the same comfortable
headset volume. They are mono PCM16 at 48 kHz. The reference is a deterministic
VOCAL-LIKE synthesizer, not actual intelligible speech. It exists so the whole
suite runs without downloading a copyrighted recording or large ML models.

## 2. A REAL voice and reproducible degradation

A mono 48 kHz signed PCM16 WAV is required. To convert a real speech or singing
recording that you have rights to use, explicitly record the conversion:

    ffmpeg -nostdin -i ORIGINAL.flac -ac 1 -ar 48000 -c:a pcm_s16le clean.wav

For an optional freely synthesized real spoken-word fixture (espeak and ffmpeg
must be installed separately):

    espeak -w /tmp/cue-speech.wav "The signal is faint, but the words must survive"
    ffmpeg -nostdin -i /tmp/cue-speech.wav -ac 1 -ar 48000 -c:a pcm_s16le clean.wav

Make an intentionally bad speech fixture with deterministic interference:

    python3 tools/voice_fixture.py --clean clean.wav --out voice-fixture --seed 20261108
    cargo run --locked --offline --example voice_restore -- --input voice-fixture/voice_bad.wav --out voice-run

The fixture manifest records input and damaged SHA-256 plus exact degradation
parameters; the restoration manifest includes SHA-256 for the processed
input and output. Record the original conversion tool version and exact
Git commit for fully repeatable evidence. Floating-point bit equality across
architectures and tool versions is NOT guaranteed; repeatability is checked
on the same toolchain/platform in CI.

## 3. Adaptation and manual overrides

Parameters are per-file and do not create ongoing profiles.

- Adaptive offline high-pass: a conservative 45/65 Hz heuristic with
  forward-backward application and no net phase rotation; override with
  --highpass-hz 60. Noncausal offline filtering is NOT suitable for a
  real-time callback and may still reshape low harmonics.
- Noise PSD: estimate from the quietest nonzero STFT frames selected by
  --profile-fraction (default 0.18). Spectral power subtraction --strength 1.7,
  floor 0.12, smoothed across bins and frames.
- Silence expander: only enabled if an estimated contrast exists; default
  quiet-section attenuation 14 dB and a threshold 7 dB above noise floor.
  It does NOT identify words or distinguish guitars from singing.
- RMS leveling: default target -26 dBFS, max boost +7 dB, fixed bounds and
  smoothing based on the earlier mixer experiment.
- Sample peak protection: if needed, uniform downward scale to <= -3 dBFS
  sample peak. This is NOT a true-peak inter-sample limiter.
- Noise bed: white noise -78 dBFS RMS by default, added LAST and seeded.
  It creates a very low artificial floor; it does NOT repair artifacts,
  dereverberate audio, or improve speech intelligibility. Disable with
  --bed-off. If audible, reduce it or disable it.
- All stages are deterministic given identical input, executable and seed.

Override, for example:

    cargo run --locked --offline --example voice_restore -- \
      --input voice-fixture/voice_bad.wav --out voice-tuned \
      --strength 2.1 --highpass-hz 90 --target-dbfs -27 \
      --max-boost-db 6 --expander-db 18 --bed-dbfs -86 --seed 222

To compare multiple samples, preserve each original/bad/after WAV and its
report.json in a separate output directory. NEVER overwrite the same
reference during tuning. Keep gain and playback volume unchanged between
listening comparisons, and also listen loudness-matched (otherwise louder
material can seem subjectively better).

## 4. Reproducibility, tests and limitations

- Independent Rust FFT tests check a round trip to < 1e-9, conjugate symmetry.
- Rust tests cover malformed WAV, invalid config, NaN/Inf, finite outputs,
  seeded deterministic output and reduction of an isolated noise interval.
- CI renders the voice demo twice and compares generated SHA-256 values plus
  WAV headers and sample-peak ceiling; original CLI and fake PipeWire
  lifecycle tests are preserved.
- Metrics are RMS dBFS and sample peak, NOT EBU R128/ITU-R BS.1770 LUFS,
  true-peak level, perceptual intelligibility, MOS or guaranteed separation.
  Loudness- and content-matched A/B tests with human voices are still required.
- Musical instruments overlap speech in time and frequency. Spectral
  noise suppression CANNOT reliably separate a vocal from dense rock
  guitars; once phonemes are clipped, masked or lost they may be unrecoverable
  without a speech/music source-separation ML stage, and even then not
  reliably or perfectly.
- 50 Hz hum is partly attenuated by HPF; no automatic harmonic de-hum,
  de-esser, adaptive dereverberation, neural voice isolation or measured
  latency exists in this slice.
- Autodetection can harm speech if low-energy phonemes are mistaken for
  noise. Always compare against raw recording and preserve the input.

The module currently allocates whole-recording FFT buffers and belongs
OFFLINE ONLY. Before a live microphone extension, create preallocated
realtime DSP with bounded callback timing, sample-rate conversion,
model latency and a fault-injection suite. Do not silently run this
whole-file algorithm in a PipeWire audio callback.

For diagnosis only, independently bypass the four stages with
--no-highpass, --no-denoise, --no-expander, --no-leveler. These flags
are recorded in report.json to prevent accidental comparisons of
unlike processing chains.
