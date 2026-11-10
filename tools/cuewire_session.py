#!/usr/bin/env python3
"""CueWire M7.3 single-session supervisor: default-node discovery + TUI.

No default DEVICE changes, no Hyprland writes, no daemons, no auto-start.
--check is read-only. --run creates ONE transient user systemd scope
holding the Python TUI, Rust engine, and its subprocesses; the existing
tools/cuewire_scope.sh emergency kills that exact marked cgroup.

A physical session is never started unless --run is explicitly specified.
M6-007 remains OFFLINE. This program does not process/capture audio.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import termios
import time

ROOT=Path(__file__).resolve().parents[1]
SCOPE="cuewire-hotkeys.scope"
DESCRIPTION="CueWire Hotkeys Scoped Foreground"
BIN=ROOT/"target/release/cuewire"
SCRIPT=Path(__file__).resolve()
PROFILE=ROOT/".audit"/"cuewire-session-v1.json"
NODENAME=re.compile(r"[A-Za-z0-9._-]{1,160}\Z")
MAX_WPCTL_TEXT=32768
MAX_GRAPH_BYTES=6*1024*1024


class SessionError(RuntimeError):
    pass


class TTYGuardian:
    """Outside-scope TTY restorer survives SIGKILL of inner raw-mode TUI."""
    def __init__(self):
        self.attributes=None
        self.fd=None

    def __enter__(self):
        if sys.stdin.isatty() and sys.stdout.isatty():
            self.fd=sys.stdin.fileno()
            self.attributes=termios.tcgetattr(self.fd)
        return self

    def __exit__(self,exc_type,exc,tb):
        if self.attributes is None:
            return
        try:
            termios.tcsetattr(self.fd,termios.TCSADRAIN,self.attributes)
        finally:
            # If the interior TUI was SIGKILLed, its alternate screen and
            # hidden cursor must be restored by THIS surviving shell owner.
            sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()


def command(argv:list[str],timeout:float=3.0)->subprocess.CompletedProcess:
    try:
        return subprocess.run(argv,stdin=subprocess.DEVNULL,
                              capture_output=True,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise SessionError("command unavailable or timed out: "+argv[0]) from exc


def default_node(alias:str,expected_class:str)->str:
    """Read current default node; never call wpctl set-default or any setter."""
    result=command(["wpctl","inspect",alias],timeout=2.0)
    if result.returncode or len(result.stdout)>MAX_WPCTL_TEXT:
        raise SessionError("WirePlumber default unavailable: "+alias)
    try: text=result.stdout.decode("utf-8")
    except UnicodeError as exc:raise SessionError("invalid wpctl output") from exc
    matches=re.findall(r'(?m)^\s*(?:\*?\s*)?node\.name\s*=\s*"?([^"\n]+)"?\s*$',text)
    # Common wpctl format: '  * node.name = "alsa_input...."'
    if len(matches)!=1:
        raise SessionError("default node.name missing/ambiguous: "+alias)
    name=matches[0].strip().strip('"')
    if not NODENAME.fullmatch(name) or name.startswith("cuewire."):
        raise SessionError("untrusted or virtual default: "+alias)
    # The exact resolved graph media.class is verified separately.
    return name


def graph_nodes()->dict[str,list[dict]]:
    result=command(["pw-dump"],timeout=3.0)
    if result.returncode or len(result.stdout)>MAX_GRAPH_BYTES:
        raise SessionError("PipeWire node enumeration failed")
    try:
        graph=json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError,ValueError) as exc:
        raise SessionError("invalid PipeWire graph JSON") from exc
    if not isinstance(graph,list):raise SessionError("unexpected PipeWire graph")
    out:dict[str,list[dict]]={}
    for item in graph:
        if not isinstance(item,dict):continue
        props=item.get("info",{}).get("props",{}) if isinstance(item.get("info"),dict) else None
        if not isinstance(props,dict):continue
        n=props.get("node.name")
        if isinstance(n,str):out.setdefault(n,[]).append(props)
    return out


def resolve_nodes(mic:str|None,monitor:str|None)->tuple[str,str]:
    mic=mic or default_node("@DEFAULT_AUDIO_SOURCE@","Audio/Source")
    monitor=monitor or default_node("@DEFAULT_AUDIO_SINK@","Audio/Sink")
    if not NODENAME.fullmatch(mic) or not NODENAME.fullmatch(monitor):
        raise SessionError("Invalid explicit device node.name")
    if mic==monitor or mic.startswith("cuewire.") or monitor.startswith("cuewire."):
        raise SessionError("Microphone/output collision or recursive CueWire route")
    nodes=graph_nodes()
    for name,expected in ((mic,"Audio/Source"),(monitor,"Audio/Sink")):
        found=nodes.get(name,[])
        if len(found)!=1 or found[0].get("media.class")!=expected:
            raise SessionError("Device absent, duplicated or wrong media.class: "+name)
        if expected=="Audio/Source" and found[0].get("node.name") != name:
            raise SessionError("Unexpected mic node")
    return mic,monitor


def parse_cues(items:list[str])->list[str]:
    if not items:raise SessionError("At least one explicit --cue N=/absolute/file.wav is required")
    seen=set()
    parsed=[]
    for value in items:
        i,sep,filename=value.partition("=")
        if not sep or not re.fullmatch(r"[0-9]",i) or i in seen:
            raise SessionError("Invalid or duplicated cue ID; use 0..9")
        seen.add(i)
        p=Path(filename)
        if not p.is_absolute() or not p.is_file() or p.is_symlink():
            raise SessionError("Cue must be an existing, non-symlink absolute file")
        try:
            with p.open("rb") as source:
                header=source.read(12)
            if p.stat().st_size<=44 or header[:4] not in (b"RIFF",b"RF64") or header[8:12]!=b"WAVE":
                raise SessionError("Cue must be a nonempty WAV container")
        except OSError as exc:
            raise SessionError("Cannot read cue WAV header") from exc
        # Extended RF64/WAVE_FORMAT_EXTENSIBLE are left to pw-play.
        # No user audio data are loaded or logged by this supervisor.
        parsed.append(f"{i}={p}")
    return parsed


def private_profile_cues()->list[str]:
    """Only a user-owned 0600 JSON file in ignored .audit is accepted."""
    if PROFILE.is_symlink():
        raise SessionError("Refuse symlink local profile")
    try:meta=PROFILE.lstat()
    except FileNotFoundError:return []
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.getuid()
            or (meta.st_mode & 0o777)!=0o600 or meta.st_size>16384):
        raise SessionError("Local profile must be user-owned regular 0600")
    try:
        data=json.loads(PROFILE.read_text(encoding="utf-8"))
    except (OSError,UnicodeError,ValueError) as exc:
        raise SessionError("Corrupt local cue profile") from exc
    if (not isinstance(data,dict) or set(data)!={"schema","cues"}
            or data["schema"]!="cuewire-local-cues-v1"
            or not isinstance(data["cues"],list)
            or not all(isinstance(x,str) for x in data["cues"])):
        raise SessionError("Unsupported cue profile schema")
    return parse_cues(data["cues"])


def write_private_profile(cues:list[str],replace:bool=False)->None:
    """Explicit manual action; atomic private file, never checked into Git."""
    parsed=parse_cues(cues)
    directory=PROFILE.parent
    if directory.is_symlink():
        raise SessionError("Refuse symlink .audit")
    directory.mkdir(mode=0o700,exist_ok=True)
    meta=directory.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=os.getuid() or meta.st_mode&0o022:
        raise SessionError("Unsafe .audit ownership or permissions")
    if PROFILE.is_symlink() or (PROFILE.exists() and not replace):
        raise SessionError("Profile exists; explicitly use --replace-profile")
    # An owner-only temporary file is atomically renamed into place.
    fd,name=tempfile.mkstemp(prefix=".cuewire-profile-",dir=directory)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as output:
            json.dump({"schema":"cuewire-local-cues-v1","cues":parsed},
                      output,ensure_ascii=True,indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name,PROFILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def scope_active()->bool:
    result=command(["systemctl","--user","is-active","--quiet",SCOPE])
    return result.returncode==0


def scope_marked()->bool:
    result=command(["systemctl","--user","show",SCOPE,
                    "--property=Description,Transient","--no-pager"])
    if result.returncode:return False
    rows=dict(x.split("=",1) for x in result.stdout.decode("utf-8","replace").splitlines() if "=" in x)
    return rows.get("Description")==DESCRIPTION and rows.get("Transient")=="yes"


def socket_path()->Path:
    root=os.environ.get("XDG_RUNTIME_DIR")
    if not root or not Path(root).is_absolute():
        raise SessionError("XDG_RUNTIME_DIR missing or not absolute")
    parent=Path(root)
    meta=parent.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=os.getuid() or meta.st_mode&0o022:
        raise SessionError("Unsafe runtime directory")
    return parent/"cuewire-v0.1.sock"


def refuse_existing()->None:
    if scope_active():
        raise SessionError("CueWire scope already active; refusing takeover")
    endpoint=socket_path()
    if endpoint.is_symlink() or endpoint.exists():
        raise SessionError("CueWire socket already exists; never hijack or delete it")


def wait_live(child:subprocess.Popen, mic:str,monitor:str)->None:
    import cuewire_tui_live as live
    end=time.monotonic()+7.0
    while time.monotonic()<end:
        if child.poll() is not None:
            raise SessionError("CueWire engine exited during startup")
        state=live.read_live()
        if state.connected:
            if state.mic!=mic or state.monitor!=monitor:
                raise SessionError("Connected engine identity differs from requested devices")
            return
        time.sleep(.12)
    raise SessionError("CueWire engine did not become ready within 7 seconds")


def stop_engine(child:subprocess.Popen)->None:
    """Try Rust's proper cleanup first, then only the owned child.

    A SIGKILL of the whole scope is available externally when this
    routine cannot complete (Super+C emergency). Never killall/pkill.
    """
    if child.poll() is not None:return
    try:
        import cuewire_tui_live as live
        report=live.read_live()
        if report.connected:
            live.send_live("quit")
    except Exception:
        pass
    try:
        child.wait(timeout=2.5)
        return
    except subprocess.TimeoutExpired:
        child.terminate()
    try:
        child.wait(timeout=1.2)
        return
    except subprocess.TimeoutExpired:
        # The scope is the only reliable owner for descendants.
        command(["systemctl","--user","kill","--kill-whom=cgroup",
                 "--signal=SIGKILL",SCOPE],timeout=2.0)


def run_inside(mic:str,monitor:str,cues:list[str],latency_ms:int,
               no_motion:bool,mono:bool)->int:
    """Start supervisor and TUI IN THE SAME verified transient cgroup."""
    if not scope_active() or not scope_marked():
        raise SessionError("Refusing direct unscoped internal run")
    refuse_socket=socket_path()
    if refuse_socket.exists() or refuse_socket.is_symlink():
        raise SessionError("Existing CueWire socket at session start")
    command_line=[str(BIN),"run","--mic",mic,"--monitor",monitor,
                  "--latency-ms",str(latency_ms)]
    for cue in cues:command_line.extend(["--cue",cue])
    # An anonymous Linux memfd captures startup errors only, with
    # NO persistent personal logs and no output in the fullscreen TUI.
    logfd=os.memfd_create("cuewire-session-diagnostics",0)
    child=None
    try:
        child=subprocess.Popen(
            command_line,stdin=subprocess.DEVNULL,
            stdout=logfd,stderr=logfd)
        try:
            wait_live(child,mic,monitor)
        except SessionError:
            os.lseek(logfd,0,os.SEEK_SET)
            detail=os.read(logfd,4096).decode("utf-8","replace")
            safe="".join(ch if ch.isprintable() else " " for ch in detail)
            if safe:
                print("CueWire Rust startup diagnostics: "+safe[-1500:],
                      file=sys.stderr)
            raise
        import cuewire_tui
        # We are in the single emergency-kill cgroup with all engine
        # child processes; TUI stays in THIS foreground process.
        return cuewire_tui.run_demo(no_motion=no_motion,mono=mono,
                                    live=True,managed=True)
    finally:
        if child is not None:stop_engine(child)
        os.close(logfd)


def main(argv:list[str]|None=None)->int:
    ap=argparse.ArgumentParser(description=__doc__)
    modes=ap.add_mutually_exclusive_group(required=True)
    modes.add_argument("--configure",action="store_true",help="explicitly save local 0600 cue profile under ignored .audit")
    modes.add_argument("--check",action="store_true",help="read-only device auto-detect and scope preflight")
    modes.add_argument("--run",action="store_true",help="explicit single foreground scoped engine+TUI")
    modes.add_argument("--inside-scope",action="store_true",help=argparse.SUPPRESS)
    ap.add_argument("--mic",help="explicit MIC node.name; omitted = WirePlumber default")
    ap.add_argument("--monitor",help="explicit SINK node.name; omitted = WirePlumber default")
    ap.add_argument("--cue",action="append",default=[],metavar="N=/absolute/file.wav")
    ap.add_argument("--latency-ms",type=int,default=25)
    ap.add_argument("--no-motion",action="store_true")
    ap.add_argument("--mono",action="store_true")
    ap.add_argument("--replace-profile",action="store_true",
                    help="allow explicit overwrite only together with --configure")
    a=ap.parse_args(argv)
    try:
        if not 5<=a.latency_ms<=150:
            raise SessionError("latency-ms must be 5..150")
        if a.replace_profile and not a.configure:
            raise SessionError("--replace-profile requires --configure")
        if a.configure:
            write_private_profile(a.cue,replace=a.replace_profile)
            print("Private local cue profile saved (0600) in ignored .audit")
            print("No PipeWire, socket or Hyprland settings changed")
            return 0
        if a.check:
            cues=parse_cues(a.cue) if a.cue else private_profile_cues()
            mic,monitor=resolve_nodes(a.mic,a.monitor)
            print("Default audio selection (READ ONLY; does NOT change defaults)")
            print("  mic:    "+mic)
            print("  output: "+monitor)
            print("  scope:  "+("ACTIVE" if scope_active() else "inactive"))
            print("  cues:   "+str(len(cues))+" validated")
            if not cues:
                print("  setup:  --configure --cue N=/absolute/audio.wav")
            return 0
        if not BIN.is_file() or not os.access(BIN,os.X_OK):
            raise SessionError("Missing compiled target/release/cuewire")
        if a.inside_scope:
            # All potentially lengthy auto-detection and file validation
            # run inside the killable scope, even during initialization.
            if not scope_active() or not scope_marked():
                raise SessionError("Refusing direct unscoped internal run")
            cues=parse_cues(a.cue) if a.cue else private_profile_cues()
            if not cues:
                raise SessionError("No cues configured; use --configure --cue N=/absolute/audio.wav")
            mic,monitor=resolve_nodes(a.mic,a.monitor)
            return run_inside(mic,monitor,cues,a.latency_ms,
                              a.no_motion,a.mono)
        # --run: refuse takeover before creating any scoped process.
        # No lengthy wpctl/pw-dump or user profile work outside scope.
        refuse_existing()
        args=["systemd-run","--user","--scope","--collect",
              "--unit="+SCOPE,"--description="+DESCRIPTION,"--",
              sys.executable,str(SCRIPT),"--inside-scope",
              "--latency-ms",str(a.latency_ms)]
        if a.mic:args.extend(["--mic",a.mic])
        if a.monitor:args.extend(["--monitor",a.monitor])
        for cue in a.cue:args.extend(["--cue",cue])
        if a.no_motion:args.append("--no-motion")
        if a.mono:args.append("--mono")
        # Entire engine + TUI belong to the same emergency cgroup.
        # The invoker stays outside and does not modify other processes.
        try:
            with TTYGuardian():
                outcome=subprocess.run(args,check=False)
            return outcome.returncode
        except KeyboardInterrupt:
            if scope_active() and scope_marked():
                killed=command(["systemctl","--user","kill",
                                "--kill-whom=cgroup","--signal=SIGKILL",
                                SCOPE],timeout=2.0)
                if killed.returncode:
                    raise SessionError("Interrupted; scoped cgroup kill failed")
            return 130
    except (SessionError,OSError) as exc:
        print("cuewire-session: "+str(exc),file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
