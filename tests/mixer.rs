use cuewire::mixer::{mix_block, sources::{SimpleBeat, WhiteNoise, SourceError}, wav::{write_pcm16_mono, WavError}, Gain, MixError};

#[test]
fn pure_linear_mix_and_independent_gain() {
    let (a, b) = ([0.5, -0.5, 0.25], [0.25, 0.5, -0.5]);
    let mut output = [0.0; 3];
    let stats = mix_block(&a, &b, &mut output,
                          Gain::linear(0.5).unwrap(), Gain::unity()).unwrap();
    assert_eq!(output, [0.5, 0.25, -0.375]);
    assert_eq!(stats.peak_abs, 0.5);
    assert_eq!(stats.overloaded_samples, 0);
}

#[test]
fn blocks_are_equivalent_to_single_pass() {
    let mut a = WhiteNoise::new(12);
    let mut b = SimpleBeat::new(48_000, 120).unwrap();
    let left: Vec<f32> = (0..5_123).map(|_|a.next_sample()).collect();
    let right: Vec<f32> = (0..5_123).map(|_|b.next_sample()).collect();
    let mut whole = vec![0.0; left.len()];
    let mut partitioned = vec![0.0; left.len()];
    let ga = Gain::from_db(-18.0).unwrap();
    let gb = Gain::from_db(-6.0).unwrap();
    mix_block(&left, &right, &mut whole, ga, gb).unwrap();
    for start in (0..left.len()).step_by(127) {
        let end = (start+127).min(left.len());
        mix_block(&left[start..end], &right[start..end],
                  &mut partitioned[start..end], ga, gb).unwrap();
    }
    assert_eq!(whole, partitioned);
}

#[test]
fn invalid_input_does_not_modify_destination() {
    let mut out = [7.0, 7.0];
    assert_eq!(mix_block(&[1.0], &[0.0, 0.0], &mut out, Gain::unity(), Gain::unity()),
               Err(MixError::LengthMismatch));
    assert_eq!(out, [7.0, 7.0]);
    let err = mix_block(&[0.2, f32::NAN], &[0.1, 0.0], &mut out,
                        Gain::unity(), Gain::unity()).unwrap_err();
    assert_eq!(err, MixError::NonFiniteInput {index:1, source:"left"});
    assert_eq!(out, [7.0, 7.0]);
}

#[test]
fn no_silent_hard_clipping() {
    let mut out = [0.0; 2];
    let stats = mix_block(&[0.8, -0.8], &[0.6, -0.6], &mut out,
                          Gain::unity(), Gain::unity()).unwrap();
    assert!((out[0] - 1.4).abs() < 1e-6);
    assert!((out[1] + 1.4).abs() < 1e-6);
    assert_eq!(stats.overloaded_samples, 2);
    assert!((stats.peak_abs - 1.4).abs() < 1e-6);
    let mut wav = Vec::new();
    assert!(matches!(write_pcm16_mono(&mut wav, 48_000, &out),
                     Err(WavError::OutOfRange { index:0, .. })));
    assert!(wav.is_empty());
}

#[test]
fn rejects_invalid_gain() {
    for value in [f32::NAN, f32::INFINITY, -0.1, 16.1] {
        assert_eq!(Gain::linear(value), Err(MixError::InvalidGain));
    }
    for db in [f32::NAN, -121.0, 25.0] {
        assert_eq!(Gain::from_db(db), Err(MixError::InvalidGain));
    }
    assert!((Gain::from_db(-6.0206).unwrap().value() - 0.5).abs() < 0.00001);
    let mut out = [0.0];
    mix_block(&[0.3], &[0.9], &mut out, Gain::silence(), Gain::unity()).unwrap();
    assert_eq!(out, [0.9]);
}

#[test]
fn deterministic_white_noise_and_beat() {
    let mut a = WhiteNoise::new(123);
    let mut same = WhiteNoise::new(123);
    let mut other = WhiteNoise::new(124);
    assert_eq!(a.next_sample(), same.next_sample());
    assert_ne!(a.next_sample(), other.next_sample());
    let mut beat = SimpleBeat::new(48_000,120).unwrap();
    let first: Vec<f32> = (0..24000).map(|_|beat.next_sample()).collect();
    assert!(first.iter().all(|s|s.is_finite()));
    assert!(first.iter().any(|s|s.abs()>0.01));
    assert_eq!(SimpleBeat::new(0,120).unwrap_err(), SourceError::InvalidSampleRate);
    assert_eq!(SimpleBeat::new(48_000,0).unwrap_err(), SourceError::InvalidBpm);
}

#[test]
fn wav_header_and_signed_pcm() {
    let mut wav = Vec::new();
    write_pcm16_mono(&mut wav, 48_000, &[-1.0,0.0,1.0]).unwrap();
    assert_eq!(&wav[0..4],b"RIFF");
    assert_eq!(&wav[8..12],b"WAVE");
    assert_eq!(u32::from_le_bytes(wav[4..8].try_into().unwrap()),42);
    assert_eq!(u32::from_le_bytes(wav[24..28].try_into().unwrap()),48_000);
    assert_eq!(&wav[36..40],b"data");
    assert_eq!(&wav[44..50],&[0,128,0,0,255,127]);
    assert_eq!(wav.len(),50);
}

#[test]
fn stems_reconstruct_float_mix() {
    let raw_a = [0.3,-0.2,0.1,0.0];
    let raw_b = [0.0,0.1,-0.2,0.3];
    let ga = Gain::from_db(-12.0).unwrap();
    let gb = Gain::from_db(-6.0).unwrap();
    let mut output = [0.0;4];
    mix_block(&raw_a,&raw_b,&mut output,ga,gb).unwrap();
    for i in 0..4 {
        let expected = raw_a[i]*ga.value() + raw_b[i]*gb.value();
        assert_eq!(output[i],expected);
    }
}
