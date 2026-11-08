#!/usr/bin/env python3
"""No-hardware, no-compositor-mutation tests for CueWire hotkey module."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
TOOL=ROOT/"tools/cuewire_hotkeys.py"
SCOPE=ROOT/"tools/cuewire_scope.sh"


def run(*args, env=None):
    return subprocess.run([*map(str,args)],capture_output=True,text=True,
                          timeout=12,env=env)


def main():
    r=run(sys.executable,TOOL,"render","--format","lua","--repo",ROOT)
    assert r.returncode==0, r.stderr
    lua=r.stdout
    assert lua.count("hl.bind(")==12
    assert 'hl.bind("KP_0"' in lua
    assert 'hl.bind("KP_9"' in lua
    assert 'hl.bind("KP_Decimal"' in lua
    assert 'hl.bind("SUPER + C"' in lua
    assert "dont_inhibit = true" in lua
    assert lua.count("ignore_mods = true")==12
    assert "ctl stop" in lua and "ctl play 0" in lua
    assert "cuewire_scope.sh emergency" in lua
    assert all(x not in lua.lower() for x in ("sudo ", "systemctl enable",
                                               "killall ", "forcekillactive "))
    r=run(sys.executable,TOOL,"render","--format","legacy","--repo",ROOT)
    assert r.returncode==0,r.stderr
    text=r.stdout
    assert text.count("bindi = , KP_")==11
    assert "bindip = SUPER, C, exec" in text
    assert "ctl play 0" in text and "ctl play 9" in text
    assert not (ROOT/"tools"/"cuewire-hotkeys-generated.conf").exists()

    with tempfile.TemporaryDirectory(prefix="cuewire-hotkeys-test-") as td:
        temp=Path(td)
        fixture=temp/"binds.json"
        fixture.write_text("[]")
        r=run(sys.executable,TOOL,"check","--repo",ROOT,
              "--binds-json",fixture)
        assert r.returncode==0,r.stdout+r.stderr
        assert "PASS" in r.stdout
        fixture.write_text(json.dumps([
            {"key":"KP_5","modmask":0,"dispatcher":"exec","arg":"other"},
            {"key":"C","modmask":64,"dispatcher":"exec","arg":"terminal"},
        ]))
        r=run(sys.executable,TOOL,"check","--repo",ROOT,
              "--binds-json",fixture)
        assert r.returncode==3,r.stdout+r.stderr
        assert "KP_5" in r.stdout
        assert '"key": "C"' in r.stdout
        # Unknown fake bindings must not trip conservative preflight.
        fixture.write_text(json.dumps([
            {"key":"F12","modmask":64,"dispatcher":"exec","arg":"other"}]))
        r=run(sys.executable,TOOL,"check","--repo",ROOT,
              "--binds-json",fixture)
        assert r.returncode==0
        # Malformed input is a fail-closed exception, not an installed bind.
        fixture.write_text("{}")
        r=run(sys.executable,TOOL,"check","--repo",ROOT,
              "--binds-json",fixture)
        assert r.returncode!=0

        r=run("bash","-n",SCOPE)
        assert r.returncode==0,r.stderr
        r=run("bash",SCOPE,"--help")
        assert r.returncode==0 and "scope" in r.stdout

        # Test Super+C dispatch against a fake systemctl executable.
        # No real processes or systemd units are ever targeted in CI.
        bindir=temp/"bin"
        bindir.mkdir()
        log=temp/"calls.log"
        fake=bindir/"systemctl"
        fake.write_text("""#!/usr/bin/env python3
import os,sys
from pathlib import Path
Path(os.environ["TEST_CTL_LOG"]).open("a").write(" ".join(sys.argv[1:])+"\\n")
args=sys.argv[1:]
if "is-active" in args: sys.exit(0)
if "show" in args:
    if "--property=Description" in args:
        print(os.environ.get("TEST_SCOPE_MARKER","CueWire Hotkeys Scoped Foreground"))
    elif "--property=Transient" in args:
        print("yes")
    sys.exit(0)
if "kill" in args: sys.exit(0)
sys.exit(3)
""")
        fake.chmod(0o755)
        env=os.environ.copy()
        env["PATH"]=str(bindir)+os.pathsep+env.get("PATH","")
        env["TEST_CTL_LOG"]=str(log)
        ok=run("bash",SCOPE,"emergency",env=env)
        assert ok.returncode==0,ok.stdout+ok.stderr
        lines=log.read_text().splitlines()
        assert any("--user kill --kill-whom=cgroup --signal=SIGKILL cuewire-hotkeys.scope" in line
                   for line in lines),lines
        log.write_text("")
        env["TEST_SCOPE_MARKER"]="Unrelated process scope"
        foreign=run("bash",SCOPE,"emergency",env=env)
        assert foreign.returncode==3,foreign.stdout+foreign.stderr
        assert all(" kill " not in line for line in log.read_text().splitlines())
    print("PASS: 12 safe bind plans, Lua and hyprlang, conflicts, no mutation, "
          "scope-only emergency and foreign-scope refusal")


if __name__=="__main__":
    main()
