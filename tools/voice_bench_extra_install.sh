#!/usr/bin/env bash
# Optional extra speech models. Explicit installs only; no sudo or audio changes.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
base="$PWD/.audit/voice-tools"
help() {
  cat <<'TXT'
CueWire additional speech models (explicit downloads only):
  bash tools/voice_bench_extra_install.sh --check
  bash tools/voice_bench_extra_install.sh --install sherpa
  bash tools/voice_bench_extra_install.sh --download sherpa-models
  bash tools/voice_bench_extra_install.sh --download dpdfnet8
  bash tools/voice_bench_extra_install.sh --install speechbrain
Python environments are isolated in .audit/voice-tools, with uv caches separate.
SpeechBrain may fetch weights at the FIRST explicit model run.
TXT
}
if (( $# == 1 )) && [[ "$1" == --help || "$1" == -h ]]; then help; exit 0; fi
if (( $# == 1 )) && [[ "$1" == --check ]]; then
  for f in "$base/sherpa/bin/python" "$base/sherpa-models/gtcrn_simple.onnx" \
           "$base/sherpa-models/dpdfnet2_48khz_hr.onnx" \
           "$base/sherpa-models/dpdfnet8_48khz_hr.onnx" \
           "$base/speechbrain/bin/python"; do
    if [[ -f "$f" ]]; then printf 'PRESENT  %s\n' "$f"
    else printf 'ABSENT   %s\n' "$f"; fi
  done
  exit 0
fi
if (( $# != 2 )); then help; exit 2; fi
action=$1
choice=$2
if [[ "$action" == --install ]]; then
  if ! command -v uv >/dev/null; then
    echo 'Нужен uv. Установи отдельно, например sudo pacman -S uv.' >&2
    exit 1
  fi
  case "$choice" in
    sherpa) name=sherpa ;;
    speechbrain) name=speechbrain ;;
    *) help; exit 2 ;;
  esac
  if [[ -e "$base/$name" ]]; then
    echo "Окружение уже существует: $base/$name. Не перезаписываю." >&2
    exit 1
  fi
  mkdir -p "$base"
  printf 'Создаю %s в %s с Python 3.11. uv может скачать интерпретатор/зависимости.\n' "$name" "$base/$name"
  uv venv --python 3.11 "$base/$name"
  if [[ "$name" == sherpa ]]; then
    uv pip install --python "$base/$name/bin/python" 'sherpa-onnx' 'numpy' 'soundfile'
  else
    uv pip install --python "$base/$name/bin/python" 'speechbrain' 'numpy' 'soundfile'
  fi
  printf 'Готово. Никаких системных аудионастроек не изменено.\n'
  exit 0
fi
if [[ "$action" == --download && "$choice" == sherpa-models ]]; then
  command -v gh >/dev/null || { echo 'Нужен gh (GitHub CLI).' >&2; exit 1; }
  dest="$base/sherpa-models"
  mkdir -p "$dest"
  for file in gtcrn_simple.onnx dpdfnet2_48khz_hr.onnx; do
    if [[ -e "$dest/$file" ]]; then echo "Уже есть $file; ничего не перезаписываю." >&2; exit 1; fi
  done
  echo 'Скачиваю две официальные ONNX-модели: GTCRN 0.5 МБ, DPDFNet2 10.6 МБ.'
  gh release download speech-enhancement-models --repo k2-fsa/sherpa-onnx \
    --pattern gtcrn_simple.onnx --pattern dpdfnet2_48khz_hr.onnx --dir "$dest"
  (cd "$dest"
    printf '%s  %s\n' \
      0b399f8a58dc4d70d8cd97541f5c39869406145193b957d00a03b66070944928 \
      dpdfnet2_48khz_hr.onnx | sha256sum -c
    sha256sum gtcrn_simple.onnx dpdfnet2_48khz_hr.onnx)
  exit 0
fi
if [[ "$action" == --download && "$choice" == dpdfnet8 ]]; then
  command -v curl >/dev/null || { echo 'Нужен curl.' >&2; exit 1; }
  dest="$base/sherpa-models"
  mkdir -p "$dest"
  if [[ -e "$dest/dpdfnet8_48khz_hr.onnx" ]]; then echo 'Файл уже есть.' >&2; exit 1; fi
  echo 'Скачиваю DPDFNet8 48 кГц с официального Ceva-IP HF. Хеш распечатаю.'
  curl --fail --location --retry 2 --connect-timeout 15 \
    --output "$dest/dpdfnet8_48khz_hr.onnx.part" \
    'https://huggingface.co/Ceva-IP/DPDFNet/resolve/main/onnx/dpdfnet8_48khz_hr.onnx'
  [[ $(stat -c %s "$dest/dpdfnet8_48khz_hr.onnx.part") -gt 1000000 ]] || {
    echo 'Неполная модель, не принимаю.' >&2
    exit 1
  }
  mv -- "$dest/dpdfnet8_48khz_hr.onnx.part" "$dest/dpdfnet8_48khz_hr.onnx"
  sha256sum "$dest/dpdfnet8_48khz_hr.onnx"
  echo 'Нужно зафиксировать ревизию и контрольную сумму модели в протоколе прогона.'
  exit 0
fi
help
exit 2
