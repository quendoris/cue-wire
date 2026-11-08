# CueWire M5 — controlled acoustic echo experiment

## Hypothesis

A known digital signal sent to a real loudspeaker can be used to estimate the loudspeaker → room → microphone acoustic path. First five seconds: music (including other people's vocals), **no local speech**. Then, while the music continues, the user reads a short passage. The calibrated echo-path FIR is frozen during the near-end speech to avoid double-talk contamination.

Model: mic[n] = local_voice[n] + sum_k h[k] * reference[n-k] + noise[n]. A pilot residual is mic[n] - estimated_echo[n].

This is **acoustic echo cancellation**, not semantic voice separation. The digital reference includes the foreign voices, so their acoustic leakage may be cancelled without identifying those voices.

## Boundaries

- **Offline research only**. Never routes the result to the game, speaker or virtual mic.
- Preserves RAW, CLEAN-003, LEVEL-001 and LEVEL-002. New files go only into private .audit/voice-aec.
- Reference is the PCM16 mono WAV *actually passed to pw-play*, not a hardware output monitor or sample-clock-locked stream. Playback latency must be estimated.
- Use an explicitly chosen physical speaker sink and ALSA mic; never CueWire's virtual sink or default/auto source.
- No other system sounds may use this speaker during the pilot; they would be missing from the reference.
- Keep speaker/mic stationary and volume constant. Comfortable low volume first.
- Five seconds of silence means **no local voice**; the speaker itself continues playing vocals/music.
- The analyzer trains on the first ~3.3 seconds of actual playback, reserves 3.45–4.40 seconds for held-out verification, and rejects a model that does not improve held-out echo by at least 1 dB.
- Only a frozen linear ~120-ms filter is fitted, no adaptation when the user speaks. No guarantees of perfect speech preservation, nonlinear/clock-drift compensation, or live AEC.
- The music recording and microphone WAV remain private; no upload.

## Step 1: inspect devices

Run on Arch:

~~~bash
cd /data/cue-wire
git pull --ff-only
python3 tools/voice_aec_inspect.py
arecord -l
~~~

From the inspect report take **the exact speaker output_sinks node.name**. The historical MICAudio97i microphone is likely at ALSA hw:0,0, but inspect will verify before each capture. If the actual card is on another ALSA index, use that observed index and expected card ID.

Choose an FFmpeg-readable local WAV/FLAC/MP3 song with other people's voices, at least 20 seconds in length. The code generates and plays an explicit mono 48-kHz PCM16 reference so the samples are known exactly.

## Step 2: record speaker plus physical mic

~~~bash
cd /data/cue-wire
mkdir -p .audit/voice-aec
python3 tools/voice_m5_capture.py \
  --music "/absolute/path/to/music-with-voices.wav" \
  --speaker "EXACT_PHYSICAL_SINK_NODE_NAME" \
  --mic hw:0,0 \
  --expect-card-id MICAudio97i \
  --seconds 20 \
  --calibration-seconds 5 \
  --volume 0.30 \
  --out .audit/voice-aec/M5-001
~~~

The program verifies physical ALSA card identity, physical sink, known reference file, and microphone capture bytes before playing anything. It records the mic through arecord and sends the reference to the explicit speaker via pw-play; it does NOT change system defaults, routes, WirePlumber or create monitor streams.

During the first five seconds of playback: **do not speak**. When the terminal prints CALIBRATION COMPLETE — NOW READ YOUR TEXT!, start reading while the music continues:

> «Сегодня я проверяю, насколько точно микрофон отделяет мой голос от звука колонки. Сейчас я говорю обычным голосом, затем чуть тише, а потом немного громче. Колонка продолжает воспроизводить музыку, но моя речь должна оставаться разборчивой, естественной и полной.»

The playback finishes after ~20 seconds; the capture stops automatically. Results:

- .audit/voice-aec/M5-001/reference-sent.wav — exact prepared digital signal sent to the output stream
- .audit/voice-aec/M5-001/microphone-raw.wav — unprocessed real microphone capture
- .audit/voice-aec/M5-001/manifest.json — SHA256, devices and timing metadata
- local logs of ALSA, playback, and reference preparation

If interrupted, process children are terminated and prior evidence is kept. Every attempt uses a **new** output directory.

## Step 3: fit acoustic transfer, validate, then subtract

Use the dedicated voice-lab Python environment with NumPy/SciPy:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -c 'import numpy, scipy; print(numpy.__version__, scipy.__version__)'

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --capture .audit/voice-aec/M5-001 \
  --out .audit/voice-aec/M5-analysis-001
~~~

The analyzer estimates unknown mic-to-digital offset by FFT correlation, fits a regularized acoustic FIR on speaker-only training seconds, reports ERLE on a **separate held-out speaker-only interval**, then freezes the FIR for the entire double-talk interval. If correlation/validation fail, it does not create subtraction WAVs.

Outputs after accepted engineering preflight:
- M5-analysis-001/aec-candidate-float32.wav — experimental subtraction
- M5-analysis-001/speaker-echo-estimate-float32.wav — predicted speaker leakage
- M5-analysis-001/manifest.json and SHA256SUMS

The candidate is not automatically endorsed as usable voice: listen during simultaneous music and local speech for consonant loss and artifacts. Do not start DeepFilterNet/DPDF processing in front of this calibration, because that changes the physical echo waveform we are trying to identify.

## Step 4: check and listen

~~~bash
cd /data/cue-wire/.audit/voice-aec/M5-analysis-001
sha256sum -c SHA256SUMS
pw-play ../M5-001/microphone-raw.wav
pw-play aec-candidate-float32.wav
~~~

The previous accepted 03-voice-leveled.wav stays untouched. Look at calibration-correlation, estimated lag, training and held-out ERLE, and actual voice quality. A failed/weak result is valid scientific evidence; do not disable protective thresholds to force a pass.

## Synthetic tests

~~~bash
cd /data/cue-wire
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover -s tests -p 'test_voice_m5_aec.py' -v
~~~

These are **synthetic** tests only. Real audio synchronization, sink identity, device levels, system mixing, double-talk quality and hardware acceptance require the user's physical experiment.

## Future real-system output reference

The user specifically distinguishes the signal sent to the speaker from acoustic sound emitted by its drivers. In a later iteration the best available reference may be an explicit **post-mix PipeWire sink monitor**, including actual game/cue streams, not just a single test file. Existing voice_aec_inspect.py can discover monitor nodes read-only. The existence of a monitor does not prove clock synchronization or capture at the exact DAC boundary.

Do not alter default source/sink, monitor the mic into headphones, or install a persistent echo-cancellation service during this pilot.
