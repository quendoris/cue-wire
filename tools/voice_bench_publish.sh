#!/usr/bin/env bash
set -euo pipefail

# Explicit opt-in. Uploads clean/noisy/user WAV files to a PRIVATE GitHub release.
usage() {
  printf 'Usage: bash tools/voice_bench_publish.sh RUN_DIR OWNER/PRIVATE_REPO TAG\n' >&2
  printf 'WARNING: archive contains personal recordings and model outputs.\n' >&2
}
if (( $# != 3 )); then usage; exit 2; fi
run_dir=$1
repo=$2
tag=$3
if [[ ! -d "$run_dir" || ! -f "$run_dir/manifest.json" || ! -f "$run_dir/SHA256SUMS" ]]; then
  printf 'ERROR: benchmark manifest and SHA256SUMS not found.\n' >&2
  exit 1
fi
if [[ ! "$repo" =~ ^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$ || ! "$tag" =~ ^[a-zA-Z0-9._-]+$ ]]; then
  printf 'ERROR: invalid repository or tag.\n' >&2
  exit 2
fi
for binary in gh tar sha256sum; do
  command -v "$binary" >/dev/null || { printf 'ERROR: need %s\n' "$binary" >&2; exit 1; }
done
visibility=$(gh repo view "$repo" --json visibility --jq '.visibility')
if [[ "$visibility" != "PRIVATE" ]]; then
  printf 'REFUSED: %s visibility is %s; do not publish voice audio publicly.\n' "$repo" "$visibility" >&2
  exit 1
fi
if gh release view "$tag" --repo "$repo" >/dev/null 2>&1; then
  printf 'ERROR: release %s already exists. Use a fresh tag.\n' "$tag" >&2
  exit 1
fi
(cd -- "$run_dir" && sha256sum --check SHA256SUMS)
archive=$(mktemp --suffix=.tar.gz)
trap 'rm -f -- "$archive"' EXIT
tar -C "$run_dir" -czf "$archive" .
printf '\n=== ПОДТВЕРЖДЕНИЕ ПУБЛИКАЦИИ ===\n'
printf 'Репозиторий: %s (проверен как PRIVATE).\n' "$repo"
printf 'Тег релиза: %s\n' "$tag"
printf 'Архив содержит ЧИСТУЮ и ЗАШУМЛЁННУЮ записи голоса, результаты моделей, логи и метрики.\n'
printf 'Сейчас ничего ещё не отправлено.\n'
printf 'Чтобы РАЗРЕШИТЬ загрузку, введи точно английское слово UPLOAD (заглавными буквами) и нажми Enter.\n'
printf 'Это не выбор SSH/HTTPS. Любой другой ответ, включая пустой ввод, ОТМЕНИТ загрузку.\n'
read -r -p 'Введи UPLOAD для загрузки: ' answer
if [[ "$answer" != "UPLOAD" ]]; then
  printf 'Отменено. Никакие файлы не загружены. Для повтора снова запусти команду.\n'
  exit 2
fi
printf 'Подтверждено: отправка архива в закрытый GitHub Release...\n'
gh release create "$tag" "$archive#cuewire-voice-benchmark.tar.gz" \
  --repo "$repo" --title "CueWire voice benchmark $tag" \
  --notes "Private reference/noisy voice recordings and model outputs. See scores.json, manifest.json and SHA256SUMS." \
  --latest=false
gh release view "$tag" --repo "$repo" --json url --jq '.url'
