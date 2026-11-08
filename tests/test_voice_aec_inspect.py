#!/usr/bin/env python3
"""M5 test: read-only physical audio node discovery, no PipeWire dependency."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import voice_aec_inspect as aud


SINKS=(
"36\talsa_output.pci-X.analog-stereo\tPipeWire\tfloat32le 2ch 48000Hz\tRUNNING\n"
"41\tbluez_output.headphones\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"
)
SOURCES=(
"44\talsa_input.usb-physical-mic\tPipeWire\ts16le 1ch 48000Hz\tRUNNING\n"
"45\talsa_output.pci-X.analog-stereo.monitor\tPipeWire\tfloat32le 2ch 48000Hz\tIDLE\n"
"47\tbluez_output.headphones.monitor\tPipeWire\tfloat32le 2ch 48000Hz\tSUSPENDED\n"
)
INFO=(
"Server Name: PulseAudio (on PipeWire 1.4)\n"
"Default Sink: bluez_output.headphones\n"
"Default Source: alsa_input.usb-physical-mic\n"
)


def check():
    a=aud.short_nodes(SINKS,"sinks")
    b=aud.short_nodes(SOURCES,"sources")
    assert a[0]["name"]=="alsa_output.pci-X.analog-stereo"
    assert b[0]["sample_spec"]=="s16le 1ch 48000Hz"
    assert len(b)==3
    assert aud.defaults(INFO)["default_sink"]=="bluez_output.headphones"
    for value in ("not enough columns","x\tn\tfake\tspec"):
        try:
            aud.short_nodes(value,"sinks")
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted unstructured or unsafe pactl response")
    with tempfile.TemporaryDirectory(prefix="cuewire-aec-preflight-") as tmp:
        base=Path(tmp)
        fake=base/"bin";fake.mkdir()
        binary=fake/"pactl"
        calls=base/"calls.jsonl"
        binary.write_text("""#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
a=sys.argv[1:]
with Path(os.environ["AEC_TEST_CALLS"]).open("a") as f:
    f.write(json.dumps(a)+"\\n")
if a==["list","short","sinks"]:
    print(os.environ["AEC_SINKS"],end="")
elif a==["list","short","sources"]:
    print(os.environ["AEC_SOURCES"],end="")
elif a==["info"]:
    print(os.environ["AEC_INFO"],end="")
else:
    sys.exit(42)
""")
        binary.chmod(0o755)
        env=os.environ.copy()
        env["PATH"]=str(fake)+os.pathsep+env.get("PATH","")
        env["AEC_TEST_CALLS"]=str(calls)
        env["AEC_SINKS"]=SINKS
        env["AEC_SOURCES"]=SOURCES
        env["AEC_INFO"]=INFO
        output=base/"read-only.json"
        cmd=[sys.executable,str(ROOT/"tools/voice_aec_inspect.py"),
             "--out",str(output)]
        result=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=10)
        assert result.returncode==0,(result.stdout,result.stderr)
        doc=json.loads(output.read_text())
        assert doc["schema"]=="cuewire-M5-echo-reference-discovery-v1"
        assert doc["mode"]=="read-only"
        assert len(doc["output_sinks"])==2
        assert len(doc["input_sources"])==1
        assert len(doc["playback_monitor_candidates"])==2
        assert doc["default_sink"]=="bluez_output.headphones"
        assert doc["output_sinks"][0]["name"]!="bluez_output.headphones",\
            "One available speaker does not have to be the default"
        assert doc["input_sources"][0]["name"]=="alsa_input.usb-physical-mic"
        assert [json.loads(x) for x in calls.read_text().splitlines()]==[
            ["list","short","sinks"],["list","short","sources"],["info"]]
        original=output.read_bytes()
        second=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=10)
        assert second.returncode!=0
        assert output.read_bytes()==original
    print("PASS: read-only M5 discovery, named speaker/monitor pairing, "
          "default≠speaker, exact three pactl queries, no overwrite")


if __name__=="__main__":
    check()
