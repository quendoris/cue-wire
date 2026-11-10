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


## Louder listening copy — preserve existing A/B/C assignments

The original files were quiet (real physical R1 peak ~-20.8 dBFS).
For subsequent rounds, use the separate private loudness helper. It
never changes the original blind ordering or the source audio; never
reads/reveals the original private mapping, and never changes system
output volume. It computes ONE identical linear gain across A, B and C
inside each round. Gains can differ between rounds. Peak target is
-3 dBFS, with no clip, limiter, compressor, noise suppression, or
resampling. It requantizes to PCM16, so boosted copies differ from
originals at the sample level; all comparisons remain fair within
each round.

~~~bash
cd /data/cue-wire
git pull --ff-only

python3 -m unittest discover \
  -s tests -p 'test_voice_m5_blind_boost.py' -v

python3 tools/voice_m5_blind_boost.py \
  --source .audit/voice-aec/M5-chaos-blind-github-001 \
  --out .audit/voice-aec/M5-chaos-blind-louder-001

cd .audit/voice-aec/M5-chaos-blind-louder-001
sha256sum -c SHA256SUMS
pw-play R2/A.wav
pw-play R2/B.wav
pw-play R2/C.wav
~~~

The new private folder contains R1–R3 files, boosted ZIP, gain report
and new checksums. Its ZIP does NOT contain the mapping key. The
original key remains in M5-chaos-blind-github-001. The identical
A/B/C labels mean the listener's prior "R1: A preferred" judgment
still refers to the SAME algorithm, even when using boosted R1 later.

Estimated gain from the supplied actual physical files:
R1 +17.8 dB, R2 +16.2 dB, R3 +7.3 dB.
The script prints measured exact gains and zero-clipping validation
on the listener's machine. If the new recording still feels quiet,
do NOT normalize each variant independently; first collect the
listening assessment and then consider a controlled playback-level
workflow with hardware headroom.
