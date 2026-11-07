#!/usr/bin/env python3
"""Hardware-free lifecycle integration test with fake pw-* executables.

Tests subprocess management and routing argv ONLY, not actual PipeWire sound.
Python standard library; no root, default audio changes or GUI.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def retry(predicate, *, timeout=6.0, interval=0.04):
    end = time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():
            return True
        time.sleep(interval)
    return False


FAKE = """#!/usr/bin/env python3
import json,os,pathlib,sys,time
tool=pathlib.Path(sys.argv[0]).name
if '--help' in sys.argv:
    sys.exit(0)
directory=pathlib.Path(os.environ['CUEWIRE_MOCK_DIR'])
if tool=='pw-dump':
    parent=os.getppid()
    rows=[
      {"node.name":"alsa_input.test_usb"},
      {"node.name":"bluez_output.test_headset"}
    ]
    trace=directory/'pw-loopback.jsonl'
    if trace.exists() and any('cuewire.bridge.' in ' '.join(x['argv'])
                             for x in [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]):
        rows.extend([
          {"node.name":f"cuewire.mix.{parent}"},
          {"node.name":f"cuewire.microphone.{parent}"}
        ])
    print(json.dumps(rows))
    sys.exit(0)
with (directory / (tool+'.jsonl')).open('a') as f:
    f.write(json.dumps({'pid':os.getpid(),'argv':sys.argv[1:]})+'\\n')
    f.flush()
while True:
    time.sleep(.5)
"""


def records(path):
    if not path.exists():
        return []
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]


def alive(pid):
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False


def main():
    if len(sys.argv)!=2:
        raise RuntimeError("usage: fake_runtime.py /path/to/cuewire")
    binary=str(Path(sys.argv[1]).resolve())
    with tempfile.TemporaryDirectory(prefix="cuewire-fake-") as tmp:
        root=Path(tmp)
        bindir=root/"bin"
        bindir.mkdir()
        runtime=root/"runtime"
        runtime.mkdir(mode=0o700)
        logdir=root/"logs"
        logdir.mkdir()
        for name in ("pw-loopback","pw-play","pw-dump"):
            target=bindir/name
            target.write_text(FAKE)
            target.chmod(0o755)
        cue1=root/"cue one.wav"
        cue2=root/"cue two.wav"
        cue1.write_bytes(b"RIFF"+b"0"*44)
        cue2.write_bytes(b"RIFF"+b"1"*44)
        env=os.environ.copy()
        env["PATH"]=str(bindir)+os.pathsep+env.get("PATH","")
        env["XDG_RUNTIME_DIR"]=str(runtime)
        env["CUEWIRE_MOCK_DIR"]=str(logdir)
        socket=runtime/"cuewire-v0.1.sock"

        def command(*args,timeout=5.0):
            return subprocess.run([binary,"ctl",*args],env=env,
                                  capture_output=True,text=True,timeout=timeout)

        def started():
            return socket.exists() and command("status").returncode==0

        def launch():
            return subprocess.Popen([
                binary,"run","--mic","alsa_input.test_usb",
                "--monitor","bluez_output.test_headset",
                "--cue","1="+str(cue1),
                "--cue","2="+str(cue2)
            ],env=env,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)

        proc=launch()
        seen=[]
        try:
            if not retry(started):
                proc.terminate() if proc.poll() is None else None
                try:
                    output, err = proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    output, err = proc.communicate()
                raise AssertionError(f"CueWire not ready: code={proc.returncode}, stdout={output!r}, stderr={err!r}, mock_log={list(logdir.iterdir())}")
            assert command("play","1").returncode==0
            assert retry(lambda:len(records(logdir/"pw-play.jsonl"))==2)
            first=records(logdir/"pw-play.jsonl")
            assert any("--target=bluez_output.test_headset" in row["argv"] for row in first)
            assert any(f"--target=cuewire.mix.{proc.pid}" in row["argv"] for row in first)
            assert all(str(cue1) in row["argv"] for row in first)
            assert command("play","2").returncode==0
            assert retry(lambda:len(records(logdir/"pw-play.jsonl"))==4)
            second=records(logdir/"pw-play.jsonl")
            seen=[row["pid"] for row in second]
            assert all(str(cue2) in row["argv"] for row in second[2:])
            assert command("stop").returncode==0
            assert retry(lambda:all(not alive(p) for p in seen)), "owned cue process survived stop"
            assert command("quit").returncode==0
            proc.wait(timeout=4)
            assert proc.returncode==0, "foreground process did not shut down cleanly"
            assert not socket.exists(), "control socket was left after quit"
            loops=records(logdir/"pw-loopback.jsonl")
            assert len(loops)==2, f"bridge / voice not spawned exactly once: {len(loops)}"
            assert all(not alive(row["pid"]) for row in loops)
            assert any("Audio/Sink" in " ".join(row["argv"]) for row in loops)
            assert any("Audio/Source" in " ".join(row["argv"]) for row in loops)
            print("PASS fake-runtime: explicit routes; single active cue; stop/quit cleanup")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=3)
            for row in records(logdir/"pw-loopback.jsonl")+records(logdir/"pw-play.jsonl"):
                if alive(row["pid"]):
                    try:
                        os.kill(row["pid"],signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        # Re-launch, verify SIGINT also disposes of all owned processes.
        proc=launch()
        try:
            assert retry(started)
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=4)
            assert proc.returncode==0
            assert not socket.exists()
            all_loops=records(logdir/"pw-loopback.jsonl")
            assert len(all_loops)==4
            assert all(not alive(row["pid"]) for row in all_loops)
            print("PASS fake-runtime: Ctrl+C/SIGINT cleanup")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=3)
            for row in records(logdir/"pw-loopback.jsonl"):
                if alive(row["pid"]):
                    try:
                        os.kill(row["pid"],signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()


if __name__=="__main__":
    main()
