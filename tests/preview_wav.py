#!/usr/bin/env python3
"""Independent standard-library check of generated PCM16 preview stems and mix.

Run: python3 tests/preview_wav.py /path/to/mixer-preview
No PipeWire, extra packages, defaults, or hardware required.
"""
import array
import sys
import wave
from pathlib import Path

RATE = 48_000


def pcm16(path: Path, seconds: int = 2):
    with wave.open(str(path), "rb") as audio:
        assert audio.getnchannels() == 1, path
        assert audio.getframerate() == RATE, path
        assert audio.getsampwidth() == 2, path
        assert audio.getnframes() == seconds * RATE, path
        data = audio.readframes(audio.getnframes())
    signal = array.array("h")
    signal.frombytes(data)
    if sys.byteorder != "little":
        signal.byteswap()
    assert len(signal) == RATE * seconds
    return signal


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: preview_wav.py OUTPUT_DIRECTORY")
    root = Path(sys.argv[1])
    noise = pcm16(root / "noise.wav")
    beat = pcm16(root / "beat.wav")
    mixed = pcm16(root / "mix.wav")

    assert max(abs(x) for x in noise) > 0, "noise is silent"
    assert max(abs(x) for x in beat) > 0, "beat is silent"
    assert max(abs(x) for x in mixed) > 0, "mix is silent"
    assert max(abs(x) for x in mixed) < 32767, "no headroom in preview"

    # The stems are quantized separately. At most a few LSBs of PCM16
    # deviation are allowed from an independently computed integer sum.
    error = max(abs(int(z) - int(x) - int(y)) for x, y, z in zip(noise, beat, mixed))
    assert error <= 3, f"stem sum differs from mix by {error} PCM16 counts"

    print(f"PASS preview WAV: three mono 48k/16-bit recordings, 2 seconds, max sum error {error} LSB")


if __name__ == "__main__":
    main()
