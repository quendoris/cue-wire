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

## Playback startup policy (revision M5-002)

The original M5-001 attempt rejected the microphone **before** any sound
played because `arecord` exited during a three-second ALSA readiness
wait. That wait has been removed: after verifying devices and preparing
the known PCM reference, CueWire now launches physical microphone capture
and **immediately launches the speaker playback process**, back-to-back.
The first five seconds are timed from the player launch, not from the
shell command or FFmpeg preprocessing.

There is no guarantee that the physical DAC produces its first sample
at that exact moment; Pulse/PipeWire buffering and device latency remain
part of the later alignment estimate. Starting playback without waiting
also means the microphone *might* start too late or fail. The capture
checks for microphone PCM bytes within one second and aborts the playback
on any recorder error. Such a failed attempt is not used as an AEC
reference or calibration.

The previous `M5-001` directory is immutable failure evidence. Inspect
the actual cause before assuming the startup order alone was responsible:

~~~bash
cd /data/cue-wire
tail -n 35 .audit/voice-aec/M5-001/arecord.log
~~~

An error such as `Device or resource busy` could indicate an
exclusive ALSA-hardware conflict. Do not kill or disable PipeWire
services to work around it; identify the actual holder and decide
separately whether capture should use the PipeWire physical-source node.
The new capture prints the end of `arecord.log` on failure.

For this changed behavior, use **M5-002**, never overwrite M5-001.

## M5-005: avoid false rejection from in-progress WAV buffering

M5-004 selected the correct physical PipeWire microphone and launched
music through the intended speaker. It stopped at one second with
`Microphone has not written PCM after 1 second`. The `pw-record.log`
tail did not report a process error: it showed only command arguments.

That observation does **not** distinguish a genuinely inactive input
from delayed/ buffered WAV writes. The one-second file-size gate was
premature and has been removed. The new protocol:

1. Launch microphone recorder and speaker back-to-back (same as M5-004).
2. While both processes run, watch for actual recorder **process exit**;
   any early exit aborts and stops playback. File size is advisory only.
3. Prompt for the local speech sample after five seconds of playback
   even if the live WAV has not visibly grown yet.
4. Stop the recorder gracefully at the end, allow WAV header finalization,
   then verify **48-kHz mono PCM16**, at least requested duration minus
   one second, non-negligible actual RMS (above -85 dBFS), and SHA256.
5. If the resulting file is absent, too short, effectively silent or
   corrupted, reject the entire experiment. No AEC model is trained.

This is **not** proof that the USB device is healthy: the final WAV
measurement decides. M5-001..M5-004 remain failed archives.

Before repeating, inspect whether M5-004 even produced a file:

~~~bash
cd /data/cue-wire
ls -lh .audit/voice-aec/M5-004/microphone-raw.wav
tail -n 20 .audit/voice-aec/M5-004/pw-record.log
~~~

Either command may legitimately report a missing file.

### Full retry with the verified physical mic (new M5-005)

~~~bash
cd /data/cue-wire
git pull --ff-only
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover -s tests -p 'test_voice_m5_aec.py' -v

python3 tools/voice_m5_capture.py \
  --music '/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --capture-backend pipewire \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --seconds 20 --calibration-seconds 5 --volume 0.30 \
  --out .audit/voice-aec/M5-005
~~~

After a successful recording:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --capture .audit/voice-aec/M5-005 \
  --out .audit/voice-aec/M5-analysis-005
~~~

This produces a *candidate* only after the held-out acoustic calibration
passes; the user's near-end voice still requires listening review.

## M5-004: direct ALSA read error and explicit PipeWire backend

The first real M5 attempts did **not** produce a valid paired recording:
`arecord -D hw:0,0` printed `pcm_read:2285: read error: Input/output error`.
M5-003 did successfully get as far as launching the speaker stream but
physical ALSA capture failed during playback. The green tests and an ERLE
of ~18.94 dB observed in the terminal were from **synthetic fixture
audio**, not a measurement of the real speaker.

The prior direct-ALSA capture worked in a different M4 session, so do
not infer permanent hardware failure. EIO alone does not identify whether
the cause is a transient USB/driver issue, a lost stream, a capture
configuration, an audio-session conflict, or another fault.

M5 capture now supports a **deliberate** choice of backend:
`--capture-backend alsa` retains the original verified `hw:0,0`;
`--capture-backend pipewire --source EXACT_ALSA_INPUT_NODE` uses
`pw-record` on a **specific** physical `alsa_input.*` node, never on
a default input, output monitor, or CueWire virtual microphone.
The program never silently falls back to another device/backend.

### Inspect actual mic sources

~~~bash
cd /data/cue-wire
python3 tools/voice_raw_capture.py inspect
pactl list short sources
~~~

Find the exact `alsa_input.*` source that corresponds to the physical
USB microphone (not any `.monitor` entry). Do not guess its name.

### Mic-only PipeWire probe, BEFORE playing music

Replace the sample placeholder with the source name you actually saw:

~~~bash
cd /data/cue-wire
pw-record \
  --target="EXACT_ALSA_INPUT_NODE" \
  --rate=48000 --channels=1 --channel-map=mono --format=s16 \
  .audit/voice-aec/pw-probe-001.wav
~~~

Allow about 3–5 seconds of recording; then press Ctrl+C so `pw-record`
closes the WAV. No system routes/defaults/settings are changed.

To check that the file contains PCM:

~~~bash
python3 -c 'import wave; w=wave.open(".audit/voice-aec/pw-probe-001.wav","rb"); print(w.getframerate(),w.getnchannels(),w.getsampwidth(),w.getnframes()); w.close()'
~~~

Do not run the 20-second acoustic experiment until the mic-only test
produces actual 48 kHz, mono PCM16 frames. If `pw-record` also fails,
keep its stderr and inspect the device/driver separately rather than
disabling PipeWire services.

### Full capture using PipeWire (after successful mic probe)

~~~bash
cd /data/cue-wire
~/.venvs/cuewire-voice-lab/bin/python -m unittest discover -s tests -p test_voice_m5_aec.py -v

python3 tools/voice_m5_capture.py \
  --music '/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --capture-backend pipewire \
  --source 'EXACT_ALSA_INPUT_NODE' \
  --seconds 20 --calibration-seconds 5 --volume 0.30 \
  --out .audit/voice-aec/M5-004
~~~

The source name is intentionally left as a placeholder: user-specific
USB node IDs have not been observed. All actual device choices must be
verified from the local inspection output.

Playback is still launched immediately after the recorder process
(no three-second waiting). Failure before/during capture aborts the
paired experiment, stops the player and records the current
`pw-record.log` tail in the failed manifest. A successful result
records the selected capture backend/node in the manifest.

The first five seconds of the **speaker playback** must contain no local
speech; after the terminal prompt, read the test passage naturally.
M5-001/M5-002/M5-003 are failed evidence and must not be overwritten.

For a successful M5-004 capture, the subsequent analysis command is:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --capture .audit/voice-aec/M5-004 \
  --out .audit/voice-aec/M5-analysis-004
~~~

This is not a promise that PipeWire capture will work. It is a
controlled attempt to isolate whether direct-ALSA read failures can be
bypassed while keeping the microphone identity explicit.

## Legacy ALSA-only recording example (not recommended while EIO persists)

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
  --out .audit/voice-aec/M5-005
~~~

This example deliberately selects the direct ALSA backend. Run it only after independently fixing the confirmed `pcm_read` EIO condition. For the immediate follow-up use the explicit PipeWire M5-004 instructions above. The program verifies physical ALSA card identity, physical sink and the known reference file before trying the streams. It records the mic through arecord and sends the reference to the explicit speaker via pw-play; it does NOT change system defaults, routes, WirePlumber or create monitor streams.

During the first five seconds of playback: **do not speak**. When the terminal prints CALIBRATION COMPLETE — NOW READ YOUR TEXT!, start reading while the music continues:

> «Сегодня я проверяю, насколько точно микрофон отделяет мой голос от звука колонки. Сейчас я говорю обычным голосом, затем чуть тише, а потом немного громче. Колонка продолжает воспроизводить музыку, но моя речь должна оставаться разборчивой, естественной и полной.»

The playback finishes after ~20 seconds; the capture stops automatically. Results:

- .audit/voice-aec/M5-005/reference-sent.wav — exact prepared digital signal sent to the output stream
- .audit/voice-aec/M5-005/microphone-raw.wav — unprocessed real microphone capture
- .audit/voice-aec/M5-005/manifest.json — SHA256, devices and timing metadata
- local logs of ALSA, playback, and reference preparation

If interrupted, process children are terminated and prior evidence is kept. Every attempt uses a **new** output directory.

## Step 3: fit acoustic transfer, validate, then subtract

Use the dedicated voice-lab Python environment with NumPy/SciPy:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -c 'import numpy, scipy; print(numpy.__version__, scipy.__version__)'

~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --capture .audit/voice-aec/M5-004 \
  --out .audit/voice-aec/M5-analysis-004
~~~

The analyzer estimates unknown mic-to-digital offset by FFT correlation, fits a regularized acoustic FIR on speaker-only training seconds, reports ERLE on a **separate held-out speaker-only interval**, then freezes the FIR for the entire double-talk interval. If correlation/validation fail, it does not create subtraction WAVs.

Outputs after accepted engineering preflight:
- M5-analysis-004/aec-candidate-float32.wav — experimental subtraction
- M5-analysis-004/speaker-echo-estimate-float32.wav — predicted speaker leakage
- M5-analysis-004/manifest.json and SHA256SUMS

The candidate is not automatically endorsed as usable voice: listen during simultaneous music and local speech for consonant loss and artifacts. Do not start DeepFilterNet/DPDF processing in front of this calibration, because that changes the physical echo waveform we are trying to identify.

## Step 4: check and listen

~~~bash
cd /data/cue-wire/.audit/voice-aec/M5-analysis-004
sha256sum -c SHA256SUMS
pw-play ../M5-004/microphone-raw.wav
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
