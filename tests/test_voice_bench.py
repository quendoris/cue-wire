#!/usr/bin/env python3
"""Self-contained synthetic regression of metric alignment and voice benchmark.
Requires NumPy and SciPy, but NO neural models. Never opens audio devices.
"""
import math
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from voice_bench_metrics import alignment, score


def write(path, data):
    data = np.clip(data, -0.95, 0.95)
    raw = np.round(data * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setframerate(48000)
        f.setnchannels(1)
        f.setsampwidth(2)
        f.writeframes(raw.tobytes())


def main():
    rng = np.random.default_rng(123456)
    rate = 48000
    time = np.arange(rate*2) / rate
    signal = (0.18*np.sin(2*np.pi*193.0*time) +
              0.06*np.sin(2*np.pi*247.0*time) +
              0.015*np.sin(2*np.pi*901.0*time)) * (
                  0.3 + 0.7*np.sin(2*np.pi*2.0*time)**2)
    noisy = signal + 0.04*rng.standard_normal(len(signal))
    with tempfile.TemporaryDirectory() as root:
        path = Path(root)
        clean = path/"reference.wav"
        bad = path/"noise.wav"
        write(clean,signal)
        write(bad,noisy)
        lagged = np.pad(noisy, (137,0))[:len(noisy)]
        offset, corr, overlap = alignment(signal,lagged)
        assert abs(offset-137) <= 8, (offset,corr,overlap)
        assert corr > 0.75
        assert overlap > 0.99
        compare = score(signal,noisy)
        assert compare["aligned_correlation_abs"] > 0.75
        output = path/"run"
        command = [sys.executable, str(TOOLS/"voice_bench.py"),
                   "--clean", str(clean), "--noisy", str(bad),
                   "--out", str(output), "--models", "identity",
                   "--chains", "identity+identity"]
        result = subprocess.run(command, timeout=60, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        if result.returncode:
            raise AssertionError(f"Benchmark CLI failed with {result.returncode}:\n{result.stdout}")
        import json
        manifest = json.loads((output/"manifest.json").read_text())
        scores = json.loads((output/"scores.json").read_text())
        assert manifest["jobs"]["identity"]["status"] == "ok",manifest["jobs"]
        assert scores["input_pair"]["reference_pair_verified_heuristically"]
        assert len(scores["results"]) == 2
        assert manifest["jobs"]["identity__identity"]["status"] == "ok"
        assert scores["results"][0]["eligible_for_reference_ranking"]
        assert (output/"results"/"identity.wav").exists()
        assert (output/"results"/"identity__identity.wav").exists()
        assert (output/"results"/"identity.wav").read_bytes() == (output/"results"/"identity__identity.wav").read_bytes()
        assert (output/"SHA256SUMS").exists()
        # Deterministic, fixed-level listening kit never touches PipeWire.
        listening = path/"listening"
        audition = subprocess.run(
            [sys.executable, str(TOOLS/"voice_bench_listen.py"),
             str(output), "--out", str(listening), "--seed", "77"],
            capture_output=True, text=True, timeout=30)
        assert audition.returncode == 0, audition.stdout + audition.stderr
        key = json.loads((listening/"answer_key.json").read_text())
        assert len(key["tracks"]) == 2, key
        assert sorted(v["model"] for v in key["tracks"].values()) == [
            "identity", "identity__identity"
        ]
        assert (listening/"reference_clean.wav").exists()
        assert (listening/"reference_noisy.wav").exists()
        # No models are actually downloaded in CI: mocks verify that
        # model variants dispatch the correct tool-specific parameters.
        mock = path/"mock-model"
        mock.write_text("""#!/usr/bin/env python3
import sys, shutil
from pathlib import Path
args=sys.argv[1:]
if "--infile" in args:
    assert args[args.index("--mode")+1] == "1"
    shutil.copyfile(args[args.index("--infile")+1],
                    args[args.index("--outfile")+1])
elif "-o" in args:
    assert "--pf" in args
    out=Path(args[args.index("-o")+1])
    out.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(args[-1],out/"processed.wav")
else:
    sys.exit(10)
""")
        mock.chmod(0o755)
        overrides = os.environ.copy()
        overrides["CUEWIRE_BENCH_DEEPFILTER"] = str(mock)
        overrides["CUEWIRE_BENCH_VOICEFIXER"] = str(mock)
        run_variant = subprocess.run(
            [sys.executable, str(TOOLS/"voice_bench.py"),
             "--clean", str(clean), "--noisy", str(bad),
             "--out", str(path/"variant-run"),
             "--models", "deepfilter_pf,voicefixer_mode1",
             "--chains", "deepfilter_pf+voicefixer_mode1"],
            capture_output=True, text=True, env=overrides, timeout=60)
        assert run_variant.returncode == 0, run_variant.stdout + run_variant.stderr
        manifest_2=json.loads((path/"variant-run"/"manifest.json").read_text())
        assert all(x["status"]=="ok" for x in manifest_2["jobs"].values()), manifest_2["jobs"]
        # Model cards only, not inference: adapter must expose 3 supported names.
        adapter_help = subprocess.run(
            [sys.executable, str(TOOLS/"voice_bench_clearvoice.py"), "--help"],
            capture_output=True, text=True, timeout=5)
        assert adapter_help.returncode == 0
        assert "FRCRN_SE_16K" in adapter_help.stdout
        assert "MossFormerGAN_SE_16K" in adapter_help.stdout
        # Publisher must REFUSE public visibility without attempting an upload.
        stub_bin = path/"fake-bin"
        stub_bin.mkdir()
        gh = stub_bin/"gh"
        gh.write_text("#!/bin/sh\nif [ \"$1\" = repo ]; then echo PUBLIC; exit 0; fi\n"
                      "echo 'UNEXPECTED RELEASE CALL' >&2; exit 99\n")
        gh.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(stub_bin) + os.pathsep + env["PATH"]
        publishing = subprocess.run(
            ["bash", str(TOOLS/"voice_bench_publish.sh"), str(output),
             "quendoris/cue-wire", "must-never-upload"],
            capture_output=True, text=True, env=env, timeout=10
        )
        assert publishing.returncode != 0
        assert "REFUSED" in publishing.stderr, publishing.stderr
        assert "UNEXPECTED RELEASE CALL" not in publishing.stderr
    print("PASS: reference alignment, 2-stage chains, opt-in variants, audition, checksums, private-release guard")


if __name__ == "__main__":
    main()
