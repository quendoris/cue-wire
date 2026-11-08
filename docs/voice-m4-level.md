# CueWire M4 — frozen clean chain and speech-aware level v1

**Approved auditory choice:** direct ALSA RAW capture -> Rust DeepFilterNet
(with compensated -D delay; padding/trimming only of generated tail) ->
sherpa-onnx DPDFNet2 Soft (attenuation limit 12 dB). The user listened
and reported that the voice sounds normal. Model scores alone are not
evidence that this processing is universally better.

The CLEAN-003 run is **frozen**: do not rewrite its intermediate files,
change model checkpoints, or overwrite the manifest. Its audited last
file is

    .audit/voice-captures/M4-R0-ALSA-007/clean-003/02-dpdfnet2-soft.wav

The measured upstream DeepFilter delay for this run is 30 ms and the
output length equals RAW. Never reprocess RAW unless you want a *new*
independent CLEAN run.

## Next stage: speech-aware volume leveling, not EQ

CueWire M4 level v1 is a separate, offline, standard-library-only
processor. It measures 20-ms window RMS, estimates likely speech frames,
bridges brief speech gaps, protects consonant onsets and word endings
with pre-roll/hangover, and applies an intentionally gentle,
smoothed adjustment to reduce the gap between quiet and loud phrases.

- The default target is **-24 dBFS median active-frame RMS**, **not**
  overall file RMS and **not** an EBU R128 LUFS or true-peak target.
- Makeup is bounded at +16 dB (or -6 dB for unexpectedly loud sources).
  Local speech adjustments are bounded to -3..+2 dB; 80-ms rise and
  300-ms fall avoid hard sentence-by-sentence gain switching.
- Long quiet pauses are not deliberately amplified; estimated speech
  regions are expanded to protect the onset and ending of speech.
- A whole-file conservative peak-safe trim limits the **sample** peak
  to -2.0 dBFS by default. It may prevent the loudness target from
  being reached, and no LUFS or true-peak compliance is claimed.
- No gating, EQ, de-essing, additional denoising, audio graph changes,
  auto-play, background services, external network traffic, or uploads.

This is not a linguistic VAD: low-level background speech, an unusual
voice level, or residual tonal noise can mislead RMS-based activity
detection. Listen to long pauses, quiet words, and phrase beginnings
before accepting a result.

## Run from the GitHub working tree

    cd /data/cue-wire
    git pull --ff-only

    python3 -m unittest discover -s tests -p 'test_voice_m4_level.py' -v

    python3 tools/voice_m4_level.py \
      --in .audit/voice-captures/M4-R0-ALSA-007/clean-003/02-dpdfnet2-soft.wav \
      --out .audit/voice-captures/M4-R0-ALSA-007/level-001

The source CLEAN manifest and RAW SHA256 are verified before processing
and again after rendering; the output directory must NOT exist already.
Do not delete failed experiments; choose level-002 for another run.

Outputs:

- level-001/03-voice-leveled.wav
- level-001/manifest.json
- level-001/SHA256SUMS

The report contains input/output RMS, sample peak, first-five-second
window RMS, activity estimate, typical speech RMS, max allowed local gain
and any safety trim. It does not include raw audio bytes.

## Manual acceptance

    cd /data/cue-wire/.audit/voice-captures/M4-R0-ALSA-007/level-001
    sha256sum -c SHA256SUMS
    pw-play ../clean-003/02-dpdfnet2-soft.wav
    pw-play 03-voice-leveled.wav

Compare using identical headphone settings: quieter phrases, the
natural difference between speaking and whispering, tails of words,
breaths, residual background, and whether the loudest phrases feel
pumped. Louder alone does not prove higher quality. If too much
dynamic leveling is audible, keep the untouched CLEAN WAV and test
another level-NNN with a different target; the frozen CLEAN remains the
accepted restoration source.

For future takes: create a new immutable RAW directory, run
voice_m4_clean.py in a new CLEAN directory and pass that result to
voice_m4_level.py in a distinct LEVEL directory. The CURRENT tool
checks the exact DPDFNet2-soft filename plus matching manifest hashes,
so reuse it only after new CLEAN is technically complete.

The public repo contains **code and reproducibility notes only**. The
actual voice files and reports remain inside the private .audit tree.
