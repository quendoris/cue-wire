# CueWire M5 — local private blind listening

PUBLIC GitHub stores ONLY this guide, the generator and synthetic tests.
Real microphone WAVs, copyrighted music, blind ZIP and private mapping
MUST stay inside the operator's local ignored .audit directory.

The generator only reads SHA256-pinned evidence and writes 9 local
listening WAVs. No capture, automatic playback, networking, daemon,
autostart, or PipeWire changes.

## Commands on Arch

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

python3 -m unittest discover \
  -s tests -p 'test_voice_m5_blind_round.py' -v

python3 tools/voice_m5_blind_round.py \
  --source .audit/voice-aec/M5-chaos-listening-001 \
  --out .audit/voice-aec/M5-chaos-blind-github-001

cd .audit/voice-aec/M5-chaos-blind-github-001
sha256sum -c SHA256SUMS
~~~

The generator verifies all twelve source WAV hashes, their PCM16
formats, the source JSON report, and its SHA256SUMS, before creating the
output. Output directories are created exclusively; no overwrites.

## Private outputs

- cuewire-m5-blind-round1.zip: 9 WAV files, README, SHA256SUMS, and a
  salted cryptographic commitment, but NO algorithm-to-letter mapping.
- R1/A.wav ... R3/C.wav: same audio for direct local listening.
- PRIVATE-KEY-DO-NOT-OPEN.json: LOCAL secret with independently shuffled
  per-round mappings and secret commitment salt. Do not inspect it before
  completing your own listening scores; reveal only afterward.

Three independently shuffled variants per round: raw physical mic,
native SpeexDSP AEC, guarded dual-Speex delta.

R1: clean voice, 5.8 seconds; R2: mouse, keyboard and music, 5.8s;
R3: live speech + music + clicks, 29.8s. Each is the ORIGINAL exact
PCM16 signal with 100ms cut from both ends; no DSP, resampling or
per-file normalization. Original shared gain is 0dB.

## Optional manual playback (foreground)

~~~bash
cd /data/cue-wire/.audit/voice-aec/M5-chaos-blind-github-001
pw-play R1/A.wav
pw-play R1/B.wav
pw-play R1/C.wav
pw-play R2/A.wav
pw-play R2/B.wav
pw-play R2/C.wav
pw-play R3/A.wav
pw-play R3/B.wav
pw-play R3/C.wav
~~~

For each round, write ranking, what sounded different, and confidence
0–5. Indistinguishable is a valid result. Listen at the same fixed
headphone output level and without player normalization.

IMPORTANT: A GitHub-generated ZIP uses a NEW random arrangement,
not the same A/B/C ordering as any prior conversation-generated ZIP.
Never reuse scores across those generations. The local key provides
verifiable reveal AFTER scoring; the salt is not in the public ZIP.
The protocol does not claim known clean speech truth during doubletalk.
