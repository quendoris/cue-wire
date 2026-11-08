use cuewire::mixer::{
    dynamics::{LevelerConfig, LevelerError, RmsLeveler},
    music::{PseudoMusic, SECTION_LEVELS_DB},
};

fn rms_db(data: &[f32]) -> f64 {
    let e = data.iter().map(|&v| f64::from(v).powi(2)).sum::<f64>() / data.len() as f64;
    10.0 * e.log10()
}

#[test]
fn configuration_is_strict() {
    let mut cfg = LevelerConfig::default();
    cfg.target_dbfs = f32::NAN;
    assert!(matches!(RmsLeveler::new(cfg), Err(LevelerError::InvalidConfig)));
    cfg = LevelerConfig::default();
    cfg.window_ms = 0;
    assert!(matches!(RmsLeveler::new(cfg), Err(LevelerError::InvalidConfig)));
    cfg = LevelerConfig::default();
    cfg.silence_floor_dbfs = -10.0;
    assert!(matches!(RmsLeveler::new(cfg), Err(LevelerError::InvalidConfig)));
}

#[test]
fn invalid_buffer_does_not_modify_state_or_output() {
    let cfg = LevelerConfig::default();
    let mut stage = RmsLeveler::new(cfg).unwrap();
    let mut out = [42.0;3];
    assert_eq!(stage.process_block(&[0.0,1.0], &mut out).unwrap_err(),
               LevelerError::LengthMismatch);
    assert_eq!(stage.process_block(&[0.0,f32::NAN,1.0], &mut out).unwrap_err(),
               LevelerError::NonFiniteInput(1));
    assert_eq!(out,[42.0;3]);
    assert_eq!(stage.gain_db(),0.0);
}

#[test]
fn silence_does_not_increase_gain() {
    let mut stage = RmsLeveler::new(LevelerConfig::default()).unwrap();
    let input = vec![0.0_f32; 48_000];
    let mut output = vec![1.0_f32; 48_000];
    stage.process_block(&input,&mut output).unwrap();
    assert!(output.iter().all(|&x| x == 0.0));
    assert_eq!(stage.gain_db(),0.0);
}

#[test]
fn steady_loud_and_quiet_fit_configured_band() {
    for amplitude in [0.03_f32, 0.2_f32] {
        let mut stage = RmsLeveler::new(LevelerConfig::default()).unwrap();
        let input = vec![amplitude; 48_000 * 4];
        let mut output = vec![0.0_f32; input.len()];
        stage.process_block(&input,&mut output).unwrap();
        let db = rms_db(&output[48_000 * 3..]);
        assert!((-29.5..=-22.5).contains(&db),
            "steady source with amplitude {amplitude}, output RMS={db:.3} dBFS");
    }
}

#[test]
fn processing_is_sample_partition_invariant() {
    let mut generator = PseudoMusic::new(48_000,2).unwrap();
    let input:Vec<f32> = (0..48_000 * 3).map(|_|generator.next_sample()).collect();
    let mut a = RmsLeveler::new(LevelerConfig::default()).unwrap();
    let mut b = RmsLeveler::new(LevelerConfig::default()).unwrap();
    let mut whole = vec![0.0;input.len()];
    let mut blocks = vec![0.0;input.len()];
    a.process_block(&input, &mut whole).unwrap();
    for (src,dst) in input.chunks(127).zip(blocks.chunks_mut(127)) {
        b.process_block(src,dst).unwrap();
    }
    assert_eq!(whole,blocks);
    assert_eq!(a.gain_db(),b.gain_db());
}

#[test]
fn music_has_sections_with_different_rms() {
    assert_eq!(SECTION_LEVELS_DB,[-12.0,-4.0,0.0,-9.0]);
    let mut song = PseudoMusic::new(48_000,2).unwrap();
    let mut song2 = PseudoMusic::new(48_000,2).unwrap();
    assert_eq!(song.next_sample(),song2.next_sample());
    let mut sections = Vec::new();
    for _ in 0..4 {
        let values:Vec<f32> = (0..96_000).map(|_|song.next_sample()).collect();
        sections.push(rms_db(&values[24_000..]));
    }
    assert!(sections[2] - sections[0] > 7.0,
            "expected clear source-level contrast: {sections:?}");
}

#[test]
fn overload_is_reported_not_hidden() {
    let mut stage = RmsLeveler::new(LevelerConfig::default()).unwrap();
    let mut out = [0.0_f32];
    let result = stage.process_block(&[1.5],&mut out).unwrap();
    assert!(out[0] > 1.0);
    assert_eq!(result.overloaded_samples,1);
}
