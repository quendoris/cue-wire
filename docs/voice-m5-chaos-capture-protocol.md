# M5 — intentionally chaotic physical capture with real voice and laughs

**Purpose:** one new CONTINUOUS physical microphone WAV for difficult
echo/noise/speech experiments. Includes isolated mouse, keyboard,
mouse+keyboard, clean speech, and open-ended physical simultaneous
doubletalk (vocal music from loudspeakers + live near-end human speech
+ mouse/keyboard + laughter). No pre-recorded speech injected.
This is a user-requested foreground research recording, not a
production AEC signal processor.

## Why volume is 0.60

The previous \`pw-play --volume 0.30\` used 30% stream
linear amplitude, whereas opening the same WAV in a player
might play it at a default stream gain of 1.00.
The requested doubling is **0.30 → 0.60**, i.e.
**+6.0206dB of stream amplitude**, *not doubling
the speaker/hardware/system sink control*. The
source WAV is not multiplied or edited.
It can still be loud depending on the speaker and
system sink gain; begin carefully, and Ctrl+C
immediately if uncomfortable.
The recorded microphone's near-0dBFS clipped-sample
count is reported; a clipped capture should not
be passed through a production gate or equalized
to hide corruption.

Important: source must be a LOCAL
WAV with **recorded vocals** (a second, far-end voice
inside the song), PCM16 mono/stereo 44.1/48kHz.
No audio codec is downloaded or generated, and
the script cannot automatically verify that
the user-chosen recording contains vocals.
Don't use the former instrumental M5-006 source
unless it happens to have real vocals.

## One physical mic stream, six distinct phases

The program immediately records one
\`microphone-chaos-raw.wav\` on an explicit
physical \`alsa_input.*\` at mono PCM16/48kHz.
It shows terminal prompts, no GUI, no keyboard
input needed for transitions.

| Stage | Duration | Speaker/music with vocals | Live near-end speech | Physical actions |
| --- | --- | --- | --- | --- |
| Warmup | 3s | OFF | silence | nothing |
| 1. \`quiet_before\` | 8s | OFF | silence | nothing |
| 2. \`mouse_music\` | 8s | ON, stream gain 0.60 | silent | L/R mouse clicks |
| 3. \`keyboard_music\` | 8s | ON | silent | keyboard only |
| 4. \`both_music\` | 8s | ON | silent | mouse + keyboard |
| Music decay | 1s | OFF | silent | nothing |
| 5. \`clean_voice\` | 8s | OFF | READ JOKES | no clicks |
| 6. \`free_stress\` | Until **Ctrl+C** | ON | READ, SPEAK, LAUGH | all actions freely |

If the chosen music file is shorter than noisy
stage or open-ended finale, the tool restarts
\`pw-play\` with a fresh process (possible small
gaps, every restart recorded). The mic recorder
does not stop between phases. Every phase marker,
each playback start/stop and volume setting
are written to the JSON manifest.
Phase markers are approximate Python monotonic wall
times, **not sample-synchronized** digital playback
timing. These captures can support perceptual
tests, speaker-ON controls and isolated events,
but strict AEC echo latency must be measured
separately before claiming objective real-talk ERLE.

The first 8s of **clean voice** excludes our
song/player (explicitly terminated + a 1s decay
gap) but still contains the actual physical mic
noise, room ambience and any unrelated sounds.
Do not speak or click during speaker-only
phases unless deliberately deviating from the
protocol (if so, report that fact).

## Jokes and natural laughter

\`docs/voice-m5-stress-jokes-ru.txt\` has
**72 original short jokes** in a Soviet
office/engineering comedy style. The two
initial clean-voice jokes advance every ~4s
to fit the 8s control stage. The final open
segment prints the next joke approximately every
7s. No keyboard or mouse is needed to
advance the jokes, so the clean control remains
clean of our own UI-input artifacts.
Read with distinct intonations, short pauses,
smile, laugh if anything is actually funny;
do NOT fake laughter for a metric.
Optional: say a normal sentence in between
jokes so the future classifier sees genuine
speech transitions and natural volume variations.

## Local Arch commands

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

Now set the EXACT physical speaker sink from
the earlier successfully verified 0.30 physical
capture. This only reads an existing local JSON,
never changes a sink:

~~~bash
SPEAKER="$(python3 -c '
import json
from pathlib import Path
p=Path(".audit/voice-aec/M5-level-030-001/manifest.json")
print(json.loads(p.read_text())["speaker_sink_name"])
')"
printf 'Using physical speaker sink: %s\n' "$SPEAKER"
pactl list short sinks
~~~

Select an existing **vocal music WAV**.
Do not enter an example path unchanged; replace
the shell value with the actual file:

~~~bash
MUSIC='/absolute/path/to/your/vocal-music.wav'
test -f "$MUSIC" || { echo "No selected WAV" >&2; exit 1; }
~~~

If only MP3/FLAC music with vocals is available,
optionally convert a private local copy with
ffmpeg beforehand (this does not play anything).
Use a NEW output path, never the original:

~~~bash
mkdir -p -m 700 .audit/voice-aec/M5-chaos-input
ffmpeg -nostdin -i '/path/to/vocal-song.flac' \
  -ar 48000 -ac 2 -c:a pcm_s16le \
  .audit/voice-aec/M5-chaos-input/vocal-music.wav

MUSIC='.audit/voice-aec/M5-chaos-input/vocal-music.wav'
~~~

All input content remains local/private.
The optional ffmpeg step is unnecessary if
\`MUSIC\` is already a compatible WAV.
It DOES NOT change output-device gain.

**Preflight first; no music or mic will run:**

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_chaos_capture.py \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --speaker "$SPEAKER" \
  --music "$MUSIC" \
  --volume 0.60 \
  --out .audit/voice-aec/M5-chaos-001 \
  --check-only
~~~

If the music and physical endpoint printout are
correct and all 55 tests passed, run the
same command **without** \`--check-only\`:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_chaos_capture.py \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --speaker "$SPEAKER" \
  --music "$MUSIC" \
  --volume 0.60 \
  --out .audit/voice-aec/M5-chaos-001
~~~

When \`[FREE_STRESS]\` is visible, continue reading
and freely making clicks for as long as desired.
**Press Ctrl+C from this foreground terminal**
to stop both owned \`pw-play\` and \`pw-record\`,
close the WAV, and hash the immutable evidence.
Super+C forcibly terminates the app/process family
via existing process ownership and parent-death
guards, but is NOT a normal audio-file-finalization
method. Never automatically schedule the capture.

~~~bash
cd /data/cue-wire/.audit/voice-aec/M5-chaos-001
sha256sum -c SHA256SUMS

python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path("manifest.json").read_text())
print("status:",d["status"])
print("duration:",d.get("physical_microphone_analysis",{}).get("recorded_seconds"))
print("clipped samples:",d.get("physical_microphone_analysis",{}).get("clipped_samples"))
for p in d["recorded_phases"]:
    print(p["name"],round(p["start_since_recorder_launch_s"],2),
          round(p["end_since_recorder_launch_s"],2),
          "music:",p["music_requested"])
PY
~~~

The output folder contains exactly one raw recorded
WAV, \`manifest.json\`, and per-process logs
plus \`SHA256SUMS\`. Send **manifest.json**
and **SHA256SUMS** first, not an unrequested
private voice/music raw WAV. Raw can be
shared in a separate chat upload later if the
user explicitly chooses; NEVER commit raw
voice/copyrighted music into GitHub.

## Engineering limits

This intentionally chaotic capture is for
future evaluation of *existing* AEC/noise
systems before designing new algorithms.
It doesn't change the working CueWire
microphone, create virtual devices, alter
system default routing, install packages,
or run a background daemon.
Do not interpret single-call overall RMS as
proof of echo suppression or true clean-voice
preservation. Report stage labels, sampled
clipping and source hashes before new claims.
