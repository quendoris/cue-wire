#!/usr/bin/env python3
"""CueWire M7 operator console: foreground, terminal-only, stdlib-only.

A CONTROL FRONTEND for the existing Rust cuewire v0.1 Unix-socket protocol.
It does NOT run CueWire, capture audio, read evdev keys, load M6 filters,
create PipeWire devices, change defaults, autostart or launch background tasks.

Commands only control the ALREADY RUNNING user-owned CueWire instance.
quit requires confirmation and shuts down the engine; exit closes only
this frontend. This script never registers Super+C (separate opt-in
scope policy), and never records or logs key names or command history.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import socket
import stat
import sys
from typing import Mapping

SOCKET_NAME = "cuewire-v0.1.sock"
MAX_RESPONSE = 2048
SOCKET_TIMEOUT_S = 1.5
STATUS_FIELDS = ("mic", "input", "virtual_source", "monitor", "active_cue")
HELP_TEXT = """Commands:
  status        Refresh existing runtime state
  play 0..9     Play a configured sound cue (mic and explicitly chosen monitor)
  stop          Stop the active sound cue ONLY; microphone continues
  research      Show status of accepted M6 offline research
  quit          Ask to shut down the running CueWire engine and its owned children
  exit          Close this console; leave the engine running
  help          Show this command list
Ctrl+C closes this console. Super+C emergency termination is a SEPARATE
opt-in CueWire scope/hotkey, never registered by this console.
"""


class ConsoleError(Exception):
    """Refuse unsafe input or uncertain response without side effects."""


@dataclass(frozen=True)
class Snapshot:
    online: bool
    mode: str
    mic: str = "—"
    virtual_source: str = "—"
    monitor: str = "—"
    cue: str = "—"
    note: str = ""


def visible(value: str, width: int = 68) -> str:
    """Render untrusted protocol text without terminal control sequences."""
    cleaned = "".join(ch if ch.isprintable() and ch not in "\r\n\t"
                      else "?" for ch in value)
    if len(cleaned) > width:
        return cleaned[:width-1] + "…"
    return cleaned


def validate_socket_location(environment: Mapping[str, str] | None = None) -> Path:
    """Never talk to a symlink, unsafe socket or wrong-user runtime directory."""
    env = os.environ if environment is None else environment
    root_text = env.get("XDG_RUNTIME_DIR", "")
    if not root_text or "\x00" in root_text:
        raise ConsoleError("XDG_RUNTIME_DIR is not configured")
    root = Path(root_text)
    if not root.is_absolute():
        raise ConsoleError("XDG_RUNTIME_DIR must be an absolute directory")
    try:
        meta = root.lstat()
    except OSError as exc:
        raise ConsoleError(f"Runtime directory unavailable: {exc}") from exc
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid():
        raise ConsoleError("Runtime directory must be a real, user-owned directory")
    if meta.st_mode & 0o022:
        raise ConsoleError("Runtime directory must not be writable by group/others")
    destination = root / SOCKET_NAME
    try:
        sock_meta = destination.lstat()
    except FileNotFoundError as exc:
        raise ConsoleError("CueWire engine is not running (socket absent)") from exc
    except OSError as exc:
        raise ConsoleError(f"Cannot examine CueWire socket: {exc}") from exc
    if not stat.S_ISSOCK(sock_meta.st_mode):
        raise ConsoleError("CueWire endpoint is not a real Unix socket")
    if sock_meta.st_uid != os.getuid() or sock_meta.st_mode & 0o077:
        raise ConsoleError("CueWire socket must be user-owned with mode 0600")
    return destination


def operator_command(value: str) -> tuple[str, str | None]:
    """Allowlist ensures no command injection and no hidden shell dispatch."""
    command = value.strip()
    if command in ("status", "stop", "quit"):
        return ("remote", command)
    if re.fullmatch(r"play [0-9]", command, flags=re.ASCII):
        return ("remote", command)
    if command in ("help", "?", "research", "exit"):
        return ("local", command)
    raise ConsoleError("Unknown command; use help, status, play 0..9, stop, quit or exit")


def request(command: str, destination: Path | None = None) -> str:
    kind, approved = operator_command(command)
    if kind != "remote" or approved is None:
        raise ConsoleError("Only allowlisted remote commands may be transmitted")
    # Explicit path injection is used by isolated unit tests only.
    path = destination if destination is not None else validate_socket_location()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(SOCKET_TIMEOUT_S)
        try:
            client.connect(str(path))
            client.sendall((approved + "\n").encode("ascii"))
            received = bytearray()
            while len(received) < MAX_RESPONSE + 1:
                chunk = client.recv(min(256, MAX_RESPONSE + 1 - len(received)))
                if not chunk:
                    break
                received.extend(chunk)
                if b"\n" in received:
                    break
        except OSError as exc:
            raise ConsoleError(f"Cannot contact CueWire: {exc}") from exc
    if b"\n" not in received or len(received) > MAX_RESPONSE:
        raise ConsoleError("CueWire reply incomplete or too long")
    try:
        line = bytes(received).split(b"\n", 1)[0].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConsoleError("CueWire returned invalid UTF-8") from exc
    if "\x1b" in line or any(ord(ch) < 32 for ch in line):
        raise ConsoleError("CueWire reply contains unsafe terminal controls")
    if not line.startswith(("OK ", "ERROR ")):
        raise ConsoleError("CueWire reply does not match v0.1 protocol")
    return line


def parse_status(reply: str) -> Snapshot:
    if not reply.startswith("OK mic="):
        raise ConsoleError("CueWire did not return a valid v0.1 status")
    values: dict[str, str] = {}
    for item in reply[3:].split():
        field, sep, value = item.partition("=")
        if not sep or field not in STATUS_FIELDS or field in values:
            raise ConsoleError("Unexpected status field")
        if not value or not value.isascii() or not all(
                ch.isalnum() or ch in "._-()[]" for ch in value):
            raise ConsoleError("Unsafe or malformed status value")
        values[field] = value
    if set(values) != set(STATUS_FIELDS):
        raise ConsoleError("Incomplete CueWire runtime status")
    return Snapshot(
        online=True,
        mode="RAW transport • no live M6 DSP",
        mic=values["mic"],
        virtual_source=values["virtual_source"],
        monitor=values["monitor"],
        cue=("none" if values["active_cue"] == "None"
             else values["active_cue"]),
        note="Connected to the existing CueWire v0.1 control socket",
    )


def read_snapshot() -> Snapshot:
    try:
        return parse_status(request("status"))
    except ConsoleError as exc:
        return Snapshot(
            online=False, mode="OFFLINE",
            note=str(exc),
        )


def render(snapshot: Snapshot) -> str:
    status = "CONNECTED" if snapshot.online else "NOT CONNECTED"
    fields = [
        ("Runtime", status),
        ("Mode", snapshot.mode),
        ("Microphone", snapshot.mic),
        ("Virtual source", snapshot.virtual_source),
        ("Monitor", snapshot.monitor),
        ("Active cue", snapshot.cue),
        ("M6-007 research", "ACCEPTED offline reference • not loaded in runtime"),
        ("Notice", snapshot.note or "—"),
    ]
    width = 82
    top = "╭" + "─"*(width-2) + "╮"
    bottom = "╰" + "─"*(width-2) + "╯"
    headline = "CueWire  /  M7 OPERATOR CONSOLE"
    lines = [top, "│ "+headline.ljust(width-4)+" │",
             "├"+"─"*(width-2)+"┤"]
    for label, value in fields:
        content = f" {label:<17} {visible(value, 56)}"
        lines.append("│"+content[:width-2].ljust(width-2)+"│")
    lines.append(bottom)
    lines.append(" status  |  play 0..9  |  stop  |  research  |  quit  |  exit  |  help")
    return "\n".join(lines)


def print_research() -> None:
    print("M4 / speech cleaning: OFFLINE (not connected to live engine)")
    print("M5 / acoustic echo: research only (not active)")
    print("M6-007 / keyboard: accepted OFFLINE algorithm, archived and frozen")
    print("M7 / UI: this frontend uses only existing run/ctl transport")
    print("No live DSP toggle exists yet; enabling it would misrepresent the engine.")


def interactive(demo: bool = False) -> int:
    if not sys.stdin.isatty():
        raise ConsoleError("Interactive console requires a real terminal; use --preview or --once")
    demo_cue: int | None = None

    def snapshot() -> Snapshot:
        if not demo:
            return read_snapshot()
        return Snapshot(
            online=False, mode="DEMO / SIMULATED • no PipeWire access",
            mic="simulated_input", virtual_source="simulated_output",
            monitor="simulated_monitor",
            cue="none" if demo_cue is None else str(demo_cue),
            note="SIMULATION ONLY • no socket or audio devices accessed")

    print(render(snapshot()))
    print("\n"+HELP_TEXT)
    if demo:
        print("DEMO MODE: play, stop and status change only simulated state.")
    while True:
        try:
            command = input("cuewire> ")
        except (EOFError, KeyboardInterrupt):
            print("\nConsole closed. CueWire engine (if running) was NOT stopped.")
            return 0
        try:
            kind, action = operator_command(command)
            if action in ("help", "?"):
                print(HELP_TEXT)
            elif action == "research":
                print_research()
            elif action == "exit":
                print("Console closed. Running CueWire engine left untouched.")
                return 0
            elif action == "status":
                print(render(snapshot()))
            elif kind == "remote" and action is not None:
                if action == "quit":
                    answer = input(
                        "Stop RUNNING CueWire and its owned processes? Type QUIT: "
                        if not demo else
                        "End SIMULATED session? Type QUIT: ")
                    if answer != "QUIT":
                        print("Cancelled; CueWire continues running.")
                        continue
                    if demo:
                        print("Simulated session closed. Real CueWire not contacted.")
                        return 0
                if demo:
                    if action.startswith("play "):
                        demo_cue = int(action[-1])
                        print(f"SIMULATED: cue {demo_cue} selected (no sound played)")
                    elif action == "stop":
                        demo_cue = None
                        print("SIMULATED: cue stopped (no engine command sent)")
                    print(render(snapshot()))
                    continue
                response = request(action)
                print(visible(response, 160))
                if action == "quit" and response.startswith("OK "):
                    print("Runtime shutdown requested; wait for the engine's own cleanup.")
                    return 0
                print(render(snapshot()))
        except (ConsoleError, EOFError, KeyboardInterrupt) as exc:
            if isinstance(exc, ConsoleError):
                print("Refused:", visible(str(exc), 140))
            else:
                print("\nCancelled. Engine left untouched.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Foreground terminal control for existing CueWire v0.1; never changes audio defaults."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true",
                      help="render UI with NO socket, mic or PipeWire access")
    mode.add_argument("--once", action="store_true",
                      help="read-only snapshot from a running CueWire; then exit")
    mode.add_argument("--demo", action="store_true",
                      help="interactive simulated session; NO socket or audio access")
    opts = parser.parse_args(argv)
    try:
        if opts.preview:
            print(render(Snapshot(
                online=False, mode="PREVIEW ONLY",
                note="No socket/device accessed; ready for integration design")))
            return 0
        if opts.once:
            snapshot = read_snapshot()
            print(render(snapshot))
            return 0 if snapshot.online else 2
        return interactive(demo=opts.demo)
    except ConsoleError as exc:
        print("cuewire-ui:", visible(str(exc), 140), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
