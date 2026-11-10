# M5 — explicitly requested private listening A/B (2026-11-09)

The researcher requested to **hear** original echo/music/voice and
our best measured candidate. Prior M5 numerical tools correctly
avoided emitting WAV. This command is a separate, manually invoked
and explicitly authorized **private local audio preview export**;
it never starts playback automatically.

## Precisely what can be heard

The physical 0.30 speaker-only M5 recording has microphone pickup
from the real loudspeaker playing music, plus actual microphone room
noise. On the selected 10–16s source music interval, inject a verified
six-second clean M4 voice excerpt digitally at two previously audited
levels (-50 and -39dBFS), then replay each same complete 36s stream
through the same native SpeexDSP before choosing these sections:

| File in each 6-second voice-level folder | What it contains |
| --- | --- |
| \`01_original_music_echo_and_injected_voice.wav\` | Unprocessed physical-microphone music echo/room noise + digitally injected original M4 speech |
| \`02_native_speex_aec_and_injected_voice.wav\` | Standard native single Speex AEC with actual music render reference, for comparison |
| \`03_cuewire_guarded_speex_delta_and_injected_voice.wav\` | Best speech-transparency candidate in prior tests: original mic plus real-vs-zero Speex differential, 3800Hz echo-estimate-only OFFLINE 513-tap guard |

Each comparison renders two voice levels:
\`voice_-50dbfs\` (more echo-prone, quieter speech)
and \`voice_-39dbfs\` (more clearly audible speech).
All files are exactly six seconds, 48kHz mono PCM16
and start on the identical physical mic sample.

**A/B level discipline**: One shared gain for the three
files at a particular voice level, selected to place
their combined largest peak at -3dBFS. There is NO
per-file normalization, limiter, compressor, EQ or
different playback gain for processed tracks.
The gain and source identifiers are written into
\`listening-manifest.json\`. Do not individually
normalize or loudness-match these WAVs before judging
remaining echo and native speech changes.

The music was physically played and captured; the clean
M4 speech was originally recorded independently and
then digitally overlaid. **This is NOT a genuine
simultaneous physical doubletalk recording.** The
acoustic path and hardware microphone DSP will not
have reacted to near-end voice, and subjective sound
does not certify real-doubletalk voice preservation.

The 3800Hz 513-tap guarded correction is offline
noncausal; this preview is not evidence of real-time
suitability. Results are only valid for the accepted
SHA256-pinned physical microphone, voice and music
source files used in previous experiments.

## Reproduce on local Arch — no new recording / no PipeWire changes

First pull the new manually invoked tool and run all
**52 existing M5 regression tests** (two new tests
verify common-gain WAV output, six WAV files,
source hashes and no overwrite). If red, stop,
send the full traceback and do not try to
'fix' a source WAV or install packages.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

Get the previously SHA-pinned M4 voice directory
from the actual local filesystem (do not guess):

~~~bash
VOICE_MANIFEST="$(find .audit -type f \
  -path '*/M4-R0-ALSA-007/manifest.json' -print -quit)"
if [ -z "$VOICE_MANIFEST" ]; then
  echo "Required original M4 voice capture missing; stop" >&2
else
  ~/.venvs/cuewire-voice-lab/bin/python \
    tools/voice_m5_listening_ab.py \
    --capture .audit/voice-aec/M5-level-030-001 \
    --volume 0.30 \
    --voice "$(dirname "$VOICE_MANIFEST")" \
    --delta-report .audit/voice-aec/M5-speex-delta-001/speex-delta.json \
    --out .audit/voice-aec/M5-listening-001
fi
~~~

Use actual prior \`M5-speex-delta-001\` report
location if your local folder name differs.
Required SHA256 for report is fixed:
\`c7753dea1ff557bcd3e56073f3d57ad34c28ffb3ea0c7212bbdac9276d6e13cf\`.
The tool refuses mismatched WAVs or reports and
refuses to overwrite any existing output folder.
If \`-001\` already exists, choose \`-002\`, etc.

All WAVs are private within
\`.audit/voice-aec/M5-listening-001/\`.
Verify hashes and then **manually** play any pair:

~~~bash
cd /data/cue-wire/.audit/voice-aec/M5-listening-001
sha256sum -c SHA256SUMS

# Quiet voice, louder relative music/echo:
pw-play voice_-50dbfs/01_original_music_echo_and_injected_voice.wav
pw-play voice_-50dbfs/03_cuewire_guarded_speex_delta_and_injected_voice.wav

# Louder voice, to hear timbre and speech:
pw-play voice_-39dbfs/01_original_music_echo_and_injected_voice.wav
pw-play voice_-39dbfs/03_cuewire_guarded_speex_delta_and_injected_voice.wav

# Optional classic Speex comparison:
pw-play voice_-39dbfs/02_native_speex_aec_and_injected_voice.wav
~~~

The \`pw-play\` commands are user's explicit choices:
they play audio to the current configured output
and are **not** invoked by the export script.
The Python tool doesn't open any audio device
or load PipeWire modules. Prefer headphones
for careful listening / to avoid physical
speaker-to-mic feedback. \`pw-play\` is a
temporary ordinary playback process, not a daemon.

**Privacy:** audio contains user's M4 voice
and captured commercial music. NEVER commit
or share the WAVs through public GitHub;
leave them solely in .audit. Only share if
the user explicitly requests a particular
private excerpt/file.

A/B subjective impressions can help judge
whether our current tiny 0.08–0.22dB
known-stem gains have audible impact.
A large gain in digital voice transfer
may be imperceptible or perceptually important,
depending on sound. Do not equate any
short listening test with an independent
real physical doubletalk acceptance result.
