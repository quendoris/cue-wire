# M6 Physical-003 — adaptive click-decay anti-wave (OFFLINE)

## Motivation from real recorded signals

M6 Physical-002 attenuated just a few milliseconds around the largest
acoustic peak. The operator heard practically no difference and clear
remaining keyboard clicks in BOTH R3 (both devices) and R5 (voice +
clicks), while reported voice remained perceptually unchanged.
Independent re-analysis of the SAME physical M6-physical-events-001
recording found among isolated usable acoustic event windows:

| Signal | Event observations | Approx median tail | 75th percentile |
|---|---:|---:|---:|
| Mouse | 36 | 5.5 ms | 11.2 ms |
| Keyboard | 23 | 40 ms | 58 ms |

These durations depend on chosen highband-envelope thresholds and
isolation windows; they are not ground truth of individual key sounds.
In particular the median fraction of modeled excess 1300–9000Hz
event energy in the FIRST 8ms was ~77% for mouse, but only ~46% for
keyboard. This suggests the old window missed much of keyboard decay.

## Algorithm

The existing 44.33s physical microphone, 364 anonymous input
press/release events and M6 verified class-dependent alignment are
reused. No new physical recording or device access.

1. Verify SHA256 of original capture mic, anonymous event stream,
   physical capture manifest, previous M6 time alignment; additionally
   verify original phase/event counts via existing M6 reader.
2. Build zero-phase 1300–9000Hz audio reference and 1ms highband
   RMS envelope. The model uses FUTURE audio and is NOT live-ready.
3. Train MAXIMUM per-event decay durations ONLY on isolated,
   speech-free mouse and keyboard calibration phases. Reject events
   with another event closer than 65ms to prevent merged signatures.
   Freeze each device class's 90th-percentile+12ms tail cap, bounded
   mouse 20–60ms and keyboard 50–110ms.
4. For each event on the full recording, use its already verified
   effective time anchor, search +/-5ms (mouse) or +/-9ms (keyboard)
   for the PRESENT physical waveform peak, and require that this
   peak exceed 4x local pre-event noise AND .0005 RMS.
5. End compensation when the 2ms-smoothed highband RMS falls below
   the event baseline for 4 of 5ms, or reaches the frozen tail cap.
   Apply local cosine onset/end tapers. Overlapping corrections are
   combined using MAX, never summed beyond unity.
6. Build an independent inverted anti-track from the current
   highband acoustic signal, with amplitude proportional to local
   highband energy exceeding the pre-event floor. Do not learn from
   clean speech. Sum ONLY at the final digital output.
7. Independently compute an optional voice guard: 150–850Hz
   18ms RMS + 61ms sustained median, threshold derived ONLY from
   speech-free mouse/keyboard calibration audio (98th percentile
   x1.25). Over voiced-looking regions, smoothly reduce the
   anti-track amplitude by at most 85%. This is a simple heuristic,
   NOT validated voice activity recognition or a proof that speech
   consonants are preserved.
8. Produce THREE output options in each scene (no new EQ/limiter):
      A_original.wav = original PCM16 microphone
      B_adaptive_tail.wav = mic - .75 * dynamic_mask * highband
      C_voice_guard.wav = mic - .75 * dynamic_mask * highband * guard
9. Fail if any sample OUTSIDE the event mask changes in B or C,
   if clipping is predicted, if private inputs changed, or if an
   output directory already exists. All audio stays in ignored .audit.

## Current physical observation — not a fresh blind claim

On the real 44-second recording, the pre-GitHub offline development
pilot with the same model class achieved approximately these
whole-stage 1300–9000Hz RMS changes versus raw:

| Scene | M6 Physical-002 strong | M6 Physical-003 B tail | M6 Physical-003 C guard |
|---|---:|---:|---:|
| Mouse alone | -1.31 dB | -1.75 dB | -1.75 dB |
| Keyboard alone | -1.32 dB | -3.00 dB | -2.85 dB |
| Mouse + keyboard | -1.47 dB | -3.66 dB | -3.66 dB |
| Voice-only stage* | -0.039 dB | -0.085 dB | -0.085 dB |
| Voice + clicks | -0.45 dB | -1.94 dB | -1.17 dB |

*The 'voice only' stage actually contains 11 input events, so it is
NOT a clean voice reference. Whole-stage RMS reduction is an ENERGY
measurement, not isolated click noise reduction, voice quality or
intelligibility. The same original capture was used during exploratory
parameter selection, so all results are DESCRIPTIVE, not an unbiased
out-of-sample proof. Zero-phase processing is offline/noncausal.

One independent local implementation of the physical prototype read
all source hashes, produced 15 candidate WAVs and passed SHA256SUMS.
The exact GitHub code and tests must still run on the operator's Arch
before listening.

## Commands on Arch — no new microphone capture

~~~bash
cd /data/cue-wire
git switch feature/pcm-mixer-playground
git pull --ff-only

timeout --signal=TERM --kill-after=3s 90s \
  ~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover \
  -s tests -p 'test_voice_m6_physical_tail_ab.py' -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m6_physical_tail_ab.py \
  --capture .audit/voice-aec/M6-physical-events-001 \
  --alignment .audit/voice-aec/M6-physical-event-alignment-001/m6-physical-event-alignment.json \
  --out .audit/voice-aec/M6-physical-tail-001

cd .audit/voice-aec/M6-physical-tail-001
sha256sum -c SHA256SUMS
cat m6-physical-tail-report.json
~~~

Run the exporter ONLY if all tests PASS. Never overwrite earlier
physical source or output directories. The output contains five
scenes x three mono PCM16 WAVs, a JSON report, and SHA256SUMS.
The report includes physical per-class training tail statistics,
event mask coverage, voice guard threshold and band-energy proxies.

## Short subjective test (max three variants)

Start at a low comfortable headset level; no automatic playback and
no changes to system audio volume or routing.

~~~bash
pw-play R3_both_holdout/A_original.wav
pw-play R3_both_holdout/B_adaptive_tail.wav
pw-play R3_both_holdout/C_voice_guard.wav

pw-play R5_voice_clicks_holdout/A_original.wav
pw-play R5_voice_clicks_holdout/B_adaptive_tail.wav
pw-play R5_voice_clicks_holdout/C_voice_guard.wav
~~~

A and B/C labels are VISIBLE (not blind). For an actual preference
claim, a separate blinded three-choice export can follow. Evaluate
both perceived clicks and ANY voice changes. The user already reported
no audible voice damage in Physical-002, but that result does NOT
prove Physical-003 voice preservation at overlapping event times.

This remains a local research experiment. To deploy in a live virtual
microphone, use independently audited causal block DSP and a measured
latency budget, not the zero-phase filtfilt used here.
