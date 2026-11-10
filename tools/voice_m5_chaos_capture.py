#!/usr/bin/env python3
"""CueWire: one physical chaotic double-talk capture, with clean controls.

User-triggered, foreground-only, PipeWire physical endpoints (EXPLICIT).
A local music WAV WITH VOCALS is played at stream volume 0.60,
twice the earlier 0.30 experiments but never changing sink/default volume.

Timeline (approximate wall time; recorder continuously active):
3s no-touch warmup, 8s OFF silence,
8s mouse + music, 8s keyboard + music,
8s mouse+keyboard + music, stop music, 1.0s settle,
8s CLEAN microphone voice only (no speaker music),
then music+near-end talking+mouse+keyboard+possible laughter UNTIL Ctrl+C.
Original Russian jokes are displayed periodically in the terminal.

User intentionally authorizes music playback and a NEW physical mic WAV.
No monitor/virtual mic, PipeWire module/default/sink gain change, autostart
or daemon. All child process groups owned and parent-death signal guarded.
Ctrl+C during free-talk finalizes ONE uninterrupted raw recording with
SHA256SUMS. Super+C/forced termination kills child processes but may
leave incomplete evidence; no system settings persist. No AEC yet.

This is a purposely contaminated physical dataset, NOT an AEC
acceptance or clean, sample-synchronized far-end truth. Each pw-play
relaunch creates a possible gap; log all events.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time
import wave

from voice_m5_aec import AUDIT, digest, RATE
from voice_m5_capture import (
    checked_program, checked_speaker, private_output,
    spawn, stop_children, terminate_group,
)
from voice_raw_capture import choose_source

JOKES = Path(__file__).resolve().parents[1] / "docs" / "voice-m5-stress-jokes-ru.txt"
PHASE_SECONDS = 8.
WARMUP_SECONDS = 3.
STOP_SETTLE_SECONDS = 1.
FREE_JOKE_SECONDS = 7.
PHASES = (
    ("quiet_before", "ТИШИНА: ни голоса, ни клавиатуры, ни мышки", False, False),
    ("mouse_music", "МЫШЬ: ЛКМ/ПКМ, клавиатуру и голос не трогаем", True, False),
    ("keyboard_music", "КЛАВИАТУРА: печатай, мышь не трогаем, не говорим", True, False),
    ("both_music", "ОБЕ: мышь + клавиатура; пока БЕЗ голоса", True, False),
    ("clean_voice", "ТОЛЬКО ТВОЙ ГОЛОС: музыка выключена, руки не стучат", False, True),
)
MUSIC_STREAM_VOLUME = .60


def validate_music(path: Path) -> dict:
    """Local source only, signed PCM16 WAV; never transcode or adjust file."""
    path=path.expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".wav":
        raise ValueError("Specify a local PCM16 WAV containing vocals")
    with wave.open(str(path),"rb") as f:
        rate, channels, width, encoding, frames=(
            f.getframerate(),f.getnchannels(),f.getsampwidth(),
            f.getcomptype(),f.getnframes())
        if (rate not in (44100,48000) or
                channels not in (1,2) or width != 2 or
                encoding != "NONE" or frames <= 8*rate or frames > 900*rate):
            raise ValueError(
                "Music WAV requires PCM16 mono/stereo 44.1/48kHz and 8..900s")
    return {
        "path":str(path),
        "sha256":digest(path),
        "rate":rate,
        "channels":channels,
        "duration_s":round(frames/rate,6),
        "contains_vocals_user_selected_not_automatically_verified":True,
    }


def load_jokes(path: Path) -> tuple[list[str],str]:
    file=path.expanduser().resolve(strict=True)
    lines=[
        line.strip() for line in file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(lines)<20 or any(len(x)>450 for x in lines):
        raise ValueError("Expected >=20 one-line original jokes")
    return lines,digest(file)


def inspect_audio(path: Path, marked_phases: list[dict]) -> dict:
    """Summaries ONLY; exact mic captured time vs wall time not calibrated."""
    import numpy as np
    with wave.open(str(path),"rb") as f:
        fmt=(f.getframerate(),f.getnchannels(),
             f.getsampwidth(),f.getcomptype())
        if fmt!=(RATE,1,2,"NONE"):
            raise ValueError("Recording invalid: expected physical mono 48k PCM16")
        frames=f.getnframes()
        payload=f.readframes(frames)
    if len(payload)!=2*frames or frames<20*RATE:
        raise ValueError("Recording incomplete")
    pcm=np.frombuffer(payload,dtype="<i2")
    max_abs=int(np.max(np.abs(pcm.astype(np.int32))))
    clips=int(np.count_nonzero(np.abs(pcm.astype(np.int32))>=32767))
    stats={}
    for phase in marked_phases:
        left=max(0,round((phase["start_since_recorder_launch_s"]+.75)*RATE))
        right=min(frames,round((phase["end_since_recorder_launch_s"]-.75)*RATE))
        if right-left<1*RATE:
            stats[phase["name"]]={"usable":False,
                "reason":"phase too short after 0.75s edge trim"}
            continue
        x=pcm[left:right].astype(np.float64)/32768.
        power=float(np.mean(x*x))
        stats[phase["name"]]={
            "usable":True,
            "approx_mic_wav_time_s":[round(left/RATE,4),
                                     round(right/RATE,4)],
            "rms_dbfs":round(10*math.log10(max(power,1e-24)),5),
            "peak_dbfs":round(20*math.log10(max(float(np.max(np.abs(x))),1e-12)),5),
            "clipped_samples":int(np.count_nonzero(np.abs(pcm[left:right].astype(np.int32))>=32767)),
        }
    return {
        "recorded_frames":frames,
        "recorded_seconds":round(frames/RATE,5),
        "max_abs_pcm16":max_abs,
        "clipped_samples":clips,
        "phase_stats":stats,
        "mic_and_pw_play_not_sample_synchronized":True,
    }


def run(args: argparse.Namespace) -> dict:
    if sys.platform != "linux":
        raise RuntimeError("Linux only: child group and parent-death semantics")
    for exe in ("pw-record","pw-play","pactl"):
        checked_program(exe)
    if not args.source.startswith("alsa_input."):
        raise ValueError("Explicit physical alsa_input.* microphone required")
    if not args.speaker.startswith("alsa_output."):
        raise ValueError("Explicit physical alsa_output.* speaker required")
    mic=choose_source(args.source)
    sink=checked_speaker(args.speaker)
    music=validate_music(args.music)
    jokes,jokes_sha=load_jokes(args.jokes)
    if not math.isclose(args.volume,MUSIC_STREAM_VOLUME,abs_tol=1e-9):
        raise ValueError("For this pinned stress protocol, use volume 0.60")
    out=private_output(args.out)
    if out==AUDIT:
        raise ValueError("Capture output cannot be .audit itself")
    out.mkdir(mode=0o700)
    raw=out/"microphone-chaos-raw.wav"
    phases=[]
    playback=[]
    joke_events=[]
    player=None
    recorder=None
    launched=None
    active="setup"
    state="running"
    failure=None
    stopped_by_user=False
    music_events_counter=0
    joke_index=0
    free_start=None
    capture_log=out/"pw-record.log"

    def timecode()->float:
        return round(time.monotonic()-launched,5) if launched else 0.0

    def next_joke()->None:
        nonlocal joke_index
        item=jokes[joke_index%len(jokes)]
        joke_index+=1
        t=timecode()
        joke_events.append({"t_since_recorder_launch_s":t,
                            "index":(joke_index-1)%len(jokes)})
        print("\n" + "═"*48 + "\n"
              + f"АНЕКДОТ {joke_index} — читай вслух:\n"
              + item + "\n" + "═"*48,flush=True)

    def music_start()->None:
        nonlocal player,music_events_counter
        if player is not None:
            raise RuntimeError("Music already playing")
        music_events_counter+=1
        path=out/f"pw-play-{music_events_counter:03d}.log"
        cmd=["pw-play","--target="+sink["name"],
             "--volume="+format(args.volume,".2f"),music["path"]]
        player=spawn(cmd,path)
        playback.append({
            "instance":music_events_counter,
            "start_since_recorder_launch_s":timecode(),
            "music_file_start_s":0.0,
            "log":path.name,
            "command":cmd,
            "end_since_recorder_launch_s":None,
            "stop_reason":None,
        })

    def music_stop(reason:str)->None:
        nonlocal player
        if player is None:
            return
        old=player
        was_running=old.poll() is None
        if was_running:
            terminate_group(old)
        else:
            old.wait()
        playback[-1]["end_since_recorder_launch_s"]=timecode()
        playback[-1]["stop_reason"]=reason
        playback[-1]["exit_code"]=old.returncode
        player=None

    def alive(music_required:bool)->None:
        if recorder is None or recorder.poll() is not None:
            raise RuntimeError(
                "Physical microphone recorder exited unexpectedly; "
                "inspect pw-record.log")
        if music_required:
            if player is None:
                music_start()
            elif player.poll() is not None:
                code=player.poll()
                if code != 0:
                    raise RuntimeError(
                        f"pw-play exited with error {code}; "
                        "inspect corresponding pw-play-XXX.log")
                music_stop("music_file_ended_loop_next")
                music_start()
        elif player is not None:
            raise RuntimeError(
                "Music player was active during clean/silent control")

    def hold(seconds:float,music_required:bool,
             show_jokes:bool=False)->None:
        deadline=time.monotonic()+seconds
        next_joke_at=time.monotonic() if show_jokes else float("inf")
        while time.monotonic()<deadline:
            alive(music_required)
            now=time.monotonic()
            if show_jokes and now>=next_joke_at:
                next_joke()
                next_joke_at=now+4.
            time.sleep(min(.10,max(.005,deadline-time.monotonic())))

    def phase(name:str,prompt:str,music_on:bool,
              voice_only:bool=False,seconds:float=PHASE_SECONDS)->None:
        nonlocal active
        active=name
        print(f"\n[{name.upper()}] {seconds:g} секунд. {prompt}",flush=True)
        start=timecode()
        hold(seconds,music_on,show_jokes=voice_only)
        end=timecode()
        phases.append({
            "name":name,"prompt":prompt,
            "start_since_recorder_launch_s":start,
            "end_since_recorder_launch_s":end,
            "music_requested":music_on,
            "clean_voice_reference":voice_only,
        })

    print("\nM5 CHAOS: ВНИМАНИЕ: это реальная запись с колонок, "
          "музыка может звучать громко. При дискомфорте Ctrl+C.",flush=True)
    print("Источник музыки:",music["path"],flush=True)
    print("Предыдущий pw-play --volume=0.30; сейчас 0.60 (амплитуда ×2).",flush=True)
    print("Музыка обязана содержать ЧУЖОЙ ВОКАЛ (выбранный WAV).",flush=True)
    print("Весь эксперимент ОДИН WAV; после этапа шуток заверши Ctrl+C.",flush=True)
    rec_cmd=["pw-record","--target="+mic["name"],
             "--rate=48000","--channels=1",
             "--channel-map=mono","--format=s16",str(raw)]
    try:
        recorder=spawn(rec_cmd,capture_log)
        launched=time.monotonic()
        print("\nПОДГОТОВКА: 3 секунды, руки убраны, музыка выключена",flush=True)
        hold(WARMUP_SECONDS,False)
        phase(*PHASES[0])
        music_start()
        phase(*PHASES[1])
        phase(*PHASES[2])
        phase(*PHASES[3])
        music_stop("explicit_clean_voice_stage")
        active="music_decay"
        print("\nПауза 1 секунда: стихает хвост колонки.",flush=True)
        hold(STOP_SETTLE_SECONDS,False)
        phase(*PHASES[4])
        music_start()
        active="free_stress"
        print("\n[FREE_STRESS] ГОЛОС + МУЗЫКА + КЛИКИ + КЛАВИАТУРА."
              "\nЧитай анекдоты, смейся естественно."
              "\nЗАВЕРШЕНИЕ: Ctrl+C в этом терминале.",flush=True)
        free_start=timecode()
        next_joke_at=time.monotonic()
        while True:
            alive(True)
            now=time.monotonic()
            if now>=next_joke_at:
                next_joke()
                next_joke_at=now+FREE_JOKE_SECONDS
            time.sleep(.08)
    except KeyboardInterrupt:
        stopped_by_user=True
        state=("captured_ctrl_c_at_free_stress"
               if active=="free_stress" else
               "interrupted_during_fixed_phase")
        print("\nCtrl+C: останавливаю только наши процессы и сохраняю WAV.",
              flush=True)
    except BaseException as error:
        failure=f"{type(error).__name__}: {error}"
        state="failed"
        print("\nERROR:",failure,file=sys.stderr,flush=True)
    finally:
        end=timecode()
        if active=="free_stress" and free_start is not None:
            phases.append({
                "name":"free_stress",
                "prompt":"Music vocals, near-end voice/laughter, keyboard+mouse; Ctrl+C ends",
                "start_since_recorder_launch_s":free_start,
                "end_since_recorder_launch_s":end,
                "music_requested":True,
                "clean_voice_reference":False,
            })
        if player is not None:
            try:
                music_stop("recording_ended")
            except BaseException as exc:
                failure=(failure or "")+" player_cleanup: "+repr(exc)
                state="failed"
        stop_children()

    result={
        "schema":"cuewire-M5-chaos-physical-doublestress-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "status":state,
        "failure":failure,
        "source_physical_mic_name":mic["name"],
        "speaker_physical_sink_name":sink["name"],
        "music_source":music,
        "music_stream_volume":args.volume,
        "previous_music_volume":.30,
        "music_gain_ratio_vs_previous":2.,
        "sink_volume_changed":False,
        "default_sink_or_source_changed":False,
        "microphone_self_monitoring":False,
        "new_continuous_physical_mic_capture":True,
        "music_with_vocals_required_but_not_automatically_verified":True,
        "record_command":rec_cmd,
        "recorder_returncode_after_owned_shutdown":
            recorder.returncode if recorder else None,
        "manual_ctrl_c":stopped_by_user,
        "recorded_phases":phases,
        "playback_software_start_stop_events":playback,
        "playback_events_not_sample_accurate":True,
        "jokes_source_sha256":jokes_sha,
        "jokes_used":joke_events,
        "raw_wav_file":"microphone-chaos-raw.wav",
        "notes":[
            "Each stage is timed in host monotonic wall clock, not synchronized to microphone sample clock.",
            "Only vocal music selected by operator is suitable; metadata cannot verify presence of vocals.",
            "Music may restart on file end with short gaps. All playback starts/stops are recorded.",
            "Digital music far-end and actual acoustic mic lag are not yet calibrated in this experiment.",
            "Clean voice includes only local speaker-off microphone; other external sounds might still be present.",
            "Keyboard/mouse/voice/laughter in free stage occur at operator-chosen times without button event markers.",
            "WAV is a deliberately contaminated dataset, no AEC or noise suppression is applied.",
            "All other M4/M5 historical files remain untouched. No live virtual microphone was created.",
        ],
    }
    if raw.exists():
        result["source_mic_sha256"]=digest(raw)
        try:
            result["physical_microphone_analysis"]=inspect_audio(raw,phases)
        except (ValueError,OSError) as exc:
            result["physical_microphone_validation_error"]=str(exc)
            if state=="captured_ctrl_c_at_free_stress":
                result["status"]="completed_but_invalid_mic_format_or_length"
    if music["sha256"]!=digest(Path(music["path"])):
        result["status"]="source_mutated_during_capture"
        result["failure"]="Digital music source SHA changed during recording"
    manifest=out/"manifest.json"
    with manifest.open("x",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,sort_keys=True,indent=2)
        f.write("\n")
    with (out/"SHA256SUMS").open("x",encoding="utf-8") as f:
        files=(raw,manifest,capture_log,*sorted(out.glob("pw-play-*.log")))
        for item in files:
            if item.exists():
                f.write(digest(item)+"  "+item.name+"\n")
    print("\nНовый WAV:",raw)
    print("Отчёт:",manifest)
    print("Статус:",result["status"])
    print("SHA: cd "+str(out)+" && sha256sum -c SHA256SUMS",flush=True)
    if result["status"]!="captured_ctrl_c_at_free_stress":
        raise RuntimeError("Capture incomplete; inspect manifest and terminal error")
    return result


def main()->None:
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",required=True,
                        help="Explicit alsa_input.* physical microphone source")
    parser.add_argument("--speaker",required=True,
                        help="Explicit alsa_output.* physical speaker sink")
    parser.add_argument("--music",type=Path,required=True,
                        help="Local PCM16 WAV WITH VOCALS (not music-only instrumental)")
    parser.add_argument("--volume",type=float,default=MUSIC_STREAM_VOLUME)
    parser.add_argument("--jokes",type=Path,default=JOKES)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--check-only",action="store_true",
                        help="Only verify physical devices, PCM16 music, jokes and private output; DO NOT record/play")
    args=parser.parse_args()
    if args.check_only:
        try:
            for exe in ("pw-record","pw-play","pactl"):
                checked_program(exe)
            if not args.source.startswith("alsa_input.") or not args.speaker.startswith("alsa_output."):
                raise ValueError("Choose explicit physical microphone and speaker")
            selected_mic=choose_source(args.source)
            selected_speaker=checked_speaker(args.speaker)
            song=validate_music(args.music)
            jokes,jokes_sha=load_jokes(args.jokes)
            private_output(args.out)
            if not math.isclose(args.volume,MUSIC_STREAM_VOLUME,abs_tol=1e-9):
                raise ValueError("This protocol requires 0.60 stream volume")
            print("PRECHECK OK; NOTHING RECORDED OR PLAYED.")
            print("Microphone:",selected_mic["name"])
            print("Speaker:",selected_speaker["name"])
            print("Music:",song["path"],song["duration_s"],"seconds; SHA256",song["sha256"])
            print("Stream volume:",args.volume,"= previous .30 x2 amplitude")
            print("Jokes:",len(jokes),"SHA256",jokes_sha)
            print("Fresh private output:",args.out)
            return
        except (ValueError,RuntimeError,OSError,wave.Error) as error:
            parser.exit(1,"PRECHECK ERROR: "+str(error)+"\n")
    try:
        run(args)
    except (ValueError,RuntimeError,FileNotFoundError,OSError,wave.Error) as exc:
        parser.exit(1,"ERROR: "+str(exc)+"\n")


if __name__=="__main__":
    main()
