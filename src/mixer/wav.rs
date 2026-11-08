//! Minimal checked PCM16 mono WAV writer, requiring only the standard library.
use std::io::{self, Write};

#[derive(Debug)]
pub enum WavError {
    Io(io::Error),
    InvalidSampleRate,
    TooManySamples,
    OutOfRange { index: usize, value: f32 },
}

impl From<io::Error> for WavError {
    fn from(value: io::Error) -> Self { Self::Io(value) }
}

/// Rejects overload and NaN/Inf rather than silently clipping the recording.
/// Validates input before writing the header, so invalid audio writes no bytes.
pub fn write_pcm16_mono<W: Write>(
    writer: &mut W,
    sample_rate: u32,
    samples: &[f32],
) -> Result<(), WavError> {
    if sample_rate == 0 || sample_rate > u32::MAX / 2 {
        return Err(WavError::InvalidSampleRate);
    }
    let data_bytes = u32::try_from(samples.len())
        .ok()
        .and_then(|n| n.checked_mul(2))
        .ok_or(WavError::TooManySamples)?;
    let riff_bytes = 36_u32.checked_add(data_bytes).ok_or(WavError::TooManySamples)?;
    for (index, &value) in samples.iter().enumerate() {
        if !value.is_finite() || !(-1.0..=1.0).contains(&value) {
            return Err(WavError::OutOfRange { index, value });
        }
    }
    writer.write_all(b"RIFF")?;
    writer.write_all(&riff_bytes.to_le_bytes())?;
    writer.write_all(b"WAVEfmt ")?;
    writer.write_all(&16_u32.to_le_bytes())?; // PCM fmt subchunk size
    writer.write_all(&1_u16.to_le_bytes())?;  // PCM
    writer.write_all(&1_u16.to_le_bytes())?;  // mono
    writer.write_all(&sample_rate.to_le_bytes())?;
    writer.write_all(&(sample_rate * 2).to_le_bytes())?; // byte rate
    writer.write_all(&2_u16.to_le_bytes())?; // block align
    writer.write_all(&16_u16.to_le_bytes())?; // bits/sample
    writer.write_all(b"data")?;
    writer.write_all(&data_bytes.to_le_bytes())?;
    for &value in samples {
        let integer = if value < 0.0 {
            (value * 32768.0).round() as i16
        } else {
            (value * 32767.0).round() as i16
        };
        writer.write_all(&integer.to_le_bytes())?;
    }
    Ok(())
}
