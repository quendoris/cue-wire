//! One-track pseudo-music, raw vs bounded RMS-leveler preview.
use cuewire::mixer::{
    dynamics::{LevelerConfig, RmsLeveler},
    music::{PseudoMusic, SECTION_LEVELS_DB},
    wav::write_pcm16_mono,
};
use std::{env, fs::{self, File}, io::{BufWriter, Write}, path::{Path, PathBuf}};

fn dbfs(rms: f64) -> f64 {
    if rms <= 0.0 { f64::NEG_INFINITY } else { 20.0 * rms.log10() }
}

fn segment_db(samples: &[f32]) -> f64 {
    if samples.is_empty() { return f64::NEG_INFINITY; }
    let squares: f64 = samples.iter().map(|&v| f64::from(v).powi(2)).sum();
    dbfs((squares / samples.len() as f64).sqrt())
}

fn save(out: &Path, name: &str, samples: &[f32], rate: u32) -> Result<(), String> {
    let f = File::create(out.join(name)).map_err(|e|format!("{name}: {e}"))?;
    write_pcm16_mono(&mut BufWriter::new(f), rate, samples)
        .map_err(|e| format!("{name}: {e:?}"))
}

fn run() -> Result<(), String> {
    let mut out = PathBuf::from("dynamics-preview");
    let mut seconds_per_section = 4_u32;
    let mut cfg = LevelerConfig::default();
    let mut args = env::args().skip(1);
    while let Some(flag) = args.next() {
        if flag == "--help" {
            println!("cargo run --example dynamics_preview -- [--out DIR] [--section-seconds 2..20] [--target-dbfs -26] [--range-db 3] [--attack-ms 45] [--release-ms 450] [--max-boost-db 9]");
            println!("--range-db is HALF-WIDTH: target -26 / range 3 -> -29..-23 dBFS RMS.");
            return Ok(());
        }
        let value = args.next().ok_or_else(||format!("{flag} requires a value"))?;
        match flag.as_str() {
            "--out" => out = PathBuf::from(value),
            "--section-seconds" => seconds_per_section = value.parse().map_err(|_|"invalid section seconds")?,
            "--target-dbfs" => cfg.target_dbfs = value.parse().map_err(|_|"invalid target")?,
            "--range-db" => cfg.tolerance_db = value.parse().map_err(|_|"invalid range")?,
            "--attack-ms" => cfg.attack_ms = value.parse().map_err(|_|"invalid attack")?,
            "--release-ms" => cfg.release_ms = value.parse().map_err(|_|"invalid release")?,
            "--max-boost-db" => cfg.max_boost_db = value.parse().map_err(|_|"invalid max boost")?,
            _ => return Err(format!("unknown argument {flag}")),
        }
    }
    if !(2..=20).contains(&seconds_per_section) {
        return Err("section seconds must be 2..20".into());
    }
    let rate = cfg.sample_rate;
    let mut source = PseudoMusic::new(rate, seconds_per_section).map_err(|e|format!("{e:?}"))?;
    let mut leveler = RmsLeveler::new(cfg).map_err(|e|format!("leveler config: {e:?}"))?;
    let frames = rate as usize * seconds_per_section as usize * 4;
    let dry: Vec<f32> = (0..frames).map(|_|source.next_sample()).collect();
    let mut leveled = vec![0.0; frames];
    let mut peak = 0.0_f32;
    let mut overloaded = 0_usize;
    for (src, dst) in dry.chunks(256).zip(leveled.chunks_mut(256)) {
        let stats = leveler.process_block(src, dst).map_err(|e|format!("{e:?}"))?;
        peak = peak.max(stats.peak_abs);
        overloaded += stats.overloaded_samples;
    }
    if overloaded > 0 {
        return Err(format!("{overloaded} samples exceed PCM full-scale (peak={peak:.4}); lower --target-dbfs or --max-boost-db"));
    }
    fs::create_dir_all(&out).map_err(|e|format!("{}: {e}",out.display()))?;
    save(&out, "music_raw.wav", &dry, rate)?;
    save(&out, "music_leveled.wav", &leveled, rate)?;

    let f = File::create(out.join("levels.csv")).map_err(|e|e.to_string())?;
    let mut csv = BufWriter::new(f);
    writeln!(csv, "second,section,section_gain_db,raw_rms_dbfs,leveled_rms_dbfs")
        .map_err(|e|e.to_string())?;
    let chunk = rate as usize;
    for (i, (before, after)) in dry.chunks(chunk).zip(leveled.chunks(chunk)).enumerate() {
        let section = i / seconds_per_section as usize;
        writeln!(csv, "{},{},{},{:.3},{:.3}",
                 i,section + 1,SECTION_LEVELS_DB[section],
                 segment_db(before),segment_db(after)).map_err(|e|e.to_string())?;
    }
    println!("Saved: {}/music_raw.wav, music_leveled.wav, levels.csv", out.display());
    println!("48 kHz mono PCM16, {} s total; detector target band: {:.1}..{:.1} dBFS",
             seconds_per_section * 4, cfg.target_dbfs - cfg.tolerance_db, cfg.target_dbfs + cfg.tolerance_db);
    for (i, &db) in SECTION_LEVELS_DB.iter().enumerate() {
        let start = i * seconds_per_section as usize * rate as usize;
        let end = start + seconds_per_section as usize * rate as usize;
        println!("section {} (source gain {:+.1} dB): raw {:.2} dBFS -> leveled {:.2} dBFS",
                 i+1, db, segment_db(&dry[start..end]), segment_db(&leveled[start..end]));
    }
    println!("peak {:.2} dBFS; overloads: {overloaded}; final gain: {:+.2} dB",
             dbfs(f64::from(peak)), leveler.gain_db());
    Ok(())
}

fn main() {
    if let Err(e) = run() { eprintln!("dynamics_preview: {e}"); std::process::exit(2); }
}
