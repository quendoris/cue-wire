#!/usr/bin/env python3
"""M5 physical source control: are the ~13kHz bursts mouse clicks?

ONE new local physical microphone RAW WAV; all music/player OFF:
  warmup 3 s; quiet 8s; mouse move WITHOUT clicking 8s;
  deliberate mouse clicking 8s; keyboard keys 8s; quiet 8s.

Console phase cues only (NO beep/audio output), 1s trim at phase edges,
48kHz/mono/PCM16, 0.5s Welch power windows, compare 8–16kHz and
12–14kHz against quiet baseline and mouse-moving control.
Output: private mic WAV, JSON, SHA256SUMS. No routing/default/gain
changes, no background service, no autoplay, no mutation of old M5.
Keyboard/mouse control is *direct physical evidence*, not a clock-
synchronized event log: phase boundaries are approximate wall times.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
import wave

import numpy as np
from scipy import signal as sp_signal

RATE = 48000
PHASE_SECONDS = 8
SETTLE_SECONDS = 1.0
WINDOW_SECONDS = 0.5
WARMUP_SECONDS = 3.0
PHASES = (
    ("silence_before", "ТИШИНА: не трогай мышь и клавиатуру"),
    ("mouse_move_only", "ДВИЖЕНИЕ: двигай мышь, но НЕ нажимай кнопки"),
    ("mouse_clicks", "КЛИКИ: нажимай ЛКМ/ПКМ примерно 2 раза в секунду"),
    ("keyboard_clicks", "КЛАВИАТУРА: нажимай обычные клавиши несколько раз в секунду"),
    ("silence_after", "ТИШИНА: снова ничего не трогай"),
)
BANDS = ((8000, 16000), (12000, 14000))
NFFT = 4096


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def analyze_window(x: np.ndarray) -> dict:
    """Exact 0.5s, mono PCM amplitude normalized by 32768."""
    if x.ndim != 1 or len(x) != RATE // 2 or not np.all(np.isfinite(x)):
        raise ValueError("Expected 0.5 seconds of valid mono physical PCM")
    freq, pxx = sp_signal.welch(
        x, fs=RATE, nperseg=NFFT, noverlap=NFFT//2)
    df = float(freq[1] - freq[0])
    values = {}
    for lo, hi in BANDS:
        keep = (freq >= lo) & (freq < hi)
        p = float(np.sum(pxx[keep])*df)
        values[f"{lo}_{hi}_power_dbfs"] = round(
            10*math.log10(max(p,1e-30)), 5)
        values[f"{lo}_{hi}_linear_power"] = p
    hi = (freq >= 8000) & (freq < 16000)
    peak = float(freq[hi][int(np.argmax(pxx[hi]))])
    total = values["8000_16000_linear_power"]
    tone = values["12000_14000_linear_power"]
    values["peak_frequency_8_16khz_hz"] = round(peak, 4)
    values["share_of_8_16khz_power_in_12_14khz"] = round(
        tone/max(total,1e-30), 6)
    return values


def analyze_samples(pcm: np.ndarray, phases: list[dict]) -> dict:
    if pcm.ndim != 1 or len(pcm) < 30*RATE:
        raise ValueError("Truncated physical microphone WAV")
    step = RATE//2
    rows = {}
    for phase in phases:
        label=phase["name"]
        a = int(math.ceil((phase["wall_start_after_launch_s"] +
                           SETTLE_SECONDS)*RATE/step)*step)
        b = int(math.floor((phase["wall_end_after_launch_s"] -
                            SETTLE_SECONDS)*RATE/step)*step)
        if a < 0 or b > len(pcm) or b-a < 8*step:
            raise ValueError("Unexpected recorder startup/phase timing; no false result")
        result=[]
        for index in range(a,b,step):
            metric = analyze_window(pcm[index:index+step])
            metric["approx_microphone_wav_seconds"] = [
                round(index/RATE,3),round((index+step)/RATE,3)]
            result.append(metric)
        power = np.array(
            [row["8000_16000_power_dbfs"] for row in result])
        tone = np.array(
            [row["share_of_8_16khz_power_in_12_14khz"] for row in result])
        rows[label] = {
            "prompt":phase["prompt"],
            "num_half_second_windows":len(result),
            "median_highband_dbfs":round(float(np.median(power)),5),
            "max_highband_dbfs":round(float(np.max(power)),5),
            "median_12_14_power_share":round(float(np.median(tone)),6),
            "windows":result,
        }
    baseline = np.array([
        row["8000_16000_power_dbfs"]
        for label in ("silence_before","silence_after")
        for row in rows[label]["windows"]
    ])
    floor = float(np.median(baseline))
    for row in rows.values():
        row["half_second_windows_over_quiet_median_plus_6db"] = sum(
            w["8000_16000_power_dbfs"] > floor+6
            for w in row["windows"])
        row["peak_above_quiet_median_db"] = round(
            row["max_highband_dbfs"]-floor,5)
    return {
        "quiet_median_8_16khz_dbfs":round(floor,5),
        "half_second_window_s":WINDOW_SECONDS,
        "all_phases":rows,
        "mouse_vs_movement_and_keyboard_control_not_causal_proof":True,
    }


def stop_recorder(rec: subprocess.Popen) -> int:
    if rec.poll() is None:
        try:
            os.killpg(rec.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        return rec.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(rec.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return rec.wait(timeout=5)


def wait_until(rec: subprocess.Popen, target: float) -> None:
    while time.monotonic() < target:
        if rec.poll() is not None:
            raise RuntimeError(
                f"pw-record exited unexpectedly with code {rec.returncode}")
        time.sleep(min(.10, max(.001, target-time.monotonic())))


def read_exact_wave(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as audio:
        metadata = (
            audio.getnchannels(), audio.getsampwidth(),
            audio.getframerate(), audio.getcomptype())
        if metadata != (1,2,RATE,"NONE"):
            raise ValueError("Wrong WAV format; expected 48kHz mono PCM16")
        frames=audio.getnframes()
        payload=audio.readframes(frames)
    if len(payload) != frames*2:
        raise ValueError("WAV incomplete")
    samples=np.frombuffer(payload,dtype="<i2").astype(np.float64)/32768.
    if np.any(np.abs(samples)>=32767/32768):
        raise ValueError("Mic has saturated/clipped samples")
    return samples


def capture(source: str, out: Path) -> dict:
    if (not source.startswith("alsa_input.") or
            "\n" in source or len(source)>255):
        raise ValueError("Explicit PipeWire physical mic source name required")
    if out.exists() or out.is_symlink():
        raise FileExistsError("Output folder exists; refuse overwrite")
    private = (Path.cwd()/".audit").resolve(strict=True)
    destination = out.expanduser()
    parent = destination.parent.resolve(strict=True)
    if private not in parent.parents and parent != private:
        raise ValueError("Place private capture under repo .audit only")
    target = parent/destination.name
    target.mkdir(mode=0o700)
    wav = target/"microphone-mouse-control-raw.wav"
    log = target/"pw-record.log"
    cmd = [
        "pw-record",f"--target={source}","--rate=48000",
        "--channels=1","--channel-map=mono","--format=s16",
        str(wav),
    ]
    phases=[]
    rec=None
    success=False
    with log.open("xb") as logfile:
        try:
            rec=subprocess.Popen(
                cmd,stdout=logfile,stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,start_new_session=True)
            started=time.monotonic()
            print("Запись началась. Музыку НЕ включаем. Разминка 3 секунды.",
                  flush=True)
            wait_until(rec,started+WARMUP_SECONDS)
            for label,prompt in PHASES:
                begin=time.monotonic()
                print(f"\n[{label.upper()}] {prompt} — {PHASE_SECONDS} секунд",
                      flush=True)
                deadline=begin+PHASE_SECONDS
                wait_until(rec,deadline)
                phases.append({
                    "name":label,
                    "prompt":prompt,
                    "wall_start_after_launch_s":round(begin-started,5),
                    "wall_end_after_launch_s":round(time.monotonic()-started,5)
                })
            success=True
        finally:
            rc=stop_recorder(rec) if rec else None
    if not success:
        raise RuntimeError(
            "Capture interrupted; private partial WAV/log retained, no PASS claimed")
    samples=read_exact_wave(wav)
    if not 39*RATE<=len(samples)<=47*RATE:
        raise ValueError("Recorder duration mismatch; discard as physical control")
    results=analyze_samples(samples,phases)
    report={
        "schema":"cuewire-M5-mouse-vs-keyboard-physical-control-v1",
        "status":"new_physical_control_captured_and_analyzed",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "physical_mic_source":source,
        "recorder_command":cmd,
        "recorder_returncode_on_stop":rc,
        "original_pcm_sha256":sha256(wav),
        "sample_rate_hz":RATE,
        "channels":1,
        "bits_per_sample":16,
        "recorded_frames":len(samples),
        "recorded_seconds":round(len(samples)/RATE,5),
        "wall_phase_markers_approximate_not_sample_synchronized":phases,
        "spectrum":results,
        "no_playback_performed":True,
        "no_audio_routes_or_volume_modified":True,
        "old_m5_sources_unchanged":True,
        "raw_audio_remains_private":True,
        "warnings":[
            "Phase labels are human instructions and wall clock, not instrumented mouse/keyboard event timestamps.",
            "Trimmed 1s at each phase boundary to reduce recording startup/phase reaction uncertainty.",
            "Striking mouse buttons causes acoustic click and desk vibration; follow-up controls can isolate path.",
            "An elevated highband click peak near 13kHz supports but does not prove it is the cause of all historical bursts.",
            "No noise suppressor was run and no claim of solved clicks is made.",
        ],
    }
    output=target/"mouse-control.json"
    with output.open("x",encoding="utf-8") as f:
        json.dump(report,f,indent=2,sort_keys=True,ensure_ascii=False)
        f.write("\n")
    files=(wav,log,output)
    with (target/"SHA256SUMS").open("x",encoding="utf-8") as f:
        for file in files:
            f.write(f"{sha256(file)}  {file.name}\n")
    print("\nГОТОВО. Нет воспроизведения и системных изменений.")
    print("Медиана тишины 8–16 кГц:",
          results["quiet_median_8_16khz_dbfs"],"dBFS")
    for label,row in results["all_phases"].items():
        print(f"  {label:19s}: max +{row['peak_above_quiet_median_db']:.2f} dB;",
              "window events >+6dB:",
              row["half_second_windows_over_quiet_median_plus_6db"])
    print("Отчёт:",output)
    return report


def main() -> None:
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    try:
        capture(args.source,args.out)
    except KeyboardInterrupt:
        parser.exit(130,"Прервано пользователем; процесс записи остановлен.\n")
    except (OSError,ValueError,RuntimeError) as e:
        parser.exit(1,f"ERROR: {e}\n")


if __name__ == "__main__":
    main()
