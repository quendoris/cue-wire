# Real-recording voice A/B laboratory

This is a separate **offline** and **optional** experiment, not a production
CueWire voice processor. It neither opens PipeWire nor changes system audio
devices. It makes no claim to recover lost/fully masked speech.

A difficult 3-second voice fixture exposed a critical failure: the original
Rust STFT enhancer created a metallic quality and a loud residual broadband
noise. Before changing the live engine, we compare three independently
generated candidates from the same input.

- A: zero-phase 85–6500 Hz bandpass, no spectral subtraction (control)
- B: A + gently smoothed nonstationary noise reduction
- C: more speech-focused 105–5100 Hz and stronger smoothing/reduction;
  can make the voice muffled

The tool adds **no artificial noise bed** and never starts playback. It
reports hashes, versions, makeup gain, RMS and peaks. Speech intelligibility
and timbre must be judged by listening; reduced RMS noise alone is NOT success.

## Optional Python environment (Arch Linux)

Keep this out of the production Rust build and outside system Python:

    python3 -m venv ~/.venvs/cuewire-voice-lab
    ~/.venvs/cuewire-voice-lab/bin/python -m pip install -r tools/voice_ab_requirements.txt

Run with an actual recording you own. The input must already be 48 kHz mono
signed PCM16 WAV (convert with ffmpeg if needed):

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_ab_lab.py \
      --input "voice_before.wav" --out voice-lab-ab

    pw-play voice-lab-ab/voice_band_only_v2.wav
    pw-play voice-lab-ab/voice_mild_v2.wav
    pw-play voice-lab-ab/voice_focus_v2.wav

Record the source, output SHA-256 checksums and software versions from
voice-lab-ab/report.json. Identical output bytes are expected when input,
Python/package versions and platform are held constant; cross-platform
floating-point equality is not guaranteed.

No personal speech samples should be committed to GitHub. Avoid automatic
fallback to an aggressive spectral gate. When comparisons are inconclusive,
preserve the original speech. More complex music/voice separation will need
a different source-separation stage and independent objective+listening tests.
