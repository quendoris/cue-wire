# M6 PHYSICAL-EVENT-001 — short anonymous hardware calibration

**Status:** experimental, operator-initiated 43-second hardware capture.
Uses existing microphone already established by M5; requires a NEW event
stream because old M5 did not record presses/releases. The old music and
voice-chaos recordings remain frozen and need NOT be repeated. This
capture is intentionally **speaker OFF/no music**: calibrate physical
keyboard/mouse acoustic transients separately from musical AEC.

## Strict privacy contract

- Source is two manually approved Linux input event devices and one
  explicitly named physical mic.
- The output JSONL row has EXACTLY two keys:
  kind: mouse_press / mouse_release / keyboard_press / keyboard_release
  and t_monotonic_ns: Linux kernel CLOCK_MONOTONIC nanoseconds.
- No key codes, scancodes, character names, button IDs, coordinates,
  mouse motion, typed text, passwords, or device names/paths written.
  EV_KEY code field is NOT examined for presses; only event type and
  press/release value. EV_SYN/SYN_DROPPED is checked to detect loss.
- The timestamps and class labels still reveal **typing/click rhythms**.
  Treat them as sensitive, private metadata. Never capture passwords,
  personal text, or an unrelated application session. Capture ONLY
  the requested isolated research phases while foreground tool runs.
- Device listing only prints names/paths locally for selection;
  it creates no output and no event log.
- No grabbing, injection, hotkeys, autostart, service, default route
  changes, automatic music playback, or mic self-monitoring.
- No implicit root privileges and no sudo escalation.
- Kernel timestamps are forced to CLOCK_MONOTONIC via EVIOCSCLOCKID.
  If unsupported, fail rather than pretending precise clock data.
- Precise PCM sample-zero time is NOT available from pw-record.
  Future audio/event alignment must be estimated from many isolated
  clicks with a heldout validity check. Current capture deliberately
  marks alignment as NOT measured.

## Step 1 — code and hardware preflight

Run from the feature branch:

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python -m unittest discover \
  -s tests -p 'test_voice_m6_physical_event_capture.py' -v

~/.venvs/cuewire-voice-lab/bin/python -c \
  'import evdev; print("python-evdev OK")'

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m6_physical_event_capture.py devices
~~~

If import fails, python-evdev is missing from that venv. Install it
explicitly into the same venv (without sudo and without changing system
Python), if desired:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python -m pip install evdev
~~~

Depending on Arch group/udev configuration, /dev/input/eventN may not
be readable. If access is denied, STOP and inspect the existing
permissions; do NOT run the entire recorder as root. The user chooses
the explicit physical keyboard and mouse paths from the local preview.
Avoid /dev/input/by-id symlinks: the initial prototype requires direct
/dev/input/eventN. One physical device may expose multiple event nodes;
select the actual KEY/BTN event node.

## Step 2 — use the known physical M5 microphone

The earlier pinned M5 manifest contains source_physical_mic_name.
This reads that single field locally and does NOT alter sources:

~~~bash
cd /data/cue-wire
MIC=$(
  ~/.venvs/cuewire-voice-lab/bin/python -c \
  'import json; from pathlib import Path; p=Path(".audit/voice-aec/M5-chaos-001/manifest.json"); print(json.loads(p.read_text())["source_physical_mic_name"])'
)
printf 'Physical mic: %s\n' "$MIC"
~~~

Do NOT proceed if the named source is disconnected or points at a
different physical device. The script requires a PipeWire alsa_input.*
node. All source PCM is recorded with explicit pw-record --target;
system defaults and mixing are untouched. pw-record options follow
the public PipeWire --target, --format=s16, --rate=48000 and --latency
specification.

## Step 3 — short 43-second operator-led calibration

Use the ACTUAL numbered nodes discovered on the current machine:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m6_physical_event_capture.py capture \
  --mic "$MIC" \
  --keyboard-device /dev/input/eventN \
  --mouse-device /dev/input/eventM \
  --acknowledge-anonymous-event-privacy \
  --speaker-off-confirmed \
  --out .audit/voice-aec/M6-physical-events-001
~~~

N and M are placeholders; replace before running. All phases and their
durations are printed in the terminal. Do NOT type a password or text.
No music, no speech in the first four phases:

1. quiet_baseline — 3s, no input/no voice
2. mouse_isolated — 9s, >=8 separate mouse presses with pauses
3. keyboard_isolated — 9s, >=8 separate arbitrary key presses with
   pauses; the actual keys are NEVER recorded
4. both_holdout — 7s, both devices, different pattern, no voice
5. voice_only_holdout — 6s, spoken voice, no clicks
6. voice_clicks_holdout — 9s, voice and clicks together

An initial 1.5-second recorder warmup precedes these phases. The
program terminates its ONLY owned child process when completed or
interrupted, and closes its evdev descriptors. Ctrl+C ABORTS; no
automatic restart. It never edits PipeWire system configuration.

## Step 4 — verify and report

~~~bash
cd /data/cue-wire/.audit/voice-aec/M6-physical-events-001
sha256sum -c SHA256SUMS
~~~

Private files:
- microphone.wav — actual 48kHz mono PCM16 physical acoustic recording
- anonymous-events.jsonl — ONLY event class/time, no key identity
- manifest.json — phase monotonic boundaries, aggregate counts and
  explicit "sample_zero_measured": false
- pw-record.stderr.log — recorder stderr, not keystrokes
- SHA256SUMS — byte fingerprints for all other files

Run is invalid if less than 8 isolated presses of the mouse or
keyboard were captured in the relevant phases, or if the recorder
exits, input events are lost, source format invalid, or anything is
interrupted. Use a NEW -002 directory on an authorized rerun; never
overwrite -001.

The next engineering step after reading the report is independently
estimating the host monotonic-to-audio PCM offset and click waveform
templates using only the isolated phases, with the both-device and
voice+click phases held out. The report intentionally claims NO
sample-accurate alignment, no click suppression and no voice
preservation yet. Never upload this audio/event JSONL to public
GitHub; send a private archive in chat if independent audit is needed.
