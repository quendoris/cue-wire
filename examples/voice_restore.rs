//! Deterministic offline speech restoration playground.
//! --input accepts mono 48 kHz PCM16 WAV. No device or PipeWire interactions.
use cuewire::mixer::{
    voice::{restore, VoiceConfig},
    voice_wav::read_pcm16_48k,
    wav::write_pcm16_mono,
};
use std::{
    env,
    f32::consts::PI,
    fs::{self, File},
    io::BufWriter,
    path::{Path,PathBuf},
    process::Command,
};

const RATE:u32 = 48_000;

struct Rng {state:u32}
impl Rng {
    fn new(seed:u32)->Self {Self {state:if seed==0 {1}else{seed}}}
    fn sample(&mut self)->f32 {
        let mut x=self.state;
        x^=x<<13; x^=x>>17; x^=x<<5;
        self.state=x;
        ((x as f64/u32::MAX as f64)*2.0-1.0) as f32
    }
}

/// Voice-like original formant synthesizer, NOT real intelligible speech.
/// Real speech requires --input with a human voice WAV.
fn synth_voicelike(seconds:u32)->Vec<f32> {
    let n = RATE as usize * seconds as usize;
    let mut output=vec![0.0_f32;n];
    let vowels = [(650.0,1200.0,2800.0),(300.0,2200.0,3000.0),
                  (430.0,1000.0,2500.0),(540.0,1700.0,2500.0)];
    for (i,x) in output.iter_mut().enumerate() {
        let t=i as f32 / RATE as f32;
        let syllable=(t/0.65) as usize;
        let intra=(t%0.65)/0.65;
        let envelope=if intra<0.07 {intra/0.07}
            else if intra>0.76 {(1.0-intra)/0.24} else {1.0};
        let silence=if syllable%7==6 || t<0.65 {0.0} else {1.0};
        let f0=112.0+8.0*(2.0*PI*0.37*t).sin();
        let (f1,f2,f3)=vowels[syllable%4];
        let mut signal=0.0_f32;
        // Fundamental and formant-coloured harmonics, all generated locally.
        for harmonic in 1..=42 {
            let f = f0 * harmonic as f32;
            let spectral=0.08 +
                0.7 * (-0.5*((f-f1)/160.0).powi(2)).exp() +
                0.5 * (-0.5*((f-f2)/230.0).powi(2)).exp() +
                0.2 * (-0.5*((f-f3)/300.0).powi(2)).exp();
            signal += spectral/harmonic as f32 * (2.0*PI*f*t).sin();
        }
        *x=0.28*signal*envelope.max(0.0)*silence;
    }
    output
}

/// Deterministic damage: white hiss, 50 Hz hum, 120 ms echo, guitar-like
/// harmonic interference and mild nonlinear saturation. Not reversible.
fn corrupt(reference:&[f32],seed:u32)->Vec<f32> {
    let mut rng=Rng::new(seed);
    let echo=(RATE as usize*120)/1000;
    reference.iter().enumerate().map(|(i,&v)| {
        let t=i as f32/RATE as f32;
        let delay=if i>=echo {reference[i-echo]} else {0.0};
        let hum=0.026*(2.0*PI*50.0*t).sin();
        let guitar=0.055*((2.0*PI*165.0*t).sin()
                         +0.35*(2.0*PI*330.0*t).sin())
            *(0.5+0.5*(2.0*PI*1.7*t).sin());
        let noise=0.065*rng.sample();
        ((v+0.3*delay+hum+guitar+noise)*2.2).tanh()*0.68
    }).collect()
}

fn save(path:&Path, audio:&[f32])->Result<(),String> {
    let f=File::create(path).map_err(|e|e.to_string())?;
    write_pcm16_mono(&mut BufWriter::new(f),RATE,audio)
        .map_err(|e|format!("WAV {}: {e:?}",path.display()))
}

fn sha256(path:&Path)->Result<String,String> {
    let result=Command::new("sha256sum").arg("--").arg(path).output()
        .map_err(|e|format!("sha256sum unavailable: {e}"))?;
    if !result.status.success() {return Err("sha256sum failed".into());}
    let s=String::from_utf8(result.stdout).map_err(|e|e.to_string())?;
    let hash=s.split_whitespace().next().ok_or("sha256sum empty response")?;
    if hash.len()!=64 || !hash.bytes().all(|c|c.is_ascii_hexdigit()) {
        return Err("sha256sum invalid hash".into());
    }
    Ok(hash.to_string())
}

fn parse() -> Result<(Option<PathBuf>,PathBuf,VoiceConfig,u32),String> {
    let mut input=None;
    let mut out=PathBuf::from("voice-preview");
    let mut cfg=VoiceConfig::default();
    let mut seconds=6_u32;
    let mut args=env::args().skip(1);
    while let Some(flag)=args.next() {
        if flag=="--help" {
            println!("cargo run --example voice_restore -- [--input dirty.wav] [--out DIR] [--seconds 2..20] [--strength 0..6] [--highpass-hz 45..250] [--target-dbfs -50..-12] [--max-boost-db 0..18] [--bed-dbfs -100..-60 | --bed-off] [--seed U32] [--profile-fraction 0.05..0.4] [--expander-db 0..36] [--spectral-floor 0.02..1.0] [--highpass-on] [--expander-on] [--leveler-on] [--no-highpass] [--no-denoise] [--no-expander] [--no-leveler]");
            println!("Without --input, an original VOICE-LIKE synthesizer fixture is generated, not intelligible speech.");
            return Err(String::new());
        }
        if flag=="--highpass-on" { cfg.enable_highpass=true; continue; }
        if flag=="--expander-on" { cfg.enable_expander=true; continue; }
        if flag=="--leveler-on" { cfg.enable_leveler=true; continue; }
        if flag=="--no-highpass" { cfg.enable_highpass=false; continue; }
        if flag=="--no-denoise" { cfg.enable_denoise=false; continue; }
        if flag=="--no-expander" { cfg.enable_expander=false; continue; }
        if flag=="--no-leveler" { cfg.enable_leveler=false; continue; }
        if flag=="--bed-off" {
            cfg.noise_bed_dbfs=None;
            continue;
        }
        let value=args.next().ok_or_else(||format!("{flag} requires a value"))?;
        match flag.as_str() {
            "--input" => input=Some(PathBuf::from(value)),
            "--out" => out=PathBuf::from(value),
            "--seconds" => seconds=value.parse().map_err(|_|"bad seconds")?,
            "--strength" => cfg.noise_strength=value.parse().map_err(|_|"bad strength")?,
            "--spectral-floor" => cfg.spectral_floor=value.parse().map_err(|_|"bad spectral floor")?,
            "--highpass-hz" => { cfg.highpass_hz=Some(value.parse().map_err(|_|"bad highpass")?); cfg.enable_highpass=true; },
            "--target-dbfs" => { cfg.target_dbfs=value.parse().map_err(|_|"bad target")?; cfg.enable_leveler=true; },
            "--max-boost-db" => { cfg.max_boost_db=value.parse().map_err(|_|"bad boost")?; cfg.enable_leveler=true; },
            "--bed-dbfs" => cfg.noise_bed_dbfs=Some(value.parse().map_err(|_|"bad bed")?),
            "--seed" => cfg.noise_seed=value.parse().map_err(|_|"bad seed")?,
            "--profile-fraction" => cfg.profile_fraction=value.parse().map_err(|_|"bad fraction")?,
            "--expander-db" => { cfg.expander_attenuation_db=value.parse().map_err(|_|"bad expander")?; cfg.enable_expander=true; },
            _ => return Err(format!("unknown flag {flag}")),
        }
    }
    if !(2..=20).contains(&seconds) {return Err("seconds must be 2..20".into());}
    Ok((input,out,cfg,seconds))
}

fn run()->Result<(),String> {
    let (file,out,cfg,seconds)=match parse(){
        Ok(args)=>args,
        Err(ref e) if e.is_empty()=>return Ok(()),
        Err(e)=>return Err(e),
    };
    let demo=file.is_none();
    let (input, reference) = if let Some(path)=file {
        (read_pcm16_48k(&path).map_err(|e|format!("{path:?}: {e:?}"))?,None)
    } else {
        let clean=synth_voicelike(seconds);
        let damaged=corrupt(&clean,cfg.noise_seed ^ 0x6F99_DA7A);
        (damaged,Some(clean))
    };
    let (output, report)=restore(&input,cfg).map_err(|e|format!("voice restoration: {e:?}"))?;
    fs::create_dir_all(&out).map_err(|e|e.to_string())?;
    let original_path=out.join("voice_before.wav");
    let processed_path=out.join("voice_after.wav");
    save(&original_path,&input)?;
    save(&processed_path,&output)?;
    let mut reference_sha="null".to_string();
    if let Some(clean)=reference {
        let path=out.join("voice_reference.wav");
        save(&path,&clean)?;
        reference_sha=format!("\"{}\"",sha256(&path)?);
    }
    let input_sha=sha256(&original_path)?;
    let output_sha=sha256(&processed_path)?;
    let manifest=format!(
        concat!(
            "{{\n",
            "  \"algorithm\": \"cuewire-voice-offline-v0.1\",\n",
            "  \"fixture\": \"{}\",\n",
            "  \"source_file_sha256\": \"{}\",\n",
            "  \"output_file_sha256\": \"{}\",\n",
            "  \"clean_reference_sha256\": {},\n",
            "  \"input_is_real_speech\": {},\n",
            "  \"sample_rate\": 48000,\n",
            "  \"frames\": {},\n",
            "  \"strength\": {},\n",
            "  \"spectral_floor\": {},\n",
            "  \"expander_threshold_above_floor_db\": {},\n",
            "  \"enable_highpass\": {},\n",
            "  \"enable_denoise\": {},\n",
            "  \"enable_expander\": {},\n",
            "  \"enable_leveler\": {},\n",
            "  \"profile_fraction\": {},\n",
            "  \"highpass_hz\": {},\n",
            "  \"highpass_auto\": {},\n",
            "  \"target_rms_dbfs\": {},\n",
            "  \"expander_attenuation_db\": {},\n",
            "  \"max_boost_db\": {},\n",
            "  \"noise_bed_dbfs\": {},\n",
            "  \"noise_seed\": {},\n",
            "  \"ceiling_dbfs\": {},\n",
            "  \"analyzed_frames\": {},\n",
            "  \"profile_frames\": {},\n",
            "  \"input_rms_dbfs\": {},\n",
            "  \"after_filter_rms_dbfs\": {},\n",
            "  \"output_rms_dbfs\": {},\n",
            "  \"output_peak_dbfs\": {},\n",
            "  \"input_quiet_rms_dbfs\": {},\n",
            "  \"post_filter_quiet_rms_dbfs\": {},\n",
            "  \"gate_active\": {},\n",
            "  \"peak_safety_gain_db\": {},\n",
            "  \"final_leveler_gain_db\": {},\n",
            "  \"input_overload_samples\": {}\n",
            "}}\n"
        ),
        if demo {"synthetic-voice-like-noise"} else {"external-wav"},
        input_sha,output_sha,reference_sha,if demo {"false"} else {"null"},input.len(),
        cfg.noise_strength,cfg.spectral_floor,cfg.expander_threshold_above_floor_db,cfg.enable_highpass,cfg.enable_denoise,cfg.enable_expander,cfg.enable_leveler,cfg.profile_fraction,report.highpass_hz,cfg.enable_highpass && cfg.highpass_hz.is_none(),
        cfg.target_dbfs,cfg.expander_attenuation_db,cfg.max_boost_db,
        cfg.noise_bed_dbfs.map(|v|v.to_string()).unwrap_or_else(||"null".into()),
        cfg.noise_seed,cfg.ceiling_dbfs,report.analyzed_frames,report.noise_profile_frames,
        report.input_rms_dbfs,report.after_filter_rms_dbfs,report.output_rms_dbfs,
        report.output_peak_dbfs,report.input_quiet_rms_dbfs,
        report.post_filter_quiet_rms_dbfs,report.gate_active,report.peak_safety_gain_db,
        report.final_leveler_gain_db,report.input_overload_samples
    );
    fs::write(out.join("report.json"),manifest).map_err(|e|e.to_string())?;
    println!("WAV generated in {}",out.display());
    println!("input {:.2} dBFS RMS -> output {:.2} dBFS RMS, sample peak {:.2} dBFS",
        report.input_rms_dbfs,report.output_rms_dbfs,report.output_peak_dbfs);
    println!("highpass {:.0} Hz, noise frames {}/{}, quiet-floor estimate {:.2} dBFS -> {:.2} dBFS",
        report.highpass_hz,report.noise_profile_frames,report.analyzed_frames,
        report.input_quiet_rms_dbfs,report.post_filter_quiet_rms_dbfs);
    println!("gate {}, peak-safety gain {:.2} dB; report.json includes SHA-256",
        report.gate_active,report.peak_safety_gain_db);
    Ok(())
}
fn main() {
    if let Err(e)=run() {eprintln!("voice_restore: {e}");std::process::exit(2);}
}
