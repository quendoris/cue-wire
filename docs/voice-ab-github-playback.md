# Listen to CueWire voice A/B/C from GitHub, not chat attachments

The repository is PUBLIC. For privacy, this workflow generates a new
synthetic non-personal 3-second vocal-like source from the Rust example;
it does NOT upload a personal voice recording. WAVs are in GitHub Actions
artifacts (30-day retention), not tracked by Git.

## 1. Download ready-made WAVs

Open the latest green "Voice A-B-C listening files" workflow run and
download the artifact "cuewire-voice-ab-preview" from its summary page.

To retrieve the same artifact via GitHub CLI (replace RUN_ID):

    gh run list --repo quendoris/cue-wire \
      --workflow voice-ab-preview.yml --branch feature/pcm-mixer-playground

    gh run download RUN_ID --repo quendoris/cue-wire \
      --name cuewire-voice-ab-preview --dir voice-ab-preview

    (cd voice-ab-preview && sha256sum -c SHA256SUMS)

    bash tools/voice_ab_play.sh voice-ab-preview all

Or select exactly one recording:

    bash tools/voice_ab_play.sh voice-ab-preview original
    bash tools/voice_ab_play.sh voice-ab-preview a
    bash tools/voice_ab_play.sh voice-ab-preview b
    bash tools/voice_ab_play.sh voice-ab-preview c

The script uses only manual, foreground pw-play. Ctrl+C stops it.
No changes to system audio defaults or background services.

## 2. Compare YOUR OWN WAV offline, without public upload

The input must be mono 48 kHz signed PCM16 WAV, such as
voice-preview/voice_before.wav created by the Rust preview example.

    python3 -m venv ~/.venvs/cuewire-voice-lab
    ~/.venvs/cuewire-voice-lab/bin/python -m pip install \
      -r tools/voice_ab_requirements.txt

    bash tools/voice_ab_generate.sh \
      voice-preview/voice_before.wav voice-lab-ab

    bash tools/voice_ab_play.sh voice-lab-ab all

The output includes 4 WAVs, report.json with source and result SHA-256,
software versions, and SHA256SUMS. File generation runs locally without
recording or PipeWire access; only explicit pw-play opens the selected
default output for listening.

No speech restoration or intelligibility improvement is guaranteed.
