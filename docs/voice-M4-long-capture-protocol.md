# CueWire — M4: long-form voice capture and acceptance protocol

Status: **approved research plan, not a new recording nor a new live DSP path**.
Voice research has resumed after the successful NumPad H2 log-only trials.
For the new loudspeaker echo-cancellation protocol, see
[voice-M5-acoustic-echo-cancellation.md](voice-M5-acoustic-echo-cancellation.md).
The M4 raw-recording baseline and immutable M1 cleanup chain remain intact.

## Frozen restoration baseline M1 (DO NOT CHANGE)

The validated short-audio winning chain is exactly:

    RAW microphone / noisy input
        -> DeepFilterNet (deepfilter)
        -> DPDFNet2 Soft (dpdfnet2_soft; 12 dB attenuation limit)
        -> cleaned voice

Original source benchmark commit:
`017e60b16f2091a709a41e01f1f2852b7286b2e9`.
Original model-output SHA256:
`5f98dff81d7445593fcced5842eda357fe437a1e26752a11bfeacf29d4517e42`.

**Playback volume normalization is separate:** the later blind
listening kit used gain `4.724004688446429` to match volume, and this
is **not a neural processing stage**. Do not hardcode that gain in a
production microphone chain. It must be calibrated to real mic headroom.

Private full verification:
https://github.com/quendoris/cue-wire-voice-bench/blob/main/reports/M1-exact-chain-and-test-comparison.json

To replay bit-identically, still capture model checkpoint and
executable hashes, versions, resampling/format and parameter flags.
The source benchmark proved the model order and output WAV hash, not
a complete model-weights freeze.

## Goal of the new long-form session

Determine whether the M1 restoration is robust to changing microphone
angle/distance, speaking level, plosives, sibilants, room noise and
quiet pauses; then objectively compare studio postproduction across
both M1-cleaned and naturally good unprocessed microphone speech.
Do not infer from the previous 3-second result that a model works
equally well on an extended recording.

**Capture the RAW mic once per take** and retain it without
denoising, AGC, voice isolation, EQ, compressor, echo cancellation,
noise gate or background filtering. Recording a processed virtual
source instead of the raw physical mic invalidates this experiment.

Use manually selected PipeWire physical source and explicitly named
output path. Never switch system default source/sink, write
PipeWire/WirePlumber configuration, start services, or capture system
playback. Never enable monitoring of raw mic in the headset unless
needed for a separately protected test (feedback risk).

## Controlled recording setup

- 48 kHz mono, lossless PCM (prefer PCM24 if hardware supports it;
  produce PCM16 derivatives for current M1 adapters only).
- Same microphone, input gain, room, pop filter, sample rate and
  recording geometry for the entire first take. Disable **software**
  AGC, environmental noise reduction and auto-leveling for this
  controlled capture when possible; record any unavoidable hardware DSP.
- Baseline microphone distance approximately **20 cm** at a slight
  off-axis angle, then other distances at fixed measured marks.
  Avoid direct breath flow into capsule during near-field speech.
- Record the setup in a **local private manifest**: UTC timestamp,
  physical mic/source node, audio interface, selected rate/bit depth,
  input gain, operator-measured distances, room noise sources, any
  noise reduction/AGC, sample count, recording WAV SHA256.
- No calibration changes **during** a take. If gain must be changed,
  start a new labelled take with a separate manifest and WAV.

## Spoken script and movement timeline (~80–100 seconds)

The following text is **originally authored for CueWire**.
Use it as a repeatable script; spoken words should not be improvised
between repeated takes when comparing processing. Segment boundaries
are approximate; absolute seconds depend on reading cadence.

| Phase | Approx. time | Position and action | Spoken content / observation |
|---|---|---|---|
| A | 0–5 s | Steady at 20 cm, silence | Room noise floor; no clicking or moving |
| B | 5–25 s | 20 cm, head straight, ordinary voice | “Сегодня я проверяю запись голоса. Я говорю спокойно, без спешки, стараюсь произносить слова чётко. Мне важно, чтобы даже после обработки остались мой тембр, дыхание и естественные окончания фраз.” |
| C | 25–37 s | Stay at 20 cm, varied consonants | “Саша шёл через шуршащий сад. Шесть щенков счастливо шепчут. Папа попросил положить пакет, потом проверить пять приборов.” |
| D | 37–49 s | Gradually turn head 45° left, center, 45° right | “Теперь я медленно поворачиваю голову. Мой голос меняет направление, но громкость и произношение остаются обычными.” |
| E | 49–62 s | Marked distance: 10 cm, then 40–60 cm, smooth return | “Сейчас я говорю немного ближе. Затем я отодвигаюсь дальше от микрофона. В конце возвращаюсь в исходное положение.” |
| F | 62–77 s | Return 20 cm, speak softly, ordinary, then expressive/louder without shouting | “Это тихая фраза. Это обычная фраза. А теперь — энергичная фраза, но без крика и перегрузки микрофона.” |
| G | 77–87 s | 20 cm, short silence, then final phrase and silence | “Последняя фраза нужна для проверки эха и затухания на конце записи.” Keep 3–5 s silence after it |

The operator should announce head positions and measured distance in
the PRIVATE run manifest. Avoid turning the head so suddenly that
the movement itself generates cable/desk/mechanical noise unless that
stress is the intended test.

A **second** near-identical take is useful for repeatability.
If one audio session is noisy on purpose, record it separately as a
distinct labelled condition, not by silently modifying the raw WAV.

## Processing and comparison matrix

Use *one* raw capture R0 and derive immutable variants:

1. **R0 — raw microphone take** (never overwritten or normalized in place).
2. **R1 — M1 denoising only**: DeepFilterNet -> DPDFNet2 Soft.
3. **R2 — raw + studio EQ/level/de-ess/compression only**, NO M1.
4. **R3 — M1 then studio stage**, exactly the same studio settings as R2.
5. **Control**: unchanged PCM output at a gain-matched listening level.
6. Optional stage ablations: DeepFilterNet alone, DPDFNet2 Soft alone.

The old `test.wav` is a DIFFERENT recording. It is **not** a matched
long-recording clean reference; do not claim SI-SDR against it is
meaningful for a different utterance.

Quantitative checks: PCM frames and alignment, sample/estimated true
peaks, clipped samples, noise during known silent intervals,
loudness over extended speech, dynamics/crest factor, changing level
as head turns or distance changes, end-of-clip ringing/reverb, latency,
and hash-verifiable source identity.

Human blind listening criteria: natural timbre, clarity of sibilants
and plosives, intelligibility at distance, pumping/noise between words,
constant perceived volume, no metallic sound, no altered speaker
identity or unexpected generated phonemes. Compare at the same
playback loudness so louder does not automatically win.

**Acceptance:** R1 must preserve the familiar M1 naturalness during
A/B, not merely improve numerical SNR. R3 must be independently
preferred over R1; otherwise studio processing is rejected. Do not
promote heavy AI models on the basis of small 3-second synthetic tests.

## Non-negotiable safety / retention

- Original audio stays on operator's Arch machine. Share only via a
  deliberate private upload with explicit UPLOAD confirmation.
- Store WAVs and SHA256 reports **outside public Git** (under
  `.audit/` or a ignored `bench-*` directory).
- One active foreground command only. No autostart, permanent daemons,
  default source/sink switching, PipeWire config modification or
  global key hooks.
- Super+C in CueWire's future hotkeys module must forcefully stop
  all CueWire-owned processes without harming system PipeWire,
  unrelated apps, or changing lasting system state. Do not claim
  this works until tested on real Hyprland and with hung children.
- Two takes and their outputs must be tagged independently and
  never overwrite the short M1 benchmark or its private milestone.

## Work order (after completing HOTKEYS)

1. Verify exact model versions/weight checksums in frozen M1.
2. Test recording capture with raw physical PipeWire source, no
   reconfiguration; produce short diagnostic WAV and SHA.
3. Record both long-form controlled takes.
4. Process raw R0 through the M1 chain to produce R1.
5. Compare M1 stability across positions and speaking levels.
6. Compare restrained studio processors on R0 and R1 separately.
7. Blind listen; retain/iterate only actual improvements.
8. Optional integration into terminal-only CueWire runtime *after*
   real hardware, latency and child cleanup acceptance tests.
