# CueWire M4/R0: first actual RAW voice capture

Status: ready for operator's live microphone experiment. M5 acoustic echo
cancellation is postponed; the NumPad H2 module is independent.

## Exact device already identified (read-only PipeWire snapshot)

    alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback

Format: 48 kHz, mono, s16le. SUSPENDED before capture is expected.
This source is the physical USB microphone, NOT a speaker monitor.
Internal mic hardware/firmware processing cannot be ruled out by
software-level raw PCM capture alone. Do not change system defaults.

## Original 80–100-second capture script and movements

Stage A: 5 seconds silence, ~20 cm distance, slight off-axis capsule.

Stage B: neutral 20 cm position:

> Сегодня я проверяю запись голоса. Я говорю спокойно, без спешки, стараюсь произносить слова чётко. Мне важно, чтобы даже после обработки остались мой тембр, дыхание и естественные окончания фраз.

Stage C: consonants/plosives at 20 cm:

> Саша шёл через шуршащий сад. Шесть щенков счастливо шепчут. Папа попросил положить пакет, потом проверить пять приборов.

Stage D: 45 degrees left -> straight -> 45 degrees right:

> Теперь я медленно поворачиваю голову. Мой голос меняет направление, но громкость и произношение остаются обычными.

Stage E: move to 10 cm, then 40–60 cm, then return to 20 cm:

> Сейчас я говорю немного ближе. Затем я отодвигаюсь дальше от микрофона. В конце возвращаюсь в исходное положение.

Stage F: softly, normally, then energetically (no shouting):

> Это тихая фраза. Это обычная фраза. А теперь — энергичная фраза, но без крика и перегрузки микрофона.

Stage G: last phrase, then 3–5 seconds of silence:

> Последняя фраза нужна для проверки эха и затухания на конце записи.

Natural reading cadence is preferable to fitting exact timestamps.
Do not play friends/music over speakers on this baseline RAW take.
Do not change physical input gain while a recording is in progress.

## First take — terminal only

    cd /data/cue-wire
    git switch feature/pcm-mixer-playground
    git pull --ff-only
    mkdir -p .audit/voice-captures
    python3 tools/voice_raw_capture.py inspect
    python3 tools/voice_raw_capture.py record \
      --mic 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
      --out .audit/voice-captures/M4-R0-001 \
      --max-seconds 150

Starts one foreground pw-record explicitly targeting the named mic.
Stops with Ctrl+C, finalizing WAV, validating header/frame count,
and writing JSON manifest with UTC capture time, WAV SHA-256,
duration, sampled peak/RMS and clipped sample count.

Creates a fresh, private take folder. Never overwrite a prior take:

    .audit/voice-captures/M4-R0-001/raw.wav
    .audit/voice-captures/M4-R0-001/manifest.json
    .audit/voice-captures/M4-R0-001/pw-record.stderr.log

The program rejects speaker monitors, ambiguous input, and any
existing output directory. It does not register hotkeys, start
a background daemon, change PipeWire defaults, monitor the raw
mic through speakers, invoke AEC, or load denoisers.

The child recorder is in a separate process group with Linux
PR_SET_PDEATHSIG=SIGKILL; Ctrl+C attempts graceful SIGINT and,
only if it hangs, SIGTERM/SIGKILL for its owned group.
Do not confuse a successful synthetic CI test with hardware proof.

## Initial quality gates

Inspect generated manifest.json. Expected rate=48000, channels=1,
sample_width_bytes=2; clipped_samples ideally zero and sample peak
comfortably below 0 dBFS. If levels clip, change gain BETWEEN
recordings and choose M4-R0-002; never rewrite the original.

Keep natural pauses, plosives, head turns, distance variations and
the final tail. Listen for missing samples, unexpected AGC, buzz,
metallic tone, popping and room echo. An SUSPENDED source should
normally start when the capture stream opens.

## Later, after R0 accepted

Derive R1 by running the verified M1 chain:

    DeepFilterNet -> DPDFNet2 Soft (12 dB suppression limit)

Do not use the old 4.724 blind-listening gain as the processing
gain. R0 stays immutable. Old test.wav is another utterance and
cannot be used for a sample-aligned SI-SDR of new speech.

Once R1 is validated, design studio stage R2/R3 and compare
naturalness, consistent loudness, plosives, sibilants and room tail.

## M5 AEC checkpoint

The read-only PipeWire M5 inspection found three speaker
monitor candidates, with the analog-stereo output currently
the default. This does not prove synchronized echo reference.
Echo cancellation with friends/game/music stays documented at:

    docs/voice-M5-acoustic-echo-cancellation.md
