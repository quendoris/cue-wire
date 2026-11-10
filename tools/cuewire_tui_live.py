"""CueWire M7.2 read-only live telemetry and guarded existing controls.

This is a tiny frontend for the EXISTING foreground CueWire v0.1
Unix control socket. It NEVER starts the engine, opens an audio device,
changes PipeWire defaults, installs hotkeys or records microphone/input
events. The optional doctor probes the graph with read-only pw-dump,
with a bounded timeout. No 'DSP enabled' claim is possible here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess

import cuewire_console as protocol

GRAPH_TIMEOUT_S = 2.0
GRAPH_MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class LiveReport:
    connected: bool
    message: str
    mic: str = "unknown"
    monitor: str = "unknown"
    virtual_source: str = "unknown"
    mix_input: str = "unknown"
    active_cue: str = "unknown"
    socket_acl: str = "UNKNOWN"
    protocol: str = "UNKNOWN"


@dataclass(frozen=True)
class Check:
    name: str
    result: str           # PASS / FAIL / SKIP
    detail: str


def short(message: str, width: int = 110) -> str:
    """Printable, bounded error display; do not leak raw socket replies."""
    return protocol.visible(str(message), width)


def read_live() -> LiveReport:
    """Fresh status on EVERY call; never reuse cached online state."""
    try:
        protocol.validate_socket_location()
    except protocol.ConsoleError as exc:
        return LiveReport(False, short(exc), socket_acl="FAIL",
                          protocol="SKIP")
    try:
        response = protocol.request("status",timeout_seconds=0.5)
        status = protocol.parse_status(response)
    except protocol.ConsoleError as exc:
        return LiveReport(False, short(exc), socket_acl="PASS",
                          protocol="FAIL")
    return LiveReport(True, "CueWire v0.1 foreground engine responded",
                      mic=status.mic, monitor=status.monitor,
                      virtual_source=status.virtual_source,
                      mix_input=_field_from_status(response, "input"),
                      active_cue=status.cue,
                      socket_acl="PASS", protocol="PASS")


def _field_from_status(status_text: str, field: str) -> str:
    """Extract only an already strictly validated protocol field."""
    for token in status_text[3:].split():
        if token.startswith(field + "="):
            return token.partition("=")[2]
    raise protocol.ConsoleError("Incomplete v0.1 status")


def send_live(command: str) -> str:
    """Only Rust allowlisted commands, no shell; errors fail closed."""
    allowed, canonical = protocol.operator_command(command)
    if allowed != "remote" or canonical is None:
        raise protocol.ConsoleError("Not an existing CueWire control action")
    response = protocol.request(canonical,timeout_seconds=1.2)
    if not response.startswith("OK "):
        raise protocol.ConsoleError(short(response))
    return short(response, 150)


def _graph_nodes() -> dict[str, dict]:
    """Read-only pw-dump with timeout. No module loading or device commands."""
    try:
        result = subprocess.run(
            ["pw-dump"], stdin=subprocess.DEVNULL, capture_output=True,
            timeout=GRAPH_TIMEOUT_S, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("pw-dump unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("pw-dump timed out") from exc
    except OSError as exc:
        raise RuntimeError("pw-dump failed to start") from exc
    if result.returncode != 0:
        raise RuntimeError("pw-dump returned nonzero status")
    if len(result.stdout) > GRAPH_MAX_BYTES:
        raise RuntimeError("pw-dump result exceeds safety bound")
    try:
        graph = json.loads(result.stdout.decode("utf-8"))
    except (ValueError,UnicodeError) as exc:
        raise RuntimeError("pw-dump output is not valid JSON") from exc
    if not isinstance(graph, list):
        raise RuntimeError("pw-dump did not return graph records")
    nodes: dict[str, dict] = {}
    for item in graph:
        if not isinstance(item, dict):
            continue
        info = item.get("info")
        if not isinstance(info, dict):
            continue
        props = info.get("props")
        if not isinstance(props, dict):
            continue
        name=props.get("node.name")
        if not isinstance(name,str) or not name:
            continue
        # Duplicated names make positive health claims ambiguous.
        if name in nodes:
            nodes[name]={"duplicate":True}
        else:
            nodes[name]=props
    return nodes


def doctor(report: LiveReport | None = None) -> tuple[Check,...]:
    """Independent read-only checks; do not claim audio flows or latency.

    Even correct node registration does NOT prove mic signal quality,
    correct linking, all child process ownership or Super+C behavior.
    """
    current=read_live() if report is None else report
    checks=[
        Check("same-user socket 0600",current.socket_acl,
              "validated before contact" if current.socket_acl=="PASS"
              else "unavailable / refused"),
        Check("CueWire v0.1 protocol",current.protocol,
              "strictly parsed status" if current.protocol=="PASS"
              else "not verified"),
    ]
    if not current.connected:
        checks.append(Check("PipeWire node graph","SKIP",
                            "no authenticated live engine status"))
        checks.append(Check("live M6 / latency","SKIP",
                            "not implemented / not measured"))
        return tuple(checks)
    try:
        nodes=_graph_nodes()
    except RuntimeError as exc:
        checks.append(Check("PipeWire node graph","FAIL",short(exc)))
    else:
        targets=(
            ("physical mic reported",current.mic,None),
            ("monitor reported",current.monitor,None),
            ("virtual source",current.virtual_source,"Audio/Source"),
            ("mix input",current.mix_input,"Audio/Sink"),
        )
        for label,name,expected_class in targets:
            props=nodes.get(name)
            if props is None or props.get("duplicate"):
                checks.append(Check(label,"FAIL","missing or ambiguous"))
            elif expected_class and props.get("media.class")!=expected_class:
                checks.append(Check(label,"FAIL","unexpected media.class"))
            else:
                checks.append(Check(label,"PASS","node registered (not signal proven)"))
    checks.append(Check("live M6 / latency","SKIP",
                        "DSP not connected / latency not measured"))
    return tuple(checks)
