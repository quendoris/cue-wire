#!/usr/bin/env python3
"""Resolve and freeze the exact audio lineage of the USER-VERIFIED blind M1 01.

This tool intentionally looks at PREVIOUS listening keys, not a model guess
from correlation. It does not modify, process or upload WAV data.
"""
import argparse
import hashlib
import json
from pathlib import Path


FROZEN_SOURCE_SHA = "357dc4303b4e1f9bbe4c4ddc374c6695b2160f1a183e0daab500fa949798bdf3"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get_key(directory, number):
    root = Path(directory)
    key_file = root / "answer_key.json"
    if not key_file.is_file():
        raise RuntimeError(f"Missing listening answer key: {key_file}")
    key = json.loads(key_file.read_text())
    if key.get("schema") != "cuewire-blind-listening-v1":
        raise RuntimeError(f"Unrecognized listening-kit schema: {key_file}")
    filename = f"track_{number:02}.wav"
    meta = key.get("tracks", {}).get(filename)
    audio = root / filename
    if meta is None or not audio.is_file():
        raise RuntimeError(f"Missing blind track/key: {audio}")
    actual = digest(audio)
    if actual != meta.get("wav_sha256"):
        raise RuntimeError(f"Tampered WAV or key: {audio}")
    return root, filename, meta, actual


def find_upstream_model(root, number, previous):
    _, filename, info, actual = get_key(root, number)
    kind = info.get("source_kind")
    if kind == "model":
        model = info.get("model")
        if not model or not all(v.replace("_", "").isalnum() for v in model.split("__")):
            raise RuntimeError(f"Malformed upstream model label for {filename}")
        stages = model.split("__")
        run_folder = Path(root).parent / root.name.replace("voice-audition-", "bench-run-")
        return {
            "source_kind": "model", "blind_listening": str(root),
            "blind_track": filename, "model_output_label": model,
            "ordered_stages": stages, "listening_sha256": actual,
            "model_source_sha256": info.get("source_sha256"),
            "origin_run_hint": str(run_folder),
            "require_source_run_manifest_for_replay": True
        }
    if kind == "prior_blind_anchor":
        old = info.get("previous_blind_id")
        if old not in ("track_01", "track_05"):
            # Do NOT silently guess where an anchor came from.
            raise RuntimeError(
                f"Anchor {old} cannot be resolved automatically; "
                "supply its actual prior listening key via --previous")
        if previous is None:
            raise RuntimeError(
                f"Track {filename} is a prior blind anchor ({old}). "
                "Specify --previous DIR containing the old answer_key.json.")
        prior_audio = Path(previous) / (old + ".wav")
        if not prior_audio.is_file() or digest(prior_audio) != info.get("source_sha256"):
            raise RuntimeError(
                "Previous blind anchor audio does not match its source SHA: "
                + str(prior_audio))
        nested = find_upstream_model(previous, int(old[-2:]), None)
        return {
            "source_kind": "prior_blind_anchor",
            "blind_listening": str(root), "blind_track": filename,
            "anchor_id": old, "listening_sha256": actual,
            "anchor_source_sha256": info.get("source_sha256"),
            "upstream": nested,
            "ordered_stages": nested["ordered_stages"]
        }
    raise RuntimeError(f"Unknown source_kind {kind} for {filename}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--studio", type=Path, required=True,
                   help="Old voice-audition-studio-001 directory")
    p.add_argument("--prior", type=Path, required=True,
                   help="Old voice-audition-005 directory with blind 04")
    p.add_argument("--previous", type=Path, default=None,
                   help="Old voice-audition-rnnoise-001, only if 04 was an anchor")
    p.add_argument("--studio-run", type=Path, required=True,
                   help="Original bench-studio-001 directory")
    p.add_argument("--source-run", type=Path, default=None,
                   help="Original source benchmark, if known. Highest-priority explicit choice.")
    p.add_argument("--scan-root", type=Path, default=None,
                   help="Otherwise inspect bench-*/manifest.json directly inside this folder and verify source WAV/hash.")
    p.add_argument("--out", type=Path, required=True,
                   help="Fresh local JSON output outside public Git tracking")
    args = p.parse_args()
    if args.out.exists():
        p.error("Output exists; refusing to overwrite frozen provenance")
    studio, name, studio_key, studio_audio_hash = get_key(args.studio, 1)
    if studio_key.get("source_kind") != "model":
        raise RuntimeError("Studio track 01 is not a local model output")
    if studio_key.get("model") != "blind04__control":
        raise RuntimeError(
            "Studio track 01 is NOT the recorded blind04 control. "
            "Stop: verify user listened to the documented studio round.")
    if studio_key.get("source_sha256") != FROZEN_SOURCE_SHA:
        raise RuntimeError("Studio track 01 points to a different source SHA")
    studio_result = args.studio_run / "results" / "blind04__control.wav"
    if digest(studio_result) != FROZEN_SOURCE_SHA:
        raise RuntimeError("Local studio control SHA differs from M1 frozen SHA")
    studio_manifest_file = args.studio_run / "manifest.json"
    studio_manifest = json.loads(studio_manifest_file.read_text())
    if studio_manifest.get("jobs", {}).get("blind04__control", {}).get("output_sha256") != FROZEN_SOURCE_SHA:
        raise RuntimeError("Studio manifest does not match frozen source")
    prior, prior_name, prior_key, prior_audio_hash = get_key(args.prior, 4)
    if studio_manifest.get("input_source_hashes", {}).get("04") != prior_audio_hash:
        raise RuntimeError(
            "Blind04 WAV is not the recorded studio input. "
            "Do NOT label a model until chain provenance is resolved.")
    # A named model in the earlier listening round is now explicitly
    # revealed because this stage is being frozen as the production basis.
    origin = find_upstream_model(args.prior, 4, args.previous)
    result = {
        "schema": "cuewire-M1-frozen-processing-chain-v1",
        "status": "locally_verified",
        "not_used_as_model_input": "test.wav",
        "frozen_stage_sha256": FROZEN_SOURCE_SHA,
        "blind_studio_wav_sha256": studio_audio_hash,
        "source_identity_verified": True,
        "source_stage": "blind04__control (no added studio EQ/compression)",
        "original_listening_round": origin,
        "playback_level_normalization_history": {
            "prior_blind04": {
                "gain_factor": prior_key.get("gain"),
                "wav_sha256": prior_audio_hash,
                "source_sha256": prior_key.get("source_sha256")
            },
            "studio_blind01": {
                "gain_factor": studio_key.get("gain"),
                "wav_sha256": studio_audio_hash,
                "source_sha256": studio_key.get("source_sha256")
            }
        },
        "ordered_models": origin["ordered_stages"],
        "reproducibility_note": (
            "Ordered model names are verified by local answer keys. "
            "Bit-identical replay also requires original git commit, "
            "model checkpoints, model settings and intermediate WAV hashes. "
            "This metadata must not be mistaken for checkpoint verification."
        )
    }
    source_run = args.source_run
    if source_run is None and args.scan_root:
        leaf = origin["upstream"] if origin["source_kind"] == "prior_blind_anchor" else origin
        label = "__".join(origin["ordered_stages"])
        expected_source_sha = leaf.get("model_source_sha256")
        matches = []
        for candidate_manifest in sorted(args.scan_root.glob("bench-*/manifest.json")):
            try:
                manifest = json.loads(candidate_manifest.read_text())
                job = manifest.get("jobs", {}).get(label, {})
                candidate_audio = candidate_manifest.parent / "results" / (label + ".wav")
                if (job.get("status") == "ok"
                        and job.get("output_sha256") == expected_source_sha
                        and candidate_audio.is_file()
                        and digest(candidate_audio) == expected_source_sha):
                    matches.append(candidate_manifest.parent)
            except (OSError, ValueError, TypeError):
                continue
        if len(matches) > 1:
            raise RuntimeError("Multiple byte-valid source benchmarks found; "
                               "choose one with --source-run: "
                               + ", ".join(str(x) for x in matches))
        if matches:
            source_run = matches[0]
        else:
            print("WARNING: could not verify original source benchmark by SHA-256; "
                  "the model stages come from listening keys only.")
    if origin["source_kind"] == "prior_blind_anchor":
        previous_id = origin["anchor_id"]
        _, _, prev_key, prev_audio_sha = get_key(
            args.previous, int(previous_id[-2:]))
        result["playback_level_normalization_history"]["previous_anchor"] = {
            "gain_factor": prev_key.get("gain"),
            "wav_sha256": prev_audio_sha,
            "source_sha256": prev_key.get("source_sha256")
        }
    if source_run:
        source_manifest_file = source_run / "manifest.json"
        if not source_manifest_file.is_file():
            raise RuntimeError(f"Missing model-run manifest: {source_manifest_file}")
        model_run = json.loads(source_manifest_file.read_text())
        ordered = origin["ordered_stages"]
        label = "__".join(ordered)
        stage_job = model_run.get("jobs", {}).get(label)
        if not stage_job or stage_job.get("status") != "ok":
            raise RuntimeError(f"The selected model chain {label} has no successful source-run job")
        source_entry = origin
        if origin["source_kind"] == "prior_blind_anchor":
            source_entry = origin["upstream"]
        source_hash = source_entry.get("model_source_sha256")
        actual_model_file = source_run/"results"/(label+".wav")
        if not actual_model_file.is_file() or digest(actual_model_file)!=source_hash:
            raise RuntimeError(
                "Source benchmark WAV does not match previous blind listening key. "
                "Do not freeze an unverifiable chain.")
        if stage_job.get("output_sha256")!=source_hash:
            raise RuntimeError("Source benchmark manifest SHA is inconsistent with previous blind key")
        result["source_run_verified"] = {
            "source_run_dir": str(source_run),
            "source_benchmark_commit": model_run.get("repo_commit"),
            "output_sha256": source_hash,
            "stage_details": stage_job.get("stage_details"),
            "stage_names": stage_job.get("steps"),
            "context_pad_ms": model_run.get("context_pad_ms"),
            "source_input_sha256": model_run.get("input_files",{}).get("noisy",{}).get("sha256"),
            "weights_recorded_by_benchmark": model_run.get("weights",[])
        }
        result["reproducibility_note"] += (
            " Source-run WAV, manifest output hash and original model path also verified."
        )
        result["status"] = "source_chain_and_run_sha256_verified"
    else:
        result["status"] = "listening_key_chain_verified_source_run_unavailable"
        result["source_run_verified"] = None
        print("NOTE: exact stage IDs verified, but add --source-run to pin benchmark commit and verify the model output.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)+"\n")
    print("PASS: M1 blind WAV, source studio control and upstream listening key verified.")
    print("Original ordered model stages:", " -> ".join(origin["ordered_stages"]))
    print("Frozen audio SHA-256:", FROZEN_SOURCE_SHA)
    print("Provenance manifest:", args.out)
    print("No WAV uploaded, deleted, changed or reprocessed.")


if __name__ == "__main__":
    main()
