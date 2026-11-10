//! One foreground owner of all CueWire PipeWire nodes and cue players.
use cuewire::{
    bridge_command, cue_command, mix_name, parse, parse_control,
    socket_path, source_name, voice_command, Action, CommandSpec, Config, Control, HELP
};
use std::env;
use std::fs;
use std::io::{self, BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::process::{self, Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::{Duration, Instant};

static INTERRUPTED: AtomicBool = AtomicBool::new(false);

extern "C" fn stop_signal(_: i32) {
    INTERRUPTED.store(true, Ordering::SeqCst);
}

unsafe extern "C" {
    fn signal(number: i32, callback: extern "C" fn(i32)) -> usize;
}

fn register_signals() {
    // Linux SIGINT=2, SIGTERM=15. The handler only sets an atomic flag.
    unsafe {
        signal(2, stop_signal);
        signal(15, stop_signal);
    }
}

fn spawn(spec: &CommandSpec) -> io::Result<Child> {
    Command::new(spec.program)
        .args(&spec.args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()
}

fn check_tool(name: &str) -> bool {
    Command::new(name)
        .arg("--help")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok()
}

fn dump_nodes() -> Result<String, String> {
    let output = Command::new("pw-dump")
        .output()
        .map_err(|e| format!("pw-dump failed: {e}"))?;
    if !output.status.success() {
        return Err(format!("pw-dump exit status: {}", output.status));
    }
    String::from_utf8(output.stdout).map_err(|e| format!("pw-dump invalid UTF-8: {e}"))
}

fn contains_node(dump: &str, name: &str) -> bool {
    dump.contains(&format!("\"{name}\""))
}

fn doctor() -> Result<(), String> {
    for name in ["pw-loopback", "pw-play", "pw-dump"] {
        if !check_tool(name) {
            return Err(format!("{name} is missing (install pipewire tools)"));
        }
    }
    let dump = dump_nodes()?;
    println!("PipeWire responded. Found {} bytes of graph data.", dump.len());
    println!("PipeWire node.name identifiers (pass exact --mic and --monitor):");
    println!("  pw-dump | grep -E 'node.name|media.class'");
    println!("  wpctl status");
    println!("No configuration or audio-device defaults changed.");
    Ok(())
}

fn verify_files(config: &Config) -> Result<(), String> {
    let mut keys=std::collections::BTreeSet::new();
    for (id,path) in &config.cues {
        if !keys.insert(id) {
            return Err("duplicate cue".into());
        }
        let meta=fs::metadata(path)
            .map_err(|e|format!("cue {id}: {}: {e}",path.display()))?;
        if !meta.is_file() || meta.len()==0 {
            return Err(format!("cue {id} must be a readable nonempty regular file"));
        }
        fs::File::open(path).map_err(|e|format!("cue {id}: cannot open file: {e}"))?;
    }
    Ok(())
}

fn stop_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

struct Supervisor {
    config: Config,
    pid: u32,
    bridge: Child,
    voice: Option<Child>,
    cue_players: Vec<Child>,
    cue_active: Option<u8>,
}

impl Drop for Supervisor {
    fn drop(&mut self) {
        self.stop_cue();
        if let Some(child)=self.voice.as_mut() {
            stop_child(child);
        }
        stop_child(&mut self.bridge);
        eprintln!("[cuewire] owned PipeWire processes closed");
    }
}

impl Supervisor {
    fn new(config: Config) -> Result<Self, String> {
        verify_files(&config)?;
        for cmd in ["pw-loopback", "pw-play", "pw-dump"] {
            if !check_tool(cmd) {
                return Err(format!("missing required PipeWire tool: {cmd}"));
            }
        }
        let graph=dump_nodes()?;
        if !contains_node(&graph,&config.mic) {
            return Err(format!("physical microphone not found: {}",config.mic));
        }
        if !contains_node(&graph,&config.monitor) {
            return Err(format!("monitor output not found: {}",config.monitor));
        }
        if config.mic==config.monitor {
            return Err("microphone and monitor cannot be same node".into());
        }
        let pid=process::id();
        if contains_node(&graph,&mix_name(pid)) || contains_node(&graph,&source_name(pid)) {
            return Err("CueWire graph node names already exist".into());
        }
        let bridge=spawn(&bridge_command(pid,config.latency_ms))
            .map_err(|e|format!("cannot launch pw-loopback bridge: {e}"))?;
        let mut owner=Self{
            config,pid,bridge,voice:None,cue_players:Vec::new(),cue_active:None
        };
        owner.wait_for_graph(Duration::from_secs(4))?;
        let voice=spawn(&voice_command(&owner.config,pid))
            .map_err(|e|format!("cannot launch physical microphone route: {e}"))?;
        owner.voice=Some(voice);
        // Bridge and voice are owned; they cannot outlive this foreground process.
        Ok(owner)
    }

    fn wait_for_graph(&mut self, timeout:Duration)->Result<(), String>{
        let start=Instant::now();
        while start.elapsed()<timeout {
            if INTERRUPTED.load(Ordering::SeqCst) {return Err("interrupted while starting".into());}
            if let Some(status)=self.bridge.try_wait().map_err(|e|e.to_string())? {
                return Err(format!("virtual microphone bridge exited unexpectedly: {status}"));
            }
            if let Ok(graph)=dump_nodes(){
                if contains_node(&graph,&mix_name(self.pid)) &&
                   contains_node(&graph,&source_name(self.pid)) {
                    return Ok(());
                }
            }
            thread::sleep(Duration::from_millis(60));
        }
        Err("virtual source/sink not visible in pw-dump within 4s".into())
    }

    fn stop_cue(&mut self){
        for player in &mut self.cue_players {
            stop_child(player);
        }
        self.cue_players.clear();
        self.cue_active=None;
    }

    fn play(&mut self,id:u8)->Result<String,String>{
        let file=self.config.cues.get(&id)
            .ok_or_else(||format!("cue {id} is not configured"))?
            .clone();
        if !file.exists(){return Err(format!("cue {id}: source file disappeared"));}
        self.stop_cue();
        // Exactly targeted PipeWire routes: one copy into the virtual mic
        // mix input, another copy into the user's explicit monitor sink.
        let mic=spawn(&cue_command(&mix_name(self.pid),&file))
            .map_err(|e|format!("cue into virtual mic failed: {e}"))?;
        self.cue_players.push(mic);
        let monitor=spawn(&cue_command(&self.config.monitor,&file))
            .map_err(|e|{
                self.stop_cue();
                format!("cue into monitor failed: {e}")
            })?;
        self.cue_players.push(monitor);
        self.cue_active=Some(id);
        Ok(format!("playing cue {id} into virtual mic and monitor"))
    }

    fn poll(&mut self)->Result<(), String>{
        if let Some(status)=self.bridge.try_wait().map_err(|e|e.to_string())? {
            return Err(format!("virtual microphone node exited: {status}"));
        }
        if let Some(voice)=self.voice.as_mut() {
            if let Some(status)=voice.try_wait().map_err(|e|e.to_string())? {
                return Err(format!("physical microphone stream exited: {status}"));
            }
        }
        if !self.cue_players.is_empty() {
            let mut finished=false;
            for p in &mut self.cue_players{
                if p.try_wait().map_err(|e|e.to_string())?.is_some(){finished=true;}
            }
            if finished{self.stop_cue();}
        }
        Ok(())
    }

    fn apply(&mut self,ctl:Control)->(String,bool){
        match ctl{
            Control::Play(n)=>match self.play(n){
                Ok(x)=>(format!("OK {x}"),false),
                Err(e)=>(format!("ERROR {e}"),false)
            },
            Control::Toggle(n)=>{
                if self.cue_active==Some(n){
                    self.stop_cue();
                    (format!("OK cue {n} stopped by toggle"),false)
                }else{
                    match self.play(n){
                        Ok(x)=>(format!("OK {x}"),false),
                        Err(e)=>(format!("ERROR {e}"),false)
                    }
                }
            },
            Control::Stop=>{
                self.stop_cue();
                ("OK cue stopped".into(),false)
            },
            Control::Status=>(
                format!(
                    "OK mic={} input={} virtual_source={} monitor={} active_cue={:?}",
                    self.config.mic,mix_name(self.pid),
                    source_name(self.pid),self.config.monitor,self.cue_active
                ),false
            ),
            Control::Quit=>("OK shutting down".into(),true),
            Control::Help=>(
                "OK commands: play 0..9 | toggle 0..9 | stop | status | quit".into(),false
            ),
        }
    }
}

struct SocketGuard(PathBuf);
impl Drop for SocketGuard {
    fn drop(&mut self) {
        let _=fs::remove_file(&self.0);
    }
}

struct Event {
    command:Control,
    answer:Option<mpsc::Sender<String>>
}

fn serve_socket(listener:UnixListener,tx:mpsc::Sender<Event>,running:Arc<AtomicBool>){
    while running.load(Ordering::SeqCst) && !INTERRUPTED.load(Ordering::SeqCst) {
        match listener.accept(){
            Ok((mut stream,_))=>{
                let _=stream.set_read_timeout(Some(Duration::from_millis(500)));
                let _=stream.set_write_timeout(Some(Duration::from_millis(500)));
                let mut line=String::new();
                let read=BufReader::new(&mut stream).read_line(&mut line);
                if read.is_ok() && line.len()<128 {
                    let words:Vec<String>=line.split_whitespace().map(str::to_string).collect();
                    match parse_control(&words){
                        Ok(command)=>{
                            let (sx,rx)=mpsc::channel();
                            if tx.send(Event{command,answer:Some(sx)}).is_ok(){
                                let answer=rx.recv_timeout(Duration::from_secs(2))
                                    .unwrap_or_else(|_|"ERROR request timed out".into());
                                let _=writeln!(stream,"{answer}");
                            }
                        }
                        Err(e)=>{let _=writeln!(stream,"ERROR {e}");}
                    }
                }else{
                    let _=writeln!(stream,"ERROR invalid or empty control message");
                }
            },
            Err(e) if e.kind()==io::ErrorKind::WouldBlock=>thread::sleep(Duration::from_millis(20)),
            Err(_)=>break
        }
    }
}

fn start_terminal(tx:mpsc::Sender<Event>){
    thread::spawn(move||{
        let stdin=io::stdin();
        for line in stdin.lock().lines(){
            let Ok(line)=line else {break;};
            let words:Vec<String>=line.split_whitespace().map(str::to_string).collect();
            match parse_control(&words) {
                Ok(command)=>{if tx.send(Event{command,answer:None}).is_err(){break;}},
                Err(e)=>eprintln!("[cuewire] {e}")
            }
        }
    });
}

fn run(config:Config)->Result<(),String>{
    register_signals();
    let path=socket_path()?;
    if path.exists() {
        return Err(format!("socket {} already exists; a CueWire instance or stale socket may be present. Never delete an active socket automatically.",path.display()));
    }
    let listener=UnixListener::bind(&path).map_err(|e|format!("control socket bind failed: {e}"))?;
    let _socket=SocketGuard(path.clone());
    fs::set_permissions(&path,fs::Permissions::from_mode(0o600))
        .map_err(|e|format!("cannot secure control socket: {e}"))?;
    listener.set_nonblocking(true).map_err(|e|e.to_string())?;
    let mut manager=Supervisor::new(config)?;
    let (tx,rx)=mpsc::channel::<Event>();
    let alive=Arc::new(AtomicBool::new(true));
    let worker_alive=Arc::clone(&alive);
    let server=thread::spawn(move||serve_socket(listener,tx.clone(),worker_alive));
    // Separate sender: the interactive stdin thread may remain blocked until exit,
    // but cannot keep the foreground process alive or leak an external daemon.
    let (in_tx,in_rx)=mpsc::channel::<Event>();
    start_terminal(in_tx);
    println!("[cuewire] ready: {}",source_name(manager.pid));
    println!("[cuewire] socket: {}",path.display());
    println!("[cuewire] enter play N / stop / status / quit; Ctrl+C cleans up");
    println!("[cuewire] select the named CueWire Microphone in Steam/game settings");
    let mut ended=false;
    let mut runtime_error=None;
    while !ended && !INTERRUPTED.load(Ordering::SeqCst){
        if let Err(e)=manager.poll(){runtime_error=Some(e);break;}
        for message in [rx.try_recv().ok(),in_rx.try_recv().ok()].into_iter().flatten(){
            let (response,quit)=manager.apply(message.command);
            if let Some(answer)=message.answer {let _=answer.send(response.clone());}
            println!("[cuewire] {response}");
            if quit {ended=true;}
        }
        thread::sleep(Duration::from_millis(25));
    }
    alive.store(false,Ordering::SeqCst);
    let _=server.join();
    drop(manager);
    if let Some(e)=runtime_error{Err(e)}else{Ok(())}
}

fn ctl(action:Control)->Result<(),String>{
    let path=socket_path()?;
    let mut s=UnixStream::connect(&path).map_err(|e|format!("no running CueWire instance: {e}"))?;
    s.set_read_timeout(Some(Duration::from_secs(3))).map_err(|e|e.to_string())?;
    let cmd=match action{
        Control::Play(id)=>format!("play {id}"),
        Control::Toggle(id)=>format!("toggle {id}"),
        Control::Stop=>"stop".into(),
        Control::Status=>"status".into(),
        Control::Quit=>"quit".into(),
        Control::Help=>"help".into()
    };
    writeln!(s,"{cmd}").map_err(|e|e.to_string())?;
    let mut answer=String::new();
    BufReader::new(&mut s).read_line(&mut answer).map_err(|e|e.to_string())?;
    if !answer.starts_with("OK "){return Err(answer.trim().to_string());}
    println!("{}",answer.trim());
    Ok(())
}

fn main(){
    let args:Vec<String>=env::args().skip(1).collect();
    let result=match parse(&args){
        Ok(Action::Help)=>{print!("{HELP}");Ok(())},
        Ok(Action::Doctor)=>doctor(),
        Ok(Action::Run(config))=>run(config),
        Ok(Action::Control(action))=>ctl(action),
        Err(e)=>Err(e)
    };
    if let Err(e)=result{
        eprintln!("cuewire: {e}");
        process::exit(2);
    }
}
