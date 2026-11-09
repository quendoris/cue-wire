# M5 — opt-in physical playback-level control, global sink left unchanged

## Motivation — 2026-11-09

The user read the current actual PipeWire/PulseAudio mixer levels:
- Physical analogue sink \`alsa_output.pci-0000_00_1f.3.analog-stereo\`:
  **60%, -13.31dB**, both channels, no mute.
- Physical USB mic \`alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback\`:
  **100%, 0dB**, no mute.
- User recalls manually reducing speaker output at some time in
  the past, but does not know whether this happened between the
  two M5 doubletalk experiments. Believes sink has ordinarily been 60%.
  NEVER assert an unlogged historical 100% setting.

The cross-take comparison on precisely the same early digital
music shows new vs old physical microphone 500–1500Hz
**-10.8 to -12.8dB**, 1500–4000Hz **-11.7 to -12.4dB**,
while 20–70Hz is relatively stable in the later two windows.
Earlier fullband speaker-only heldout ERLE **5.73dB**,
prospective new take **0.67881dB FAIL**.

The **-13.31dB** gap between system-reported present
60% and theoretical 100% is numerically close to the
11–12dB change in the speaker-related microphone
spectrum. This is an intriguing UNVERIFIED hypothesis,
not a proof of historical volume, old sound pressure,
mic AGC, gain, or whether physical attenuation is linear.

## Experimental rules

Use the EXISTING unmodified
\`tools/voice_m5_aba.py\` on **two fresh private captures**
with the same 20s SHA256-verified
\`M5-006/reference-sent.wav\`.
This reuses the working OFF → ON → OFF recorder and
child-process ownership, without any new code.

The physical sink remains **at its current 60%**:
**never issue \`pactl set-sink-volume\`, \`wpctl set-volume\`,
change system defaults, or set sink to 100%** for this test.
Use **ONLY the isolated \`pw-play --volume\` of the experiment**.
Fixed order and levels:

1. Per-stream volume **0.15**, 8s silent → 20s reference
   music → 8s silent, operator silent throughout.
2. Per-stream volume **0.30**, same 8/20/8s experiment,
   same unchanged hardware positions, no intervening
   system music, voice or mic processing.
3. Read sink/source volume and mute again after captures.
   If settings changed or either take was interrupted,
   do not claim a matched comparison.

Two independent ~36s captures are a convenience and can
be affected by run-to-run ambient/AGC changes. They cannot
recreate the unknown historical mixer configuration.
If required later, a tighter within-one-take level sweep
can be developed separately; do not pass retrospective
conclusions off as a controlled historical reconstruction.

The \`pw-play\` per-stream .15 → .30 change is a factor
two in nominal linear amplitude, or roughly **+6.02dB**,
IF the player treats the supplied volume as linear gain.
The actual speaker and physical microphone effect MUST
be measured, not assumed. Fullband raw mic RMS may change
less due to reference-independent background. Compare
both OFF and ON mic RMS, ON/OFF power ratio, and whether
OFF baseline remains stable.

The user should not speak, move the mic, switch hardware
outputs, change system gain, or play another audio source.
There must be no loopback to a speaker or headset.

## Instructions — opt-in, local only

Run local tests first if the current working tree has
unverified changes. Check current levels:

~~~bash
cd /data/cue-wire
git pull --ff-only
pactl get-sink-volume 'alsa_output.pci-0000_00_1f.3.analog-stereo'
pactl get-source-volume 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback'
pactl get-sink-mute 'alsa_output.pci-0000_00_1f.3.analog-stereo'
pactl get-source-mute 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback'
~~~

Run **one at a time**. Never overwrite older records:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_aba.py \
  --capture .audit/voice-aec/M5-006 \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --pre-seconds 8 --post-seconds 8 \
  --volume 0.15 \
  --out .audit/voice-aec/M5-level-015-001

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_aba.py \
  --capture .audit/voice-aec/M5-006 \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --pre-seconds 8 --post-seconds 8 \
  --volume 0.30 \
  --out .audit/voice-aec/M5-level-030-001
~~~

Read the sink/source volumes/mutes again and verify
each private manifest's SHA256 against its own
\`SHA256SUMS\`. Both manifests must have status
\`captured_aba_for_noise_diagnostics\` and nonzero
music-on relative to off controls. Pass/fail thresholds
for practical AEC have NOT changed; this is
**an acoustic level-control diagnostic**, not voice
quality validation.

The only files requested for review are the two
private \`manifest.json\` files, each with its
\`SHA256SUMS\`. No WAV upload is needed. Keep originals
as evidence; the two WAVs remain private under \`.audit\`.

## Actual sequential playback-level A/B results — 2026-11-09

Both real runs were supplied as paired \`manifest.json\` and
\`SHA256SUMS\`, each independently checked for manifest integrity:

| Per-stream volume | Actual manifest SHA256 | Physical microphone raw WAV SHA256 (from source manifest/SHA256SUMS) |
| --- | --- | --- |
| 0.15 | \`d38b12b6e5c4740deb9149dcfd43d0c49610ad4d9a098e96da425e4ae40786cf\` | \`f895567b0abe53612ee20385435d31577066f2f1589bad25b1af98b980adc30b\` |
| 0.30 | \`3c0b6c2ba4468b84edd9fb550251277c62bd1a6ec57dc262f08818922ac9f9f4\` | \`5c9fa9b70b2c23e8c62dac5d1b17dc3e9106593af8f5e5754b05ee69013ccb50\` |

**These uploaded files do NOT contain the original microphone WAVs**.
The two WAV digests match between the submitted \`SHA256SUMS\` and
their manifests, but independent byte-for-byte WAV rehashing
is not possible without the RAW files.

Both have \`status=captured_aba_for_noise_diagnostics\`,
explicit identical physical mic and speaker names and
sample spec \`s16le 1ch 48000Hz\`, the same 20s
digital reference
\`afd3b0ea1141147d6df284bfab62d2a9c9adc579053cfbea8c35559f2e94aa90\`,
0 clipped mic samples, mic WAV duration 36.0533s and
36.0747s respectively, and near-identical
playback startup 8.0267 and 8.0224s after recorder launch.
The 20s playback wall times were 20.0504 and 20.0520s.
No AEC or voice was processed.

**Total/phase power levels, dBFS:**

| Metric | 0.15 | 0.30 | Change |
| --- | ---: | ---: | ---: |
| All mic samples, RMS | -58.201 | -56.888 | +1.313 |
| Speaker OFF combined (mean of pre/post powers) | -58.56368 | -57.62583 | +0.93785 |
| Speaker ON, selected interior | -57.84618 | -56.01336 | **+1.83282** |
| ON minus combined OFF | +0.71750dB | +1.61247dB | +0.89497dB |
| ON half-second-window median | -57.92998 | -56.01541 | +1.91457 |
| Pre-OFF RMS | -57.95061 | -57.20516 | +0.74545 |
| Post-OFF RMS | -59.27774 | -58.09165 | +1.18609 |
| Pre-OFF median | -58.28488 | -57.28505 | +0.99983 |
| Post-OFF median | -59.29004 | -58.84268 | +0.44736 |

The OFF background is NOT constant: in the 0.15
take, post-OFF mean-power RMS is **1.32713dB lower**
than pre-OFF (medians: **1.00516dB** lower).
In the 0.30 take, post-OFF is **0.88649dB lower**
(medians: **1.55763dB** lower). There are short
high-energy windows both while music plays and in
supposedly speaker-OFF intervals (e.g. -55.29183dBFS
in one post-OFF 0.30 window).

**Conditional engineering estimate, NOT a measured isolated
speaker-echo fraction:** assuming the arithmetic mean of
two OFF-phase mean powers accurately represents independent
stationary background throughout the ON interval, the
extra ON power estimate
\`P_on - P_off\` increases by **3.106x in power**
(**+4.922dB**) as \`pw-play\` level goes from
0.15 to 0.30. The nominal per-stream amplitude
doubling corresponds to **+6.021dB in power level**
for a pure linear speaker component. The discrepancy
cannot be assigned to a particular nonlinearity:
the OFF baseline itself varies within and across
takes, microphone DSP/AGC is unmeasured, and
speaker playback may not scale identically.

The raw observations support that changing the
isolated playback gain changes the physical mic
signal in the expected direction, with ON increasing
more than baseline OFF. They DO NOT prove that
physical speaker SPL doubled, that speaker 60% was
different in the historical experiments, that
AEC ERLE should increase in a monotone fashion,
or that voice quality is established.

**Next experiment, if needed:** use one uninterrupted
mic recording with quiet → .15 playback → quiet →
.30 playback → quiet to compare levels and recover
OFF baseline trends inside the SAME run. Better
still, measure a known reference-correlated
component separately from total raw RMS.
A two-take chronological A/B without repeats
cannot exclude ambient drift and transient noise;
the above should not be promoted into a causal
volume attribution.

The prospective **0.67881dB FAIL** and M4 approved
voice master remain unchanged. No more retrospective
threshold changes or AEC filter tuning.

## Next read-only quantitative check: reference-coherent microphone power

Added \`tools/voice_m5_playback_coherence.py\` to reuse the
**already stored two A/B/A physical microphone WAVs** and
the ORIGINAL exact digital 20s M5-006 music reference,
with NO new capture needed. The new tool has immutable
source SHA256 constants for the two manifests and the
two physical WAVs verified above, plus the M5-006
reference/source-manifest hashes. Different data
will be rejected rather than silently compared.

For each take independently:

1. Align the digital reference to mic using the
   **first 1.0–3.7 seconds** and a 1500–4000Hz
   normalized correlation search near the logged
   \`pw-play\` launch. Do not change any historical
   AEC timing, FIR, acceptance gate or recordings.
2. Evaluate only reference **4–9 seconds** and
   **10–16 seconds**, not the alignment search
   interval; compute per-band mic PSD and
   magnitude-squared reference/mic coherence.
3. Estimate a **linear reference-linked acoustic
   power PROXY** by integrating \`P_yy(f) C_xy(f)\`
   over each band (Welch PSD, 4096 frames/50%
   overlap) for both volumes; compare in dB.
4. Run an unrelated-reference negative control
   using physical microphone speaker-OFF 2–6s
   versus music reference 3–7s (the music was
   not playing during that OFF interval).

This coherence-derived proxy is NOT an exact
measured speaker-echo SPL or clean voice transfer.
Finite-sample coherence bias, nonlinear mic DSP,
mic/background noise, 60% system-sink gain and
separate-run environmental changes remain relevant.
But this is a more source-specific comparison
than raw ON/OFF RMS and needs no additional sound
playback.

The script writes only numeric
\`playback-coherence.json\` and \`SHA256SUMS\`.
It has no hardware access and never writes audio.

Two regression tests were added. The M5 regression
file now contains **35 tests**; new tests check
the expected ~6dB increase for a doubled synthetic
source amplitude, exact source hash protection,
recordings' immutability and numeric-only outputs.
They still require local execution.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_playback_coherence.py \
  --low-capture .audit/voice-aec/M5-level-015-001 \
  --high-capture .audit/voice-aec/M5-level-030-001 \
  --out .audit/voice-aec/M5-playback-coherence-001

cd .audit/voice-aec/M5-playback-coherence-001
sha256sum -c SHA256SUMS
~~~

If the two input directories actually have other
names, use their real paths: the script checks
content SHA256s and stored per-stream volumes.
If output already exists, choose a new identifier
but never overwrite history.

Share **\`playback-coherence.json\`** only. Do not
share the private WAV unless separately needed.
Original **0.67881dB prospective FAIL** remains
unchanged regardless of this new diagnostic.

## Interpretation

Compare on/off RMS and background stability; use
the known relative player amplitude to test the
prediction that weaker speaker bleed approaches
the noncoherent noise floor. A comparable relative
change in mic level supports the sound-level
hypothesis but does NOT establish why historical
take 002 was quieter. It could be speaker
gain/position, mic gain/AGC, acoustic path,
or unrelated environmental differences.

If new recordings show the same physical output
level under different per-stream gains despite
different digital \`pw-play\` volumes, investigate
mixer, DSP, limiter or routing instead of
assuming a clean linear acoustic response.

Never relax the original prospective **0.67881dB FAIL**.
