//! Adaptive, deterministic, OFFLINE speech-oriented restoration prototype.
//! Not neural source separation. Cannot recover clipped or masked phonemes.
//! Core uses: 2-pole HPF, STFT Wiener-style suppression, pause expander,
//! bounded RMS leveler, sample-peak safety scale, optional tiny white-noise bed.
use super::dynamics::{LevelerConfig, RmsLeveler};
use super::fft::{transform, Complex};
use std::f64::consts::PI;

const FFT: usize = 2048;
const HOP: usize = 512;
const BINS: usize = FFT / 2 + 1;

#[derive(Clone, Copy, Debug)]
pub struct VoiceConfig {
    /// None = select 75 or 105 Hz from the input's low-frequency spectrum.
    pub highpass_hz: Option<f32>,
    /// Noise PSD multiplier in power-spectrum subtraction.
    pub noise_strength: f32,
    /// Gain floor prevents full subtraction / musical-noise holes.
    pub spectral_floor: f32,
    /// Fraction of quietest nonzero STFT frames for deterministic profile.
    pub profile_fraction: f32,
    pub expander_attenuation_db: f32,
    pub expander_threshold_above_floor_db: f32,
    pub target_dbfs: f32,
    pub max_boost_db: f32,
    /// Some(value) adds a constant extremely low level white-noise floor.
    /// None produces no synthetic noise. The bed is NOT denoising.
    pub noise_bed_dbfs: Option<f32>,
    pub noise_seed: u32,
    pub ceiling_dbfs: f32,
}

impl Default for VoiceConfig {
    fn default() -> Self {
        Self {
            highpass_hz: None,
            noise_strength: 1.7,
            spectral_floor: 0.12,
            profile_fraction: 0.18,
            expander_attenuation_db: 14.0,
            expander_threshold_above_floor_db: 7.0,
            target_dbfs: -26.0,
            max_boost_db: 7.0,
            noise_bed_dbfs: Some(-78.0),
            noise_seed: 0xC0E5_2026,
            ceiling_dbfs: -3.0,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum VoiceError { InvalidConfig, EmptyInput, NonFiniteInput(usize), Internal }

#[derive(Clone, Debug)]
pub struct VoiceReport {
    pub sample_rate: u32,
    pub analyzed_frames: usize,
    pub noise_profile_frames: usize,
    pub input_rms_dbfs: f32,
    pub after_filter_rms_dbfs: f32,
    pub output_rms_dbfs: f32,
    pub output_peak_dbfs: f32,
    pub highpass_hz: f32,
    pub input_quiet_rms_dbfs: f32,
    pub post_filter_quiet_rms_dbfs: f32,
    pub gate_active: bool,
    pub peak_safety_gain_db: f32,
    pub final_leveler_gain_db: f32,
    pub input_overload_samples: usize,
}

fn linear_to_db(value: f64) -> f32 {
    if value <= 0.0 { -150.0 }
    else { (20.0 * value.log10()) as f32 }
}
fn rms(input: &[f32]) -> f64 {
    if input.is_empty() { return 0.0; }
    (input.iter().map(|&x| f64::from(x) * f64::from(x))
        .sum::<f64>() / input.len() as f64).sqrt()
}
fn low_percentile_rms(input: &[f32], window: usize) -> f32 {
    let mut blocks: Vec<f64> = input.chunks(window.max(1))
        .filter(|v| v.len() >= window.max(1) / 2)
        .map(rms)
        .filter(|&v| v > 1e-10)
        .collect();
    if blocks.is_empty() { return -150.0; }
    blocks.sort_by(|a,b| a.total_cmp(b));
    linear_to_db(blocks[blocks.len()/5])
}
fn validate(cfg: VoiceConfig) -> Result<(), VoiceError> {
    if cfg.highpass_hz.is_some_and(|v| !v.is_finite() || !(45.0..=250.0).contains(&v))
        || !cfg.noise_strength.is_finite()
        || !(0.0..=6.0).contains(&cfg.noise_strength)
        || !cfg.spectral_floor.is_finite()
        || !(0.02..=1.0).contains(&cfg.spectral_floor)
        || !cfg.profile_fraction.is_finite()
        || !(0.05..=0.4).contains(&cfg.profile_fraction)
        || !cfg.expander_attenuation_db.is_finite()
        || !(0.0..=36.0).contains(&cfg.expander_attenuation_db)
        || !cfg.expander_threshold_above_floor_db.is_finite()
        || !(2.0..=20.0).contains(&cfg.expander_threshold_above_floor_db)
        || !cfg.target_dbfs.is_finite()
        || !(-50.0..=-12.0).contains(&cfg.target_dbfs)
        || !cfg.max_boost_db.is_finite()
        || !(0.0..=18.0).contains(&cfg.max_boost_db)
        || cfg.noise_bed_dbfs.is_some_and(|v| !v.is_finite() || !(-100.0..=-60.0).contains(&v))
        || !cfg.ceiling_dbfs.is_finite()
        || !(-12.0..=-1.0).contains(&cfg.ceiling_dbfs)
    {
        return Err(VoiceError::InvalidConfig);
    }
    Ok(())
}

/// 2x simple 1-pole high-pass; reset for each independent file.
fn highpass(input: &[f32], rate: u32, hz: f32) -> Vec<f32> {
    let rc = 1.0_f64 / (2.0 * PI * f64::from(hz));
    let dt = 1.0 / f64::from(rate);
    let coefficient = rc / (rc + dt);
    let mut stage = vec![0.0_f32; input.len()];
    let mut before = 0.0_f64;
    let mut after = 0.0_f64;
    for (&x, out) in input.iter().zip(stage.iter_mut()) {
        let x = f64::from(x);
        let y = coefficient * (after + x - before);
        *out = y as f32;
        before = x;
        after = y;
    }
    before = 0.0;
    after = 0.0;
    for out in &mut stage {
        let x = f64::from(*out);
        let y = coefficient * (after + x - before);
        *out = y as f32;
        before = x;
        after = y;
    }
    stage
}

/// Compare energy in 0..90 Hz and 90..500 Hz to pick a conservative HPF.
/// Heuristic is observable in the report; the user can always override it.
fn choose_highpass(input: &[f32], rate: u32) -> f32 {
    let weak = highpass(input, rate, 45.0);
    let stronger = highpass(input, rate, 110.0);
    let e_weak = rms(&weak).powi(2);
    let e_stronger = rms(&stronger).powi(2);
    if e_weak > 0.0 && e_stronger / e_weak < 0.7 { 105.0 } else { 75.0 }
}

fn stft_denoise(
    input: &[f32], strength: f32, floor: f32, fraction: f32
) -> (Vec<f32>, usize, usize) {
    let mut padded = vec![0.0_f32; input.len() + 2 * FFT];
    padded[FFT..FFT+input.len()].copy_from_slice(input);
    let window: Vec<f64> = (0..FFT)
        .map(|i| (PI * (i as f64 + 0.5) / FFT as f64).sin()).collect();
    let mut complex = vec![Complex::default(); FFT];
    let mut frames = Vec::new();
    for pos in (0..=padded.len() - FFT).step_by(HOP) {
        let center = pos + FFT / 2;
        if center < FFT || center >= FFT + input.len() { continue; }
        let frame = &padded[pos..pos + FFT];
        let amplitude = rms(frame);
        if amplitude > 1e-10 { frames.push((amplitude, pos)); }
    }
    frames.sort_by(|a,b| a.0.total_cmp(&b.0));
    let selected = ((frames.len() as f32 * fraction).ceil() as usize).max(1).min(frames.len());
    let mut noise_psd = vec![0.0_f64; BINS];
    if !frames.is_empty() {
        for &(_,pos) in frames.iter().take(selected) {
            for i in 0..FFT {
                complex[i] = Complex { re: f64::from(padded[pos+i]) * window[i], im: 0.0 };
            }
            transform(&mut complex,false);
            for (bin,value) in noise_psd.iter_mut().zip(complex.iter()) {
                *bin += value.power() / selected as f64;
            }
        }
    }
    let mut overlap = vec![0.0_f64; padded.len()];
    let mut norm = vec![0.0_f64; padded.len()];
    let mut previous_gains = vec![1.0_f64; BINS];
    let mut target_gains = vec![1.0_f64; BINS];

    for pos in (0..=padded.len() - FFT).step_by(HOP) {
        for i in 0..FFT {
            complex[i] = Complex { re: f64::from(padded[pos+i]) * window[i], im: 0.0 };
        }
        transform(&mut complex, false);
        for k in 0..BINS {
            let power = complex[k].power();
            // Smooth spectral subtraction / Wiener-like power mask.
            let residual = (power - f64::from(strength) * noise_psd[k]).max(0.0);
            let ratio = residual / (power + 1e-20);
            target_gains[k] = ratio.sqrt().max(f64::from(floor));
        }
        // Neighbour averaging avoids narrow, chattering frequency holes.
        for k in 0..BINS {
            let lower = if k>0 { target_gains[k-1] } else { target_gains[k] };
            let upper = if k+1<BINS { target_gains[k+1] } else { target_gains[k] };
            let smooth = 0.25 * lower + 0.5 * target_gains[k] + 0.25 * upper;
            let gain = 0.55 * previous_gains[k] + 0.45 * smooth;
            previous_gains[k] = gain;
            complex[k].re *= gain;
            complex[k].im *= gain;
            if k>0 && k<FFT/2 {
                complex[FFT-k].re *= gain;
                complex[FFT-k].im *= gain;
            }
        }
        transform(&mut complex, true);
        for i in 0..FFT {
            overlap[pos+i] += complex[i].re * window[i];
            norm[pos+i] += window[i] * window[i];
        }
    }
    let mut out = vec![0.0_f32; input.len()];
    for (i,sample) in out.iter_mut().enumerate() {
        let p = i + FFT;
        *sample = if norm[p] > 1e-12 { (overlap[p] / norm[p]) as f32 } else { 0.0 };
    }
    (out, frames.len(), if frames.is_empty() { 0 } else { selected })
}

/// Gate uses low-percentile output RMS only if the recording contains
/// a detectable quiet-versus-loud contrast; no guessed voice transcript.
fn expander(input: &mut [f32], cfg: VoiceConfig) -> (f32,bool) {
    let rate = 48_000_usize;
    let frame = rate / 50;
    let db: Vec<f32> = input.chunks(frame).map(|chunk| linear_to_db(rms(chunk))).collect();
    let mut ordered = db.clone();
    ordered.sort_by(|a,b|a.total_cmp(b));
    let noise_db = ordered[ordered.len()/5];
    let middle_db = ordered[ordered.len()/2];
    let has_pauses = middle_db - noise_db >= 6.0;
    if !has_pauses { return (noise_db,false); }
    let threshold = noise_db + cfg.expander_threshold_above_floor_db;
    let low_gain = 10_f32.powf(-cfg.expander_attenuation_db / 20.0);
    let up = 1.0 - (-1.0_f32 / (0.008 * 48_000.0)).exp();
    let down = 1.0 - (-1.0_f32 / (0.16 * 48_000.0)).exp();
    let mut gain = 1.0_f32;
    for (i,sample) in input.iter_mut().enumerate() {
        // One analysis frame of lookahead is safe in offline mode.
        let ahead = ((i/frame)+1).min(db.len()-1);
        let active = db[ahead] >= threshold;
        let desired = if active { 1.0 } else { low_gain };
        let speed = if desired > gain { up } else { down };
        gain += speed * (desired - gain);
        *sample *= gain;
    }
    (noise_db,true)
}

struct NoiseRng { state:u32 }
impl NoiseRng {
    fn new(seed:u32)->Self { Self {state: if seed==0 {1} else {seed}} }
    fn next(&mut self)->f32 {
        let mut x=self.state;
        x^=x<<13; x^=x>>17; x^=x<<5;
        self.state=x;
        ((x as f64 / u32::MAX as f64)*2.0-1.0) as f32
    }
}

/// Whole-file offline conversion. No side effects; output is a new vector.
/// Both source signal and parameters are explicitly supplied by the caller.
pub fn restore(
    input: &[f32], cfg: VoiceConfig
) -> Result<(Vec<f32>, VoiceReport),VoiceError> {
    validate(cfg)?;
    if input.is_empty() {return Err(VoiceError::EmptyInput);}
    for (i,&s) in input.iter().enumerate() {
        if !s.is_finite() {return Err(VoiceError::NonFiniteInput(i));}
    }
    let input_overload_samples = input.iter().filter(|&&s|s.abs()>1.0).count();
    let input_rms_dbfs = linear_to_db(rms(input));
    let input_quiet_rms_dbfs = low_percentile_rms(input, 960);
    let highpass_hz = cfg.highpass_hz.unwrap_or_else(||choose_highpass(input,48_000));
    let first = highpass(input,48_000,highpass_hz);
    let (mut result, analyzed_frames, noise_profile_frames) =
        stft_denoise(&first, cfg.noise_strength, cfg.spectral_floor, cfg.profile_fraction);
    let after_filter_rms_dbfs = linear_to_db(rms(&result));
    let (post_filter_quiet_rms_dbfs, gate_active) = expander(&mut result,cfg);

    let mut leveled = vec![0.0_f32; result.len()];
    let leveler_cfg = LevelerConfig {
        target_dbfs: cfg.target_dbfs,
        max_boost_db: cfg.max_boost_db,
        ..LevelerConfig::default()
    };
    let mut leveler = RmsLeveler::new(leveler_cfg).map_err(|_|VoiceError::InvalidConfig)?;
    for (src,dst) in result.chunks(512).zip(leveled.chunks_mut(512)) {
        leveler.process_block(src,dst).map_err(|_|VoiceError::Internal)?;
    }
    let final_leveler_gain_db = leveler.gain_db();
    let ceiling = 10_f32.powf(cfg.ceiling_dbfs / 20.0);
    let peak = leveled.iter().fold(0.0_f32,|p,&x|p.max(x.abs()));
    let safety = if peak > ceiling { ceiling / peak } else { 1.0_f32 };
    for sample in &mut leveled { *sample *= safety; }
    if let Some(dbfs) = cfg.noise_bed_dbfs {
        let mut rng = NoiseRng::new(cfg.noise_seed);
        let amplitude = 10_f32.powf(dbfs / 20.0) * 3.0_f32.sqrt();
        for sample in &mut leveled {
            *sample += amplitude * rng.next();
        }
        // Keep the bed below the declared sample-peak ceiling too.
        let new_peak = leveled.iter().fold(0.0_f32,|p,&x|p.max(x.abs()));
        if new_peak > ceiling {
            let adjust = ceiling / new_peak;
            for s in &mut leveled { *s *= adjust; }
        }
    }
    let output_peak_dbfs = linear_to_db(
        f64::from(leveled.iter().fold(0.0_f32,|p,&x|p.max(x.abs())))
    );
    let output_rms_dbfs = linear_to_db(rms(&leveled));
    Ok((leveled,VoiceReport {
        sample_rate:48_000, analyzed_frames, noise_profile_frames,
        input_rms_dbfs, after_filter_rms_dbfs, output_rms_dbfs,
        output_peak_dbfs, highpass_hz, input_quiet_rms_dbfs,
        post_filter_quiet_rms_dbfs, gate_active,
        peak_safety_gain_db: linear_to_db(f64::from(safety)),
        final_leveler_gain_db, input_overload_samples
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn deterministic_finite_output() {
        let input:Vec<f32>=(0..48_000).map(|i|
            0.1*(2.0*std::f32::consts::PI*200.0*i as f32/48_000.0).sin()
        ).collect();
        let (a,ar)=restore(&input,VoiceConfig::default()).unwrap();
        let (b,br)=restore(&input,VoiceConfig::default()).unwrap();
        assert_eq!(a,b);
        assert_eq!(ar.output_rms_dbfs,br.output_rms_dbfs);
        assert!(a.iter().all(|v|v.is_finite() && v.abs()<1.0));
        assert_eq!(a.len(),input.len());
    }
    #[test]
    fn bad_input_is_rejected() {
        assert!(matches!(restore(&[],VoiceConfig::default()),Err(VoiceError::EmptyInput)));
        assert!(matches!(restore(&[0.1,f32::NAN],VoiceConfig::default()),
            Err(VoiceError::NonFiniteInput(1))));
        let cfg=VoiceConfig {noise_strength:f32::NAN,..VoiceConfig::default()};
        assert!(matches!(restore(&[0.1],cfg),Err(VoiceError::InvalidConfig)));
    }
    #[test]
    fn no_noise_bed_means_silence_stays_nearly_silent() {
        let cfg=VoiceConfig {noise_bed_dbfs:None,..VoiceConfig::default()};
        let (out,_)=restore(&vec![0.0;12_000],cfg).unwrap();
        assert!(out.iter().all(|&v|v.abs()<1e-7));
    }
}
