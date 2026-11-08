#!/usr/bin/env python3
"""CueWire M4, offline-only RAW -> DeepFilterNet2 -> DPDFNet2 Soft.

No implicit downloads, playback, system/audio changes, gain, EQ, AEC or mastering.
All derivatives are produced in a *new* directory inside private .audit.
This tool does not certify perceptual quality without human listening.
"""
from __future__ import annotations

import argparse
from array import array
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / ".audit"
RATE = 48000
MAX_SECONDS = 300
DPDFNET2_SHA256 = "0b399f8a58dc4d70d8cd97541f5c39869406145193b957d00a03b66070944928"
ACTIVE: subprocess.Popen | None = None


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def metrics(path: Path) -> dict:
    """Stream a 48 kHz mono PCM16 WAV, including a separately labelled first 5 s."""
    with wave.open(str(path), "rb") as wav:
        metadata = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(),
                    wav.getcomptype())
        if metadata != (RATE, 1, 2, "NONE"):
            raise ValueError(f"Not PCM16 mono 48 kHz: {path}: {metadata}")
        frames = wav.getnframes()
        if not RATE <= frames <= RATE * MAX_SECONDS:
            raise ValueError(f"Duration outside 1..{MAX_SECONDS}s: {path}")
        n = 0
        total_square = 0
        peak = 0
        clipped = 0
        first_n = 0
        first_square = 0
        while n < frames:
            block = wav.readframes(min(8192, frames - n))
            if len(block) % 2:
                raise ValueError(f"Odd PCM16 payload bytes: {path}")
            if not block:
                raise ValueError(f"Truncated WAV: {path}")
            values = array("h")
            values.frombytes(block)
            if sys.byteorder != "little":
                values.byteswap()
            for v in values:
                a = abs(v)
                peak = max(peak, a)
                total_square += v * v
                clipped += a >= 32767
                if n < 5 * RATE:
                    first_square += v * v
                    first_n += 1
                n += 1
        if wav.readframes(1):
            raise ValueError(f"Unexpected extra WAV samples: {path}")
    def db(value: float) -> float:
        return round(20 * math.log10(max(value, 1e-12)), 5)
    return {
        "frames": n, "duration_s": round(n / RATE, 6),
        "peak_dbfs": db(peak / 32768),
        "rms_dbfs": db(math.sqrt(total_square / n) / 32768),
        "first_5s_rms_dbfs": db(math.sqrt(first_square / first_n) / 32768),
        "clipped_samples": int(clipped)
    }


def private_paths(source: Path, output: Path) -> tuple[Path, Path]:
    audit = AUDIT.resolve(strict=True)
    source = source.expanduser().resolve(strict=True)
    if audit not in source.parents or source.name != "raw.wav":
        raise ValueError("Source must be a local private .audit/.../raw.wav")
    # Resolve the parent, but reject a pre-existing leaf (incl. broken symlinks).
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refuse to overwrite a previous run: {output}")
    parent = output.expanduser().parent.resolve(strict=True)
    if audit not in parent.parents and parent != audit:
        raise ValueError("Output must be under the local private .audit directory")
    # Do not create output outside the capture's own directory by accident.
    if source.parent != parent:
        raise ValueError("Output must be a new child directory beside raw.wav")
    return source, parent / output.name


def executable(path: str) -> Path:
    candidate = shutil.which(path) if "/" not in path else os.path.expanduser(path)
    if not candidate or not Path(candidate).is_file() or not os.access(candidate, os.X_OK):
        raise FileNotFoundError(f"Missing executable: {path}")
    return Path(candidate).resolve(strict=True)


def parent_death_guard(parent_pid: int) -> None:
    """Linux safety: kill direct model process if the foreground parent dies."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        os._exit(127)


def stop_child() -> None:
    global ACTIVE
    p = ACTIVE
    if p is None or p.poll() is not None:
        return
    for sig, timeout in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        if p.poll() is not None:
            return
        try:
            os.killpg(p.pid, sig)
            p.wait(timeout=timeout)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            continue


def run_stage(command: list[str], log_path: Path, timeout: int) -> None:
    global ACTIVE
    with log_path.open("x", encoding="utf-8") as log:
        log.write("command: " + json.dumps(command, ensure_ascii=False) + "\n")
        log.flush()
        owner_pid = os.getpid()
        ACTIVE = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT,
                                  start_new_session=True,
                                  preexec_fn=lambda: parent_death_guard(owner_pid))
        try:
            rc = ACTIVE.wait(timeout=timeout)
        except subprocess.TimeoutExpired as ex:
            stop_child()
            raise RuntimeError(f"Stage timed out after {timeout}s; see {log_path}") from ex
        finally:
            if ACTIVE is not None and ACTIVE.poll() is not None:
                ACTIVE = None
    if rc:
        raise RuntimeError(f"Stage failed with exit {rc}; see {log_path}")


def check_model(path: Path) -> str:
    if not path.is_file() or path.stat().st_size < 100_000:
        raise FileNotFoundError(f"Missing/invalid DPDFNet2 ONNX: {path}")
    found = digest(path)
    if found != DPDFNET2_SHA256:
        raise RuntimeError("DPDFNet2 ONNX SHA256 mismatch. Refusing an unpinned checkpoint. "
                           f"expected {DPDFNET2_SHA256}, observed {found}")
    return found


def write_json_exclusive(path: Path, obj: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def process(args: argparse.Namespace) -> dict:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Requires Linux process lifecycle control")
    source, outdir = private_paths(args.source, args.out)
    raw_hash = digest(source)
    raw_metrics = metrics(source)
    if raw_metrics["clipped_samples"]:
        raise RuntimeError("RAW has clipped samples; preserve it and inspect before processing")
    original_manifest = source.parent / "manifest.json"
    if original_manifest.is_file():
        original = json.loads(original_manifest.read_text(encoding="utf-8"))
        if (original.get("raw_sha256") or original.get("wav_sha256")) != raw_hash:
            raise RuntimeError("Capture manifest SHA256 differs from raw.wav; stop")
        if original.get("status") != "recorded_unprocessed_raw":
            raise RuntimeError("Capture manifest does not certify an unprocessed RAW take")

    df_name = os.environ.get("CUEWIRE_BENCH_DEEPFILTER", str(AUDIT / "voice-tools/deep-filter-0.5.6-x86_64-unknown-linux-musl"))
    df_binary = executable(df_name)
    ffmpeg = executable("ffmpeg")
    sherpa_name = os.environ.get("CUEWIRE_BENCH_SHERPA_PYTHON", str(AUDIT / "voice-tools/sherpa/bin/python"))
    sherpa_python = executable(sherpa_name)
    models = Path(os.environ.get("CUEWIRE_BENCH_SHERPA_MODELS_DIR", str(AUDIT / "voice-tools/sherpa-models"))).expanduser().resolve(strict=True)
    model = models / "dpdfnet2_48khz_hr.onnx"
    model_hash = check_model(model)
    adapter = ROOT / "tools/voice_bench_sherpa.py"
    if not adapter.is_file():
        raise FileNotFoundError(adapter)
    if args.timeout_sec < 30 or args.timeout_sec > 3600:
        raise ValueError("--timeout-sec must be 30..3600")
    if digest(source) != raw_hash:
        raise RuntimeError("RAW changed during preflight")

    outdir.mkdir(mode=0o700)
    stage_dir = outdir / "deepfilter-cli"
    stage_dir.mkdir(mode=0o700)
    df_wav = outdir / "01-deepfilter.wav"
    soft_wav = outdir / "02-dpdfnet2-soft.wav"
    info = {
        "schema": "cuewire-M4-offline-clean-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "purpose": "cleaning_only_no_gain_eq_compression_aec",
        "raw": {"path": str(source), "sha256": raw_hash, "metrics": raw_metrics},
        "ordered_models": ["deepfilter", "dpdfnet2_soft"],
        "models": {
            "deepfilter": {"backend": "Rikorose Rust deep-filter", "binary": str(df_binary),
                           "binary_sha256": digest(df_binary), "delay_compensation": True,
                           "post_filter": False},
            "dpdfnet2_soft": {"backend": "sherpa-onnx offline", "python": str(sherpa_python),
                               "model": str(model), "model_sha256": model_hash,
                               "attenuation_limit_db": 12.0}
        },
        "outputs": {},
        "notes": ["First 5 seconds are a fixed time window, not a proven noise-only interval.",
                  "RMS includes silence and must not be interpreted as speech loudness.",
                  "Model output requires listening review for consonants, tone, and metallic artifacts.",
                  "No playback, network, microphone access, or audio routing changes."]
    }
    try:
        run_stage([str(df_binary), "-D", "-o", str(stage_dir), str(source)],
                  outdir / "01-deepfilter.log", args.timeout_sec)
        candidates = sorted(p for p in stage_dir.rglob("*.wav") if p.is_file())
        if len(candidates) != 1:
            raise RuntimeError(f"DeepFilter produced {len(candidates)} WAVs, expected exactly 1")
        # The Rust CLI may emit non-PCM16 WAV; the sherpa adapter requires PCM16.
        # Explicit conversion is logged, with no normalization or gain applied.
        run_stage([str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-n", "-i", str(candidates[0]), "-map", "0:a:0",
                   "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(df_wav)],
                  outdir / "01b-pcm16-conversion.log", args.timeout_sec)
        first_metrics = metrics(df_wav)
        if abs(first_metrics["frames"] - raw_metrics["frames"]) > RATE // 100:
            raise RuntimeError("DeepFilter unexpectedly changed audio duration by >10 ms")
        if first_metrics["clipped_samples"]:
            raise RuntimeError("DeepFilter output clipped; refusing next stage")
        info["outputs"]["deepfilter"] = {"file": df_wav.name, "sha256": digest(df_wav),
                                             "metrics": first_metrics}
        run_stage([str(sherpa_python), str(adapter), str(df_wav), str(soft_wav),
                   "--model", "dpdfnet2_soft", "--models-dir", str(models)],
                  outdir / "02-dpdfnet2-soft.log", args.timeout_sec)
        last_metrics = metrics(soft_wav)
        if abs(last_metrics["frames"] - raw_metrics["frames"]) > RATE // 100:
            raise RuntimeError("DPDFNet2 unexpectedly changed audio duration by >10 ms")
        if last_metrics["clipped_samples"]:
            raise RuntimeError("DPDFNet2 output clipped; stop and investigate")
        info["outputs"]["dpdfnet2_soft"] = {"file": soft_wav.name,
                                               "sha256": digest(soft_wav), "metrics": last_metrics}
        if digest(source) != raw_hash:
            raise RuntimeError("RAW changed during inference; outputs NOT accepted")
        info["status"] = "rendered_awaiting_auditory_review"
        with (outdir / "SHA256SUMS").open("x", encoding="utf-8") as sums:
            for name in (df_wav.name, soft_wav.name):
                sums.write(f"{digest(outdir / name)}  {name}\n")
        print("PASS: models ran; source SHA256 unchanged.")
        print("RAW:       ", raw_metrics["rms_dbfs"], "RMS", raw_metrics["peak_dbfs"], "peak")
        print("DeepFilter:", first_metrics["rms_dbfs"], "RMS", first_metrics["peak_dbfs"], "peak")
        print("DPDF Soft: ", last_metrics["rms_dbfs"], "RMS", last_metrics["peak_dbfs"], "peak")
        print("For manual review:", soft_wav)
        print("NO audio automatically played; NO system settings changed.")
    except BaseException as exc:
        info["status"] = "failed_or_interrupted"
        info["failure"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        stop_child()
        info["source_sha256_after"] = digest(source)
        write_json_exclusive(outdir / "manifest.json", info)
        print("Manifest:", outdir / "manifest.json")
    return info


def main() -> None:
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in", dest="source", type=Path, default=AUDIT / "voice-captures/M4-R0-ALSA-007/raw.wav")
    p.add_argument("--out", type=Path, required=True, help="NEW sibling folder beside raw.wav")
    p.add_argument("--timeout-sec", type=int, default=1200)
    args = p.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        process(args)
    except KeyboardInterrupt:
        print("Interrupted: model processes terminated; raw preserved.", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, wave.Error) as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
