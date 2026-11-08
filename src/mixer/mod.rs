//! Side-effect-free mono PCM mixing core.
//! No PipeWire clients, threads, allocations or device/default changes are created here.
pub mod sources;
pub mod dynamics;
pub mod music;
pub mod wav;
pub mod fft;
pub mod voice_wav;
pub mod voice;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Gain(f32);

impl Gain {
    /// Finite, nonnegative linear gain; deliberately capped to avoid accidental huge amplification.
    pub fn linear(value: f32) -> Result<Self, MixError> {
        if !value.is_finite() || !(0.0..=16.0).contains(&value) {
            return Err(MixError::InvalidGain);
        }
        Ok(Self(value))
    }

    /// dBFS amplitude gain (not power dB). -120..=24 dB, with 0 dB = unity.
    pub fn from_db(db: f32) -> Result<Self, MixError> {
        if !db.is_finite() || !(-120.0..=24.0).contains(&db) {
            return Err(MixError::InvalidGain);
        }
        Self::linear(10.0_f32.powf(db / 20.0))
    }

    pub const fn unity() -> Self { Self(1.0) }
    pub const fn silence() -> Self { Self(0.0) }
    pub fn value(self) -> f32 { self.0 }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MixError {
    LengthMismatch,
    InvalidGain,
    NonFiniteInput { index: usize, source: &'static str },
    NonFiniteOutput { index: usize },
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MixStats {
    /// Largest absolute sample in the pre-quantization, unclipped result.
    pub peak_abs: f32,
    /// Number of samples outside [-1, 1]. The core never clips or normalizes them.
    pub overloaded_samples: usize,
}

/// Sample-aligned summation into a caller-owned output buffer.
///
/// Two-pass validation guarantees that an error leaves output untouched.
/// No allocation, I/O, hard clipping, automatic normalization or limiting.
/// The caller is responsible for sample-rate/channel alignment and for handling
/// overload before converting to an integer format.
pub fn mix_block(
    left: &[f32],
    right: &[f32],
    out: &mut [f32],
    left_gain: Gain,
    right_gain: Gain,
) -> Result<MixStats, MixError> {
    if left.len() != right.len() || left.len() != out.len() {
        return Err(MixError::LengthMismatch);
    }

    for i in 0..left.len() {
        if !left[i].is_finite() {
            return Err(MixError::NonFiniteInput { index: i, source: "left" });
        }
        if !right[i].is_finite() {
            return Err(MixError::NonFiniteInput { index: i, source: "right" });
        }
        if !(left[i] * left_gain.0 + right[i] * right_gain.0).is_finite() {
            return Err(MixError::NonFiniteOutput { index: i });
        }
    }

    let mut peak_abs = 0.0_f32;
    let mut overloaded_samples = 0;
    for ((&a, &b), dst) in left.iter().zip(right.iter()).zip(out.iter_mut()) {
        let value = a * left_gain.0 + b * right_gain.0;
        *dst = value;
        let abs = value.abs();
        peak_abs = peak_abs.max(abs);
        if abs > 1.0 { overloaded_samples += 1; }
    }

    Ok(MixStats { peak_abs, overloaded_samples })
}
