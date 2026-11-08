#!/usr/bin/env python3
"""M4 synthetic-only test: exact mic capture, safe signal cleanup and no overwrite."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave

ROOT=Path(__file__).resolve().parents[1]
CAPTURE=ROOT/"tools/voice_raw_capture.py"

FAKE_PACTL=r'''#!/usr/bin/env python3
import sys
a=sys.argv[1:]
if a==["list","short","sources"]:
    print("1835\talsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback\tPipeWire\ts16le 1ch 48000Hz\tSUSPENDED")
    print("1836\talsa_output.pci-0000_00_1f.3.analog-stereo.monitor\tPipeWire\ts32le 2ch 48000Hz\tRUNNING")
else:
    sys.exit(6)
'''

FAKE_PW_RECORD=r'''#!/usr/bin/env python3
import json,os,signal,sys,time,wave
args=sys.argv[1:]
with open(os.environ["TEST_CAPTURE_CALLS"],"w") as fp:
    json.dump(args,fp)
if not any(arg=="--target=alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback" for arg in args):
    sys.exit(9)
if not {"--rate=48000","--channels=1","--format=s16","--channel-map=mono"}.issubset(set(args)):
    sys.exit(9)
with wave.open(args[-1],"wb") as w:
    w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000)
    w.writeframes(b"\0\0"*(48000*2))
with open(os.environ["TEST_CAPTURE_PID"],"w") as fp:
    fp.write(str(os.getpid()))
def done(_signal,_frame):
    sys.exit(0)
signal.signal(signal.SIGINT,done)
signal.signal(signal.SIGTERM,done)
while True:time.sleep(0.1)
'''


def main():
    with tempfile.TemporaryDirectory(prefix="cuewire-m4-fixture-") as tmp:
        temp=Path(tmp)
        bindir=temp/"bin";bindir.mkdir()
        for name,src in (("pactl",FAKE_PACTL),("pw-record",FAKE_PW_RECORD)):
            f=bindir/name;f.write_text(src);f.chmod(0o755)
        env=os.environ.copy()
        env.update({
            "PATH":str(bindir)+os.pathsep+env.get("PATH",""),
            "TEST_CAPTURE_CALLS":str(temp/"capture-args.json"),
            "TEST_CAPTURE_PID":str(temp/"capture-pid"),
        })
        inspect=subprocess.run([sys.executable,str(CAPTURE),"inspect"],
                               env=env,capture_output=True,text=True,timeout=8)
        assert inspect.returncode==0,inspect.stderr
        rows=json.loads(inspect.stdout)["physical_sources"]
        assert len(rows)==1 and rows[0]["name"].startswith("alsa_input.usb-Anlya")
        audit=ROOT/".audit"
        audit.mkdir(exist_ok=True)
        # Fixed name unique only to CI; never overwrite a past experiment.
        out=audit/("M4-raw-mock-"+str(os.getpid()))
        try:
            mic=rows[0]["name"]
            cmd=[sys.executable,str(CAPTURE),"record","--mic",mic,
                 "--out",str(out),"--max-seconds","10"]
            proc=subprocess.Popen(cmd,env=env,stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,text=True)
            try:
                deadline=time.monotonic()+5
                while not (temp/"capture-pid").exists():
                    if proc.poll() is not None:
                        raise AssertionError("Recorder unexpectedly exited early: "
                                             +str(proc.communicate()))
                    if time.monotonic()>deadline:raise AssertionError("Mock recorder not started")
                    time.sleep(0.05)
                os.kill(proc.pid,signal.SIGINT)
                stdout,stderr=proc.communicate(timeout=10)
                assert proc.returncode==0,(stdout,stderr)
            finally:
                if proc.poll() is None:
                    proc.kill();proc.communicate(timeout=4)
            log=json.loads((out/"manifest.json").read_text())
            assert log["schema"]=="cuewire-M4-raw-capture-v1"
            assert log["mic_node_name"]==mic
            assert log["stop_reason"]=="user_ctrl_c",log
            assert log["measurements"]["frames"]==96000,log
            assert log["measurements"]["clipped_samples"]==0
            assert log["explicit_processing"]["denoise"] is False
            assert log["explicit_processing"]["aec"] is False
            assert log["mic_pactl_state_at_start"]=="SUSPENDED"
            assert log["wav_sha256"] and len(log["wav_sha256"])==64
            args=json.loads((temp/"capture-args.json").read_text())
            assert args[0]=="--target="+mic
            assert (out/"raw.wav").is_file()
            second=subprocess.run(cmd,env=env,capture_output=True,
                                  text=True,timeout=8)
            assert second.returncode!=0
            assert "Refusing to overwrite" in second.stderr
            invalid=subprocess.run([
                sys.executable,str(CAPTURE),"record",
                "--mic","alsa_output.pci-0000_00_1f.3.analog-stereo.monitor",
                "--out",str(out.parent/"another-M4-mock"),"--max-seconds","10"],
                env=env,capture_output=True,text=True,timeout=8)
            assert invalid.returncode!=0
            assert "Refuse monitor" in invalid.stderr
            assert not (out.parent/"another-M4-mock").exists()
            print("PASS M4 RAW: exact physical mic, Ctrl+C finalizes WAV, "
                  "SHA manifest, zero clipping, no overwrite or monitor recording")
        finally:
            if out.exists():
                shutil.rmtree(out)


if __name__=="__main__":
    main()
