# M6 Physical-007 — C006-frozen residual keyboard attack and ringing

**Status:** experimental OFFLINE, noncausal. Human listening NOT yet
performed. No new microphone capture, no changes to PipeWire.

## Motivation from actual hearing

The operator chose C_006_percussive_strong over B_006 in the
Physical-006 A/B, with quieter keyboard clicks and natural voice.
**The old C006 is frozen as A**; it must remain byte-for-byte identical.

Physical-004 broadband suppression cut off ends of prolonged vowels
like "Ааааа". Physical-005 and 006 restored natural voice. We must
not recreate the 004 broadband damage by arbitrarily raising gain.

The same existing original M6 recording is reused (44.330667s mono
48kHz PCM16, 364 anonymous class-only keyboard/mouse events).
No individual key ID, button ID, pointer data or typed text is read.

## Model

The new correction applies ONLY inside an intersection of:
1. Previously verified acoustic event mask used by M6-006.
2. Anonymous *keyboard-class* press/release event times, from 12ms
   before each effective acoustic anchor until 125ms after.

Stage B — an extra short (512-sample) STFT with hop 128 estimates
residual broadband attack using 25-frame harmonic temporal and
19-bin percussive frequency medians. Its frequency mask is
attenuated below 2 kHz and smoothly fades at 12–14 kHz.
The inverse reference is mixed with the EXACT old C006 output.

Stage C — a stronger short-stage correction plus longer
(2048-sample, hop 256) STFT residual resonance processing.
Long-tail spectral correction is additionally suppressed near
recognized voice harmonic multiples, +/-35 Hz. A 90–470Hz
autocorrelation pitch cue is estimated in 50ms windows.
If an existing voice-protected interval has no confident pitch,
additional long-tail suppression is DISABLED below 4200Hz.

This preserves much of the lower harmonic speech structure, but
DOES NOT prove it preserves all upper harmonics or consonants.
Long-time STFT requires future audio; NOT realtime ready.

## A/B options: exactly three WAVs per stage

| A/B label | Exact filename | Difference from prior C006 |
|---|---|---|
| A | A_006_percussive_strong.wav | Byte-for-byte preferred C006 |
| B | B_007_attack.wav | Extra short residual acoustic attack, gain 1.0 |
| C | C_007_attack_and_tail.wav | Stronger attack (1.2) plus harmonic-protected tail (1.2) |

Privacy, fail-closed integrity and speech-safety checks:
- Immutable original mic, event JSONL, manifest and alignment
  SHA256 verified before and after calculations.
- No correction outside verified KEYBOARD event windows; there B/C
  must be bit-identical to user's preferred C006.
- Off ALL original events, the user PCM16 must be unchanged from mic.
- Mouse-only stage MUST be bit-identical among A, B and C.
- Refuse missing data, symlink sources, overwritten output, clipping,
  invalid events or extra fields that could reveal key identity.
- Added 90–650Hz error in protected voice+keyboard windows must be
  45dB below the corresponding C006 voice lowband RMS, or fail.
  This is ONLY a lowband error proxy, not proof of good voice quality.

## Initial physical experimental estimates — NOT independent

On the SAME original 44-second physical WAV, candidate parameters
were explored. Relative to C006 in full R5 voice+clicks stage:
- B: approx **-0.45 dB** more overall 1300–9000Hz RMS attenuation.
- C: approx **-0.66 dB** more overall 1300–9000Hz RMS attenuation.
- Added 90–650Hz lowband error in voiced keyboard windows below
  -60dB relative to C006 lowband.

Those small gains may or may not be audible, and the highband
also includes speech content. These are descriptive changes, NOT
clean-source click SNR, speech-quality proof, or unbiased validation
on heldout data: we explored this same recording repeatedly.
Human hearing is a necessary gate before any selection.

## Run on Arch: tests BEFORE export

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

timeout --signal=TERM --kill-after=3s 120s \
  ~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests \
  -p 'test_voice_m6_*residual*.py' -v
~~~

Expected 10 synthetic tests from two files. STOP on any red.

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m6_physical_residual_ab.py \
  --capture .audit/voice-aec/M6-physical-events-001 \
  --alignment .audit/voice-aec/M6-physical-event-alignment-001/m6-physical-event-alignment.json \
  --out .audit/voice-aec/M6-physical-residual-001

cd .audit/voice-aec/M6-physical-residual-001
sha256sum -c SHA256SUMS
~~~

Exports 15 private WAVs (five phases x three roles), a numerical
m6-physical-007-report.json and SHA256SUMS. No event timing list or
private microphone source is copied to GitHub. No autoplay.

### Mandatory control: unchanged C006

From /data/cue-wire, check that BOTH hashes are the same:

~~~bash
sha256sum \
  .audit/voice-aec/M6-physical-percussive-001/R5_voice_clicks_holdout/C_006_percussive_strong.wav \
  .audit/voice-aec/M6-physical-residual-001/R5_voice_clicks_holdout/A_006_percussive_strong.wav
~~~

If they differ, STOP before listening and investigate.

## Listen ONLY to voice+clicks R5 first

~~~bash
cd /data/cue-wire/.audit/voice-aec/M6-physical-residual-001
pw-play R5_voice_clicks_holdout/A_006_percussive_strong.wav
pw-play R5_voice_clicks_holdout/B_007_attack.wav
pw-play R5_voice_clicks_holdout/C_007_attack_and_tail.wav
~~~

Keep low comfortable fixed volume. Listen separately for residual
keyboard ATTACK, ringing tail, the ending of "Аааа", consonants,
sibilants and any musical artifacts. If B/C sound worse than A,
A stays the preferred benchmark even if a dB metric improves.

A genuinely unseen physical capture is eventually needed for robust
generalization; a separately measured CAUSAL implementation is needed
before live virtual microphone operation.
