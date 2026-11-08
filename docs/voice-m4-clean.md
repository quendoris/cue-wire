# CueWire M4 / R0: offline cleaning, no master processing

**Input:** `.audit/voice-captures/M4-R0-ALSA-007/raw.wav` (48 kHz, mono PCM16, unchanged)

**Pipeline:** Rust deep-filter (default DeepFilterNet2, `-D`, without extra post-filter) → sherpa-onnx DPDFNet2 48 kHz HR, **offline** `attenuation_limit_db=12` → private WAV derivatives.

The 12 dB setting is the sherpa-onnx offline denoiser attenuation limit, **not** gain normalization. No compression, voice EQ, AEC, limiter, or mic/Steam/PipeWire changes are made. Listening comparison is still mandatory; RMS and peak numbers do not certify speech quality.

## Preflight

Run from `/data/cue-wire` on Arch. Inspect the existing local model environment without changing system configuration:

```bash
cd /data/cue-wire
bash tools/voice_bench_install.sh --check
bash tools/voice_bench_extra_install.sh --check
```

The script expects the local Rust deep-filter executable at `.audit/voice-tools/deep-filter-0.5.6-x86_64-unknown-linux-musl`, sherpa at `.audit/voice-tools/sherpa/bin/python`, and the ONNX checkpoint in `.audit/voice-tools/sherpa-models`. The ONNX SHA256 must match the pinned checkpoint from `voice_bench_extra_install.sh`.

If sherpa-onnx or the ONNX models are genuinely missing, the existing **explicit opt-in** installers are:

```bash
bash tools/voice_bench_extra_install.sh --install sherpa
bash tools/voice_bench_extra_install.sh --download sherpa-models
```

These commands may download external dependencies; the cleanup script never installs/downloads anything. If the deep-filter binary has a different location, export `CUEWIRE_BENCH_DEEPFILTER=/absolute/path/to/deep-filter` for the current shell. Do not point to the Python `deepFilter` CLI: this pipeline uses the Rust executable matching existing CueWire benchmarks.

## First M4 cleanup

```bash
cd /data/cue-wire
python3 tools/voice_m4_clean.py \
  --in .audit/voice-captures/M4-R0-ALSA-007/raw.wav \
  --out .audit/voice-captures/M4-R0-ALSA-007/clean-001
```

`clean-001/` **must not exist** before the run. To retry, use a new output folder such as `clean-002`, or inspect the failed logs without deleting/replacing prior evidence.

Artifacts are private and never uploaded automatically:

- `01-deepfilter.wav`: first stage;
- `02-dpdfnet2-soft.wav`: second stage;
- `01-deepfilter.log`, `01b-pcm16-conversion.log`, `02-dpdfnet2-soft.log`;
- `manifest.json`: input hash, stage hashes, metrics, pinned model hash;
- `SHA256SUMS`: both produced WAVs.

The first five seconds' RMS is reported as **time-window RMS**, not a measured noise floor. The run succeeds technically with status `rendered_awaiting_auditory_review`, not a proof of audible superiority.

## Acceptance

```bash
cd /data/cue-wire
cd .audit/voice-captures/M4-R0-ALSA-007/clean-001
sha256sum -c SHA256SUMS
pw-play ../raw.wav
pw-play 01-deepfilter.wav
pw-play 02-dpdfnet2-soft.wav
```

These are **separate, manually invoked playback commands**, never simultaneous monitoring. Compare speech consonants (`с`, `ш`, `ф`, `т`), natural low-frequency tone, residual room noise, and musical/metallic artifacts. If the final output sounds less natural than stage 1, stage 1 may be preferred; do not assume two denoisers are automatically better.

Expected technical checks: positive duration, mono PCM16 48 kHz outputs, no clipped samples, no unexpected >10 ms duration drift, original SHA256 unchanged. The offline command does not change system settings or create daemons. Normal termination cleans model process groups; sudden SIGKILL of the parent is also guarded for direct child processes, but it cannot certify behavior of unknown grandchildren.

## Local self-tests (mock engines, NOT neural quality tests)

```bash
python3 -m unittest discover -s tests -p test_voice_m4_clean.py -v
```

No real audio content, checkpoint or microphone data should be committed to the PUBLIC repository. `.audit` must remain private.
