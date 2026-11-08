//! Bounded RMS leveler: PCM only, no PipeWire, no LUFS/BS.1770 claim.
#[derive(Debug, Clone, Copy)]
pub struct LevelerConfig {
    pub sample_rate: u32,
    pub target_dbfs: f32,
    pub tolerance_db: f32,
    pub window_ms: u32,
    pub attack_ms: u32,
    pub release_ms: u32,
    pub max_boost_db: f32,
    pub max_cut_db: f32,
    pub silence_floor_dbfs: f32,
}

impl Default for LevelerConfig {
    fn default() -> Self {
        Self {
            sample_rate: 48_000, target_dbfs: -26.0, tolerance_db: 3.0,
            window_ms: 350, attack_ms: 45, release_ms: 450,
            max_boost_db: 9.0, max_cut_db: 18.0, silence_floor_dbfs: -55.0,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LevelerError { InvalidConfig, LengthMismatch, NonFiniteInput(usize) }

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LevelerStats {
    pub peak_abs: f32,
    pub overloaded_samples: usize,
    pub ending_gain_db: f32,
}

pub struct RmsLeveler {
    config: LevelerConfig,
    squares: Vec<f64>,
    cursor: usize,
    filled: usize,
    energy_sum: f64,
    gain_db: f32,
    attack_alpha: f32,
    release_alpha: f32,
}

fn alpha(ms: u32, rate: u32) -> f32 {
    (1.0 - (-1000.0 / (ms as f64 * rate as f64)).exp()) as f32
}

impl RmsLeveler {
    pub fn new(config: LevelerConfig) -> Result<Self, LevelerError> {
        if !(8_000..=192_000).contains(&config.sample_rate)
            || !config.target_dbfs.is_finite()
            || !(-60.0..=-9.0).contains(&config.target_dbfs)
            || !config.tolerance_db.is_finite()
            || !(0.5..=12.0).contains(&config.tolerance_db)
            || !(50..=1000).contains(&config.window_ms)
            || !(1..=2000).contains(&config.attack_ms)
            || !(1..=5000).contains(&config.release_ms)
            || !config.max_boost_db.is_finite()
            || !(0.0..=24.0).contains(&config.max_boost_db)
            || !config.max_cut_db.is_finite()
            || !(0.0..=48.0).contains(&config.max_cut_db)
            || !config.silence_floor_dbfs.is_finite()
            || !(-100.0..=-30.0).contains(&config.silence_floor_dbfs)
            || config.silence_floor_dbfs >= config.target_dbfs - config.tolerance_db
        {
            return Err(LevelerError::InvalidConfig);
        }
        let frames = (u64::from(config.sample_rate) * u64::from(config.window_ms) / 1000) as usize;
        Ok(Self {
            attack_alpha: alpha(config.attack_ms, config.sample_rate),
            release_alpha: alpha(config.release_ms, config.sample_rate),
            config,
            squares: vec![0.0; frames.max(1)],
            cursor: 0, filled: 0, energy_sum: 0.0, gain_db: 0.0,
        })
    }

    pub fn gain_db(&self) -> f32 { self.gain_db }

    /// No allocations during processing; reject bad buffers before state mutation.
    /// No clipping or peak limiting is performed.
    pub fn process_block(
        &mut self, input: &[f32], output: &mut [f32]
    ) -> Result<LevelerStats, LevelerError> {
        if input.len() != output.len() { return Err(LevelerError::LengthMismatch); }
        for (i, &s) in input.iter().enumerate() {
            if !s.is_finite() { return Err(LevelerError::NonFiniteInput(i)); }
        }
        let mut peak_abs = 0.0_f32;
        let mut overloaded_samples = 0;
        for (&sample, dst) in input.iter().zip(output.iter_mut()) {
            let energy = f64::from(sample) * f64::from(sample);
            self.energy_sum += energy - self.squares[self.cursor];
            self.squares[self.cursor] = energy;
            self.cursor = (self.cursor + 1) % self.squares.len();
            self.filled = (self.filled + 1).min(self.squares.len());
            let mean_square = (self.energy_sum / self.filled as f64).max(0.0);
            let rms_dbfs = if mean_square == 0.0 {
                f32::NEG_INFINITY
            } else {
                (10.0 * mean_square.log10()) as f32
            };
            // Do not boost digital silence or passages below the detector floor.
            if rms_dbfs >= self.config.silence_floor_dbfs {
                let observed = rms_dbfs + self.gain_db;
                let desired = if (observed - self.config.target_dbfs).abs() > self.config.tolerance_db {
                    (self.config.target_dbfs - rms_dbfs)
                        .clamp(-self.config.max_cut_db, self.config.max_boost_db)
                } else { self.gain_db };
                let alpha = if desired < self.gain_db {
                    self.attack_alpha
                } else { self.release_alpha };
                self.gain_db += alpha * (desired - self.gain_db);
            }
            let result = sample * 10.0_f32.powf(self.gain_db / 20.0);
            *dst = result;
            peak_abs = peak_abs.max(result.abs());
            if result.abs() > 1.0 { overloaded_samples += 1; }
        }
        Ok(LevelerStats { peak_abs, overloaded_samples, ending_gain_db: self.gain_db })
    }
}
