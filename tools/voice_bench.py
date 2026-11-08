#!/usr/bin/env python3
"""CueWire offline multi-model voice benchmark: no GUI, no system audio changes.

Only programs explicitly installed by the operator are run. This tool never
installs dependencies or creates background services. All subprocesses run
foreground in owned process groups and are terminated on Ctrl+C / SIGTERM.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

from voice_bench_metrics import collect, measure_speech_file

RATE = 48000
KNOWN = {"identity", "cplus", "deepfilter", "deepfilter_pf", "rnnoise", "rnnoise_lib",
         "clearvoice", "clearvoice_frcrn", "clearvoice_gan",
         "gtcrn", "dpdfnet2", "dpdfnet2_soft", "dpdfnet8", "dpdfnet8_soft",
         "metricgan_plus",
         "sr_lavasr", "sr_lavasr_denoise", "sr_flowhigh",
         "sr_apbwe", "sr_sidon", "sr_novasr",
         "resemble_denoise", "resemble_full", "voicefixer",
         "voicefixer_mode1", "roformer"}
DEFAULT_MODELS = ("identity,cplus,deepfilter,deepfilter_pf,rnnoise,"
                  "clearvoice,clearvoice_frcrn,clearvoice_gan,"
                  "resemble_denoise,resemble_full,voicefixer,voicefixer_mode1")
DEFAULT_CHAINS = (
    "deepfilter+clearvoice,deepfilter+clearvoice_frcrn,"
    "deepfilter+resemble_denoise,deepfilter+resemble_full,"
    "rnnoise+clearvoice,cplus+clearvoice,"
    "deepfilter+cplus,cplus+deepfilter,"
    "clearvoice+resemble_full,resemble_denoise+clearvoice"
)
ACTIVE = None


def shasum(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stop_active(*_args):
    global ACTIVE
    if ACTIVE is not None and ACTIVE.poll() is None:
        try:
            os.killpg(ACTIVE.pid, signal.SIGTERM)
            ACTIVE.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(ACTIVE.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    raise KeyboardInterrupt


def execute(command, log, timeout, cwd=None):
    global ACTIVE
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as fd:
        fd.write(("$ " + " ".join(map(str, command)) + "\n").encode())
        fd.flush()
        ACTIVE = subprocess.Popen(
            [str(x) for x in command], cwd=cwd, stdout=fd,
            stderr=subprocess.STDOUT, start_new_session=True,
            stdin=subprocess.DEVNULL
        )
        try:
            result = ACTIVE.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(ACTIVE.pid, signal.SIGTERM)
                ACTIVE.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try: os.killpg(ACTIVE.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            raise RuntimeError(f"Timeout after {timeout}s: {command[0]}")
        finally:
            ACTIVE = None
    if result != 0:
        raise RuntimeError(f"exit {result}; inspect {log}")


def executable(name):
    return os.access(name, os.X_OK) if "/" in name else shutil.which(name) is not None


def canonicalize(source, target, outdir, name, timeout):
    execute([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-y", "-i", source, "-map", "0:a:0",
        "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", target,
    ], outdir/"logs"/(name + ".log"), timeout)
    with wave.open(str(target), "rb") as f:
        assert (f.getframerate(), f.getnchannels(), f.getsampwidth()) == (RATE, 1, 2)
        if f.getnframes() < RATE or f.getnframes() > RATE * 180:
            raise RuntimeError("Input length outside 1..180 seconds")


def _pcm16(path):
    with wave.open(str(path), "rb") as audio:
        fmt = (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(),
               audio.getcomptype())
        if fmt != (RATE, 1, 2, "NONE"):
            raise RuntimeError(f"Expected mono PCM16/48k: {path}: {fmt}")
        samples = np.frombuffer(audio.readframes(audio.getnframes()),
                                dtype="<i2").copy()
    return samples


def _save_pcm16(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def reflect_context(source, dest, frames):
    """Add genuine preceding/following context without using CLEAN reference.

    Non-causal offline experiment. Real-time capture would require a
    different latency-aware design. Padding is not information recovery.
    """
    raw = _pcm16(source)
    if not 0 < frames < len(raw):
        raise RuntimeError(f"Invalid reflection padding: {frames} for {len(raw)} frames")
    padded = np.pad(raw, (frames, frames), mode="reflect")
    _save_pcm16(dest, padded)
    return int(len(raw))


def crop_context(source, dest, frames, expected_length):
    """Return the original region from a model's padded output.

    Explicit failure rather than silently zero-padding, loop-padding or
    concealing a model that removed too many samples.
    """
    raw = _pcm16(source)
    if len(raw) < frames + expected_length:
        raise RuntimeError(
            f"Cannot safely crop model output {len(raw)} frames: "
            f"need >= {frames + expected_length} (context {frames}, "
            f"original {expected_length})")
    _save_pcm16(dest, raw[frames:frames + expected_length])
    return int(len(raw))


def select_one(directory, kind="any"):
    files = sorted(p for p in Path(directory).rglob("*") if
                   p.is_file() and p.suffix.lower() in (".wav", ".flac"))
    if kind == "vocals":
        files = [p for p in files if
                 "vocal" in p.name.lower() and "instrumental" not in p.name.lower()]
    if len(files) != 1:
        raise RuntimeError(
            f"Expected exactly one {kind} output WAV/FLAC, got {files}. "
            "Do not silently choose the wrong stem."
        )
    return files[0]


def python_env(key, default):
    return os.path.expanduser(os.environ.get(key, default))


def tool_env(key, default):
    return os.environ.get(key, default)


def process_stage(model, source, dest, scratch, workdir, timeout, device):
    """Return selected model output. Caller canonicalizes it to PCM16/48k."""
    scratch.mkdir(parents=True, exist_ok=True)
    log = workdir/"logs"/(dest.stem + ".log")
    if model == "identity":
        shutil.copyfile(source, dest)
        return
    if model == "cplus":
        python = python_env("CUEWIRE_BENCH_AB_PYTHON", "~/.venvs/cuewire-voice-lab/bin/python")
        if not executable(python): raise FileNotFoundError(f"{python}: install the optional A/B venv")
        script = Path(__file__).with_name("voice_ab_lab.py")
        execute([python, script, "--input", source, "--out", scratch], log, timeout)
        selected = scratch/"voice_c_plus_v3.wav"
    elif model in ("deepfilter", "deepfilter_pf"):
        binary = tool_env("CUEWIRE_BENCH_DEEPFILTER", "deep-filter")
        if not executable(binary): raise FileNotFoundError(f"{binary}: install DeepFilterNet CLI")
        # Rust deep-filter (not Python deepFilter) as specified in upstream README
        command = [binary, "-D", "-o", scratch]
        if model == "deepfilter_pf":
            command.append("--pf")
        execute(command + [source], log, timeout)
        selected = select_one(scratch)
    elif model == "rnnoise_lib":
        selected = scratch/"rnnoise_lib.wav"
        execute([sys.executable, Path(__file__).with_name("voice_bench_rnnoise_lib.py"),
                 source, selected], log, timeout)
    elif model == "rnnoise":
        binary = tool_env("CUEWIRE_BENCH_RNNOISE", "rnnoise_demo")
        if not executable(binary): raise FileNotFoundError(f"{binary}: build xiph/rnnoise demo")
        if sys.byteorder != "little": raise RuntimeError("RNNoise raw PCM adapter expects little-endian")
        raw_in = scratch/"in.pcm"
        raw_out = scratch/"out.pcm"
        execute(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-y", "-i", source, "-f", "s16le", "-ar", str(RATE),
                 "-ac", "1", raw_in], log, timeout)
        execute([binary, raw_in, raw_out], log, timeout)
        if not raw_out.is_file() or raw_out.stat().st_size == 0:
            raise RuntimeError("RNNoise produced no RAW PCM")
        selected = scratch/"rnnoise.wav"
        execute(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-y", "-f", "s16le", "-ar", str(RATE), "-ac", "1",
                 "-i", raw_out, "-c:a", "pcm_s16le", selected], log, timeout)
    elif model in ("gtcrn", "dpdfnet2", "dpdfnet2_soft", "dpdfnet8", "dpdfnet8_soft"):
        python = python_env(
            "CUEWIRE_BENCH_SHERPA_PYTHON",
            str(Path(__file__).resolve().parent.parent / ".audit/voice-tools/sherpa/bin/python"))
        if not executable(python):
            raise FileNotFoundError(f"{python}: install optional sherpa-onnx environment")
        model_dir = Path(tool_env("CUEWIRE_BENCH_SHERPA_MODELS_DIR",
            str(Path(__file__).resolve().parent.parent / ".audit/voice-tools/sherpa-models")))
        selected = scratch / "sherpa_output.wav"
        execute([python, Path(__file__).with_name("voice_bench_sherpa.py"),
                 source, selected, "--model", model,
                 "--models-dir", model_dir], log, timeout)
    elif model == "metricgan_plus":
        python = python_env("CUEWIRE_BENCH_SPEECHBRAIN_PYTHON",
            str(Path(__file__).resolve().parent.parent / ".audit/voice-tools/speechbrain/bin/python"))
        if not executable(python):
            raise FileNotFoundError(f"{python}: install optional SpeechBrain environment")
        selected = scratch / "metricgan_16k.wav"
        cache_dir = Path(__file__).resolve().parent.parent / ".audit/voice-tools/speechbrain-model-cache"
        execute([python, Path(__file__).with_name("voice_bench_metricgan.py"),
                 source, selected, "--cache-dir", cache_dir,
                 "--device", "cpu" if device == "auto" else device], log, timeout)
    elif model in ("sr_lavasr", "sr_lavasr_denoise", "sr_flowhigh",
                   "sr_apbwe", "sr_sidon", "sr_novasr"):
        binary = tool_env(
            "CUEWIRE_BENCH_AUDIOSRONNX",
            str(Path(__file__).resolve().parent.parent /
                ".audit/voice-tools/audiosronnx/bin/audiosronnx"))
        if not executable(binary):
            raise FileNotFoundError(
                f"{binary}: install optional audiosronnx in its own venv")
        engine_name = {
            "sr_lavasr": "lavasr", "sr_lavasr_denoise": "lavasr",
            "sr_flowhigh": "flowhigh", "sr_apbwe": "apbwe",
            "sr_sidon": "sidon", "sr_novasr": "novasr"
        }[model]
        selected = scratch / "restored_48k.wav"
        cmd = [binary, "upscale", source, selected, "--engine", engine_name]
        if model == "sr_lavasr_denoise":
            cmd.append("--denoise")
        execute(cmd, log, timeout)
    elif model in ("clearvoice", "clearvoice_frcrn", "clearvoice_gan"):
        python = python_env("CUEWIRE_BENCH_CLEARVOICE_PYTHON", "~/.venvs/cuewire-clearvoice/bin/python")
        if not executable(python): raise FileNotFoundError(f"{python}: install ClearerVoice-Studio")
        selected = scratch/"clearvoice.wav"
        model_name = {
            "clearvoice": "MossFormer2_SE_48K",
            "clearvoice_frcrn": "FRCRN_SE_16K",
            "clearvoice_gan": "MossFormerGAN_SE_16K",
        }[model]
        execute([python, Path(__file__).with_name("voice_bench_clearvoice.py"),
                 source, selected, "--model", model_name], log, timeout)
    elif model in ("resemble_denoise", "resemble_full"):
        binary = tool_env("CUEWIRE_BENCH_RESEMBLE", "resemble-enhance")
        if not executable(binary): raise FileNotFoundError(f"{binary}: install Resemble Enhance")
        inputdir = scratch/"in"
        outputdir = scratch/"out"
        inputdir.mkdir()
        shutil.copyfile(source, inputdir/"input.wav")
        cmd = [binary, inputdir, outputdir]
        if model == "resemble_denoise": cmd += ["--denoise_only"]
        if device != "auto": cmd += ["--device", device]
        execute(cmd, log, timeout)
        selected = select_one(outputdir)
    elif model in ("voicefixer", "voicefixer_mode1"):
        binary = tool_env("CUEWIRE_BENCH_VOICEFIXER", "voicefixer")
        if not executable(binary): raise FileNotFoundError(f"{binary}: install VoiceFixer")
        selected = scratch/"voicefixer.wav"
        mode = "1" if model == "voicefixer_mode1" else "0"
        execute([binary, "--infile", source, "--outfile", selected, "--mode", mode],
                log, timeout)
    elif model == "roformer":
        binary = tool_env("CUEWIRE_BENCH_SEPARATOR", "audio-separator")
        if not executable(binary): raise FileNotFoundError(f"{binary}: install python-audio-separator")
        model_name = tool_env("CUEWIRE_BENCH_ROFORMER_MODEL",
                              "model_bs_roformer_ep_317_sdr_12.9755.ckpt")
        execute([binary, source, "--model_filename", model_name,
                 "--output_format", "WAV", "--output_dir", scratch], log, timeout)
        selected = select_one(scratch, "vocals")
    else:
        raise ValueError(f"Unknown model {model}")
    if not selected.is_file() or selected.stat().st_size == 0:
        raise RuntimeError(f"{model} produced no output: {selected}")
    canonicalize(selected, dest, workdir, dest.stem + "-convert", timeout)


def parse_names(value, separator=","):
    return [v.strip() for v in value.split(separator) if v.strip()]


def get_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent,
            stderr=subprocess.DEVNULL, text=True, timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def record(run, state):
    (run/"manifest.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", required=True, type=Path)
    parser.add_argument("--noisy", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--models", default=DEFAULT_MODELS)
    parser.add_argument("--chains", default=DEFAULT_CHAINS)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--timeout-sec", type=int, default=1200)
    parser.add_argument("--context-pad-ms", type=int, default=0,
                        help="OFFLINE reflect context on each model stage (0=disabled, 50..1000 ms)")
    parser.add_argument("--force-unverified-pair", action="store_true")
    parser.add_argument("--weights", nargs="*", type=Path, default=[])
    args = parser.parse_args()
    if args.context_pad_ms != 0 and not 50 <= args.context_pad_ms <= 1000:
        parser.error("--context-pad-ms must be 0 or 50..1000")
    if not 20 <= args.timeout_sec <= 10800:
        parser.error("timeout must be in 20..10800 seconds")
    if not args.clean.is_file() or not args.noisy.is_file():
        parser.error("The clean and noisy recordings must exist on this machine.")
    if not executable("ffmpeg"):
        parser.error("ffmpeg not found; no automatic installation is performed")
    singles = parse_names(args.models)
    chains = [parse_names(x, "+") for x in parse_names(args.chains)]
    if not singles and not chains: parser.error("No models or chains were selected")
    if any(x not in KNOWN for x in singles) or any(
        len(x) < 2 or any(y not in KNOWN for y in x) for x in chains):
        parser.error("Unknown model or invalid chain")
    if args.out.exists():
        parser.error("Output folder exists: use a NEW folder for independent runs.")
    args.out.mkdir(parents=True)
    for folder in ("inputs", "results", "logs", "scratch"):
        (args.out/folder).mkdir()

    state = {
        "schema": "cuewire-voice-benchmark-v1", "repo_commit": get_sha(),
        "platform": platform.platform(), "python": sys.version,
        "device_request": args.device, "context_pad_ms": args.context_pad_ms,
        "models_requested": singles,
        "chains_requested": chains, "timeout_sec": args.timeout_sec,
        "input_files": {
            "clean": {"filename": args.clean.name, "sha256": shasum(args.clean)},
            "noisy": {"filename": args.noisy.name, "sha256": shasum(args.noisy)}
        },
        "weights": [
            {"file": x.name, "sha256": shasum(x)} for x in args.weights if x.is_file()
        ],
        "weight_provenance_warning":
            "List of checkpoint hashes may be incomplete. Provide --weights paths for full verification.",
        "jobs": {}
    }
    record(args.out, state)
    canonicalize(args.clean.resolve(), args.out/"inputs"/"clean.wav",
                 args.out, "clean-convert", args.timeout_sec)
    canonicalize(args.noisy.resolve(), args.out/"inputs"/"noisy.wav",
                 args.out, "noisy-convert", args.timeout_sec)
    _, _, initial = measure_speech_file(args.out/"inputs"/"clean.wav",
                                       args.out/"inputs"/"noisy.wav")
    state["reference_preflight"] = initial
    state["input_files"]["clean"]["normalized_sha256"] = shasum(args.out/"inputs"/"clean.wav")
    state["input_files"]["noisy"]["normalized_sha256"] = shasum(args.out/"inputs"/"noisy.wav")
    record(args.out, state)
    print("Preflight clean/noisy:", json.dumps(initial, sort_keys=True), flush=True)
    if not initial["reference_pair_verified_heuristically"] and not args.force_unverified_pair:
        raise RuntimeError("Input pair not verified as the same utterance. "
                           "Check both files or explicitly pass --force-unverified-pair; "
                           "objective rankings remain disabled.")
    work = args.out.resolve()
    normalized = work/"inputs"/"noisy.wav"

    def run_chain(name, parts):
        start_time = time.perf_counter()
        src = normalized
        item = {"status": "running", "steps": parts,
                "context_pad_ms": args.context_pad_ms,
                "stage_details": [], "input_sha256": shasum(src)}
        state["jobs"][name] = item
        record(work, state)
        try:
            for i, model in enumerate(parts):
                if i == len(parts)-1:
                    destination = work/"results"/(name+".wav")
                else:
                    destination = work/"scratch"/(name+f"-stage{i+1}.wav")
                stage_dir = work/"scratch"/(name+f"-{i+1}-{model}")
                if args.context_pad_ms and model != "identity":
                    context_frames = args.context_pad_ms * RATE // 1000
                    pad_in = work/"scratch"/(name + f"-stage{i+1}-pad-in.wav")
                    pad_out = work/"scratch"/(name + f"-stage{i+1}-pad-out.wav")
                    original_frames = reflect_context(src, pad_in, context_frames)
                    process_stage(model, pad_in, pad_out, stage_dir,
                                  work, args.timeout_sec, args.device)
                    raw_model_frames = crop_context(
                        pad_out, destination, context_frames, original_frames)
                    item["stage_details"].append({
                        "model": model, "pad_frames_each_side": context_frames,
                        "original_frames": original_frames,
                        "raw_model_output_frames": raw_model_frames,
                        "cropped_frames": original_frames,
                        "strategy": "reflect_context_then_crop"
                    })
                else:
                    process_stage(model, src, destination, stage_dir,
                                  work, args.timeout_sec, args.device)
                    with wave.open(str(destination), "rb") as stage_audio:
                        stage_frames = stage_audio.getnframes()
                    item["stage_details"].append({
                        "model": model,
                        "model_output_frames": stage_frames,
                        "strategy": "none"
                    })
                src = destination
            with wave.open(str(destination), "rb") as f:
                count = f.getnframes()
            with wave.open(str(normalized), "rb") as f:
                source_count = f.getnframes()
            if count < source_count * 0.90:
                raise RuntimeError("Model removed more than 10% of recording length")
            item["status"] = "ok"
            item["output_sha256"] = shasum(destination)
            item["output_frames"] = count
        except FileNotFoundError as error:
            item["status"] = "missing_dependency"
            item["error"] = str(error)
        except (OSError, ValueError, RuntimeError, AssertionError) as error:
            item["status"] = "failed"
            item["error"] = str(error)
        if item["status"] != "ok":
            # A failed model may leave a partial or invalid output WAV.
            # Never allow collect() to score that artifact as a success.
            failed_output = work/"results"/(name+".wav")
            if failed_output.is_file():
                failed_output.unlink()
        item["elapsed_seconds"] = round(time.perf_counter()-start_time, 3)
        record(work, state)
        print(f"{name}: {item['status']} ({item['elapsed_seconds']}s) "
              + item.get("error", ""), flush=True)

    # Run singles first, then actual chained models. Each chain has isolated
    # scratch files; no prior output is secretly reused or normalized twice.
    for name in singles:
        run_chain(name, [name])
    for chain in chains:
        run_chain("__".join(chain), chain)
    scores = collect(work)
    state["scoring_file"] = "scores.json"
    state["scoring_input_pair_valid"] = scores["input_pair"]["reference_pair_verified_heuristically"]
    record(work, state)
    entries = {}
    for path in sorted(work.rglob("*")):
        if path.is_file() and path.name not in ("SHA256SUMS", "manifest.json"):
            entries[str(path.relative_to(work))] = shasum(path)
    with (work/"SHA256SUMS").open("w") as f:
        for path, digest in entries.items():
            f.write(f"{digest}  {path}\n")
    completed = sum(v["status"] == "ok" for v in state["jobs"].values())
    print(f"Results: {work} / passed {completed}/{len(state['jobs'])}. "
          "Review scores.csv, manifest.json, logs, outputs. No audio was played.")


if __name__ == "__main__":
    # Ctrl+C is just as strict as SIGTERM: kill the owned model process group.
    signal.signal(signal.SIGINT, stop_active)
    signal.signal(signal.SIGTERM, stop_active)
    try:
        main()
    except KeyboardInterrupt:
        print("Benchmark stopped; owned child process group terminated.", file=sys.stderr)
        sys.exit(130)
