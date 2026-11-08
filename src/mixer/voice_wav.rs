//! Strict offline input: mono 48 kHz signed PCM16 RIFF/WAV.
//! Convert other codecs explicitly and preserve the conversion command.
use std::{fs, path::Path};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VoiceWavError {
    Io(String), InvalidRiff, MissingFmt, MissingData, UnsupportedFormat, TooLong,
}
fn u16_at(b: &[u8], i: usize) -> Option<u16> {
    Some(u16::from_le_bytes(b.get(i..i+2)?.try_into().ok()?))
}
fn u32_at(b: &[u8], i: usize) -> Option<u32> {
    Some(u32::from_le_bytes(b.get(i..i+4)?.try_into().ok()?))
}

pub fn parse_pcm16_48k(data: &[u8]) -> Result<Vec<f32>, VoiceWavError> {
    if data.len() < 12 || &data[0..4] != b"RIFF" || &data[8..12] != b"WAVE" {
        return Err(VoiceWavError::InvalidRiff);
    }
    let declared = u32_at(data, 4).ok_or(VoiceWavError::InvalidRiff)? as usize;
    if declared.checked_add(8).ok_or(VoiceWavError::InvalidRiff)? > data.len() {
        return Err(VoiceWavError::InvalidRiff);
    }
    let end = declared + 8;
    let mut position = 12_usize;
    let mut fmt = None;
    let mut samples = None;
    while position < end {
        if position.checked_add(8).ok_or(VoiceWavError::InvalidRiff)? > end {
            return Err(VoiceWavError::InvalidRiff);
        }
        let kind = &data[position..position + 4];
        let len = u32_at(data, position + 4).ok_or(VoiceWavError::InvalidRiff)? as usize;
        let first = position + 8;
        let last = first.checked_add(len).ok_or(VoiceWavError::InvalidRiff)?;
        if last > end { return Err(VoiceWavError::InvalidRiff); }
        if kind == b"fmt " {
            if fmt.is_some() || len < 16 { return Err(VoiceWavError::InvalidRiff); }
            fmt = Some((
                u16_at(data,first).ok_or(VoiceWavError::InvalidRiff)?,
                u16_at(data,first+2).ok_or(VoiceWavError::InvalidRiff)?,
                u32_at(data,first+4).ok_or(VoiceWavError::InvalidRiff)?,
                u32_at(data,first+8).ok_or(VoiceWavError::InvalidRiff)?,
                u16_at(data,first+12).ok_or(VoiceWavError::InvalidRiff)?,
                u16_at(data,first+14).ok_or(VoiceWavError::InvalidRiff)?,
            ));
        } else if kind == b"data" {
            if samples.is_some() || len % 2 != 0 {
                return Err(VoiceWavError::InvalidRiff);
            }
            if len > 48_000 * 2 * 60 * 10 { return Err(VoiceWavError::TooLong); }
            samples = Some((first, last));
        }
        position = last.checked_add(len & 1).ok_or(VoiceWavError::InvalidRiff)?;
    }
    let (format, channels, rate, byte_rate, align, bits) = fmt.ok_or(VoiceWavError::MissingFmt)?;
    if format != 1 || channels != 1 || rate != 48_000 || byte_rate != 96_000
        || align != 2 || bits != 16 {
        return Err(VoiceWavError::UnsupportedFormat);
    }
    let (first,last) = samples.ok_or(VoiceWavError::MissingData)?;
    if first == last { return Err(VoiceWavError::MissingData); }
    Ok(data[first..last].chunks_exact(2).map(|chunk| {
        let value = i16::from_le_bytes([chunk[0],chunk[1]]);
        f32::from(value) / 32768.0
    }).collect())
}

pub fn read_pcm16_48k(path: &Path) -> Result<Vec<f32>, VoiceWavError> {
    let meta = fs::metadata(path).map_err(|e|VoiceWavError::Io(e.to_string()))?;
    if meta.len() > 48_000 * 2 * 60 * 10 + 16 * 1024 * 1024 {
        return Err(VoiceWavError::TooLong);
    }
    let data = fs::read(path).map_err(|e|VoiceWavError::Io(e.to_string()))?;
    parse_pcm16_48k(&data)
}
