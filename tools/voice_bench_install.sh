#!/usr/bin/env bash
# Explicit opt-in installers: local .audit only; no sudo, services or PipeWire.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
root="$PWD/.audit/voice-tools"
usage() {
  cat <<'TXT'
CueWire — optional offline voice models.
  bash tools/voice_bench_install.sh --check
  bash tools/voice_bench_install.sh --resume rnnoise
  bash tools/voice_bench_install.sh --install rnnoise
  bash tools/voice_bench_install.sh --install clearvoice
  bash tools/voice_bench_install.sh --install resemble
  bash tools/voice_bench_install.sh --install voicefixer
  bash tools/voice_bench_install.sh --install separator

The --install command explicitly DOWNLOADS third-party code and may install
large PyTorch packages. RNNoise additionally downloads a model at build time.
Everything goes into .audit/voice-tools (not system-wide).
ClearVoice/Resemble/VoiceFixer can fetch model weights at first inference.
Afterwards: source tools/voice_bench_env.sh
TXT
}
if [[ "$#" == 1 && "$1" == --help ]]; then usage; exit 0; fi
if [[ "$#" == 1 && "$1" == --check ]]; then
  if [[ -x "$root/deep-filter-0.5.6-x86_64-unknown-linux-musl" ]]; then
    printf 'OK      %s\n' "$root/deep-filter-0.5.6-x86_64-unknown-linux-musl"
  fi
  for file in "$root/rnnoise/examples/rnnoise_demo" \
    "$root/clearvoice/bin/python" "$root/resemble/bin/resemble-enhance" \
    "$root/voicefixer/bin/voicefixer" "$root/separator/bin/audio-separator"; do
    if [[ -x "$file" ]]; then printf 'OK      %s\n' "$file"
    else printf 'ABSENT  %s\n' "$file"; fi
  done
  exit 0
fi
if [[ "$#" == 2 && "$1" == --resume && "$2" == rnnoise ]]; then
  if [[ ! -d "$root/rnnoise/.git" ]]; then
    echo 'RNNoise source checkout is missing. Use --install rnnoise first.' >&2
    exit 1
  fi
  for program in wget sha256sum autoreconf make; do
    command -v "$program" >/dev/null 2>&1 || {
      echo "Missing tool: $program" >&2
      exit 1
    }
  done
  (
    cd "$root/rnnoise"
    hash=$(cat model_version)
    [[ "$hash" =~ ^[a-f0-9]{64}$ ]] || { echo 'Invalid model_version hash' >&2; exit 1; }
    archive="rnnoise_data-$hash.tar.gz"
    echo "Renewing or resuming $archive (upstream file is ~56 MB)."
    if [[ ! -f "$archive" ]] || ! printf '%s  %s\n' "$hash" "$archive" | sha256sum -c --status; then
      wget --continue --timeout=25 --tries=3 \
        "https://media.xiph.org/rnnoise/models/$archive"
    fi
    printf '%s  %s\n' "$hash" "$archive" | sha256sum -c
    ./download_model.sh
    autoreconf -isf
    ./configure
    make -j2
    test -x examples/rnnoise_demo
    sha256sum examples/rnnoise_demo
  )
  echo 'RNNoise build completed with an upstream-verified model SHA-256.'
  exit 0
fi
if [[ "$#" != 2 || "$1" != --install ]]; then usage; exit 2; fi
if [[ "$2" == rnnoise ]]; then
  for dep in git autoreconf autoconf automake libtoolize pkg-config make cc; do
    command -v "$dep" >/dev/null || {
      printf 'Missing build tool: %s. Check Arch packages base-devel, automake, autoconf, libtool, pkgconf.\n' "$dep" >&2
      exit 1
    }
  done
  if [[ -e "$root/rnnoise" ]]; then echo 'RNNoise checkout exists; not overwriting.' >&2; exit 1; fi
  mkdir -p "$root"
  echo 'RNNoise: клонирую исходники и собираю локально. autogen.sh скачивает веса.'
  git clone https://github.com/xiph/rnnoise.git "$root/rnnoise"
  (cd "$root/rnnoise"
   git rev-parse HEAD | tee source-commit.txt
   ./autogen.sh
   ./configure
   make -j2
   test -x examples/rnnoise_demo
   sha256sum examples/rnnoise_demo)
  exit 0
fi
case "$2" in
  clearvoice) py=3.11; package=clearvoice; target=clearvoice ;;
  resemble) py=3.10; package=resemble-enhance; target=resemble ;;
  voicefixer) py=3.10; package=voicefixer; target=voicefixer ;;
  separator) py=3.11; package='audio-separator[cpu]'; target=separator ;;
  *) usage; exit 2 ;;
esac
if ! command -v uv >/dev/null 2>&1; then
  echo 'Missing uv. Install uv separately before installing model venvs.' >&2
  exit 1
fi
if [[ -e "$root/$target" ]]; then echo 'Environment exists; not overwriting.' >&2; exit 1; fi
mkdir -p "$root"
echo "Устанавливаю $package в $root/$target (Python $py), без изменения системного Python."
uv venv --python "$py" "$root/$target"
uv pip install --python "$root/$target/bin/python" "$package"
echo 'Готово. Выполни: source tools/voice_bench_env.sh'
