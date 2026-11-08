#!/usr/bin/env python3
"""Synthetic M1 provenance/comparison tests; no personal WAVs or external models."""
import hashlib
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

TOOLS=Path(__file__).resolve().parents[1]/"tools"
sys.path.insert(0,str(TOOLS))
import voice_m1_freeze as frozen
import voice_m1_publish_report as publisher


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wave_write(path,data):
    with wave.open(str(path),"wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.round(np.clip(data,-.8,.8)*32767).astype("<i2").tobytes())


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        t=np.arange(48000*3)/48000
        b0=.2*np.sin(2*np.pi*181*t)+.05*np.sin(2*np.pi*2700*t)
        rng=np.random.default_rng(20261108)
        b2=b0+rng.normal(0,0.025,len(t))
        b1=(b0+rng.normal(0,0.004,len(t)))[:-1440]
        clean=root/"clean.wav"
        noisy=root/"noisy.wav"
        restored=root/"restored.wav"
        wave_write(clean,b0)
        wave_write(noisy,b2)
        wave_write(restored,b1)
        chain=root/"M1-chain.json"
        chain.write_text(json.dumps({
            "schema":"cuewire-M1-frozen-processing-chain-v1",
            "status":"listening_key_chain_verified_source_run_unavailable",
            "blind_studio_wav_sha256":sha(restored),
            "frozen_stage_sha256":"frozen-control-sha",
            "ordered_models":["test_mock","test_mock_stage2"],
            "original_listening_round":{
                "source_kind":"model","model_output_label":"test_mock__test_mock_stage2",
                "model_source_sha256":"synthetic"},
            "source_run_verified":None
        }))
        out=root/"compare.json"
        p=subprocess.run([
            sys.executable,str(TOOLS/"voice_m1_compare.py"),
            "--chain-report",str(chain),
            "--clean",str(clean),"--noisy",str(noisy),
            "--restored",str(restored),
            "--out",str(out)],
            capture_output=True,text=True,timeout=60)
        assert p.returncode==0,p.stdout+p.stderr
        data=json.loads(out.read_text())
        assert data["schema"]=="cuewire-M1-clean-comparison-v1"
        assert data["restored_B1_vs_clean_B0"]["reference_coverage"]>=.98
        assert data["restored_B1_vs_clean_B0"]["duration_difference_ms"]==-30
        assert data["gain_over_original_noisy_si_sdr_db"]>0
        assert data["references"]["B1"]["source_sha256"]==sha(restored)
        assert data["references"]["B0"]["measurements"]["frame_count"]==144000
        report=publisher.make_report(json.loads(chain.read_text()),data)
        assert report["stage_order"]==["test_mock","test_mock_stage2"]
        assert not report["source_run_sha256_verified"]
        assert "source_run_dir" not in json.dumps(report)
        pub=subprocess.run([
            sys.executable,str(TOOLS/"voice_m1_publish_report.py"),
            "--chain",str(chain),"--comparison",str(out),"--check-only"],
            capture_output=True,text=True,timeout=20)
        assert pub.returncode==0,pub.stdout+pub.stderr
        assert "CHECK_ONLY" in pub.stdout
        assert "No WAV/audio uploaded" in pub.stdout
        again=subprocess.run([
            sys.executable,str(TOOLS/"voice_m1_compare.py"),
            "--chain-report",str(chain),"--clean",str(clean),
            "--noisy",str(noisy),"--restored",str(restored),
            "--out",str(out)],
            capture_output=True,text=True,timeout=15)
        assert again.returncode!=0 and "already exists" in again.stderr
        # Verify a nested prior blind key can only be followed when
        # the anchor WAV and its SHA agree, without unblinding control.
        previous=root/"previous";previous.mkdir()
        old=previous/"track_01.wav"
        old.write_bytes(restored.read_bytes())
        (previous/"answer_key.json").write_text(json.dumps({
            "schema":"cuewire-blind-listening-v1",
            "tracks":{"track_01.wav":{
                "source_kind":"model","model":"mock_chain__filter",
                "source_sha256":"fixture",
                "wav_sha256":sha(old),"gain":1.0
            }}
        }))
        prior=root/"prior";prior.mkdir()
        anchor=prior/"track_04.wav";anchor.write_bytes(old.read_bytes())
        (prior/"answer_key.json").write_text(json.dumps({
            "schema":"cuewire-blind-listening-v1",
            "tracks":{"track_04.wav":{
                "source_kind":"prior_blind_anchor",
                "previous_blind_id":"track_01","source_sha256":sha(old),
                "wav_sha256":sha(anchor),"gain":1.0
            }}
        }))
        chain_res=frozen.find_upstream_model(prior,4,previous)
        assert chain_res["ordered_stages"]==["mock_chain","filter"]
        old.write_bytes(b"tamper")
        try:
            frozen.find_upstream_model(prior,4,previous)
            raise AssertionError("Tampered prior anchor accepted")
        except RuntimeError as err:
            assert "Tampered" in str(err) or "SHA" in str(err)
    print("PASS: frozen M1 source-hash check, B0/B1/B2 scoring, "
          "30ms mismatch, explicit chain, private JSON-only publishing dry-run and anchor tamper check")


if __name__=="__main__":
    main()
