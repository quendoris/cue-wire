# CueWire M5 Chaos — private listening A/B with ACTUAL physical near-end speech

The user previously requested to **hear** the difference between
unprocessed music echo and the best research prototype.
On 2026-11-10 they supplied native SpeexDSP results for
a new 89.96267s physical one-take doubletalk dataset.
Use \`tools/voice_m5_chaos_listen_ab.py\` to reproduce those
previously verified physical-run parameters and manually export
short PRIVATE local comparison WAVs.

Nothing is recorded, played, uploaded, installed,
sent to GitHub, monitored or reconfigured.
This is EXPLICIT user-initiated private listening
rather than default WAV export by research scripts.

All original data are immutable and SHA256-checked:
- mic: \`63322a193843100e87124fb47fd1445604fbc6118accbc1fe489e772326980fb\`;
- original capture manifest:
  \`20d52a781d33c6df05d9bc551afb8fea5899dbd070a6690e5bfaabb3fd6b7c66\`;
- first acoustic lag report:
  \`ea1855721441b4a859a4409af707f0ff554953184b5bd634519a9349cc67b243\`;
- multiwindow lag check:
  \`c3d89762d5fc3147876ab45af1546f117ab342960396cd41df3be5d98d13f3e7\`;
- original 48kHz stereo music source:
  \`495d4fb1845a369129df4a659f62f18c087c7de1bff4f895dcf60a1c38351e6a\`;
- native 90s Speex/dual-delta numeric report:
  \`3fdb522dbbcbe456709c11987d1e795c9b9fc9f086b91718dff374cd6919ad58\`.

The local operator's music with vocal performance
remains a private file at its actual original path.
The command fails on a changed or missing original SHA.
DO NOT replace the song, alter the historical mic
capture, normalize input, or fake a missing source.

## Four fixed sections, three equal-level variants each

| Folder | Original mic clock | Material |
| --- | --- | --- |
| \`01_mouse_and_music\` | 12–18s | Actual mouse clicks while song plays |
| \`02_mouse_keyboard_music\` | 28–34s | Actual combined keyboard and mouse while song plays |
| \`03_real_voice_no_music\` | 37–43s | Real live user's clean speaker-OFF voice |
| \`04_real_full_chaos\` | 53–83s | 30 seconds of real live near-end speech, other person's vocal song, keyboard and mouse |

Each folder has:
1. \`01_unprocessed_physical_microphone.wav\`;
2. \`02_native_speex_aec.wav\`;
3. \`03_guarded_dual_speex_delta.wav\`.

All 12 files are exactly 48kHz, MONO, PCM16.
**ONE shared gain across ALL 12 files** is unity
unless the largest sample needs downscaling to
stay <= -3dBFS. There is no per-track or
per-section normalization, compression, denoising,
sibilance EQ, limiter, or loudness matching.
Differences in output level and spectral effects
are not hidden by export gain manipulation.

The full 90s physical microphone and both music
segments are processed in ORIGINAL temporal order
through native SpeexDSP, with two exact separate
pinned music lag estimates and L/R arithmetic
mean source. The zero-reference state is independent.
The actual guarded output uses exactly the
**previously tested 513-tap offline noncausal**
3800Hz filter applied to the reference-dependent
correction ONLY. It is NOT a future live mic
latency guarantee. Before output, RMS of prior
full physical clean-speech and chaotic intervals
is cross-checked against the independently
uploaded 90s numerical report, and exact
untouched clean speech is asserted. A difference
triggers failure, not automatic output.

## Arch commands

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

Expected suite now contains 61 test methods,
including matched-level export, exclusive
private file creation, PCM16 format checks
and shared attenuation. These new tests
have not been executed in the assistant's
runtime. If any red, don't continue to
physical export; send traceback.

After green tests:

~~~bash
cd /data/cue-wire

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_chaos_listen_ab.py \
  --capture .audit/voice-aec/M5-chaos-001 \
  --initial-alignment .audit/voice-aec/M5-chaos-alignment-001/music-alignment.json \
  --consistency .audit/voice-aec/M5-chaos-alignment-consistency-001/alignment-consistency.json \
  --previous-report .audit/voice-aec/M5-chaos-speex-real-001/physical-chaos-speex.json \
  --out .audit/voice-aec/M5-chaos-listening-001

cd .audit/voice-aec/M5-chaos-listening-001
sha256sum -c SHA256SUMS
~~~

No automatic playback: to hear the difference
through the already configured usual output
(playback happens ONLY when user manually
invokes \`pw-play\`):

~~~bash
# Start with a clean acoustic-voice comparison.
pw-play 03_real_voice_no_music/01_unprocessed_physical_microphone.wav
pw-play 03_real_voice_no_music/02_native_speex_aec.wav
pw-play 03_real_voice_no_music/03_guarded_dual_speex_delta.wav

# Then compare 30 seconds of actual full chaos.
pw-play 04_real_full_chaos/01_unprocessed_physical_microphone.wav
pw-play 04_real_full_chaos/02_native_speex_aec.wav
pw-play 04_real_full_chaos/03_guarded_dual_speex_delta.wav

# Mouse/keyboard short tests are in folders 01 and 02.
~~~

Listen through headphones to avoid creating
new acoustic feedback. \`pw-play\` is a
temporary manually launched foreground audio
process and does not set system default
volume or install a PipeWire module.

## Interpret honestly

- The already measured 500–4000Hz reference-linked
  proxy improved 6.345dB with guarded delta
  in the full final real doubletalk; native
  Speex gave 6.402dB. This is **not**
  literal music-only SPL, exact ERLE or
  true voice preservation SNR.
- During the separately recorded real speaker-OFF
  clean voice, guarded delta returned a
  sample-exact unchanged microphone signal
  in the prior numeric report.
- During overlapping near-end speech/music,
  speech has no separately recorded clean
  same-utterance truth. Listening is therefore
  subjective. A/B can reveal obvious musical
  echo, pumping, clicks, speech distortion
  and unnatural timbre, but is NOT a certified
  objective speech-quality metric.
- The output music can be copyrighted and
  near-end voice is personal private audio.
  Do not publicly post WAV or commit them
  to GitHub. Only numeric reports and code
  belong in the repository.

The next architecture study should compare
established WebRTC AEC3 and existing click
suppressors under identical capture material,
without constructing yet another new AEC
until we find a specific measured gap.
