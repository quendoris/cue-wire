#!/usr/bin/env bash
set -euo pipefail
if (( $# != 2 )); then
    printf 'Usage: bash tools/voice_ab_generate.sh INPUT_MONO_PCM16_48K.wav OUTPUT_DIR\n' >&2
    printf 'CUEWIRE_VOICE_PYTHON can override the dedicated Python interpreter.\n' >&2
    exit 2
fi
source_file=$1
outdir=$2
python_bin="$HOME/.venvs/cuewire-voice-lab/bin/python"
if [[ -v CUEWIRE_VOICE_PYTHON ]]; then python_bin="$CUEWIRE_VOICE_PYTHON"; fi
if [[ ! -f "$source_file" ]]; then
    printf 'ERROR: no such input WAV: %s\n' "$source_file" >&2
    exit 1
fi
if [[ ! -x "$python_bin" ]]; then
    printf 'ERROR: missing voice lab venv: %s\n' "$python_bin" >&2
    printf 'Set up with:\n  python3 -m venv ~/.venvs/cuewire-voice-lab\n' >&2
    printf '  ~/.venvs/cuewire-voice-lab/bin/python -m pip install -r tools/voice_ab_requirements.txt\n' >&2
    exit 1
fi
if [[ -e "$outdir/voice_before.wav" ]]; then
    printf 'ERROR: output already exists: %s/voice_before.wav\n' "$outdir" >&2
    exit 1
fi
"$python_bin" tools/voice_ab_lab.py --input "$source_file" --out "$outdir"
cp -- "$source_file" "$outdir/voice_before.wav"
(
  cd -- "$outdir"
  sha256sum voice_before.wav voice_band_only_v2.wav \
    voice_mild_v2.wav voice_focus_v2.wav voice_c_plus_v3.wav > SHA256SUMS
)
printf 'Ready: bash tools/voice_ab_play.sh "%s" all\n' "$outdir"
