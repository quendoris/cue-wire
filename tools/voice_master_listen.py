#!/usr/bin/env python3
"""Blinded, gain-matched audition of M3 studio presets, BY INPUT SOURCE.

No original WAV or completed benchmark is modified. Loudness matching is
for listening ONLY, so louder ≠ better. Separate B0 and B1 playlists
ensure 'clean input' cannot be confused with 'restored input'.
"""
import argparse
import json
import random
from pathlib import Path
from voice_bench_listen import read_wav,normalized,write_wav,sha


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir",type=Path)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--seed",type=int,default=20261108)
    p.add_argument("--rms-dbfs",type=float,default=-28)
    args=p.parse_args()
    if not -40<=args.rms_dbfs<=-20:p.error("RMS audition range -40..-20 dBFS")
    if args.out.exists():p.error("Output already exists; use a fresh directory")
    manifest_file=args.run_dir/"manifest.json"
    if not manifest_file.is_file():p.error("Missing mastering manifest")
    manifest=json.loads(manifest_file.read_text())
    if manifest.get("schema")!="cuewire-studio-master-v1":
        p.error("Unsupported mastering schema")
    files=manifest.get("outputs",{})
    by_source={}
    for name,meta in files.items():
        source=meta.get("source")
        path=args.run_dir/"results"/(name+".wav")
        if source not in manifest.get("inputs",{}):
            p.error(f"Unrecognized source {source}")
        if not path.is_file() or sha(path)!=meta.get("output_sha256"):
            p.error(f"Missing or tampered result: {path}")
        by_source.setdefault(source,[]).append((name,path))
    if not by_source:p.error("No WAV output in mastering run")
    args.out.mkdir(parents=True)
    for index,(source,items) in enumerate(sorted(by_source.items())):
        order=sorted(items)
        random.Random(args.seed+index).shuffle(order)
        folder=args.out/source
        folder.mkdir()
        key={"schema":"cuewire-studio-audition-v1",
             "source_id":source,"shuffle_seed":args.seed+index,
             "playback_rms_target_dbfs":args.rms_dbfs,
             "source_sha256":manifest["inputs"][source]["source_sha256"],
             "tracks":{}}
        lines=[]
        for i,(name,path) in enumerate(order,1):
            dest=folder/f"track_{i:02}.wav"
            wav=read_wav(path)
            matched,gain=normalized(wav,args.rms_dbfs,-3.0)
            write_wav(dest,matched)
            key["tracks"][dest.name]={"studio_profile":name,"original_sha256":sha(path),
                                       "listening_sha256":sha(dest),"gain_factor":gain}
            lines.append(str(dest))
        (folder/"answer_key.json").write_text(json.dumps(
            key,indent=2,sort_keys=True)+"\n")
        (folder/"playlist.txt").write_text("\n".join(lines)+"\n")
        print(f"{source}: {len(lines)} tracks -> {folder}; key stays closed until ranking.")
    print("No playback or upload started; source versions and processing results are unchanged.")
    print(f'Listen separately: for f in "{args.out}"/B1/track_*.wav; do echo "$f"; pw-play "$f"; done')


if __name__=="__main__":
    main()
