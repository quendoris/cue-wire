# M6 Physical-002 — waveform-aware event-gated click reduction (OFFLINE)

**Status:** research candidate, NOT certified natural voice quality,
NOT real-time and NOT a production microphone processor. No new capture.

## Input and privacy

Uses the previous 44.330667-second physical mic WAV, 364 anonymous
press/release events and the independently checked M6 offset report.
All four SHA256 files (mic, anonymous events, capture manifest,
physical alignment report) are pinned. The event JSON reader accepts
only kind and monotonic timestamp, NEVER keycodes or button identity.
No private audio or event timestamps are uploaded to GitHub.

The final recorded phase ends approximately 175ms past the last
actual WAV frame. The tool permits at most 250ms terminal trimming
and never fills missing audio.

## Predictive event-gated highband correction

Previous mean/median class-based waveform inversion INCREASED
highband noise RMS. This pilot instead uses the event as the time
anchor and builds the compensating waveform from the PRESENT acoustic
transient, without identifying the key or button.

1. Fixed independently checked acoustic offsets: mouse press 19ms,
   release 17ms; keyboard press 24ms, release 10ms.
2. Offline ZERO-PHASE 1300–9000Hz extraction from mic. Subtracting
   the causally phase-shifted filtered signal had worsened highband
   energy. This zero-phase filter uses future samples and cannot
   simply be used as a live low-latency DSP stage.
3. Search +/-5ms around mouse and +/-9ms around keyboard event.
   Trigger only if the peak exceeds 4x preceding acoustic background
   and 0.0005 RMS.
4. Short 2.5+4.5ms (mouse) or 3+5ms (keyboard) cosine tapers around
   actual acoustic peaks; overlapping tapers use max, not sum.
5. Three output variants per scene:
     A_original.wav = input physical mic
     B_conservative.wav = mic - 0.40 * mask * extracted_highband
     C_stronger.wav = mic - 0.75 * mask * extracted_highband
   No compressor, limiter, EQ, normalization or external settings.
6. Compare all PCM16 off-event samples to original EXACTLY.
   Fail on clip, wrong source SHA or unsafe directory overwrite.

The parallel inverted highband correction is mathematically equivalent
to attenuating a limited high-frequency component. It does NOT
reproduce perfect independent acoustic noise cancellation and can
remove speech consonants that coincide with a click. Whole-phase
band RMS is NOT isolated click SNR or speech intelligibility.

**Important experimental limitation:** gains, window sizes and
thresholds were chosen after inspecting the real physical recording,
including its both-device phase. Reported heldout RMS changes are
DESCRIPTIVE, NOT unbiased proof of generalization. Later tests need
truly unseen physical data or pre-registered cross-validation.

## Run from GitHub on Arch (offline only)

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

timeout --signal=TERM --kill-after=3s 60s \
  ~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover \
  -s tests -p 'test_voice_m6_physical_click_ab.py' -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m6_physical_click_ab.py \
  --capture .audit/voice-aec/M6-physical-events-001 \
  --alignment .audit/voice-aec/M6-physical-event-alignment-001/m6-physical-event-alignment.json \
  --out .audit/voice-aec/M6-physical-click-ab-001

cd .audit/voice-aec/M6-physical-click-ab-001
sha256sum -c SHA256SUMS
cat m6-physical-click-ab.json
~~~

If tests fail, do not run the exporter. All original WAV and event
records stay unchanged in local ignored .audit. The output includes
three private WAVs per scene:
- R1_mouse_isolated
- R2_keyboard_isolated
- R3_both_holdout
- R4_voice_only_holdout (11 real input events; NOT globally clean)
- R5_voice_clicks_holdout

Optional local playback, starting at a LOWER headset volume because
transients can be sharp. There are just THREE choices each round.

~~~bash
pw-play R3_both_holdout/A_original.wav
pw-play R3_both_holdout/B_conservative.wav
pw-play R3_both_holdout/C_stronger.wav

pw-play R5_voice_clicks_holdout/A_original.wav
pw-play R5_voice_clicks_holdout/B_conservative.wav
pw-play R5_voice_clicks_holdout/C_stronger.wav
~~~

Listen for mechanical reduction AND speech integrity, especially
consonants/sibilants. Indistinguishable is an acceptable judgement.
Numeric reports include fraction of changed PCM samples, protected
samples outside event masks, and whole-stage highband energy change.
No user speech clean ground truth exists in physical doubletalk.
