#!/usr/bin/env python3
"""CueWire M4 ALSA hardware capture: synthetic tests; no real mic touched."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/"tools/voice_alsa_capture.py"
FAKE_ARECORD=r'''#!/usr/bin/env python3
import os,sys,signal,time,wave
a=sys.argv[1:]
if a==["-l"]:
    print("**** List of CAPTURE Hardware Devices ****")
    print("card 0: "+os.environ.get("FAKE_ALSA_ID","MICAudio97i")+" [MICAudio-97i], device 0: USB Audio [USB Audio]")
    print("  Subdevices: 1/1")
    sys.exit(0)
if a[:2]!=["-D","hw:0,0"] or not set(["-f","S16_LE","-c","1","-r","48000","-t","wav","-d"]).issubset(set(a)):
    sys.exit(6)
with wave.open(a[-1],"wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(48000)
    wav.writeframes(b"\0\0"*int(os.environ.get("FAKE_PCM_FRAMES","96000")))
Path=os.path
with open(os.environ["FAKE_READY"],"w") as f:f.write(str(os.getpid()))
def finish(*_):
    sys.exit(0)
signal.signal(signal.SIGINT,finish)
signal.signal(signal.SIGTERM,finish)
while True:time.sleep(.1)
'''


def main():
    with tempfile.TemporaryDirectory(prefix="cuewire-alsa-m4-") as td:
        tmp=Path(td)
        bindir=tmp/"bin"
        bindir.mkdir()
        (bindir/"arecord").write_text(FAKE_ARECORD)
        (bindir/"arecord").chmod(0o755)
        env=os.environ.copy()
        env["PATH"]=str(bindir)+os.pathsep+env.get("PATH","")
        env["FAKE_READY"]=str(tmp/"ready")
        root=ROOT/".audit"
        root.mkdir(exist_ok=True)
        dest=root/("test-alsa-m4-"+str(os.getpid()))
        try:
            proc=subprocess.Popen([sys.executable,str(APP),"--out",str(dest),
                                   "--max-seconds","20"],
                                  env=env,capture_output=True,text=True)
            try:
                end=time.monotonic()+4
                while not (tmp/"ready").exists():
                    if time.monotonic()>end:
                        raise AssertionError("Mock arecord did not start")
                    if proc.poll() is not None:
                        raise AssertionError(str(proc.communicate()))
                    time.sleep(.06)
                proc.send_signal(signal.SIGINT)
                stdout,stderr=proc.communicate(timeout=7)
                assert proc.returncode==0,(stdout,stderr)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=3)
            state=json.loads((dest/"manifest.json").read_text())
            assert state["status"]=="recorded_unprocessed_raw",state
            assert state["device"]=="hw:0,0"
            assert state["card_id"]=="MICAudio97i"
            assert state["stop_reason"]=="user_ctrl_c",state
            assert state["measurements"]["frames"]==96000
            assert state["measurements"]["duration_s"]==2
            assert state["measurements"]["clipped_samples"]==0
            assert state["processing"]["denoise"] is False
            assert state["processing"]["aec"] is False
            assert len(state["raw_sha256"])==64
            assert (dest/"raw.wav").stat().st_size>192000
            assert (dest/"arecord.stderr.log").is_file()
            repeat=subprocess.run([sys.executable,str(APP),"--out",str(dest)],
                                  env=env,capture_output=True,text=True,timeout=7)
            assert repeat.returncode!=0
            assert "Refusing to overwrite" in repeat.stderr
            env_bad=dict(env)
            env_bad["FAKE_ALSA_ID"]="UnrelatedUSB"
            outsider=root/("test-alsa-unrelated-"+str(os.getpid()))
            mismatch=subprocess.run(
                [sys.executable,str(APP),"--out",str(outsider)],
                env=env_bad,capture_output=True,text=True,timeout=7)
            assert mismatch.returncode!=0
            assert "ALSA card has changed" in mismatch.stderr
            assert not outsider.exists()
            print("PASS M4 ALSA: exact capture hardware, PCM16, Ctrl+C, "
                  "manifest/SHA256, no overwrite, fail closed on card reordering")
        finally:
            if dest.exists():shutil.rmtree(dest)


if __name__=="__main__":
    main()
