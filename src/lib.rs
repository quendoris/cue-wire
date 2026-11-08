//! Pure configuration, command generation and request validation.
//! All effects are in main.rs; no shell or default-device changes.
pub mod mixer;

use std::collections::BTreeMap;
use std::path::{Path,PathBuf};

pub const HELP: &str = r#"CueWire 0.1 — foreground PipeWire virtual microphone

Usage:
  cuewire doctor
  cuewire run --mic NODE --monitor SINK --cue N=FILE [--cue N=FILE ...] [--latency-ms 25]
  cuewire ctl play N | stop | status | quit

Terminal commands: play N, stop, status, quit. Ctrl+C / SIGTERM cleans owned nodes.
Use exact PipeWire node.name strings. Audio is not captured from system playback.
v0.1: raw microphone plus one active sound cue, with cue-only monitoring.
NumPad 0..9 can trigger cues via optional Hyprland bindings; KP_Decimal stops cues only.
Hotkeys are opt-in; CueWire never alters Hyprland configuration automatically.
Voice DSP and global Hyprland key bindings are NOT implemented in this slice.
"#;

#[derive(Debug,Clone,PartialEq,Eq)]
pub struct Config {
    pub mic: String,
    pub monitor: String,
    pub cues: BTreeMap<u8,PathBuf>,
    pub latency_ms:u32,
}

#[derive(Debug,Clone,PartialEq,Eq)]
pub enum Action { Help, Doctor, Run(Config), Control(Control) }

#[derive(Debug,Clone,PartialEq,Eq)]
pub enum Control { Play(u8), Stop, Status, Quit, Help }

#[derive(Debug,Clone,PartialEq,Eq)]
pub struct CommandSpec { pub program: &'static str, pub args:Vec<String> }

impl CommandSpec {
    pub fn new(program:&'static str,args:Vec<String>)->Self { Self{program,args} }
}

pub fn valid_node(node:&str)->bool {
    !node.is_empty() && node.len()<=160 &&
      node.chars().all(|c|c.is_ascii_alphanumeric()||"._-".contains(c))
}

pub fn parse_control(parts:&[String])->Result<Control,String> {
    match parts {
        [s] if s=="stop"=>Ok(Control::Stop),
        [s] if s=="status"=>Ok(Control::Status),
        [s] if s=="quit"=>Ok(Control::Quit),
        [s] if s=="help"=>Ok(Control::Help),
        [s,n] if s=="play"=>{
            let id=n.parse::<u8>().map_err(|_|"play needs integer 0..9".to_string())?;
            if id>9 {return Err("play needs integer 0..9".into());}
            Ok(Control::Play(id))
        }
        _=>Err("expected: play N, stop, status, quit, or help".into())
    }
}

pub fn parse(args:&[String])->Result<Action,String>{
    let (cmd,tail)=args.split_first().ok_or("missing command; see --help")?;
    match cmd.as_str(){
        "-h"|"--help"|"help" if tail.is_empty()=>Ok(Action::Help),
        "doctor" if tail.is_empty()=>Ok(Action::Doctor),
        "ctl"=>Ok(Action::Control(parse_control(tail)?)),
        "run"=>{
            let(mut mic,mut monitor)=(None,None);
            let mut cues=BTreeMap::new();
            let mut latency_ms=25;
            let mut i=0;
            while i<tail.len(){
                let flag=tail[i].as_str();
                if !["--mic","--monitor","--cue","--latency-ms"].contains(&flag){
                    return Err(format!("unknown option {flag}"));
                }
                let val=tail.get(i+1).ok_or_else(||format!("{flag} requires value"))?;
                match flag{
                    "--mic"=>{if mic.replace(val.clone()).is_some(){return Err("duplicate --mic".into());}}
                    "--monitor"=>{if monitor.replace(val.clone()).is_some(){return Err("duplicate --monitor".into());}}
                    "--cue"=>{
                        let(n,path)=val.split_once('=').ok_or("cue needs N=FILE")?;
                        let id=n.parse::<u8>().map_err(|_|"cue id needs 0..9")?;
                        if id>9||path.is_empty(){
                            return Err("cue needs nonempty N=FILE with N in 0..9".into());
                        }
                        if cues.insert(id,PathBuf::from(path)).is_some(){
                            return Err(format!("duplicate cue {id}"));
                        }
                    }
                    "--latency-ms"=>{
                        latency_ms=val.parse::<u32>().map_err(|_|"invalid latency-ms")?;
                        if !(5..=150).contains(&latency_ms){
                            return Err("latency-ms must be 5..150".into());
                        }
                    }
                    _=>unreachable!()
                }
                i+=2;
            }
            let mic:String=mic.ok_or("run requires --mic exact node.name")?;
            let monitor:String=monitor.ok_or("run requires --monitor exact node.name")?;
            if !valid_node(&mic)||!valid_node(&monitor){
                return Err("device node names allow ASCII alphanumerics, dot, dash and underscore only".into());
            }
            if cues.is_empty(){return Err("run needs at least one --cue N=FILE".into());}
            Ok(Action::Run(Config{mic,monitor,cues,latency_ms}))
        }
        _=>Err("unknown command; see --help".into())
    }
}

pub fn socket_path()->Result<PathBuf,String>{
    let dir=std::env::var_os("XDG_RUNTIME_DIR").ok_or("XDG_RUNTIME_DIR is required")?;
    let dir=PathBuf::from(dir);
    if !dir.is_absolute()||!dir.is_dir(){
        return Err("XDG_RUNTIME_DIR must be an existing absolute directory".into());
    }
    Ok(dir.join("cuewire-v0.1.sock"))
}

pub fn mix_name(pid:u32)->String{format!("cuewire.mix.{pid}")}
pub fn source_name(pid:u32)->String{format!("cuewire.microphone.{pid}")}

pub fn bridge_command(pid:u32,latency_ms:u32)->CommandSpec{
    let sink_props=format!(
      r#"{{"node.name":"{}","node.description":"CueWire Input","media.class":"Audio/Sink","audio.position":["MONO"],"node.autoconnect":false}}"#,
      mix_name(pid)
    );
    let source_props=format!(
      r#"{{"node.name":"{}","node.description":"CueWire Microphone","media.class":"Audio/Source","audio.position":["MONO"],"node.autoconnect":false}}"#,
      source_name(pid)
    );
    CommandSpec::new("pw-loopback",vec![
        "-n".into(),format!("cuewire.bridge.{pid}"),
        "-c".into(),"1".into(),"-m".into(),"[ MONO ]".into(),
        "-l".into(),latency_ms.to_string(),
        "-i".into(),sink_props,"-o".into(),source_props
    ])
}

pub fn voice_command(c:&Config,pid:u32)->CommandSpec{
    let capture=format!(
      r#"{{"node.name":"cuewire.capture.{pid}","target.object":"{}","node.dont-reconnect":true,"node.passive":true,"stream.dont-remix":true}}"#,
      c.mic
    );
    let playback=format!(
      r#"{{"node.name":"cuewire.voice.{pid}","target.object":"{}","node.dont-reconnect":true,"stream.dont-remix":true}}"#,
      mix_name(pid)
    );
    CommandSpec::new("pw-loopback",vec![
        "-n".into(),format!("cuewire.voice-loop.{pid}"),
        "-c".into(),"1".into(),"-m".into(),"[ MONO ]".into(),
        "-l".into(),c.latency_ms.to_string(),
        "-C".into(),c.mic.clone(),"-P".into(),mix_name(pid),
        "-i".into(),capture,"-o".into(),playback
    ])
}

pub fn cue_command(target:&str,file:&Path)->CommandSpec{
    CommandSpec::new("pw-play",vec![format!("--target={target}"),
                                  file.to_string_lossy().into_owned()])
}

#[cfg(test)]
mod tests{
    use super::*;
    fn a(items:&[&str])->Vec<String>{items.iter().map(|s|s.to_string()).collect()}
    fn config()->Config{
        Config{
            mic:"alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback".into(),
            monitor:"bluez_output.28_7C_0F_6D_57_51.1".into(),
            cues:BTreeMap::from([(1,PathBuf::from("/tmp/one.wav"))]),
            latency_ms:25
        }
    }

    #[test] fn parses_user_config(){
        let Action::Run(c)=parse(&a(&[
            "run","--mic","alsa_input.usb-mic","--monitor","bluez_output.headset.1",
            "--cue","0=/tmp/z.wav","--cue","1=/tmp/a.wav","--cue","9=/tmp/b.wav","--latency-ms","20"
        ])).unwrap() else {panic!("expected run")};
        assert_eq!(c.latency_ms,20);
        assert_eq!(c.cues.len(),3);
        assert!(c.cues.contains_key(&0));
    }
    #[test] fn requires_cues_and_rejects_unknown_args(){
        assert!(parse(&a(&["run","--mic","a","--monitor","b"])).is_err());
        assert!(parse(&a(&["run","--mic","a","--monitor","b","--cue","1=x","--bad","z"])).is_err());
    }
    #[test] fn rejects_duplicate_control_params(){
        assert!(parse(&a(&["run","--mic","a","--mic","b","--monitor","c","--cue","1=x"])).is_err());
        assert!(parse(&a(&["run","--mic","a","--monitor","b","--cue","1=x","--cue","1=y"])).is_err());
    }
    #[test] fn node_values_cannot_inject_shell_or_json(){
        for bad in ["x;rm","a b","","../foo","x\nb","x,$(date)","x'"]{
            assert!(!valid_node(bad),"{bad:?}");
        }
        assert!(valid_node(&config().mic));
        assert!(valid_node(&config().monitor));
    }
    #[test] fn bridge_exposes_explicit_virtual_sink_and_source(){
        let cmd=bridge_command(123,25);
        assert_eq!(cmd.program,"pw-loopback");
        assert!(cmd.args.iter().any(|x|x.contains("Audio/Sink")));
        assert!(cmd.args.iter().any(|x|x.contains("Audio/Source")));
        assert!(!cmd.args.iter().any(|x|x.contains("set-default")));
    }
    #[test] fn voice_route_is_pinned_and_no_monitor_capture(){
        let cmd=voice_command(&config(),123);
        assert!(cmd.args.contains(&config().mic));
        assert!(cmd.args.contains(&mix_name(123)));
        assert!(cmd.args.iter().any(|x|x.contains("node.dont-reconnect")));
        assert!(!cmd.args.iter().any(|x|x.contains("stream.capture.sink")));
    }
    #[test] fn cue_file_with_spaces_passes_as_single_argument(){
        let x=cue_command("cuewire.mix.123",Path::new("/tmp/sound sample.wav"));
        assert_eq!(x.args,vec!["--target=cuewire.mix.123","/tmp/sound sample.wav"]);
    }
    #[test] fn control_protocol_is_bounded(){
        assert_eq!(parse_control(&a(&["play","3"])),Ok(Control::Play(3)));
        assert_eq!(parse_control(&a(&["play","0"])),Ok(Control::Play(0)));
        assert!(parse_control(&a(&["play","10"])).is_err());
        assert!(parse_control(&a(&["quit","extra"])).is_err());
        assert_eq!(parse_control(&a(&["stop"])),Ok(Control::Stop));
    }
    #[test] fn rejects_out_of_range_latency(){
        for bad in ["0","4","151","10000","NaN"]{
            assert!(parse(&a(&["run","--mic","a","--monitor","b","--cue","1=x","--latency-ms",bad])).is_err());
        }
    }
    #[test] fn default_commands_never_touch_hyprland_or_system_services(){
        let c=bridge_command(4,25);
        assert!(!c.args.iter().any(|x|x.contains("hyprctl")||x.contains("systemctl")||x.contains("pactl")));
    }
}
