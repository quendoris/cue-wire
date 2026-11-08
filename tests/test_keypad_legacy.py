#!/usr/bin/env python3
"""CI legacy hyprlang backend: fake hyprctl, live datagrams, error rollback."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/"tools/cuewire_keypad.py"
EMIT=ROOT/"tools/cuewire_keypad_emit.py"


FAKE_HYPRCTL=r'''#!/usr/bin/env python3
import json,os,re,sys
from pathlib import Path
state=Path(os.environ["H2_FAKE_STATE"])
calls=Path(os.environ["H2_FAKE_CALLS"])
argv=sys.argv[1:]
with calls.open("a") as f:
    f.write(json.dumps(argv)+"\n")
data=json.loads(state.read_text())
if argv[0]=="eval":
    print("eval is only supported with the lua config manager",file=sys.stderr)
    sys.exit(1)
if argv[:2]==["-j","binds"]:
    print(json.dumps(data))
    sys.exit(0)
if argv[0]=="keyword":
    name=argv[1]
    value=argv[2]
    match=re.match(r"\s*,\s*code:(\d+)(?:\s*,\s*exec\s*,\s*(.*))?$",value)
    if not match:sys.exit(4)
    code=int(match.group(1))
    if name=="unbind":
        data=[r for r in data if r.get("keycode")!=code]
    elif name in ("bindi","bindri","bindip","bindrip"):
        fails=int(os.environ.get("H2_FAIL_AFTER","0"))
        before=int(os.environ.get("H2_KEYWORD_COUNT","0"))
        counter=Path(os.environ["H2_FAKE_COUNTER"])
        count=int(counter.read_text())+1
        counter.write_text(str(count))
        if fails and count>fails:
            print("simulated registration failure",file=sys.stderr)
            sys.exit(5)
        data.append({"keycode":code,"key":"code:"+str(code),
                     "dispatcher":"exec","arg":match.group(2),
                     "modmask":0})
    else:
        sys.exit(6)
    state.write_text(json.dumps(data))
    print("ok")
    sys.exit(0)
sys.exit(8)
'''


def fixture(root):
    fake=root/"bin"
    fake.mkdir()
    exe=fake/"hyprctl"
    exe.write_text(FAKE_HYPRCTL)
    exe.chmod(0o755)
    state=root/"binds.json"
    state.write_text("[]")
    calls=root/"calls.jsonl"
    counter=root/"counter.txt"
    counter.write_text("0")
    runtime=root/"runtime"
    runtime.mkdir()
    env=os.environ.copy()
    env.update({
        "PATH":str(fake)+os.pathsep+env.get("PATH",""),
        "XDG_RUNTIME_DIR":str(runtime),
        "HYPRLAND_INSTANCE_SIGNATURE":"test-legacy",
        "H2_FAKE_STATE":str(state),
        "H2_FAKE_CALLS":str(calls),
        "H2_FAKE_COUNTER":str(counter),
    })
    return env,state,calls


def wait_ready(path,proc,secs=12):
    deadline=time.monotonic()+secs
    while time.monotonic()<deadline:
        if path.exists():return
        if proc.poll() is not None:
            raise AssertionError("Adapter exited before ready: "+
                                 str(proc.communicate()))
        time.sleep(.06)
    raise AssertionError("Adapter did not become ready")


def test_success(root):
    env,state,calls=fixture(root)
    logfile=root/"legacy-001.jsonl"
    proc=subprocess.Popen([
        sys.executable,str(APP),"run","--temporary-bindings",
        "--log-jsonl",str(logfile)],
        env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        wait_ready(logfile,proc)
        data=json.loads(state.read_text())
        assert len(data)==32,data
        assert sum(row["arg"].split()[-2]=="down" for row in data)==16
        assert sum(row["arg"].split()[-2]=="up" for row in data)==16
        owner=data[0]["arg"].split()[-1]
        assert owner and "-" in owner
        for key,phase in (("K1","down"),("K1","up"),
                          ("K2","down"),("K2","up"),
                          ("K2","down"),("K2","up")):
            r=subprocess.run([sys.executable,str(EMIT),key,phase,owner],
                             env=env,capture_output=True,text=True,timeout=3)
            assert r.returncode==0,r.stderr
            time.sleep(.07)
        time.sleep(.42)
        proc.send_signal(signal.SIGTERM)
        out,err=proc.communicate(timeout=8)
        assert proc.returncode==0,(out,err)
        events=[json.loads(x) for x in logfile.read_text().splitlines()]
        assert any(x["event"]=="single" and x["keys"]==["K1"] for x in events)
        assert any(x["event"]=="double" and x["keys"]==["K2"] for x in events)
        assert any(x["event"]=="reset" for x in events)
        assert json.loads(state.read_text())==[],state.read_text()
        assert not list(Path(env["XDG_RUNTIME_DIR"]).glob("cuewire-keypad-h2-*.sock"))
        calls_j=[json.loads(x) for x in calls.read_text().splitlines()]
        assert any(c[0]=="eval" for c in calls_j)
        assert sum(c[:2]==["keyword","unbind"] for c in calls_j)==16
        print("PASS hyprlang: 32 temporary binds, K1 single, K2 double, cleanup")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=3)


def test_partial_failure(root):
    env,state,calls=fixture(root)
    env["H2_FAIL_AFTER"]="5"
    logfile=root/"should-not-exist.jsonl"
    p=subprocess.run([sys.executable,str(APP),"run",
        "--temporary-bindings","--log-jsonl",str(logfile)],
        env=env,text=True,capture_output=True,timeout=16)
    assert p.returncode!=0,p.stdout+p.stderr
    assert "registration failure" in p.stderr
    assert not logfile.exists(),"Failure must not create a zero-length log"
    assert json.loads(state.read_text())==[],state.read_text()
    assert not list(Path(env["XDG_RUNTIME_DIR"]).glob("cuewire-keypad-h2-*.sock"))
    print("PASS hyprlang: partial registration rollback and no empty JSONL")


def test_foreign_conflict(root):
    env,state,calls=fixture(root)
    existing=[{"keycode":90,"key":"code:90","dispatcher":"exec",
               "arg":"unrelated","modmask":0}]
    state.write_text(json.dumps(existing))
    logfile=root/"should-not-exist.jsonl"
    p=subprocess.run([sys.executable,str(APP),"run",
        "--temporary-bindings","--log-jsonl",str(logfile)],
        env=env,text=True,capture_output=True,timeout=8)
    assert p.returncode!=0
    assert "REFUSED" in p.stderr or "conflict" in p.stderr.lower()
    assert json.loads(state.read_text())==existing
    assert not logfile.exists()
    print("PASS hyprlang: foreign keybind conflict is not overwritten")


def main():
    with tempfile.TemporaryDirectory(prefix="cuewire-H2-legacy-ok-") as tmp:
        test_success(Path(tmp))
    with tempfile.TemporaryDirectory(prefix="cuewire-H2-legacy-bad-") as tmp:
        test_partial_failure(Path(tmp))
    with tempfile.TemporaryDirectory(prefix="cuewire-H2-legacy-foreign-") as tmp:
        test_foreign_conflict(Path(tmp))


if __name__=="__main__":
    main()
