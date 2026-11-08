# RNNoise: quick workaround for slow 56 MB model archive (Arch)

Upstream RNNoise autogen.sh automatically runs download_model.sh. It downloads
a roughly 56 MB model archive from media.xiph.org and verifies it using the
hash in model_version. Interrupted downloads may leave a partial archive.
Do not bypass the checksum.

The official Arch package rnnoise supplies librnnoise.so but does NOT supply
rnnoise_demo. The additional CueWire model ID rnnoise_lib directly calls the
shared C library in the FOREGROUND via Python ctypes. No build, new weights
download, autostart, service, PulseAudio or PipeWire reconfiguration.

After Ctrl+C cancels a stalled upstream RNNoise model download:

    pacman -Q rnnoise
    ldconfig -p | grep rnnoise || true

If present, use a fresh output directory:

    ~/.venvs/cuewire-voice-lab/bin/python tools/voice_bench.py \
      --clean /data/cue-wire/test.wav \
      --noisy /data/cue-wire/voice_before.wav \
      --out /data/cue-wire/bench-rnnoise-lib-001 \
      --models identity,cplus,deepfilter,rnnoise_lib \
      --chains deepfilter+rnnoise_lib,rnnoise_lib+cplus,deepfilter+rnnoise_lib+cplus

For a library extracted locally from an official Arch package rather
than installed globally, set CUEWIRE_BENCH_RNNOISE_LIBRARY to its full
verified path. The default system library is not assumed to be installed.

If not installed, do NOT run sudo automatically. The operator may elect
to install the official package with sudo pacman -S rnnoise, but that
does change the system package set. Other benchmarks can proceed without it.

Keep rnnoise (standalone demo compiled from source) and rnnoise_lib
(system library with its embedded model) distinct. They may contain
different model versions. Record pacman -Q rnnoise and library SHA-256
before comparing across machines.

Upstream:
- https://github.com/xiph/rnnoise
- https://github.com/xiph/rnnoise/blob/main/include/rnnoise.h
- https://archlinux.org/packages/extra/x86_64/rnnoise/files/
