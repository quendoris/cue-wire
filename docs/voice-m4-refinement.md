# CueWire M4 / LEVEL-002 — loud phrase smoothing, two independent candidates

**Audio provenance:** M4-R0-ALSA-007, original RAW -> DeepFilterNet (-D,
30 ms corrected) -> DPDFNet2 Soft (12 dB attenuation limit) ->
LEVEL-001/03-voice-leveled.wav.

The user has listened and accepted LEVEL-001, **especially its treatment
of quiet speech**. Do not modify RAW, CLEAN-003 or LEVEL-001. New dynamics
tests are made from *LEVEL-001*, not from RAW or CLEAN, and do not
re-run the successful denoising or quiet speech leveling.

## Goal / feedback

- Keep the now-good upward correction of quiet speech intact.
- Compress louder speech more noticeably, without eliminating intonation.
- Add a little more loudness for speech only, not long pauses.
- Protect transients using a local offline lookahead limiter instead
  of forcing a global gain trim on the whole clip.
- Compare two candidates on the same frozen source, preserving the
  existing accepted WAV as the control.

The experiment is intentionally **offline**; it is NOT a live virtual
mic implementation and does NOT touch PipeWire, Bluetooth, ALSA, system
settings, automatic playback, hotkeys or background services.

## Profiles

| Parameter | Soft | Dense |
| --- | ---: | ---: |
| Downward compression ratio | 1.6:1 | 2.0:1 |
| Soft knee width | 5 dB | 5 dB |
| Maximum reduction | 3.5 dB | 5.5 dB |
| Extra speech-only makeup | +2 dB | +3 dB |
| Compression attack | 28 ms | 18 ms |
| Compression release | 200 ms | 160 ms |
| Makeup rise/fall | 60/250 ms | 60/250 ms |
| Peak ceiling (sample peak) | -2 dBFS | -2 dBFS |
| Limiter lookahead/release | 8/130 ms | 8/130 ms |

The compressor threshold is inferred independently from LEVEL-001
active-frame RMS (lower 35th percentile plus 1 dB). Earlier prototypes
used the 60th percentile, which can fall inside a long loud phrase and
hardly compress anything, so that choice was rejected.

The frame detector is an *RMS activity heuristic*, not a semantic VAD.
It uses 20-ms frames with pre-roll/hangover for quiet consonants. The
new envelope is a **downward** compressor plus independent speech-region
makeup. Long pauses are not deliberately boosted, but reverberation
and background included in speech regions can still be affected.
Sample-peak limiting is *not* certified true-peak limiting, and RMS
numbers alone cannot prove subjective quality.

## Execute in existing GitHub branch

```bash
cd /data/cue-wire
git pull --ff-only
python3 -m unittest discover -s tests -p test_voice_m4_refine.py -v

python3 tools/voice_m4_refine.py \
  --in .audit/voice-captures/M4-R0-ALSA-007/level-001/03-voice-leveled.wav \
  --out .audit/voice-captures/M4-R0-ALSA-007/level-002
```

Output folder must **not** exist beforehand. All audio stays under
the ignored private `.audit`; it is not uploaded. If an attempt fails,
keep its log/partial evidence and use a distinct new folder next time
(e.g. `level-003`).

Three files for the comparison:

- `level-001/03-voice-leveled.wav` — known-good reference, unchanged.
- `level-002/04-soft.wav` — delicate loud-phrase compression.
- `level-002/04-dense.wav` — more assertive loud-phrase compression.

Both candidates are generated **independently** from the untouched
`03-voice-leveled.wav`. The manifest records every profile and SHA256
for both versions, the input, RAW and CLEAN, plus before/after measured
RMS, peak, frame count, speech p25/p50/p90 range, maximum gain
reduction and limiting activity. A separate `SHA256SUMS` covers outputs
and the manifest. Incomplete runs are marked failed.

## Audition

```bash
cd /data/cue-wire/.audit/voice-captures/M4-R0-ALSA-007/level-002
sha256sum -c SHA256SUMS

pw-play ../level-001/03-voice-leveled.wav
pw-play 04-soft.wav
pw-play 04-dense.wav
```

**Compare without changing headphones volume:** unusually loud words,
quiet sentence endings, onset/offset of phrases, breaths, residual
background and pumping. Stronger RMS can merely sound more impressive.
Choose a winner **by listening**, not by numerical loudness.

No result is promoted to production or linked to the live virtual mic
until the user confirms it. The accepted LEVEL-001 remains an independent
fallback and the immutable reference for any further experiments.

Once this A/B is judged, the next separate project step is combining
the processed voice with output/soundboard paths. That requires
independent gain staging, separate routing, a measured microphone
mixing path and lifecycle checks, and must not accidentally monitor
microphone audio back into the headphones.
