use cuewire::mixer::{
    voice::{restore, VoiceConfig, VoiceError},
    voice_wav::{parse_pcm16_48k, VoiceWavError},
    wav::write_pcm16_mono,
};

#[test]
fn wav_round_trip_and_reject_wrong_format() {
    let mut wav=Vec::new();
    write_pcm16_mono(&mut wav,48_000,&[-0.5,0.0,0.5]).unwrap();
    let parsed=parse_pcm16_48k(&wav).unwrap();
    assert_eq!(parsed.len(),3);
    assert!((parsed[0]+0.5).abs()<0.0001);
    assert!((parsed[2]-0.5).abs()<0.0001);
    assert_eq!(parse_pcm16_48k(b"garbage"), Err(VoiceWavError::InvalidRiff));
    wav[22]=2; // channels (mono -> stereo): reject, do not silently remix
    assert_eq!(parse_pcm16_48k(&wav), Err(VoiceWavError::UnsupportedFormat));
}

#[test]
fn invalid_audio_and_config_are_rejected() {
    assert!(matches!(restore(&[],VoiceConfig::default()),Err(VoiceError::EmptyInput)));
    assert!(matches!(restore(&[0.0,f32::INFINITY],VoiceConfig::default()),
                    Err(VoiceError::NonFiniteInput(1))));
    let config=VoiceConfig{noise_bed_dbfs:Some(-20.0),..VoiceConfig::default()};
    assert!(matches!(restore(&[0.1],config),Err(VoiceError::InvalidConfig)));
}

#[test]
fn strong_noise_profile_does_not_create_a_loud_noise_floor() {
    // Synthetic speech intervals at 200 Hz, silence with white noise,
    // and a noise profile learned from the quiet interval.
    let rate=48_000_usize;
    let mut state=12345_u32;
    let input:Vec<f32>=(0..rate*2).map(|i|{
        state^=state<<13; state^=state>>17; state^=state<<5;
        let n=((state as f64/u32::MAX as f64)*2.0-1.0) as f32*0.024;
        let t=i as f32/rate as f32;
        let vocal=if i<rate/3 || i>rate*5/3 {0.0}
            else {0.17*(2.0*std::f32::consts::PI*200.0*t).sin()};
        vocal+n
    }).collect();
    let (out, report)=restore(&input, VoiceConfig::default()).unwrap();
    assert_eq!(out.len(),input.len());
    assert!(out.iter().all(|s|s.is_finite() && s.abs()<1.0));
    assert!(report.output_peak_dbfs < -2.5);
    assert!(report.noise_profile_frames > 0);
    // Quiet segment should be noticeably suppressed, not louder.
    let input_quiet=(input[..rate/4].iter().map(|v|f64::from(*v).powi(2)).sum::<f64>()
        / (rate/4) as f64).sqrt();
    let output_quiet=(out[..rate/4].iter().map(|v|f64::from(*v).powi(2)).sum::<f64>()
        / (rate/4) as f64).sqrt();
    assert!(output_quiet < input_quiet * 0.9,
        "quiet noise not sufficiently reduced: {input_quiet} -> {output_quiet}");
}

#[test]
fn repeatability_same_seed_different_seed_bed_only() {
    let source:Vec<f32>=(0..8_192).map(|i| {
        if i%512<250 {0.08} else {-0.08}
    }).collect();
    let cfg=VoiceConfig::default();
    let (first,report_a)=restore(&source,cfg).unwrap();
    let (second,report_b)=restore(&source,cfg).unwrap();
    assert_eq!(first,second);
    assert_eq!(report_a.output_peak_dbfs,report_b.output_peak_dbfs);
    let changed=VoiceConfig{noise_seed:cfg.noise_seed+1,..cfg};
    let (third,_)=restore(&source,changed).unwrap();
    assert_ne!(first,third);
}


#[test]
fn speech_safe_defaults_keep_optional_processing_separate() {
    let cfg=VoiceConfig::default();
    assert!(cfg.enable_denoise);
    assert!(!cfg.enable_expander, "quiet consonants must not be gated without user opt-in");
    assert!(!cfg.enable_leveler, "no pumping during restoration until separately auditioned");
    assert!(!cfg.enable_highpass, "do not cut low voice fundamentals by default");
    assert!(cfg.spectral_floor >= 0.35, "too-deep per-bin holes cause musical noise");
    assert!(cfg.noise_strength <= 1.25);
}

#[test]
fn unvoiced_sibilant_like_energy_is_preserved_in_gentle_mode() {
    let sr=48_000_usize;
    let mut state=0xA143_6C21_u32;
    let mut data=vec![0.0_f32;sr*2];
    let mut previous=0.0_f32;
    for (i, x) in data.iter_mut().enumerate() {
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        let white = ((state as f64/u32::MAX as f64)*2.0-1.0) as f32;
        let fricative = if (sr/2..sr).contains(&i) {
            // Sharp high-frequency signal: a synthetic /s/-like consonant.
            (white - previous) * 0.035
        } else { 0.0 };
        *x = fricative + 0.002 * white;
        previous=white;
    }
    let cfg=VoiceConfig{noise_bed_dbfs:None,..VoiceConfig::default()};
    let (out,_)=restore(&data,cfg).unwrap();
    let begin=sr*3/5;
    let end=sr*9/10;
    let input_energy=data[begin..end].iter().map(|&x| f64::from(x).powi(2)).sum::<f64>();
    let output_energy=out[begin..end].iter().map(|&x| f64::from(x).powi(2)).sum::<f64>();
    assert!(output_energy > input_energy * 0.10,
        "sibilant was almost destroyed: output/input energy ratio {}",output_energy/input_energy);
    assert!(out.iter().all(|&x|x.is_finite() && x.abs()<1.0));
}
