#!/usr/bin/env python3
"""Independent check of rendered before/after music WAVs without PipeWire."""
import array
import csv
import math
import sys
import wave
from pathlib import Path

RATE = 48_000


def read_pcm16(path, seconds):
    with wave.open(str(path), "rb") as f:
        assert f.getnchannels() == 1
        assert f.getframerate() == RATE
        assert f.getsampwidth() == 2
        assert f.getnframes() == seconds * RATE
        pcm = array.array("h")
        pcm.frombytes(f.readframes(f.getnframes()))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm


def rms_db(arr):
    return 10.0 * math.log10(sum(float(x) * float(x) for x in arr) / len(arr))


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: dynamics_preview.py OUTPUT_DIR SECTION_SECONDS")
    root = Path(sys.argv[1])
    section_seconds = int(sys.argv[2])
    total = 4 * section_seconds
    before = read_pcm16(root / "music_raw.wav", total)
    after = read_pcm16(root / "music_leveled.wav", total)
    assert max(abs(s) for s in before) < 32767
    assert max(abs(s) for s in after) < 32767

    with (root / "levels.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == total
    assert [int(r["second"]) for r in rows] == list(range(total))
    assert [int(r["section"]) for r in rows] == [
        i // section_seconds + 1 for i in range(total)
    ]
    assert all(math.isfinite(float(r["leveled_rms_dbfs"])) for r in rows)

    # Skip each section's first second (ramp and detector transition).
    before_levels = []
    after_levels = []
    for section in range(4):
        start = (section * section_seconds + 1) * RATE
        end = (section + 1) * section_seconds * RATE
        before_levels.append(rms_db(before[start:end]))
        after_levels.append(rms_db(after[start:end]))
    raw_span = max(before_levels) - min(before_levels)
    leveled_span = max(after_levels) - min(after_levels)
    assert raw_span > 7.0, f"insufficient initial contrast: {raw_span:.2f} dB"
    assert leveled_span < raw_span - 2.0, (
        f"leveler did not reduce section RMS range enough: {raw_span:.2f} -> {leveled_span:.2f} dB"
    )
    print(f"PASS dynamics-preview WAV: raw section span {raw_span:.2f} dB "
          f"-> leveled span {leveled_span:.2f} dB, 4 sections, no PCM clipping")


if __name__ == "__main__":
    main()
