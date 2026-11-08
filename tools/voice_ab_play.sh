#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf 'Usage: bash tools/voice_ab_play.sh OUTPUT_DIR [original|a|b|c|all]\n' >&2
    printf 'No system audio settings are changed.\n' >&2
}
if (( $# < 1 || $# > 2 )); then usage; exit 2; fi
outdir=$1
choice=all
if (( $# == 2 )); then choice=$2; fi
if ! command -v pw-play >/dev/null 2>&1; then
    printf 'ERROR: pw-play not found.\n' >&2
    exit 1
fi
play_one() {
    local label=$1
    local file=$2
    if [[ ! -f "$outdir/$file" ]]; then
        printf 'ERROR: Missing %s\n' "$outdir/$file" >&2
        return 1
    fi
    printf '\n== %s == %s\n' "$label" "$outdir/$file"
    pw-play -- "$outdir/$file"
}
case "$choice" in
    original|o) play_one ORIGINAL voice_before.wav ;;
    a|A) play_one 'A - band only' voice_band_only_v2.wav ;;
    b|B) play_one 'B - mild denoise' voice_mild_v2.wav ;;
    c|C) play_one 'C - speech focus' voice_focus_v2.wav ;;
    all)
        play_one ORIGINAL voice_before.wav
        play_one 'A - band only' voice_band_only_v2.wav
        play_one 'B - mild denoise' voice_mild_v2.wav
        play_one 'C - speech focus' voice_focus_v2.wav
        ;;
    *) usage; exit 2 ;;
esac
