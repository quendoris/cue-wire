#!/usr/bin/env python3
"""M6 PHYSICAL-EVENT-001: foreground joint mic + anonymous key-event capture.

Records ONLY the fact of keyboard/mouse press/release and the kernel
CLOCK_MONOTONIC time. Never records keycodes, button IDs, text,
coordinates, movement, scan codes, device names or device paths in
the output files. Keycode is never used in classification.

The PipeWire microphone recorder and evdev readers exist only during
this explicit timed session. No grab(), hotkeys, auto playback, default
device changes, system configuration, daemon, persistent process,
background collector, or audio monitoring. NO MUSIC in this capture.
The raw event timestamps are NOT yet matched to mic PCM sample zero.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import termios
import time
import wave

from voice_m6_event_anc import RATE, ALLOWED_EVENTS
from voice_m5_aec import AUDIT

# Linux <linux/input.h>: _IOW('E', 0xa0, int). On supported x86_64 Arch.
EVIOCSCLOCKID = 0x400445A0
KERNEL_CLOCK_ID = time.CLOCK_MONOTONIC
PHASES = (
    ("quiet_baseline", 3., "ТИШИНА. Не говори, не нажимай кнопки"),
    ("mouse_isolated", 9., "ТОЛЬКО мышь: >=8 раздельных щелчков с паузами"),
    ("keyboard_isolated", 9., "ТОЛЬКО клавиатура: >=8 раздельных нажатий. Никаких паролей"),
    ("both_holdout", 7., "Мышь + клавиатура, без голоса. Другой ритм"),
    ("voice_only_holdout", 6., "ТОЛЬКО голос. Не трогай мышь/клавиатуру"),
    ("voice_clicks_holdout", 9., "Голос + мышь + клавиатура. Без музыки"),
)
MAX_EVENT_ROWS = 10000
PR_SET_PDEATHSIG = 1
LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.prctl.argtypes = [
    ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
    ctypes.c_ulong, ctypes.c_ulong,
]
LIBC.prctl.restype = ctypes.c_int


def make_child_die_with_parent(expected_parent_pid: int) -> None:
    """Linux child pre-exec: no orphan recorder after parent SIGKILL.

    Do not create descendants or launch shell. This script is
    deliberately single-threaded; the child uses only libc prctl,
    os.getppid, os._exit before exec.
    """
    if (LIBC.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0
            or os.getppid() != expected_parent_pid):
        os._exit(127)



def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_device_path(path: str) -> str:
    if not re.fullmatch(r"/dev/input/event[0-9]+", path):
        raise ValueError("Provide explicit non-symlink /dev/input/eventN")
    item = Path(path)
    if item.is_symlink() or not stat.S_ISCHR(item.stat().st_mode):
        raise ValueError("Input device must be a direct character device")
    return path


def classify_event(device_class: str, event, evdev) -> tuple[str, int] | None:
    """Use ONLY event.type and event.value; NEVER event.code for keys.

    Kernel SYN_DROPPED is handled separately by the capture loop.
    """
    if device_class not in ("mouse", "keyboard"):
        raise ValueError("Unknown authorized event device class")
    if event.type != evdev.ecodes.EV_KEY or event.value not in (0, 1):
        return None
    kind = device_class + ("_press" if event.value == 1 else "_release")
    if kind not in ALLOWED_EVENTS:
        raise RuntimeError("Unexpected class-only input marker")
    stamp = int(event.sec) * 1_000_000_000 + int(event.usec) * 1_000
    if stamp <= 0:
        raise RuntimeError("Missing monotonic kernel input timestamp")
    return kind, stamp


def event_line(kind: str, stamp: int) -> str:
    if (kind not in ALLOWED_EVENTS or
            type(stamp) is not int or stamp <= 0):
        raise ValueError("Unrecognized anonymous event data")
    return json.dumps(
        {"kind":kind, "t_monotonic_ns":stamp},
        separators=(",",":"),sort_keys=True) + "\n"


def ensure_private_output(path:Path) -> Path:
    audit = AUDIT.resolve(strict=True)
    if path.is_symlink() or path.exists():
        raise FileExistsError("Never overwrite previous private capture")
    dest = path.expanduser().parent.resolve(strict=True) / path.name
    if audit not in dest.parents or not dest.name.startswith("M6-physical-events-"):
        raise ValueError("Expected NEW .audit/voice-aec/M6-physical-events-NNN")
    if dest.parent != audit / "voice-aec":
        raise ValueError("Capture must be a direct child of .audit/voice-aec")
    return dest


@contextmanager
def noecho_capture_terminal():
    """Prevent test key presses from echoing or reaching the next shell.

    Never read terminal input. Keep ISIG enabled so Ctrl+C still works.
    On ordinary exit flush typed bytes before restoring the terminal.
    A forced SIGKILL cannot run a finally handler; use 'stty sane'
    in the terminal if a forcible kill leaves it without echo.
    """
    if not sys.stdin.isatty():
        raise RuntimeError("M6 capture requires an interactive terminal for no-echo protection")
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    new = old.copy()
    new[3] = old[3] & ~(termios.ECHO | termios.ICANON)
    # Do NOT disable ISIG: Ctrl+C must still be recognized by the TTY.
    termios.tcsetattr(fd, termios.TCSANOW, new)
    try:
        termios.tcflush(fd, termios.TCIFLUSH)
        yield
    finally:
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)


def set_kernel_monotonic(device) -> None:
    try:
        fcntl.ioctl(
            device.fd, EVIOCSCLOCKID,
            struct.pack("i", KERNEL_CLOCK_ID))
    except OSError as exc:
        raise RuntimeError(
            "Cannot set evdev CLOCK_MONOTONIC; timestamps untrusted") from exc
    # Drain events queued before the clock switch. python-evdev read()
    # returns an iterable: an EMPTY iterable must terminate the loop.
    # Bound BOTH batches and events so even a broken/flooding device
    # cannot wedge unit tests or hold the microphone capture hostage.
    for _batch in range(64):
        saw_event = False
        try:
            for index, _event in enumerate(device.read()):
                if index >= 8192:
                    raise RuntimeError("Input event backlog too large to drain")
                saw_event = True
        except BlockingIOError:
            return  # Normal end of the nonblocking evdev queue.
        if not saw_event:
            return
    raise RuntimeError("Input event queue did not quiesce after clock change")


def terminate_owned(process: subprocess.Popen | None) -> None:
    """Stop ONLY the recorder's process group; no detached children."""
    if process is None:
        return
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
    else:
        process.wait()


def check_wav(path:Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing original microphone WAV")
    with wave.open(str(path),"rb") as w:
        fmt=(w.getframerate(),w.getnchannels(),
             w.getsampwidth(),w.getcomptype())
        frames=w.getnframes()
    if fmt != (RATE,1,2,"NONE") or frames < 30*RATE:
        raise ValueError("Invalid/incomplete 48kHz mono PCM16 capture")
    return {"sample_rate":RATE,"channels":1,"pcm_width_bytes":2,
            "frames":frames,"duration_seconds":round(frames/RATE,6)}


def devices() -> None:
    """Interactive PREVIEW of device names/paths ONLY, nothing saved."""
    import evdev
    for name in sorted(evdev.list_devices()):
        dev = evdev.InputDevice(name)
        try:
            print(f"{dev.path} | {dev.name}")
        finally:
            dev.close()


def run(args: argparse.Namespace) -> dict:
    if not args.acknowledge_anonymous_event_privacy:
        raise ValueError("Explicit consent to store event timing required")
    if not args.speaker_off_confirmed:
        raise ValueError("Speaker-off/no-music calibration must be confirmed")
    if (not args.mic.startswith("alsa_input.")
            or any(c in args.mic for c in "\n\r\t")):
        raise ValueError("Specify an explicit PipeWire physical alsa_input.* node")
    keyboard_path = validate_device_path(args.keyboard_device)
    mouse_path = validate_device_path(args.mouse_device)
    if keyboard_path == mouse_path:
        raise ValueError("Distinct physical input event devices required")
    if not shutil.which("pw-record"):
        raise RuntimeError("pw-record is not installed")
    try:
        import evdev
    except ImportError as exc:
        raise RuntimeError(
            "python-evdev not installed in this Python environment") from exc

    os.umask(0o077)
    target = ensure_private_output(args.out)
    selector = selectors.DefaultSelector()
    open_devices = []
    for label, path in (("keyboard",keyboard_path),("mouse",mouse_path)):
        try:
            device = evdev.InputDevice(path)
            try:
                set_kernel_monotonic(device)
                selector.register(device, selectors.EVENT_READ, data=label)
            except BaseException:
                device.close()
                raise
            open_devices.append(device)
        except BaseException:
            for old in open_devices:
                old.close()
            selector.close()
            raise

    target.mkdir(mode=0o700)
    mic_path=target/"microphone.wav"
    log_path=target/"pw-record.stderr.log"
    events_path=target/"anonymous-events.jsonl"
    process = None
    phases=[]
    counts=Counter()
    status="incomplete"
    failure=None
    start_ns=None
    end_ns=None
    try:
        with noecho_capture_terminal():
            with log_path.open("xb") as log, events_path.open("x", encoding="utf-8") as stream:
                argv=[
                    "pw-record", "--target="+args.mic,
                    "--rate=48000","--channels=1",
                    "--channel-map=mono","--format=s16",
                    "--latency=20ms",str(mic_path)
                ]
                parent_pid = os.getpid()
                process=subprocess.Popen(
                    argv, stdout=subprocess.DEVNULL, stderr=log,
                    stdin=subprocess.DEVNULL, start_new_session=True,
                    preexec_fn=lambda: make_child_die_with_parent(parent_pid))
                start_ns=time.monotonic_ns()
                print("M6: only timing and device class, no key/button identity.")
                print("The mic is recorded ONLY in this opt-in session. Ctrl+C aborts.")
                time.sleep(1.5)
                if process.poll() is not None:
                    raise RuntimeError("Physical recorder exited before capture")
                for phase_name, seconds, instruction in PHASES:
                    print(f"\n[{phase_name}] {seconds:g}s — {instruction}",flush=True)
                    left=time.monotonic_ns()
                    deadline=left+round(seconds*1e9)
                    local=Counter()
                    while time.monotonic_ns() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError("Microphone recorder exited during capture")
                        timeout=min(.035,max(0,(deadline-time.monotonic_ns())/1e9))
                        for key,_ in selector.select(timeout):
                            for event in key.fileobj.read():
                                if (event.type==evdev.ecodes.EV_SYN
                                        and event.code==evdev.ecodes.SYN_DROPPED):
                                    raise RuntimeError("EV_SYN/SYN_DROPPED: input events lost")
                                item=classify_event(key.data,event,evdev)
                                if item is None:
                                    continue
                                kind,t_ns=item
                                now=time.monotonic_ns()
                                if (t_ns > now+10_000_000
                                        or t_ns < now-2_000_000_000):
                                    raise RuntimeError("evdev kernel time not monotonic/recent")
                                stream.write(event_line(kind,t_ns))
                                local[kind]+=1
                                counts[kind]+=1
                                if sum(counts.values()) > MAX_EVENT_ROWS:
                                    raise RuntimeError("Too many event rows")
                    end=time.monotonic_ns()
                    phases.append({
                        "phase":phase_name,
                        "start_monotonic_ns":left,
                        "end_monotonic_ns":end,
                        "event_counts_by_class":dict(local),
                        "speaker_off_operator_confirmed":True,
                        "nearend_speech_intended":
                            phase_name in ("voice_only_holdout","voice_clicks_holdout")
                    })
                    print("Markers:",dict(local),flush=True)
                status="capture_complete"
    except KeyboardInterrupt:
        failure="Ctrl+C: deliberately interrupted by user"
    except BaseException as exc:
        failure=f"{type(exc).__name__}: {exc}"
    finally:
        end_ns=time.monotonic_ns()
        try:
            terminate_owned(process)
        finally:
            selector.close()
            for dev in open_devices:
                dev.close()

    mic_meta={}
    if mic_path.exists():
        try:
            mic_meta=check_wav(mic_path)
        except (OSError, ValueError, wave.Error) as exc:
            failure=f"Invalid mic WAV: {exc}"
            status="invalid"
    else:
        failure=failure or "Recorder did not produce WAV"
        status="invalid"
    if status=="capture_complete":
        phase_counts = {p["phase"]:p["event_counts_by_class"] for p in phases}
        if (phase_counts.get("mouse_isolated",{}).get("mouse_press",0) < 8
                or phase_counts.get("keyboard_isolated",{}).get(
                    "keyboard_press",0) < 8):
            status="invalid_insufficient_calibration_events"
            failure="Need >=8 isolated mouse and >=8 isolated keyboard presses"
    record = {
        "schema":"cuewire-M6-physical-anonymous-events-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "status":status,
        "failure":failure,
        "session_start_monotonic_ns":start_ns,
        "session_end_monotonic_ns":end_ns,
        "input_event_clock":"CLOCK_MONOTONIC via EVIOCSCLOCKID",
        "mic_sample_zero_monotonic_ns":None,
        "sample_zero_measured":False,
        "acoustic_event_to_sample_offset_measured":False,
        "input_codes_buttons_text_coordinates_device_names_saved":False,
        "input_class_only":True,
        "event_jsonl_schema":{"kind":"keyboard|mouse_press|release",
                              "t_monotonic_ns":"kernel event monotonic nanoseconds"},
        "event_counts_by_class":dict(counts),
        "phases":phases,
        "speaker_off_operator_confirmed":True,
        "microphone":mic_meta,
        "recorder_owned_process_exit_code":(
            process.returncode if process is not None else None),
        "no_PipeWire_default_or_route_changes":True,
        "no_new_service_autostart_or_hotkeys":True,
        "recorder_parent_death_signal":"SIGKILL (PR_SET_PDEATHSIG)",
        "forced_parent_kill_wav_header_integrity_not_guaranteed":True,
        "no_key_event_identity_collected":True,
        "event_to_PCM_sample_mapping_unmeasured":True,
        "user_recordings_are_private":True,
    }
    output={}
    for item in (mic_path,events_path,log_path):
        if item.is_file():
            output[item.name]=sha(item)
    record["capture_files_sha256"]=output
    report_path=target/"manifest.json"
    report_path.write_text(json.dumps(record,indent=2,sort_keys=True)+"\n",
                           encoding="utf-8")
    output["manifest.json"]=sha(report_path)
    (target/"SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n"
                for name,digest in sorted(output.items())),encoding="utf-8")
    print("\nCapture",status,"—",target,flush=True)
    print("Anonymous event counts:",dict(counts),flush=True)
    print("No key identity/device identifier/audio-clock alignment saved.")
    if status != "capture_complete":
        raise RuntimeError(failure or status)
    return record


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    cmd=p.add_subparsers(dest="action",required=True)
    cmd.add_parser("devices",help="List input device name/path locally; no storage")
    rec=cmd.add_parser("capture")
    rec.add_argument("--mic",required=True,
                     help="Explicit alsa_input.* node from the M5 manifest")
    rec.add_argument("--keyboard-device",required=True)
    rec.add_argument("--mouse-device",required=True)
    rec.add_argument("--acknowledge-anonymous-event-privacy",action="store_true")
    rec.add_argument("--speaker-off-confirmed",action="store_true")
    rec.add_argument("--out",required=True,type=Path)
    args=p.parse_args()
    try:
        if args.action=="devices":
            devices()
        else:
            run(args)
    except (OSError,RuntimeError,ValueError,KeyError,wave.Error) as exc:
        p.exit(1,"ERROR: "+str(exc)+"\n")


if __name__=="__main__":
    main()
