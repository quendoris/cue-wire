//! Offline pseudo-music: kick/snare + bass + lead + hats in ONE track.
use super::sources::{SimpleBeat, SourceError};
use std::f32::consts::TAU;

pub const SECTION_LEVELS_DB: [f32; 4] = [-12.0, -4.0, 0.0, -9.0];

#[derive(Debug, Clone)]
pub struct PseudoMusic {
    drums: SimpleBeat,
    rate: u32,
    position: u64,
    frames_per_beat: u64,
    frames_per_section: u64,
}

impl PseudoMusic {
    pub fn new(rate: u32, seconds_per_section: u32) -> Result<Self, SourceError> {
        let drums = SimpleBeat::new(rate, 120)?;
        if !(1..=30).contains(&seconds_per_section) {
            return Err(SourceError::InvalidBpm);
        }
        Ok(Self {
            drums, rate, position: 0,
            frames_per_beat: u64::from(rate) / 2,
            frames_per_section: u64::from(rate) * u64::from(seconds_per_section),
        })
    }

    pub fn next_sample(&mut self) -> f32 {
        let n = self.position;
        self.position += 1;
        let t = n as f32 / self.rate as f32;
        let beat_index = (n / self.frames_per_beat) as usize;
        let beat_time = (n % self.frames_per_beat) as f32 / self.rate as f32;
        let eighth_frames = self.frames_per_beat / 2;
        let eighth_index = (n / eighth_frames) as usize;
        let eighth_time = (n % eighth_frames) as f32 / self.rate as f32;

        let drums = 0.58 * self.drums.next_sample();
        let bass_notes = [55.0, 65.41, 73.42, 65.41, 82.41, 73.42, 61.74, 49.00];
        let freq = bass_notes[beat_index % bass_notes.len()];
        let bass = 0.24 * (-3.5 * beat_time).exp() * (TAU * freq * t).sin();
        let lead_notes = [261.63, 329.63, 392.0, 329.63, 293.66, 261.63, 220.0, 293.66];
        let lead_freq = lead_notes[eighth_index % lead_notes.len()];
        let lead = 0.12 * (-9.0 * eighth_time).exp() *
            ((TAU * lead_freq * t).sin() + 0.15 * (TAU * lead_freq * 2.0 * t).sin());
        let hat = 0.025 * (-75.0 * eighth_time).exp() *
            ((TAU * 9_100.0 * t).sin() + 0.35 * (TAU * 11_200.0 * t).sin());

        // 100 ms crossfade between level presets prevents amplitude jumps.
        let section = ((n / self.frames_per_section) as usize).min(3);
        let inside = n % self.frames_per_section;
        let fade_frames = u64::from(self.rate) / 10;
        let level_db = if section > 0 && inside < fade_frames {
            let fraction = inside as f32 / fade_frames as f32;
            SECTION_LEVELS_DB[section - 1] * (1.0 - fraction)
                + SECTION_LEVELS_DB[section] * fraction
        } else { SECTION_LEVELS_DB[section] };
        0.68 * (drums + bass + lead + hat) * 10.0_f32.powf(level_db / 20.0)
    }
}
