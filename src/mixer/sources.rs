//! Deterministic test sources; offline only, not realtime or cryptographic randomness.
use std::f32::consts::TAU;

#[derive(Debug, Clone)]
pub struct WhiteNoise { state: u32 }

impl WhiteNoise {
    pub fn new(seed: u32) -> Self {
        Self { state: if seed == 0 { 0x9E37_79B9 } else { seed } }
    }

    pub fn next_sample(&mut self) -> f32 {
        let mut x = self.state;
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        self.state = x;
        (x as f64 / u32::MAX as f64 * 2.0 - 1.0) as f32
    }
}

fn hashed_noise(index: u64) -> f32 {
    let mut x = (index as u32).wrapping_add(0xA341_316C);
    x ^= x >> 16;
    x = x.wrapping_mul(0x7FEB_352D);
    x ^= x >> 15;
    x = x.wrapping_mul(0x846C_A68B);
    x ^= x >> 16;
    (x as f64 / u32::MAX as f64 * 2.0 - 1.0) as f32
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SourceError { InvalidSampleRate, InvalidBpm }

#[derive(Debug, Clone)]
pub struct SimpleBeat {
    sample_rate: u32,
    frames_per_beat: u64,
    position: u64,
}

impl SimpleBeat {
    pub fn new(sample_rate: u32, bpm: u32) -> Result<Self, SourceError> {
        if !(8_000..=192_000).contains(&sample_rate) {
            return Err(SourceError::InvalidSampleRate);
        }
        if !(40..=240).contains(&bpm) {
            return Err(SourceError::InvalidBpm);
        }
        Ok(Self {
            sample_rate,
            frames_per_beat: (u64::from(sample_rate) * 60) / u64::from(bpm),
            position: 0,
        })
    }

    /// Simple kick every quarter note and a noise snare on beats 2 and 4.
    pub fn next_sample(&mut self) -> f32 {
        let pos = self.position;
        self.position = self.position.wrapping_add(1);
        let beat = pos / self.frames_per_beat;
        let phase_frames = pos % self.frames_per_beat;
        let t = phase_frames as f32 / self.sample_rate as f32;

        let kick = if t < 0.28 {
            let decay = (-19.0 * t).exp();
            // Analytically integrated 55 Hz + falling 90 Hz sweep.
            let phase = 55.0 * t + (90.0 / 24.0) * (1.0 - (-24.0 * t).exp());
            0.72 * decay * (TAU * phase).sin()
        } else { 0.0 };

        let snare = if beat % 4 == 1 || beat % 4 == 3 {
            if t < 0.14 {
                let noise = (hashed_noise(pos) - hashed_noise(pos.wrapping_sub(1))) * 0.5;
                0.25 * (-36.0 * t).exp() * noise
            } else { 0.0 }
        } else { 0.0 };

        kick + snare
    }
}
