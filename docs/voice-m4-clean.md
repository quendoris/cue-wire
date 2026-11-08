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

## DeepFilterNet compensated-length correction (v2)

The Rust CLI's `-D` / `--compensate-delay` slices the known STFT and
model lookahead latency from the **beginning** of its produced buffer; the
Rust CLI does *not* automatically extend the ending. Passing unpadded RAW
to `deep-filter -D` therefore produces a shortened WAV (often about 30 ms).
The original CueWire v1 correctly failed closed on a >10 ms change, but did
not prepare a padded model input.

CueWire v2 makes a *private temporary derivative* of RAW with at least 250 ms
of trailing zero-valued PCM16 frames, rounded to 480-frame STFT hops, **before**
running DeepFilterNet. It then converts model output to PCM16 and keeps exactly
the first `raw_frames` compensated samples. This discards only the deliberately
added artificial tail. It does **not** invent audio samples or discard speech
to conceal a model failure. A model-reported delay outside 0..100 ms still
stops the run. The measured delay, input/output frames, and the padded input
hash are recorded in `manifest.json`.

Previously failed `clean-001` is evidence and must **not** be overwritten.
After updating the branch, use `clean-002` and inspect its new manifest.

## M4 R0 recovery after a sherpa NumPy import error

An earlier version resolved the executable `.audit/voice-tools/sherpa/bin/python`
through its symlink to the **base** uv Python 3.11. That bypassed
`pyvenv.cfg` and produced `ModuleNotFoundError: No module named 'numpy'`.
The fixed runner preserves the virtualenv's `bin/python` pathname.

Verify which environment imports the modules, **without** global installation:

```bash
cd /data/cue-wire
.audit/voice-tools/sherpa/bin/python -c 'import sys,numpy,soundfile,sherpa_onnx; print("venv:",sys.prefix); print("base:",sys.base_prefix); print("numpy:",numpy.__version__)'
python3 -m unittest discover -s tests -p test_voice_m4_clean.py -v
```

The displayed prefix should refer to `.audit/voice-tools/sherpa`.
If a direct `sherpa/bin/python` import *still* fails, inspect that
environment separately; only then install the missing dependency
into that isolated interpreter using `uv pip install --python
.audit/voice-tools/sherpa/bin/python numpy soundfile sherpa-onnx`
(explicit network/installation action).

**Do not overwrite** the failed `clean-002` run. It contains the
DeepFilter result and failure log, and is legitimate audit evidence.
For an end-to-end rerun use a fresh directory `clean-003`.

## First M4 cleanup

```bash
cd /data/cue-wire
python3 tools/voice_m4_clean.py \
  --in .audit/voice-captures/M4-R0-ALSA-007/raw.wav \
  --out .audit/voice-captures/M4-R0-ALSA-007/clean-003
```

`clean-003/` **must not exist** before the run. To retry, use a new output folder such as `clean-004`, or inspect the failed logs without deleting/replacing prior evidence.

Artifacts are private and never uploaded automatically:

- `01-deepfilter.wav`: first stage (exact RAW length, model latency compensated);
- `02-dpdfnet2-soft.wav`: second stage;
- `01-deepfilter.log`, `01b-pcm16-conversion.log`, `02-dpdfnet2-soft.log`;\n- `deepfilter-padded-input.wav`, `deepfilter-compensated-full.wav`: private, auditable intermediate WAVs;
- `manifest.json`: input hash, stage hashes, metrics, pinned model hash;
- `SHA256SUMS`: both produced WAVs.

The first five seconds' RMS is reported as **time-window RMS**, not a measured noise floor. The run succeeds technically with status `rendered_awaiting_auditory_review`, not a proof of audible superiority.

## Acceptance

```bash
cd /data/cue-wire
cd .audit/voice-captures/M4-R0-ALSA-007/clean-003
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
