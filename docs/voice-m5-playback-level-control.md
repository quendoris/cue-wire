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
