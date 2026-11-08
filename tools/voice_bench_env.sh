#!/usr/bin/env bash
# SOURCE only: exports paths for the current shell, no persistent settings.
_cue_base="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/.audit/voice-tools"
if [[ -x "$_cue_base/rnnoise/examples/rnnoise_demo" && -z "$(printenv CUEWIRE_BENCH_RNNOISE)" ]]; then
  export CUEWIRE_BENCH_RNNOISE="$_cue_base/rnnoise/examples/rnnoise_demo"
fi
if [[ -x "$_cue_base/clearvoice/bin/python" && -z "$(printenv CUEWIRE_BENCH_CLEARVOICE_PYTHON)" ]]; then
  export CUEWIRE_BENCH_CLEARVOICE_PYTHON="$_cue_base/clearvoice/bin/python"
fi
if [[ -x "$_cue_base/resemble/bin/resemble-enhance" && -z "$(printenv CUEWIRE_BENCH_RESEMBLE)" ]]; then
  export CUEWIRE_BENCH_RESEMBLE="$_cue_base/resemble/bin/resemble-enhance"
fi
if [[ -x "$_cue_base/voicefixer/bin/voicefixer" && -z "$(printenv CUEWIRE_BENCH_VOICEFIXER)" ]]; then
  export CUEWIRE_BENCH_VOICEFIXER="$_cue_base/voicefixer/bin/voicefixer"
fi
if [[ -x "$_cue_base/separator/bin/audio-separator" && -z "$(printenv CUEWIRE_BENCH_SEPARATOR)" ]]; then
  export CUEWIRE_BENCH_SEPARATOR="$_cue_base/separator/bin/audio-separator"
fi
unset _cue_base
echo 'CueWire: model paths are active only in the current terminal.'
