//! Standalone, reproducible offline playground. No PipeWire or device interactions.
use cuewire::mixer::{mix_block, sources::{SimpleBeat, WhiteNoise}, wav::write_pcm16_mono, Gain};
use std::fs::{self, File};
use std::io::BufWriter;
use std::path::PathBuf;

const RATE: u32 = 48_000;
const BLOCK: usize = 256;

fn rms(samples: &[f32]) -> f64 {
    if samples.is_empty() { return 0.0; }
    let mean_square = samples.iter().map(|&x| f64::from(x) * f64::from(x)).sum::<f64>()
        / samples.len() as f64;
    mean_square.sqrt()
}

fn dbfs(value: f64) -> f64 {
    if value <= 0.0 { f64::NEG_INFINITY } else { 20.0 * value.log10() }
}

fn save(out: &PathBuf, name: &str, samples: &[f32]) -> Result<(), String> {
    let file = File::create(out.join(name)).map_err(|e| format!("{name}: {e}"))?;
    let mut writer = BufWriter::new(file);
    write_pcm16_mono(&mut writer, RATE, samples)
        .map_err(|e| format!("{name}: WAV write failed: {e:?}"))
}

fn run() -> Result<(), String> {
    let mut out = PathBuf::from("mixer-preview");
    let mut seconds = 12_u32;
    let mut noise_db = -18.0_f32;
    let mut beat_db = -6.0_f32;
    let mut seed = 0xC0FE_1234_u32;
    let mut args = std::env::args().skip(1);
    while let Some(flag) = args.next() {
        if flag == "--help" {
            println!("cargo run --example mixer_preview -- [--out DIR] [--seconds 1..120] [--noise-db DB] [--beat-db DB] [--seed U32]");
            return Ok(());
        }
        let value = args.next().ok_or_else(|| format!("{flag} requires a value"))?;
        match flag.as_str() {
            "--out" => out = PathBuf::from(value),
            "--seconds" => seconds = value.parse().map_err(|_|"invalid seconds")?,
            "--noise-db" => noise_db = value.parse().map_err(|_|"invalid noise-db")?,
            "--beat-db" => beat_db = value.parse().map_err(|_|"invalid beat-db")?,
            "--seed" => seed = value.parse().map_err(|_|"invalid seed")?,
            _ => return Err(format!("unknown flag: {flag}")),
        }
    }
    if !(1..=120).contains(&seconds) {return Err("seconds must be 1..120".into());}
    let noise_gain = Gain::from_db(noise_db).map_err(|e| format!("noise gain: {e:?}"))?;
    let beat_gain = Gain::from_db(beat_db).map_err(|e| format!("beat gain: {e:?}"))?;
    let length = RATE as usize * seconds as usize;
    let mut noise = WhiteNoise::new(seed);
    let mut beat = SimpleBeat::new(RATE, 120).map_err(|e| format!("{e:?}"))?;
    let mut noise_raw = Vec::with_capacity(length);
    let mut beat_raw = Vec::with_capacity(length);
    for _ in 0..length {
        noise_raw.push(noise.next_sample());
        beat_raw.push(beat.next_sample());
    }

    let mut mixed = vec![0.0_f32; length];
    let mut peak = 0.0_f32;
    let mut overloads = 0_usize;
    for start in (0..length).step_by(BLOCK) {
        let end = (start + BLOCK).min(length);
        let stats = mix_block(
            &noise_raw[start..end], &beat_raw[start..end], &mut mixed[start..end],
            noise_gain, beat_gain
        ).map_err(|e| format!("mix: {e:?}"))?;
        peak = peak.max(stats.peak_abs);
        overloads += stats.overloaded_samples;
    }
    if overloads > 0 {
        return Err(format!("{overloads} samples overloaded (peak={peak:.4}); lower gains"));
    }
    // Stems already include their gain so that noise.wav + beat.wav = mix.wav
    // within floating point / PCM16 quantization tolerance.
    let noise_stem: Vec<f32> = noise_raw.iter().map(|&x| x * noise_gain.value()).collect();
    let beat_stem: Vec<f32> = beat_raw.iter().map(|&x| x * beat_gain.value()).collect();
    fs::create_dir_all(&out).map_err(|e| format!("cannot create {}: {e}", out.display()))?;
    save(&out, "noise.wav", &noise_stem)?;
    save(&out, "beat.wav", &beat_stem)?;
    save(&out, "mix.wav", &mixed)?;
    println!("Saved {}/{{noise,beat,mix}}.wav at {RATE} Hz, PCM16 mono, {seconds} s", out.display());
    println!("noise RMS={:.2} dBFS, beat RMS={:.2} dBFS, mix RMS={:.2} dBFS, peak={:.2} dBFS, overloads={overloads}",
        dbfs(rms(&noise_stem)), dbfs(rms(&beat_stem)), dbfs(rms(&mixed)), dbfs(f64::from(peak)));
    Ok(())
}

fn main() {
    if let Err(message) = run() {
        eprintln!("mixer_preview: {message}");
        std::process::exit(2);
    }
}
