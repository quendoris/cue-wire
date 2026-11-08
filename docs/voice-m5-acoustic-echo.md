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

## M5 hardware recovery: alternate powered hub (2026-11-08)

The user could not connect the microphone directly to the laptop because
its onboard USB ports were already occupied. Two external hubs were
available: an inexpensive hub and a higher-quality, separately powered hub.

Moving **only** the MICAudio-97i microphone to the other, separately
powered hub restored recording through the existing PipeWire
`pw-record` path. The completed `mic-isolated-002.wav` contains
**181,248 frames at 48,000 Hz = 3.776 seconds**, rather than zero
frames. This is an observed **real-hardware capture**, not a synthetic
unit test. The source remains unprocessed RAW; recording quality,
peak/RMS and the full 20-second stability have not yet been audited.

The evidence strongly suggests that USB-path conditions affect the
fault, but does **not** isolate the cause to hub power quality.
Unplug/replug also resets USB device state; alternative explanations
include cable/connector integrity, topology, driver state, and
firmware reinitialization. Keep the working device on the current
hub for the next experiment and do not disturb other devices.

**Next gate before AEC M5-006:** re-inspect the physical microphone
source after re-enumeration. The source name/ALSA card index may have
changed; never assume the old name is still valid.

~~~bash
cd /data/cue-wire
pactl list short sources
arecord -l
lsusb -t
~~~

The historical sink was
`alsa_output.pci-0000_00_1f.3.analog-stereo`.
If it remains present and the physical mic source is still exactly
`alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback`,
run fresh **M5-006** (not any prior directory):

~~~bash
python3 tools/voice_m5_capture.py \
  --music '/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --capture-backend pipewire \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --seconds 20 --calibration-seconds 5 --volume 0.30 \
  --out .audit/voice-aec/M5-006
~~~

Check for an explicit capture PASS and a finalized full-length microphone
WAV before any analysis. On successful capture:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python tools/voice_m5_aec.py \
  --capture .audit/voice-aec/M5-006 \
  --out .audit/voice-aec/M5-analysis-006
~~~

A successful 3.776-second isolated take makes a paired experiment
reasonable, but does not itself demonstrate that the 5-second acoustic
calibration or 20-second simultaneous capture will succeed. Keep the
five-second speaker-only period free from local speech; read after
the console prompt. The resulting AEC WAV remains experimental until
verified against the microphone RAW by listening and measurement.

## USB topology finding: microphone behind cascaded hubs

The read-only check confirms the following **observed, current** device
state, without guessing from generic USB errors:

- Anlya.cn `MICAudio-97i`; USB VID:PID `0031:1133`, serial
  `08613544166503`
- USB bus path `3-3.1.4`: host xHCI → external/embedded USB hub
  `3-3` → downstream hub `3-3.1` → **port 4** microphone
- Device enumerates at **12 Mbps (Full Speed)**, uses kernel
  `snd-usb-audio`; normal USB 1.1 audio speed alone does not
  establish a bandwidth fault
- ALSA `card 0 [MICAudio97i], device 0`, capture subdevice present
- `/sys/bus/usb/devices/3-3.1.4/power/control=on` and
  `runtime_status=active` at inspection; it is **not currently in
  runtime suspend**, so do not apply autosuspend hacks without evidence
- Previous `pw-record` stream was correctly linked to the physical
  `capture_MONO` port but finalized with **zero audio frames**.
  PipeWire node RUNNING and ALSA card enumeration do not prove actual
  samples are streaming.

**Controlled physical isolation test (optional, explicit user action):**

1. Stop/finish *only* the microphone recording process; don't unplug
   anything with an active read or write.
2. Identify a **direct motherboard USB port** and move **only the
   USB microphone** from the cascaded hubs to that port. Do not reset
   or unplug the shared hubs containing printer, storage, mouse, etc.
3. Check new `lsusb -t`, `arecord -l`, and
   `pactl list short sources`. The ALSA card index and source name may
   change. Confirm matching VID:PID and serial before using a source.
4. Run a **fresh 5-second isolated microphone test** using the *new,
   explicitly observed* `alsa_input.*` source; gracefully Ctrl+C,
   then check WAV frame count and source peak.
5. If frames appear, this supports a USB path/port dependency, but
   doesn't yet prove the exact failing hub, connector, power issue, or
   driver behavior. Repeated stable captures and a later paired AEC
   run would be separate acceptance stages.
6. If still zero, collect targeted kernel/USB audio logs and
   `pw-top` diagnostics before any service restarts, driver reloads,
   default routing changes or permanent system configuration changes.

Example read-only pre/post checks:

~~~bash
lsusb -t
cat /proc/asound/cards
arecord -l
pactl list short sources
~~~

This recommendation leaves the approved M4 speech WAVs and all private
failed M5 attempts immutable; no AEC result may be claimed while the
microphone produces zero samples.

## M5 upstream USB / ALSA log diagnosis (2026-11-08)

Subsequent diagnostics from the actual Arch host:

- `pactl get-source-mute`: `Mute: no`; `get-source-volume`:
  **100%, 0.00 dB**. Neither mute nor zero source volume explains the
  silent capture.
- WirePlumber reports **`spa.alsa: poll fd error/hangup (card removed?)`**
  on at least two occasions. This describes a failed ALSA poll
  descriptor but does not by itself prove a physical unplug.
- The kernel log explicitly enumerates **`Anlya.cn MICAudio-97i`**
  on **USB path `3-3.1.4`**, behind a hub. Enumerated USB nodes can
  still fail to stream captured audio.
- Kernel `bogus dB values (-12800/-12700), disabling dB reporting`
  belongs to the USB audio mixer range sanity check. The kernel handles
  bad device-supplied mixer units; this line does not establish
  streaming failure.
- **Do not misattribute `usblp0: nonzero read bulk status ... -71` to
  the microphone.** That line names the USB printer interface.
  `-71` is USB EPROTO, but the named endpoint matters.
- The user's earlier M4 physical ALSA RAW capture succeeded, whereas
  current direct ALSA capture returns EIO and the PipeWire mic stream
  is physically linked but writes zero frames. A source/USB/ALSA
  failure is plausible; exact root cause is not established.

**Next diagnostic is read-only and isolated to the USB microphone:**

~~~bash
echo '=== USB TREE ==='
lsusb -t
echo '=== CURRENT SOUND CARDS ==='
cat /proc/asound/cards
arecord -l
echo '=== MIC USB SYSFS ==='
for key in idVendor idProduct manufacturer product serial speed; do
  file="/sys/bus/usb/devices/3-3.1.4/$key"
  if test -r "$file"; then printf '%s: ' "$key"; cat "$file"; fi
done
for key in control runtime_status; do
  file="/sys/bus/usb/devices/3-3.1.4/power/$key"
  if test -r "$file"; then printf 'power/%s: ' "$key"; cat "$file"; fi
done
echo '=== TARGETED KERNEL EVENTS ==='
journalctl -k -b --no-pager | grep -Ei \
  '3-3\.1\.4|MICAudio|snd.usb.audio|usb.*disconnect|usb.*reset' | tail -n 65
~~~

These commands do **not** change drivers, PipeWire, sysfs or system
settings. The sysfs path can change after USB re-enumeration; if files
are absent, consult `lsusb -t` again and do not write to sysfs.

Only after preserving the read-only evidence, consider a controlled
physical replug of **just the microphone** (with no recording in
progress), ideally testing a direct motherboard USB port rather than
the intervening hub. Re-check its new source/card identifiers and
perform a new 5-second mic-only test **before** another 20-second
speaker/music AEC calibration. Never indiscriminately reset a common
USB hub, which could disrupt keyboard, mouse, printer, storage and
other peripherals.

Reference context: Linux kernel USB error codes document -EPROTO
(-71) as possibly including bad signalling, no response, or device
disconnect, and caution that the host controller often cannot
disambiguate these causes. Kernel ALSA USB mixer code explicitly
detects implausible dB ranges.

## M5 standalone capture diagnostic: live graph linked, zero frames

After M5-005, a standalone explicit capture targeted the correct
physical USB source:

`alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback`

During `pw-record --verbose`, the source showed **RUNNING** in
`pactl list short sources` and `pw-link -l` showed the concrete port link
`...capture_MONO -> pw-record:input_MONO`. Nevertheless, after graceful
completion the standalone `mic-isolated-001.wav` also had **zero
frames**. The verbose recorder repeatedly printed `buffered:0
buffers:2 avail:0` (and `ticks:0` in the screenshot). This rules out
the M5 program's startup timing or final-file acceptance checks as the
sole explanation. Linking and node RUNNING status do **not** prove a
driver actually delivered PCM samples.

**Next phase is upstream hardware/graph diagnostics, not another AEC
recording.** Do not restart audio services, change defaults, or relax
the final zero-frame check to force apparent success.

Read-only checks:

~~~bash
MIC='alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback'
pactl get-source-mute "$MIC"
pactl get-source-volume "$MIC"
journalctl --user -b -u pipewire -u wireplumber --no-pager | tail -n 70
journalctl -k -b --no-pager | grep -Ei 'snd.usb.audio|usb|xhci|audio' | tail -n 70
~~~

For a live diagnosis when a short isolated `pw-record` is running,
use `pw-top` to inspect the *driver* and input stream's
`WAIT`, `BUSY`, `ERR`, `QUANT` and `RATE`. Note whether counters
change, not just whether the node says `RUNNING`. The source is
correctly linked but has not produced WAV frames.

The earlier M4 direct ALSA microphone recording did work in another
session. The later repeated `arecord: pcm_read:2285: read error:
Input/output error` and zero PipeWire frames suggest a possible
USB/ALSA source/driver or stalled PipeWire graph. Exact cause
**remains unproven**; gather logs and check the active device before
any corrective state changes.

## M5-005 actual-hardware outcome: zero microphone frames

The user confirmed that the speaker music completed all ~20 seconds,
the five-second speech prompt appeared, and the PipeWire mic recorder
survived through playback. However, the finalized microphone WAV
contained **0.0 seconds** of audio. This was a correctly rejected
capture; no valid real-world AEC measurement or candidate was made.
Successful AEC ERLE scores earlier in the terminal were synthetic only.

**Do not retry complete music-plus-voice takes until a mic-only
recording proves that the explicit physical source produces samples.**
Do not loosen the final zero-length WAV check. Investigate the source
link and USB audio path independently:

~~~bash
cd /data/cue-wire
pw-record --list-targets
pw-record --verbose \
  --target='alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --rate=48000 --channels=1 --channel-map=mono --format=s16 \
  .audit/voice-aec/mic-isolated-001.wav
~~~

In this foreground diagnostic, speak for 5 seconds and press Ctrl+C.
In a second terminal during capture, run:

~~~bash
pactl list short sources
pw-link -l
~~~

Determine whether the actual mic node changes from SUSPENDED to RUNNING
and whether the capture stream is linked to its output port. Check the
new file **after** recording has stopped and the WAV has finalized:

~~~bash
python3 - <<'PY'
from pathlib import Path
import wave
p = Path('.audit/voice-aec/mic-isolated-001.wav')
print('bytes:', p.stat().st_size)
with wave.open(str(p), 'rb') as w:
    print('rate:', w.getframerate(), 'channels:', w.getnchannels(),
          'frames:', w.getnframes(), 'seconds:', w.getnframes()/w.getframerate())
PY
~~~

If this mic-only WAV also has zero frames, diagnose USB/ALSA/PipeWire
ports before any more M5 paired takes. A zero-length WAV cannot be
fixed by AEC, denoising, timing or loosening acceptance conditions.

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
