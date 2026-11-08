#!/usr/bin/env python3
"""Publish M1 MODEL CHAIN + COMPARISON JSON only to the pinned PRIVATE repo.

No WAV, source paths, answer keys, personal audio, tokens or environment
variables are ever written to the report. Requires literal UPLOAD consent.
This updates GitHub repository contents; NOT a release/audio upload.
"""
import argparse
import base64
import json
import subprocess
from pathlib import Path

REPO="quendoris/cue-wire-voice-bench"
TARGET="reports/M1-exact-chain-and-test-comparison.json"


def gh(*args, input_data=None, check=True):
    p=subprocess.run(["gh",*args],input=input_data,text=True,
                     capture_output=True,timeout=35)
    if check and p.returncode:
        raise RuntimeError("GitHub CLI error: "+p.stderr[-1300:])
    return p


def make_report(chain,comparison):
    verified=chain.get("source_run_verified")
    if chain.get("schema")!="cuewire-M1-frozen-processing-chain-v1":
        raise ValueError("Unexpected chain schema")
    if comparison.get("schema")!="cuewire-M1-clean-comparison-v1":
        raise ValueError("Unexpected comparison schema")
    if comparison.get("references",{}).get("B1",{}).get("source_sha256") != chain.get("blind_studio_wav_sha256"):
        raise ValueError("B1 SHA mismatch between comparison and chain report")
    allowed=("source_benchmark_commit","output_sha256","stage_details",
             "stage_names","context_pad_ms","source_input_sha256",
             "weights_recorded_by_benchmark")
    run={k:verified[k] for k in allowed if k in verified} if verified else None
    origin=chain.get("original_listening_round",{})
    leaf=origin.get("upstream",origin)
    return {
        "schema":"cuewire-M1-exact-chain-comparison-published-v1",
        "evidence_status":chain.get("status"),
        "source_run_sha256_verified":bool(run),
        "stage_order":chain.get("ordered_models"),
        "stage_names_from_listening_key":leaf.get("model_output_label"),
        "verified_studio_control_sha256":chain.get("frozen_stage_sha256"),
        "verified_blind_01_sha256":chain.get("blind_studio_wav_sha256"),
        "original_model_output_sha256":leaf.get("model_source_sha256"),
        "model_source_run":run,
        "playback_level_normalization_history":
            chain.get("playback_level_normalization_history",{}),
        "audio_comparison":comparison,
        "privacy":"JSON metadata only; no WAV or local absolute paths",
        "remaining_gaps":[
            "Bit-exact rerun still requires recording executable/model checkpoints and dependencies."
        ] if not (run and run.get("weights_recorded_by_benchmark")) else [
            "Recorded weight paths/hashes may not cover all auto-downloaded third-party checkpoints."
        ]
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--chain",type=Path,required=True)
    p.add_argument("--comparison",type=Path,required=True)
    p.add_argument("--check-only",action="store_true",
                   help="Validate reports and display exact privacy-safe payload; no GitHub write")
    args=p.parse_args()
    if not args.chain.is_file() or not args.comparison.is_file():
        p.error("Both report JSON files must exist")
    report=make_report(json.loads(args.chain.read_text()),
                       json.loads(args.comparison.read_text()))
    out=json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+"\n"
    print("Source stages:", " -> ".join(report["stage_order"]))
    print("Source model run / SHA verified:",report["source_run_sha256_verified"])
    print("Upload target:",f"https://github.com/{REPO}/blob/main/{TARGET}")
    print("Payload:",len(out.encode()),"bytes of JSON only. No WAV/audio uploaded.")
    if args.check_only:
        print("CHECK_ONLY: everything is validated; no network write performed.")
        return
    visibility=gh("repo","view",REPO,"--json","visibility","--jq",".visibility").stdout.strip()
    if visibility!="PRIVATE":
        raise RuntimeError("ABORT: private repository is not PRIVATE; nothing uploaded.")
    existing=gh("api",f"repos/{REPO}/contents/{TARGET}",check=False)
    if existing.returncode==0:
        raise RuntimeError("ABORT: report already exists; refusing to overwrite a prior audit.")
    if "404" not in existing.stderr and "Not Found" not in existing.stderr:
        raise RuntimeError("Cannot establish that private target is absent: "+existing.stderr[-1000:])
    print("ВНИМАНИЕ: отправляю ТОЛЬКО модельную цепочку, параметры и численные метрики.")
    print("Ни WAV, ни локальные абсолютные пути, ни записи речи не отправляются.")
    confirmation=input("Для публикации введи буквально UPLOAD большими латинскими буквами: ").strip()
    if confirmation!="UPLOAD":
        print("Отменено. В GitHub ничего не отправлено.")
        return
    body=json.dumps({
        "message":"audit(private): publish verified M1 chain and numeric clean-reference comparison",
        "content":base64.b64encode(out.encode("utf-8")).decode("ascii")
    })
    gh("api","--method","PUT",f"repos/{REPO}/contents/{TARGET}",
       "--input","-",input_data=body)
    print("Published verified JSON metadata ONLY:",f"https://github.com/{REPO}/blob/main/{TARGET}")


if __name__=="__main__":
    main()
