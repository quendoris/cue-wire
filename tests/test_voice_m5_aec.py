"""CueWire M5 synthetic AEC regression tests. No user recordings or audio devices."""
from __future__ import annotations

import json
import math
from types import SimpleNamespace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave

import numpy as np
from scipy import signal

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import voice_m5_aec as aec
import voice_m5_capture as capture
import voice_m5_spectral as spectral
import voice_m5_model_probe as model_probe
import voice_m5_noise_probe as noise_probe
import voice_m5_aba as aba
import voice_m5_aba_spectrum as aba_spectrum
import voice_m5_refcoherence as refcoherence
import voice_m5_frozen_heldout as frozen_heldout
import voice_m5_doubletalk_gate as doubletalk_gate
import voice_m5_guarded_fir as guarded_fir
import voice_m5_known_stem as known_stem
import voice_m5_lag_postmortem as lag_postmortem
import voice_m5_early_cross_take as cross_take
import voice_m5_playback_coherence as playback_coherence
import voice_m5_echo_floor as echo_floor
import voice_m5_off_floor_windows as off_windows
import voice_m5_off_spectral_fingerprint as off_fingerprint
import voice_m5_speex_baseline as speex_baseline
import voice_m5_speex_zero_reference as speex_zero
import voice_m5_speex_known_voice as speex_voice
import voice_m5_speex_delta as speex_delta
import voice_m5_listening_ab as listening_ab
import voice_m5_mouse_click_control as mouse_control
import voice_m5_chaos_capture as chaos
import voice_m5_chaos_reference_probe as chaos_ref
import voice_m5_chaos_reference_validate as chaos_validate
import voice_m5_chaos_speex_real as chaos_real
import voice_m5_capture as capture_runtime
from contextlib import ExitStack
import ctypes

RATE = aec.RATE


def pcm16_file(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(np.round(np.clip(samples, -0.99, 0.99) * 32768).astype("<i2").tobytes())


def synthetic_pair(length=11):
    rng = np.random.default_rng(1729)
    reference = rng.normal(0, 0.055, RATE * length)
    delay = int(.38 * RATE)
    impulse = np.zeros(4000)
    impulse[450] = 0.28
    impulse[1700] = 0.10
    echo = signal.fftconvolve(reference, impulse, mode="full")[:len(reference)]
    mic = np.zeros(RATE * length)
    mic[delay:] += echo[:len(mic)-delay]
    mic += rng.normal(0, .0012, len(mic))
    # Synthetic local voice starts after five seconds.
    start = int(5.5 * RATE)
    time = np.arange(len(mic)-start)/RATE
    mic[start:] += .035 * np.sin(2*np.pi*173*time)
    return reference, mic, delay + 450


class M5Tests(unittest.TestCase):
    def test_known_acoustic_delay_and_holdout_echo_reduction(self):
        reference, mic, expected_lag = synthetic_pair()
        lag, correlation = aec.lag_estimate(reference, mic, 5)
        self.assertLess(abs(lag-expected_lag), 20)
        self.assertGreater(correlation, .2)
        _, echo, fit = aec.fit_transfer(reference, mic, lag, 5)
        self.assertGreater(fit["heldout_speaker_only_erle_db"], 3.0)
        self.assertEqual(len(echo), len(mic))
        # The synthetic local voice should remain largely intact with a
        # frozen acoustic model; don't use mic samples after calibration.
        residual = mic - echo
        clean_voiced = mic[int(6*RATE):int(10*RATE)]
        after_voiced = residual[int(6*RATE):int(10*RATE)]
        target_speech = .035 * np.sin(2*np.pi*173*np.arange(4*RATE)/RATE
                                     + 2*np.pi*173*.5)
        # Compare energy/correlation, not exact equality: residual includes noise.
        self.assertGreater(np.std(after_voiced), .010)
        self.assertGreater(abs(float(np.corrcoef(clean_voiced, after_voiced)[0,1])), .75)

    def test_short_or_weak_speaker_pair_fails_closed(self):
        ref = np.zeros(RATE*8)
        mic = np.zeros(RATE*8)
        with self.assertRaisesRegex(ValueError, "too quiet"):
            aec.lag_estimate(ref, mic)
        rng = np.random.default_rng(11)
        mic = rng.normal(0, .01, RATE*8)
        ref = rng.normal(0, .01, RATE*8)
        with self.assertRaisesRegex(ValueError, "Unreliable calibration"):
            aec.lag_estimate(ref, mic)

    def test_private_capture_directory_and_explicit_sink(self):
        with tempfile.TemporaryDirectory() as temp:
            audit = Path(temp) / ".audit"
            audit.mkdir()
            parent = audit / "voice-aec"
            parent.mkdir()
            with mock.patch.object(capture, "AUDIT", audit.resolve()):
                self.assertEqual(capture.private_output(parent/"M5-001"), parent/"M5-001")
                occupied = parent/"M5-001"
                occupied.mkdir()
                with self.assertRaises(FileExistsError):
                    capture.private_output(occupied)
            with self.assertRaisesRegex(ValueError, "explicit"):
                capture.checked_speaker("auto")

    def test_paired_start_has_no_three_second_alsa_wait(self):
        """Recorder and player are spawned back-to-back, regardless of PCM readiness."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = []
            recorder = mock.Mock()
            player = mock.Mock()
            def fake_spawn(argv, log):
                events.append((argv[0], log.name))
                return recorder if argv[0] == "arecord" else player
            with mock.patch.object(capture, "spawn", side_effect=fake_spawn):
                got = capture.start_paired_streams(["arecord", "-D", "hw:0,0"],
                                                   ["pw-play", "--target", "speaker"],
                                                   root)
            self.assertEqual(got, (recorder, player))
            self.assertEqual(events, [("arecord", "arecord.log"),
                                      ("pw-play", "pw-play.log")])
            recorder.poll.assert_not_called()

    def test_paired_start_terminates_recorder_on_player_spawn_error(self):
        with tempfile.TemporaryDirectory() as temp:
            recorder = mock.Mock()
            def fake_spawn(argv, log):
                if argv[0] == "arecord":
                    return recorder
                raise OSError("speaker unavailable")
            with mock.patch.object(capture, "spawn", side_effect=fake_spawn), \
                 mock.patch.object(capture, "terminate_group") as stop:
                with self.assertRaisesRegex(OSError, "speaker unavailable"):
                    capture.start_paired_streams(["arecord"], ["pw-play"], Path(temp))
                stop.assert_called_once_with(recorder)

    def test_pipewire_capture_explicit_physical_node_without_alsa(self):
        args = SimpleNamespace(capture_backend="pipewire",
                               source="alsa_input.usb-MICAudio97i.mono-fallback",
                               mic="hw:0,0", expect_card_id="MICAudio97i",
                               seconds=20)
        chosen = {"name": args.source, "id": 42,
                  "sample_spec": "s16le 1ch 48000Hz"}
        with mock.patch.object(capture, "choose_source", return_value=chosen) as get, \
             mock.patch.object(capture, "physical_card") as alsa_verify, \
             mock.patch.object(capture, "checked_program", return_value="/usr/bin/pw-record"):
            cmd, log_name, info = capture.build_mic_recorder(
                args, Path("/tmp/private.wav"))
        get.assert_called_once_with(args.source)
        alsa_verify.assert_not_called()
        self.assertEqual(cmd[0], "pw-record")
        self.assertIn("--target=" + args.source, cmd)
        self.assertIn("--rate=48000", cmd)
        self.assertIn("--channels=1", cmd)
        self.assertIn("--format=s16", cmd)
        self.assertEqual(log_name, "pw-record.log")
        self.assertEqual(info["backend"], "pipewire")

    def test_reject_monitor_and_implicit_pipewire_fallback(self):
        args = SimpleNamespace(capture_backend="pipewire", source=None,
                               mic="hw:0,0", expect_card_id="MICAudio97i", seconds=20)
        with self.assertRaisesRegex(ValueError, "requires --source"):
            capture.build_mic_recorder(args, Path("/tmp/private.wav"))
        for invalid in ("alsa_output.fake.monitor", "cuewire.virtual_mic", "auto"):
            args.source = invalid
            with self.assertRaisesRegex(ValueError, "physical alsa_input"):
                capture.build_mic_recorder(args, Path("/tmp/private.wav"))

    def test_alsa_backend_remains_explicit_option(self):
        args = SimpleNamespace(capture_backend="alsa", source=None,
                               mic="hw:0,0", expect_card_id="MICAudio97i", seconds=20)
        card = {"arecord_listing": "expected ALSA card"}
        with mock.patch.object(capture, "physical_card", return_value=card), \
             mock.patch.object(capture, "checked_program", return_value="/usr/bin/arecord"):
            cmd, name, info = capture.build_mic_recorder(
                args, Path("/tmp/private.wav"))
        self.assertEqual(cmd[0], "arecord")
        self.assertEqual(name, "arecord.log")
        self.assertEqual(info["backend"], "alsa")

    def test_buffered_capture_progress_does_not_abort_playback(self):
        """Live WAV may not grow until pw-record closes; fail only on final WAV."""
        class Recorder:
            returncode = None
            def poll(self):
                return None

        clock = [0.0]
        class Player:
            returncode = None
            def poll(self):
                if clock[0] >= 20.1:
                    self.returncode = 0
                    return 0
                return None
        report = {}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            with mock.patch.object(capture.time, "monotonic",
                                   side_effect=lambda: clock[0]), \
                 mock.patch.object(capture.time, "sleep",
                                   side_effect=lambda seconds: clock.__setitem__(
                                       0, clock[0] + seconds)), \
                 mock.patch("builtins.print"):
                capture.watch_paired_streams(
                    Recorder(), Player(), path / "not-yet-flushed.wav", path,
                    "pw-record.log", 5, 20, report, 0.0)
        self.assertFalse(report["capture_wav_bytes_observed_during_playback"])
        self.assertGreaterEqual(clock[0], 20.1)

    def test_finalized_capture_validation_rejects_short_or_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            log = folder / "pw-record.log"
            log.write_text("synthetic test log")
            short_file = folder / "short.wav"
            pcm16_file(short_file, np.full(RATE, 0.02, dtype=np.float64))
            with self.assertRaisesRegex(RuntimeError, "too short"):
                capture.validate_completed_microphone(short_file, 20, log)
            zero_file = folder / "silent.wav"
            pcm16_file(zero_file, np.zeros(RATE * 20, dtype=np.float64))
            with self.assertRaisesRegex(RuntimeError, "effectively silent"):
                capture.validate_completed_microphone(zero_file, 20, log)
            good_file = folder / "good.wav"
            pcm16_file(good_file, np.full(RATE * 20, 0.03, dtype=np.float64))
            report = capture.validate_completed_microphone(good_file, 20, log)
            self.assertEqual(report["measurements"]["frames"], RATE * 20)
            self.assertEqual(report["sha256"], capture.sha256(good_file))

    def test_cli_import_help_offline(self):
        for script in ("voice_m5_capture.py", "voice_m5_aec.py",
                       "voice_m5_frozen_heldout.py",
                       "voice_m5_guarded_fir.py",
                       "voice_m5_known_stem.py",
                       "voice_m5_lag_postmortem.py",
                       "voice_m5_early_cross_take.py",
                       "voice_m5_playback_coherence.py",
                       "voice_m5_echo_floor.py",
                       "voice_m5_off_floor_windows.py",
                       "voice_m5_off_spectral_fingerprint.py",
                       "voice_m5_speex_baseline.py",
                       "voice_m5_speex_zero_reference.py",
                       "voice_m5_speex_known_voice.py",
                       "voice_m5_speex_delta.py",
                       "voice_m5_listening_ab.py",
                       "voice_m5_mouse_click_control.py",
                       "voice_m5_chaos_capture.py",
                       "voice_m5_chaos_reference_probe.py",
                       "voice_m5_chaos_reference_validate.py",
                       "voice_m5_chaos_speex_real.py"):
            proc = subprocess.run([sys.executable, str(TOOLS / script), "--help"],
                                  capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_spectral_diagnostics_synthetic_coherence_and_lag(self):
        x, y, expected_lag = synthetic_pair(9)
        lag, correlation = aec.lag_estimate(x, y, 5)
        local = spectral.correlation_windows(x, y, lag)
        spectrum = spectral.spectrum_summary(x, y, lag)
        band_lag = spectral.band_lag_windows(x, y, lag)
        self.assertEqual(len(band_lag), 5)
        self.assertTrue(any(v["status"] == "measured_not_validated"
                            for v in band_lag))
        for band in band_lag:
            if band["status"] == "measured_not_validated":
                self.assertEqual(len(band["local_windows"]), 4)
                self.assertTrue(all(math.isfinite(w["best_lag_ms"])
                                    and 0 <= w["global_lag_score"] <= 1.00001
                                    for w in band["local_windows"]))
        self.assertEqual(len(local), 4)
        self.assertTrue(all("top_alternative_peaks" in w for w in local))
        self.assertTrue(all(len(w["top_alternative_peaks"]) >= 1 for w in local))
        self.assertTrue(all(len(w["top_alternative_peaks"]) <= 5 for w in local))
        self.assertTrue(all(w["correlation_at_global_lag"] >= 0
                            for w in local))
        self.assertTrue(all(w["top_alternative_peaks"][0]
                            ["normalized_correlation"] >= w["correlation_at_global_lag"] - 0.00001
                            for w in local))
        self.assertLess(abs(lag - expected_lag), 20)
        self.assertTrue(all(v["normalized_correlation"] > .15 for v in local))
        self.assertEqual(len(spectrum["bands"]), 4)
        self.assertTrue(all(
            0 <= v["median_magnitude_squared_coherence"] <= 1.00001
            for v in spectrum["bands"]))
        self.assertGreater(
            max(v["reference_energy_weighted_coherence"]
                for v in spectrum["bands"]), 0.3)

    def test_m5_fir_model_probe_matches_existing_baseline(self):
        """120ms/0.02 model must reproduce original frozen M5 filter fit."""
        x, y, _ = synthetic_pair(9)
        lag, _ = aec.lag_estimate(x, y, 5)
        _, _, original = aec.fit_transfer(x, y, lag, 5)
        offset = max(0, lag - int(0.010 * RATE))
        aligned = np.zeros(offset + int(4.60 * RATE))
        aligned[offset:] = x[:len(aligned)-offset]
        current = model_probe.fit_one(aligned, y[:len(aligned)],
                                      offset, 120, 0.02)
        self.assertAlmostEqual(
            current["training_erle_db"], original["training_erle_db"],
            delta=0.03)
        self.assertAlmostEqual(
            current["heldout_erle_db"], original["heldout_speaker_only_erle_db"],
            delta=0.03)
        self.assertEqual(len(current["heldout_erle_by_frequency_db"]), 4)
        short = model_probe.fit_one(aligned, y[:len(aligned)],
                                    offset, 30, 0.10)
        self.assertTrue(math.isfinite(short["heldout_erle_db"]))

    def test_m5_model_probe_writes_only_numeric_audit_and_refuses_reuse(self):
        """No WAV is emitted, and source WAV hashes are enforced."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / ".audit"
            capture_dir = audit / "voice-aec/M5-001"
            capture_dir.mkdir(parents=True)
            x, y, _ = synthetic_pair(9)
            ref_file = capture_dir / "reference-sent.wav"
            mic_file = capture_dir / "microphone-raw.wav"
            pcm16_file(ref_file, x)
            pcm16_file(mic_file, y)
            manifest_path = capture_dir / "manifest.json"
            manifest = {
                "schema": "cuewire-M5-paired-physical-capture-v1",
                "status": "captured_for_offline_aec",
                "reference": {"sha256": aec.digest(ref_file)},
                "microphone": {"sha256": aec.digest(mic_file)},
                "speaker_only_calibration_s": 5,
            }
            manifest_path.write_text(json.dumps(manifest))
            hashes = [aec.digest(p) for p in (ref_file, mic_file, manifest_path)]
            with (mock.patch.object(model_probe, "AUDIT", audit.resolve()),
                  mock.patch.object(model_probe, "LENGTH_MS", (120,)),
                  mock.patch.object(model_probe, "RIDGE_FACTORS", (0.02,))):
                dest = audit / "voice-aec/M5-model-001"
                report = model_probe.probe(capture_dir, dest)
                self.assertEqual(report["status"],
                                 "exploratory_only_no_voice_output")
                self.assertEqual(len(report["candidates"]), 1)
                self.assertEqual(list(dest.glob("*.wav")), [])
                self.assertTrue((dest/"model-probe.json").is_file())
                self.assertTrue((dest/"SHA256SUMS").is_file())
                self.assertEqual(
                    hashes, [aec.digest(p) for p in
                             (ref_file, mic_file, manifest_path)])
                with self.assertRaises(FileExistsError):
                    model_probe.probe(capture_dir, dest)

    def test_noise_probe_offline_sha_guard_and_windowed_levels(self):
        """Compare captured silence and speaker-only with immutable WAV inputs."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / ".audit"
            silence_dir = audit / "voice-captures/M5-room-baseline-001"
            music_dir = audit / "voice-aec/M5-006"
            silence_dir.mkdir(parents=True)
            music_dir.mkdir(parents=True)
            x, y, _ = synthetic_pair(9)
            mic_name = "alsa_input.usb-test-synthetic.microphone"
            ref_file = music_dir / "reference-sent.wav"
            mic_file = music_dir / "microphone-raw.wav"
            silence_file = silence_dir / "raw.wav"
            pcm16_file(ref_file, x)
            pcm16_file(mic_file, y)
            quiet = np.random.default_rng(77).normal(0, .00015, RATE * 8)
            pcm16_file(silence_file, quiet)
            baseline_manifest = {
                "schema": "cuewire-M4-raw-capture-v2",
                "status": "recorded_unprocessed_raw",
                "mic_node_name": mic_name,
                "wav": "raw.wav",
                "wav_sha256": aec.digest(silence_file),
            }
            speaker_manifest = {
                "schema": "cuewire-M5-paired-physical-capture-v1",
                "status": "captured_for_offline_aec",
                "mic_capture": {"mic_source_name": mic_name},
                "reference": {"sha256": aec.digest(ref_file)},
                "microphone": {"sha256": aec.digest(mic_file)},
                "speaker_only_calibration_s": 5,
            }
            (silence_dir / "manifest.json").write_text(json.dumps(baseline_manifest))
            (music_dir / "manifest.json").write_text(json.dumps(speaker_manifest))
            originals = [aec.digest(p) for p in (silence_file, ref_file, mic_file)]
            with mock.patch.object(noise_probe, "AUDIT", audit.resolve()):
                out = audit / "voice-aec/M5-floor-probe-006"
                report = noise_probe.analyze(silence_dir, music_dir, out)
                self.assertEqual(report["status"],
                                 "diagnostic_only_no_aec_model_selected")
                self.assertEqual(report["baseline_time_windows"]["windows"], 14)
                self.assertGreater(
                    report["speaker_on_time_windows"]["windows"], 0)
                self.assertTrue((out / "noise-probe.json").is_file())
                self.assertTrue((out / "SHA256SUMS").is_file())
                self.assertEqual(list(out.glob("*.wav")), [])
                with self.assertRaises(FileExistsError):
                    noise_probe.analyze(silence_dir, music_dir, out)
                self.assertEqual(
                    originals, [aec.digest(p) for p in
                                (silence_file, ref_file, mic_file)])

    def test_continuous_aba_phase_metrics_are_consistent(self):
        """A/B/A uses one fixed mic gain and separate interior windows."""
        rng = np.random.default_rng(293)
        pre, on, post = 8, 20, 8
        seconds = pre + on + post
        signal_data = rng.normal(0, 0.0008, seconds * RATE)
        idx = np.arange(on * RATE)
        signal_data[pre * RATE:(pre + on) * RATE] += (
            .006 * np.sin(2 * np.pi * 1000. * idx / RATE))
        stats = aba.summarize_phases(signal_data, pre, on, post)
        self.assertEqual(set(stats["windows"]), {
            "before_speaker_on", "speaker_on", "after_speaker_off"})
        self.assertGreater(stats["on_minus_off_rms_db"], 10.0)
        self.assertLess(abs(stats["pre_post_median_rms_delta_db"]), 1.0)
        self.assertTrue(0.0 < stats["conditional_off_power_fraction_if_stationary"] < 1.0)
        self.assertEqual(stats["windows"]["before_speaker_on"]["windows"], 8)
        self.assertEqual(stats["windows"]["speaker_on"]["windows"], 24)
        self.assertEqual(stats["windows"]["after_speaker_off"]["windows"], 8)
        with self.assertRaises(ValueError):
            aba.summarize_phases(np.zeros(RATE * 6), 8, 20, 8)
        with self.assertRaises(ValueError):
            aba.summarize_phases(signal_data, 3, 20, 8)

    def test_aba_spectral_analysis_identifies_stationary_hum(self):
        """Synthetic stable low-frequency noise and extra 2kHz speaker."""
        rng = np.random.default_rng(900)
        data = rng.normal(0, 0.00025, 36 * RATE)
        time_axis = np.arange(len(data)) / RATE
        data += .0008 * np.sin(2 * np.pi * 50 * time_axis)
        on_start, on_end = 8 * RATE, 28 * RATE
        data[on_start:on_end] += .004 * np.sin(
            2 * np.pi * 2000 * time_axis[on_start:on_end])
        windows = {
            "before_speaker_on": {"approx_wav_position_s": [2., 6.]},
            "speaker_on": {"approx_wav_position_s": [12., 24.]},
            "after_speaker_off": {"approx_wav_position_s": [30., 34.]},
        }
        observed = aba_spectrum.inspect_samples(data, windows)
        by_band = {
            tuple(row["band_hz"]): row for row in observed["bands"]}
        self.assertLess(abs(by_band[(20, 70)]["on_minus_mean_off_db"]), 0.3)
        self.assertGreater(by_band[(1500, 4000)]["on_minus_mean_off_db"], 10)
        self.assertGreater(observed["on_minus_off_db"], 5)
        self.assertAlmostEqual(
            observed["phase_levels"]["before_speaker_on"][
                "dominant_low_frequency_lines"][0]["peak_frequency_hz"],
            50.0, delta=1.0)
        self.assertTrue(all(
            0 <= row["fraction_of_off_total_power"] <= 1
            for row in observed["bands"]))
        self.assertEqual(len(observed["top_quarter_second_transients"]), 8)

    def test_reference_coherence_lag_and_negative_control_synthetic(self):
        """Band-lag identifies real delayed reference despite independent 50Hz hum."""
        rng = np.random.default_rng(401)
        x = rng.normal(0, .03, 8 * RATE)
        mic = rng.normal(0, .0008, 14 * RATE)
        # Time-invariant coupling: source starts after 2s software lead-in,
        # additional 85ms acoustic/digital latency.
        lag_samples = round((2.0 + .085) * RATE)
        mic[lag_samples:lag_samples + len(x)] += .13 * x
        t = np.arange(len(mic)) / RATE
        mic += .002 * np.sin(2 * np.pi * 50 * t)
        lag, info = refcoherence.band_lag(x, mic, 2.0)
        self.assertLess(abs(lag - lag_samples), 5)
        self.assertGreater(info["peak_highmid_normalized_correlation"], .25)
        ons = refcoherence.coherence_by_bands(
            x[3*RATE:7*RATE], mic[lag + 3*RATE:lag + 7*RATE])
        offs = refcoherence.coherence_by_bands(
            x[3*RATE:7*RATE], mic[:4*RATE])
        on = next(c for c in ons if c["band_hz"] == [1500, 4000])
        off = next(c for c in offs if c["band_hz"] == [1500, 4000])
        self.assertGreater(on["mic_power_weighted_coherence"], .75)
        self.assertLess(off["mic_power_weighted_coherence"], .3)
        self.assertEqual(len(ons), len(refcoherence.BANDS))

    def test_frozen_aec_separate_training_and_heldout_coherence(self):
        """Train-only FIR reduces independent echo without fitting future PCM."""
        rng = np.random.default_rng(30102)
        reference = rng.normal(0, .04, 20 * RATE)
        lag = round(8.26 * RATE)
        microphone = rng.normal(0, .0008, 36 * RATE)
        t = np.arange(len(microphone)) / RATE
        microphone += .001 * np.sin(2 * np.pi * 50 * t)
        microphone[lag:lag+len(reference)] += .19 * reference
        estimate, model = frozen_heldout.fit_frozen(
            reference, microphone, lag)
        a, b = frozen_heldout.indices(
            frozen_heldout.HELDOUT_REFERENCE_S, lag, len(microphone))
        original = microphone[a:b]
        remaining = original - estimate[a:b]
        self.assertGreater(frozen_heldout.erle_db(original, remaining), 9.0)
        self.assertGreater(model["training_erle_db"], 9.)
        self.assertEqual(frozen_heldout.TRAIN_REFERENCE_S, (3.0, 9.0))
        self.assertEqual(frozen_heldout.HELDOUT_REFERENCE_S, (10.0, 16.0))
        self.assertAlmostEqual(float(np.max(np.abs(estimate[2*RATE:6*RATE]))),
                               0., delta=1e-12)
        ref_heldout = reference[10*RATE:16*RATE]
        compared = frozen_heldout.compare_coherence_same_weights(
            ref_heldout, original, remaining)
        highmid = next(m for m in compared if m["band_hz"] == [1500, 4000])
        self.assertGreater(
            highmid["coherence_mic_original_psd_weights_before"], 0.8)
        self.assertLess(
            highmid["coherence_mic_original_psd_weights_after"], 0.3)

        altered = microphone.copy()
        altered[a:b] += rng.normal(0, .02, b-a)
        estimate_again, model_again = frozen_heldout.fit_frozen(
            reference, altered, lag)
        # Absolutely NO heldout microphone sample influences the fit.
        np.testing.assert_allclose(estimate, estimate_again, rtol=0, atol=0)
        self.assertEqual(model, model_again)

    def test_frozen_aec_report_is_numeric_and_source_hash_guarded(self):
        """Verified A/B/A source; no overwrite, no voice or WAV rendering."""
        rng = np.random.default_rng(781)
        reference = rng.normal(0, .05, 20 * RATE)
        lag = round((8.0 + .19) * RATE)
        microphone = rng.normal(0, .0009, 36 * RATE)
        microphone[lag:lag+len(reference)] += .2 * reference
        with tempfile.TemporaryDirectory() as temp:
            audit = Path(temp) / ".audit"
            ref_dir = audit / "voice-aec/M5-006"
            aba_dir = audit / "voice-aec/M5-ABA-001"
            ref_dir.mkdir(parents=True)
            aba_dir.mkdir(parents=True)
            x_path = ref_dir / "reference-sent.wav"
            y_path = aba_dir / "microphone-aba-raw.wav"
            pcm16_file(x_path, reference)
            pcm16_file(y_path, microphone)
            reference_manifest = {
                "schema": "cuewire-M5-paired-physical-capture-v1",
                "status": "captured_for_offline_aec",
                "reference": {"sha256": aec.digest(x_path)},
                "mic_capture": {"mic_source_name": "alsa_input.usb-test-mic"},
                "speaker_sink_name": "alsa_output.pci-test-speaker",
                "volume_pw_play": .3,
            }
            reference_manifest_path = ref_dir / "manifest.json"
            reference_manifest_path.write_text(json.dumps(reference_manifest))
            aba_manifest = {
                "schema": "cuewire-M5-continuous-ABA-control-v1",
                "status": "captured_aba_for_noise_diagnostics",
                "reference_source_capture": str(ref_dir),
                "reference_source_manifest_sha256":
                    aec.digest(reference_manifest_path),
                "reference_wav_sha256": aec.digest(x_path),
                "source_mic_name": "alsa_input.usb-test-mic",
                "speaker_sink_name": "alsa_output.pci-test-speaker",
                "speaker_play_volume": .3,
                "playback_start_after_recorder_launch_s": 8.0,
                "microphone": {"sha256": aec.digest(y_path)},
            }
            aba_manifest_path = aba_dir / "manifest.json"
            aba_manifest_path.write_text(json.dumps(aba_manifest))
            original_hashes = [aec.digest(p) for p in
                               (x_path, y_path, reference_manifest_path,
                                aba_manifest_path)]
            with mock.patch.object(frozen_heldout, "AUDIT", audit.resolve()):
                out = audit / "voice-aec/M5-frozen-aec-heldout-001"
                result = frozen_heldout.analyze(aba_dir, out)
                self.assertEqual(
                    result["status"], "research_only_no_voice_no_audio_output")
                self.assertTrue(result["heldout"]["meets_prior_minimum_total_erle"])
                self.assertGreater(result["heldout"]["erle_db"], 8)
                self.assertGreater(len(result["heldout"]["band_metrics"]), 5)
                self.assertEqual(
                    result["speaker_off_control"]["echo_prediction_off_max_abs"],
                    0.0)
                self.assertTrue((out / "frozen-aec-heldout.json").is_file())
                self.assertTrue((out / "SHA256SUMS").is_file())
                self.assertEqual(list(out.glob("*.wav")), [])
                with self.assertRaises(FileExistsError):
                    frozen_heldout.analyze(aba_dir, out)
            self.assertEqual(
                original_hashes, [aec.digest(p) for p in
                                  (x_path, y_path, reference_manifest_path,
                                   aba_manifest_path)])
            # Corrupt the raw source bytes without changing capture digest.
            with y_path.open("ab") as f:
                f.write(b"tamper")
            with mock.patch.object(frozen_heldout, "AUDIT", audit.resolve()):
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    frozen_heldout.analyze(
                        aba_dir, audit / "voice-aec/M5-frozen-aec-heldout-002")

    def test_extended_doubletalk_calibration_requires_silent_first_12s(self):
        """Explicit 32s, 12s calibration allowed; no recording via CLI test."""
        argv = [
            "voice_m5_capture.py",
            "--music", "/tmp/synthetic-unused-reference.wav",
            "--speaker", "alsa_output.synthetic",
            "--capture-backend", "pipewire",
            "--source", "alsa_input.usb-synthetic",
            "--seconds", "32", "--calibration-seconds", "12",
            "--volume", "0.30",
            "--out", "/tmp/no-capture-is-created-by-this-test"
        ]
        with (mock.patch.object(sys, "argv", argv),
              mock.patch.object(capture, "experiment") as run):
            capture.main()
            run.assert_called_once()
            args = run.call_args[0][0]
            self.assertEqual(args.seconds, 32)
            self.assertEqual(args.calibration_seconds, 12)
            self.assertEqual(args.capture_backend, "pipewire")
        bad_argv = [*argv]
        bad_argv[bad_argv.index("12")] = "15"
        with (mock.patch.object(sys, "argv", bad_argv),
              mock.patch.object(capture, "experiment") as run,
              self.assertRaises(SystemExit) as err):
            capture.main()
        self.assertEqual(err.exception.code, 2)
        run.assert_not_called()

    def test_doubletalk_gate_training_independent_of_future_voice(self):
        """Changing voice after 12s MUST NOT change the frozen FIR."""
        rng = np.random.default_rng(88552)
        reference = rng.normal(0, .06, 32*RATE)
        lag = round(.18 * RATE)
        mic = rng.normal(0, .0008, 33*RATE)
        mic[lag:lag+len(reference)] += .20*reference
        prediction, model = doubletalk_gate.fit_calibration(reference, mic, lag)
        a = doubletalk_gate.cut(
            doubletalk_gate.VALIDATE_S, lag, mic)
        b = doubletalk_gate.cut(
            doubletalk_gate.VALIDATE_S, lag, prediction)
        self.assertGreater(
            doubletalk_gate.erle_db(a, a-b), 8)
        speech = mic.copy()
        voice_start = lag + 13 * RATE
        speech[voice_start:] += rng.normal(0, .02, len(speech)-voice_start)
        prediction2, model2 = doubletalk_gate.fit_calibration(
            reference, speech, lag)
        np.testing.assert_array_equal(prediction, prediction2)
        self.assertEqual(model, model2)
        self.assertEqual(doubletalk_gate.TRAIN_S, (3.0, 9.0))
        self.assertEqual(doubletalk_gate.VALIDATE_S, (10.0, 11.5))
        self.assertEqual(doubletalk_gate.DOUBLE_TALK_S, (13.0, 28.0))
        self.assertGreater(model["training_erle_db"], 8)
        compared = doubletalk_gate.band_score(
            reference[10*RATE:round(11.5*RATE)], a, a-b)
        self.assertEqual(len(compared), 6)
        self.assertEqual(doubletalk_gate.MIN_ERLE_DB, 1.0)

    def test_doubletalk_gate_rehash_rejects_tamper_and_no_wav_output(self):
        """End-to-end new numeric report, verified hashes, no source mutation."""
        rng = np.random.default_rng(88553)
        reference = rng.normal(0, .05, 32*RATE)
        lag = round(.11 * RATE)
        mic = rng.normal(0, .001, 33*RATE)
        mic[lag:lag+len(reference)] += .20*reference
        with tempfile.TemporaryDirectory() as root:
            audit = Path(root)/".audit"
            source = audit/"voice-aec/M5-doubletalk-001"
            source.mkdir(parents=True)
            x_path = source/"reference-sent.wav"
            y_path = source/"microphone-raw.wav"
            pcm16_file(x_path, reference)
            pcm16_file(y_path, mic)
            manifest = {
                "schema": "cuewire-M5-paired-physical-capture-v1",
                "status": "captured_for_offline_aec",
                "speaker_only_calibration_s": 12,
                "requested_duration_s": 32,
                "mic_capture": {
                    "backend": "pipewire",
                    "mic_source_name": "alsa_input.usb-test-synthetic"},
                "speaker_sink_name": "alsa_output.pci-test-synthetic",
                "reference": {"sha256": aec.digest(x_path)},
                "microphone": {"sha256": aec.digest(y_path)},
            }
            manifest_path = source/"manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            before = [aec.digest(p) for p in (
                x_path, y_path, manifest_path)]
            with mock.patch.object(doubletalk_gate, "AUDIT", audit.resolve()):
                output = audit/"voice-aec/M5-doubletalk-gate-001"
                report = doubletalk_gate.study(source, output)
                self.assertTrue(
                    report["speaker_only_validation"][
                        "passes_minimum_wholeband_erle"])
                self.assertFalse(report["approved_for_virtual_mic"])
                self.assertFalse(report["voice_preservation_validated"])
                self.assertIsNotNone(
                    report["double_talk_numerical_exploration"])
                self.assertEqual(list(output.glob("*.wav")), [])
                self.assertTrue((output/"doubletalk-gate.json").is_file())
                self.assertTrue((output/"SHA256SUMS").is_file())
                with self.assertRaises(FileExistsError):
                    doubletalk_gate.study(source, output)
                self.assertEqual(
                    before, [aec.digest(p) for p in (
                        x_path, y_path, manifest_path)])
                with y_path.open("ab") as file:
                    file.write(b"tamper")
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    doubletalk_gate.study(
                        source, audit/"voice-aec/M5-doubletalk-gate-002")

    def test_guarded_echo_high_frequency_preserves_only_mic_not_filter(self):
        """A predeclared LPF leaves 1 kHz echo, suppresses >5 kHz echo."""
        fs = RATE
        t = np.arange(11*fs, dtype=float)/fs
        lo = .010 * np.sin(2*np.pi*1000*t)
        hi = .010 * np.sin(2*np.pi*8000*t)
        echo = lo+hi
        original_copy = echo.copy()
        filtered, response = guarded_fir.guarded_echo(echo)
        # Verify original data are never modified by the numerical guard.
        np.testing.assert_array_equal(echo, original_copy)
        center = slice(fs, 10*fs)
        self.assertGreater(float(np.dot(filtered[center], lo[center])) /
                           float(np.dot(lo[center], lo[center])), .99)
        self.assertLess(abs(float(np.dot(filtered[center], hi[center])) /
                            float(np.dot(hi[center], hi[center]))), .005)
        self.assertLess(response["max_gain_5000_to_16000_hz"], .0032)
        self.assertFalse(response["original_physical_mic_filtering"])
        self.assertTrue(response["noncausal_not_realtime"])

    def test_guarded_fir_audio_remains_private_json_only(self):
        """New guarded model audit cannot overwrite WAVs or claim voice safety."""
        rng = np.random.default_rng(7193)
        ref = rng.normal(0, .05, 32*RATE)
        lag = round(.10*RATE)
        mic = rng.normal(0, .0008, 33*RATE)
        mic[lag:lag+len(ref)] += .20*ref
        start = lag + 13*RATE
        mic[start:] += rng.normal(0, .018, len(mic)-start)
        with tempfile.TemporaryDirectory() as root:
            audit = Path(root)/".audit"
            capture_dir = audit/"voice-aec/M5-doubletalk-001"
            capture_dir.mkdir(parents=True)
            x_path = capture_dir/"reference-sent.wav"
            y_path = capture_dir/"microphone-raw.wav"
            pcm16_file(x_path, ref)
            pcm16_file(y_path, mic)
            manifest = {
                "schema": "cuewire-M5-paired-physical-capture-v1",
                "status": "captured_for_offline_aec",
                "speaker_only_calibration_s": 12,
                "requested_duration_s": 32,
                "reference": {"sha256": aec.digest(x_path)},
                "microphone": {"sha256": aec.digest(y_path)},
            }
            manifest_file = capture_dir/"manifest.json"
            manifest_file.write_text(json.dumps(manifest))
            initial = [aec.digest(p) for p in (manifest_file, x_path, y_path)]
            with mock.patch.object(guarded_fir, "AUDIT", audit.resolve()):
                out = audit/"voice-aec/M5-guarded-001"
                result = guarded_fir.analyze(capture_dir, out)
                self.assertFalse(result["virtual_microphone_approved"])
                self.assertFalse(result["near_end_voice_preservation_approved"])
                self.assertTrue(result["frozen_original_speaker_only_gate_passes"])
                self.assertEqual(result["guard"]["cutoff_hz"], 3800)
                self.assertIsNotNone(result["double_talk"])
                self.assertEqual(len(result["double_talk"]["bands"]), 6)
                self.assertTrue((out/"guarded-fir.json").is_file())
                self.assertTrue((out/"SHA256SUMS").is_file())
                self.assertEqual(list(out.glob("*.wav")), [])
                with self.assertRaises(FileExistsError):
                    guarded_fir.analyze(capture_dir, out)
                self.assertEqual(
                    initial, [aec.digest(p) for p in (
                        manifest_file, x_path, y_path)])
                with x_path.open("ab") as file:
                    file.write(b"tamper")
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    guarded_fir.analyze(
                        capture_dir, audit/"voice-aec/M5-guarded-002")

    def test_known_clean_stem_metrics_preserve_exact_injected_signal(self):
        """Known stem SNR must measure the actual error to clean voice."""
        rng = np.random.default_rng(77129)
        clean = rng.normal(0, .009, 3*RATE)
        noise = rng.normal(0, .003, 3*RATE)
        clean_before = clean.copy()
        original = clean + noise
        modeled = .70*noise
        full_output = original-modeled
        guarded_output = original-.65*noise
        metrics = known_stem.clean_stem_score(
            clean, original, full_output, guarded_output)
        self.assertGreater(metrics["full_fir_subtraction"]
                           ["exact_stem_snr_improvement_db"], 9)
        self.assertGreater(metrics["guarded_fir_subtraction"]
                           ["exact_stem_snr_improvement_db"], 8)
        np.testing.assert_array_equal(clean, clean_before)
        bands = known_stem.error_by_band(clean, {
            "original": original,
            "full_fir": full_output,
            "echo_guard": guarded_output})
        self.assertEqual(len(bands), 6)
        self.assertTrue(all(math.isfinite(
            item["echo_guard_exact_voice_snr_db"]) for item in bands))
        with self.assertRaises(ValueError):
            known_stem.clean_stem_score(
                clean, original[:-1], full_output, guarded_output)

    def test_m5_known_stem_end_to_end_hash_guard_and_no_audio_writes(self):
        """ABA and M4 originals stay byte-identical; emit only numeric JSON."""
        rng = np.random.default_rng(77130)
        x = rng.normal(0, .05, 20*RATE)
        lag = round((8.0+.14)*RATE)
        mic = rng.normal(0, .0008, 36*RATE)
        mic[lag:lag+len(x)] += .19*x
        # Authentic M4 ALSA capture protocol: 5s silent lead-in.
        voice = rng.normal(0, .006, 15*RATE)
        voice[:6*RATE] *= .02
        with tempfile.TemporaryDirectory() as root:
            audit = Path(root)/".audit"
            ref_dir = audit/"voice-aec/M5-006"
            aba_dir = audit/"voice-aec/M5-ABA-001"
            m4_dir = audit/"voice-captures/M4-R0-ALSA-007"
            ref_dir.mkdir(parents=True)
            aba_dir.mkdir(parents=True)
            m4_dir.mkdir(parents=True)
            x_path = ref_dir/"reference-sent.wav"
            mic_path = aba_dir/"microphone-aba-raw.wav"
            voice_path = m4_dir/"raw.wav"
            pcm16_file(x_path, x)
            pcm16_file(mic_path, mic)
            pcm16_file(voice_path, voice)
            rm = {
                "schema": "cuewire-M5-paired-physical-capture-v1",
                "status": "captured_for_offline_aec",
                "reference": {"sha256": aec.digest(x_path)},
                "mic_capture": {"mic_source_name": "alsa_input.usb-test"},
                "speaker_sink_name": "alsa_output.pci-test",
                "volume_pw_play": .30,
            }
            rm_path = ref_dir/"manifest.json"
            rm_path.write_text(json.dumps(rm))
            am = {
                "schema": "cuewire-M5-continuous-ABA-control-v1",
                "status": "captured_aba_for_noise_diagnostics",
                "reference_source_capture": str(ref_dir),
                "reference_source_manifest_sha256": aec.digest(rm_path),
                "reference_wav_sha256": aec.digest(x_path),
                "source_mic_name": "alsa_input.usb-test",
                "speaker_sink_name": "alsa_output.pci-test",
                "speaker_play_volume": .30,
                "playback_start_after_recorder_launch_s": 8.0,
                "microphone": {"sha256": aec.digest(mic_path)},
            }
            am_path = aba_dir/"manifest.json"
            am_path.write_text(json.dumps(am))
            vm = {
                "schema": "cuewire-M4-raw-alsa-v1",
                "status": "recorded_unprocessed_raw",
                "source": "physical_alsa_hw",
                "device": "hw:1,0",
                "card_id": "MICAudio97i",
                "raw_wav": "raw.wav",
                "raw_sha256": aec.digest(voice_path),
                "processing": {
                    "aec": False, "denoise": False, "eq": False,
                    "compression": False, "virtual_mic": False,
                    "normalization": False, "monitoring": False
                },
            }
            vm_path = m4_dir/"manifest.json"
            vm_path.write_text(json.dumps(vm))
            original_paths = [x_path, mic_path, voice_path,
                              rm_path, am_path, vm_path]
            original_hashes = [aec.digest(p) for p in original_paths]
            with (mock.patch.object(known_stem, "AUDIT", audit.resolve()),
                  mock.patch.object(frozen_heldout, "AUDIT", audit.resolve())):
                out = audit/"voice-aec/M5-known-stem-001"
                report = known_stem.analyze(aba_dir, m4_dir, out)
                self.assertEqual(
                    report["status"],
                    "research_only_known_stem_synthetic_doubletalk")
                self.assertFalse(report["approved_for_virtual_mic"])
                self.assertFalse(
                    report["voice_preservation_validated_under_real_acoustics"])
                self.assertGreater(
                    report["speaker_only_heldout_erle_db"], 1.0)
                self.assertEqual(report["voice_source_capture_schema"],
                                 "cuewire-M4-raw-alsa-v1")
                self.assertEqual(report["voice_source_capture_backend"],
                                 "physical_alsa_hw")
                self.assertEqual(report["voice_excerpt_start_s"], 6.0)
                self.assertTrue((out/"known-stem.json").exists())
                self.assertTrue((out/"SHA256SUMS").exists())
                self.assertEqual(list(out.glob("*.wav")), [])
                self.assertEqual(original_hashes,
                                 [aec.digest(p) for p in original_paths])
                with self.assertRaises(FileExistsError):
                    known_stem.analyze(aba_dir, m4_dir, out)
                with voice_path.open("ab") as file:
                    file.write(b"corrupted")
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    known_stem.analyze(
                        aba_dir, m4_dir, audit/"voice-aec/M5-known-stem-002")

    def test_known_voice_m4_manifest_schema_strict_and_long_alsa_excerpt(self):
        """Real M4 ALSA v1 and later PipeWire v2 have different SHA keys."""
        alsa = {
            "schema": "cuewire-M4-raw-alsa-v1",
            "status": "recorded_unprocessed_raw",
            "source": "physical_alsa_hw",
            "device": "hw:2,0",
            "card_id": "MICAudio97i",
            "raw_wav": "raw.wav",
            "raw_sha256": "a"*64,
            "processing": {
                "aec": False, "denoise": False, "eq": False,
                "compression": False, "virtual_mic": False,
                "normalization": False, "monitoring": False,
            },
            "measurements": {"rate": 48000, "channels": 1,
                             "sample_width_bytes": 2, "clipped_samples": 0}
        }
        h, schema, kind = known_stem.validate_m4_voice_manifest(alsa)
        self.assertEqual((h, schema, kind),
                         ("a"*64, "cuewire-M4-raw-alsa-v1",
                          "physical_alsa_hw"))
        bad = dict(alsa, raw_sha256="wrong-hash")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            known_stem.validate_m4_voice_manifest(bad)
        bad = dict(alsa, source="not-physical")
        with self.assertRaisesRegex(ValueError, "physical"):
            known_stem.validate_m4_voice_manifest(bad)
        bad = dict(alsa, processing={**alsa["processing"], "aec": True})
        with self.assertRaisesRegex(ValueError, "unprocessed"):
            known_stem.validate_m4_voice_manifest(bad)
        pw = {
            "schema": "cuewire-M4-raw-capture-v2",
            "status": "recorded_unprocessed_raw",
            "mic_node_name": "alsa_input.usb-MICAudio97i",
            "wav": "raw.wav",
            "wav_sha256": "b"*64,
            "explicit_processing": {
                "aec": False, "denoise": False, "eq": False,
                "compression": False, "manual_gain_normalization": False,
                "playback": False, "virtual_microphone": False,
            }
        }
        self.assertEqual(known_stem.validate_m4_voice_manifest(pw),
                         ("b"*64, "cuewire-M4-raw-capture-v2",
                          "physical_pipewire"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"long-alsa.wav"
            rng = np.random.default_rng(70001)
            pcm16_file(path, rng.normal(0, .005, 52*RATE))
            excerpt = known_stem.read_voice_excerpt(path, 6.0)
            self.assertEqual(len(excerpt), 6*RATE)
            self.assertGreater(aec.rms(excerpt), .003)
            with self.assertRaisesRegex(ValueError, "too short"):
                known_stem.read_voice_excerpt(path, 50.0)

    def test_m5_lag_postmortem_known_fixed_lag_early_windows(self):
        """Diagnostic must expose stable known time shift, not fit/override it."""
        rng = np.random.default_rng(88114)
        reference = rng.normal(0, .05, 11*RATE)
        lag = round(.2813*RATE)
        mic = rng.normal(0, .0005, 12*RATE)
        mic[lag:lag+len(reference)] += reference*.16
        for name, bounds in lag_postmortem.PROBE_BANDS:
            if bounds is None:
                xx, yy = reference, mic
            else:
                sos = signal.butter(4, bounds, fs=RATE, output="sos",
                                    btype="bandpass")
                xx = signal.sosfiltfilt(sos, reference)
                yy = signal.sosfiltfilt(sos, mic)
            result = lag_postmortem.correlation_probe(
                xx, yy, (3.0, 4.5), lag)
            self.assertAlmostEqual(
                result["reported_original_lag_ms"],
                1000*lag/RATE, places=4)
            self.assertLess(abs(
                result["diagnostic_top_separated_peaks"][0]["lag_ms"] -
                1000*lag/RATE), .09)
            self.assertGreater(result["absolute_correlation_at_original_lag"], .95)
            # A multi-second search has abundant alternatives outside the
            # excluded +/-3ms peak neighborhood. Keep the top-three list
            # complete instead of accidentally reporting unique peaks.
            peaks = result["diagnostic_top_separated_peaks"]
            self.assertEqual(len(peaks), 3)
            # Regression: 3 milliseconds == 144 frames, NOT 3 seconds.
            for i, peak in enumerate(peaks):
                for earlier in peaks[:i]:
                    self.assertGreaterEqual(
                        abs(peak["lag_ms"] - earlier["lag_ms"]),
                        lag_postmortem.PEAK_SEPARATION_MS - 0.00002)
            self.assertIsNotNone(result["first_minus_second_peak_abs_correlation"])
            self.assertGreaterEqual(result["first_minus_second_peak_abs_correlation"], 0)
            self.assertTrue(result["diagnostic_not_a_retiming"])
        with self.assertRaisesRegex(ValueError, "outside available PCM"):
            lag_postmortem.correlation_probe(
                reference, mic, (10, 12), lag)

    def test_m5_failed_prospective_lag_postmortem_no_gate_override(self):
        """Only immutable source hashes, early windows, numeric report."""
        rng = np.random.default_rng(88115)
        reference = rng.normal(0, .055, 32*RATE)
        lag = round(.28*RATE)
        mic = rng.normal(0, .001, 33*RATE)
        mic[lag:lag+len(reference)] += .17*reference
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp)/".audit"
            capture_dir = audit/"voice-aec/M5-doubletalk-002"
            prev_dir = audit/"voice-aec/M5-guarded-fir-prospective-002"
            capture_dir.mkdir(parents=True)
            prev_dir.mkdir(parents=True)
            ref_path = capture_dir/"reference-sent.wav"
            mic_path = capture_dir/"microphone-raw.wav"
            pcm16_file(ref_path, reference)
            pcm16_file(mic_path, mic)
            meta = {
                "schema": "cuewire-M5-paired-physical-capture-v1",
                "status": "captured_for_offline_aec",
                "requested_duration_s": 32,
                "speaker_only_calibration_s": 12,
                "mic_capture": {"mic_source_name": "alsa_input.usb-test"},
                "speaker_sink_name": "alsa_output.pci-test",
                "reference": {"sha256": aec.digest(ref_path), "frames": 32*RATE},
                "microphone": {"sha256": aec.digest(mic_path)},
            }
            manifest_path = capture_dir/"manifest.json"
            manifest_path.write_text(json.dumps(meta))
            hashes = {
                "capture_manifest": aec.digest(manifest_path),
                "digital_reference": aec.digest(ref_path),
                "physical_mic_raw": aec.digest(mic_path),
            }
            prior = {
                "schema": "cuewire-M5-doubletalk-guarded-fir-ablation-v1",
                "frozen_original_speaker_only_gate_passes": False,
                "double_talk": None,
                "estimated_alignment_lag_ms": round(lag*1000/RATE, 5),
                "input_sha256": hashes,
                "speaker_only": {"guarded_fir_erle_db": .67881}
            }
            old_file = prev_dir/"guarded-fir.json"
            old_file.write_text(json.dumps(prior))
            originals = [aec.digest(p) for p in
                         (ref_path, mic_path, manifest_path, old_file)]
            with mock.patch.object(lag_postmortem, "AUDIT", audit.resolve()):
                out = audit/"voice-aec/M5-lag-postmortem-002"
                result = lag_postmortem.analyze(
                    capture_dir, old_file, out)
                self.assertEqual(
                    result["status"],
                    "research_postmortem_no_model_or_gate_changes")
                self.assertFalse(result["original_full_model_gate"])
                self.assertEqual(
                    result["original_reported_speaker_only_erle_db"], .67881)
                self.assertEqual(len(result["bands"]), 3)
                self.assertTrue((out/"lag-postmortem.json").exists())
                self.assertTrue((out/"SHA256SUMS").exists())
                self.assertEqual(list(out.glob("*.wav")), [])
                with self.assertRaises(FileExistsError):
                    lag_postmortem.analyze(capture_dir, old_file, out)
                self.assertEqual(
                    originals, [aec.digest(p) for p in
                                (ref_path, mic_path, manifest_path, old_file)])
                with mic_path.open("ab") as file:
                    file.write(b"tamper")
                with self.assertRaisesRegex(ValueError, "hashes"):
                    lag_postmortem.analyze(
                        capture_dir, old_file,
                        audit/"voice-aec/M5-lag-postmortem-003")

    def test_m5_early_cross_take_fixed_lag_does_not_fit_from_holdout(self):
        """No timing search; exact old/new early mic coherence and noise."""
        rng = np.random.default_rng(98010)
        x = rng.normal(0, .055, 12*RATE)
        y_old = rng.normal(0, .0006, 13*RATE)
        y_new = rng.normal(0, .0006, 13*RATE)
        lag_old, lag_new = 4690, 13502
        y_old[lag_old:lag_old+len(x)] += .15*x
        y_new[lag_new:lag_new+len(x)] += .02*x
        first = cross_take.window_statistics(
            x, y_old, lag_old, 3.0, 4.5)
        second = cross_take.window_statistics(
            x, y_new, lag_new, 3.0, 4.5)
        self.assertGreater(
            first["microphone_total_rms_dbfs"] -
            second["microphone_total_rms_dbfs"], 7.0)
        old_band = next(b for b in first["bands"]
                        if b["hz"] == [1500, 4000])
        new_band = next(b for b in second["bands"]
                        if b["hz"] == [1500, 4000])
        self.assertGreater(
            old_band["mic_power_weighted_coherence"],
            new_band["mic_power_weighted_coherence"])
        self.assertEqual(first["frozen_original_lag_ms"],
                         round(lag_old*1000/RATE, 5))
        self.assertEqual(second["frozen_original_lag_ms"],
                         round(lag_new*1000/RATE, 5))
        with self.assertRaisesRegex(ValueError, "outside available WAV"):
            cross_take.window_statistics(x, y_new, lag_new, 13., 14.)

    def test_m5_early_cross_take_pinned_hashes_and_numeric_only(self):
        """Two strictly pinned captures, no processing or output WAV."""
        rng = np.random.default_rng(98011)
        ref = rng.normal(0, .05, 32*RATE)
        old_mic = rng.normal(0, .0008, 33*RATE)
        new_mic = rng.normal(0, .0008, 33*RATE)
        old_mic[cross_take.OLD_LAG_FRAMES:
                cross_take.OLD_LAG_FRAMES+len(ref)] += .2*ref
        new_mic[cross_take.NEW_LAG_FRAMES:
                cross_take.NEW_LAG_FRAMES+len(ref)] += .04*ref
        with tempfile.TemporaryDirectory() as root:
            audit = Path(root)/".audit"
            first = audit/"voice-aec/M5-doubletalk-001"
            second = audit/"voice-aec/M5-doubletalk-002"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            names = []
            for folder, m in ((first, old_mic), (second, new_mic)):
                xr = folder/"reference-sent.wav"
                yr = folder/"microphone-raw.wav"
                pcm16_file(xr, ref)
                pcm16_file(yr, m)
                names.extend([xr, yr])
                meta = {
                    "schema": "cuewire-M5-paired-physical-capture-v1",
                    "status": "captured_for_offline_aec",
                    "requested_duration_s": 32,
                    "speaker_only_calibration_s": 12,
                    "mic_capture": {
                        "backend": "pipewire",
                        "mic_source_name": "alsa_input.usb-microphone",
                        "mic_source_id_at_start": 10788,
                        "mic_source_sample_spec": "s16le 1ch 48000Hz",
                    },
                    "speaker_sink_name": "alsa_output.pci-speaker",
                    "speaker_sink_id_at_start": 1836,
                    "volume_pw_play": .30,
                    "reference": {"frames": 32*RATE,
                                  "sha256": aec.digest(xr)},
                    "microphone": {"sha256": aec.digest(yr)}
                }
                mf = folder/"manifest.json"
                mf.write_text(json.dumps(meta))
                names.append(mf)
            sha_before = [aec.digest(p) for p in names]
            with (mock.patch.object(cross_take, "AUDIT", audit.resolve()),
                  mock.patch.object(cross_take, "OLD_MANIFEST_SHA",
                                    aec.digest(first/"manifest.json")),
                  mock.patch.object(cross_take, "NEW_MANIFEST_SHA",
                                    aec.digest(second/"manifest.json")),
                  mock.patch.object(cross_take, "OLD_MIC_SHA",
                                    aec.digest(first/"microphone-raw.wav")),
                  mock.patch.object(cross_take, "NEW_MIC_SHA",
                                    aec.digest(second/"microphone-raw.wav")),
                  mock.patch.object(cross_take, "REFERENCE_SHA",
                                    aec.digest(first/"reference-sent.wav"))):
                out = audit/"voice-aec/M5-cross-take-calibration-001"
                result = cross_take.compare(first, second, out)
                self.assertEqual(
                    result["status"],
                    "postmortem_only_prospective_gate_still_failed")
                self.assertEqual(len(result["comparisons"]), 3)
                self.assertFalse(result["aec_fitting_or_retiming_performed"])
                self.assertTrue(result["speech_doubletalk_not_analyzed"])
                self.assertTrue((out/"cross-take-calibration.json").is_file())
                self.assertTrue((out/"SHA256SUMS").is_file())
                self.assertEqual(list(out.glob("*.wav")), [])
                self.assertEqual(sha_before, [aec.digest(p) for p in names])
                with self.assertRaises(FileExistsError):
                    cross_take.compare(first, second, out)
                with (second/"microphone-raw.wav").open("ab") as f:
                    f.write(b"tamper")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    cross_take.compare(
                        first, second,
                        audit/"voice-aec/M5-cross-take-calibration-002")

    def test_playback_coherence_proxy_tracks_nominal_amplitude(self):
        """Reference-linked power proxy rises ~6dB for doubled echo amp."""
        rng = np.random.default_rng(901441)
        x = rng.normal(0, .07, 5*RATE)
        noise = rng.normal(0, .0015, 5*RATE)
        low = .08*x+noise
        high = .16*x+noise
        l = playback_coherence.inspect_spectral_window(x, low)
        h = playback_coherence.inspect_spectral_window(x, high)
        ll = next(b for b in l["bands"] if b["band_hz"] == [1500, 4000])
        hh = next(b for b in h["bands"] if b["band_hz"] == [1500, 4000])
        gain = (
            hh["linear_reference_linked_power_proxy_dbfs"] -
            ll["linear_reference_linked_power_proxy_dbfs"])
        self.assertGreater(gain, 5.5)
        self.assertLess(gain, 6.5)
        self.assertGreater(
            hh["mic_psd_weighted_magnitude_squared_coherence"], .75)
        self.assertEqual(len(l["bands"]), len(playback_coherence.BANDS))
        with self.assertRaisesRegex(ValueError, "equal-length"):
            playback_coherence.inspect_spectral_window(x, low[:-1])

    def test_playback_coherence_full_sha_pinned_numeric_only(self):
        """Prior two music volumes, no device opens or source mutation."""
        rng = np.random.default_rng(901442)
        music = rng.normal(0, .06, 20*RATE)
        delay = round(8.20*RATE)
        m_low = rng.normal(0, .0007, 36*RATE)
        m_high = rng.normal(0, .0007, 36*RATE)
        m_low[delay:delay+len(music)] += .08*music
        m_high[delay:delay+len(music)] += .16*music
        with tempfile.TemporaryDirectory() as root:
            audit = Path(root)/".audit"
            digital = audit/"voice-aec/M5-006"
            low = audit/"voice-aec/M5-level-015-001"
            high = audit/"voice-aec/M5-level-030-001"
            for directory in (digital, low, high):
                directory.mkdir(parents=True)
            ref_path = digital/"reference-sent.wav"
            pcm16_file(ref_path, music)
            digital_manifest = digital/"manifest.json"
            digital_manifest.write_text('{"source":"test"}')
            for path, data, volume in (
                    (low, m_low, .15), (high, m_high, .30)):
                wav = path/"microphone-aba-raw.wav"
                pcm16_file(wav, data)
                manifest = {
                    "schema": "cuewire-M5-continuous-ABA-control-v1",
                    "status": "captured_aba_for_noise_diagnostics",
                    "source_mic_sample_spec": "s16le 1ch 48000Hz",
                    "source_mic_name": "alsa_input.usb-test",
                    "speaker_sink_name": "alsa_output.pci-test",
                    "speaker_play_volume": volume,
                    "reference_source_capture": str(digital),
                    "reference_source_manifest_sha256":
                        aec.digest(digital_manifest),
                    "reference_wav_sha256": aec.digest(ref_path),
                    "reference_duration_s": 20.0,
                    "requested_speaker_off_before_s": 8,
                    "requested_speaker_off_after_s": 8,
                    "microphone": {"sha256": aec.digest(wav)},
                    "playback_start_after_recorder_launch_s": 8.0,
                    "phase_analysis": {
                        "windows": {
                            "before_speaker_on":
                                {"segment_rms_dbfs": -60.},
                            "after_speaker_off":
                                {"segment_rms_dbfs": -60.},
                            "speaker_on": {
                                "segment_rms_dbfs": -50.}
                        }
                    }
                }
                (path/"manifest.json").write_text(json.dumps(manifest))
            old_paths = [ref_path, digital_manifest,
                         low/"microphone-aba-raw.wav", low/"manifest.json",
                         high/"microphone-aba-raw.wav", high/"manifest.json"]
            old_hashes = [aec.digest(p) for p in old_paths]
            with (mock.patch.object(playback_coherence, "AUDIT", audit.resolve()),
                  mock.patch.object(playback_coherence, "LOW_MANIFEST_SHA",
                                    aec.digest(low/"manifest.json")),
                  mock.patch.object(playback_coherence, "HIGH_MANIFEST_SHA",
                                    aec.digest(high/"manifest.json")),
                  mock.patch.object(playback_coherence, "LOW_MIC_SHA",
                                    aec.digest(low/"microphone-aba-raw.wav")),
                  mock.patch.object(playback_coherence, "HIGH_MIC_SHA",
                                    aec.digest(high/"microphone-aba-raw.wav")),
                  mock.patch.object(playback_coherence, "REFERENCE_SHA",
                                    aec.digest(ref_path)),
                  mock.patch.object(playback_coherence, "SOURCE_MANIFEST_SHA",
                                    aec.digest(digital_manifest))):
                out = audit/"voice-aec/M5-playback-coherence-001"
                report = playback_coherence.analyze(low, high, out)
                self.assertEqual(len(report["per_band_change"]), 2)
                self.assertFalse(report["voice_recorded_or_analyzed"])
                self.assertTrue(report["no_aec_filter_fitted"])
                self.assertTrue(report[
                    "prospective_prior_gate_0_67881_db_remains_failed"])
                self.assertTrue((out/"playback-coherence.json").is_file())
                self.assertTrue((out/"SHA256SUMS").is_file())
                self.assertEqual(list(out.glob("*.wav")), [])
                band = next(b for b in report["per_band_change"][0]["bands"]
                            if b["band_hz"] == [1500, 4000])
                self.assertGreater(
                    band["linked_power_proxy_change_030_minus_015_db"], 5.0)
                with self.assertRaises(FileExistsError):
                    playback_coherence.analyze(low, high, out)
                self.assertEqual(old_hashes, [aec.digest(p) for p in old_paths])
                with (high/"microphone-aba-raw.wav").open("ab") as f:
                    f.write(b"tamper")
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    playback_coherence.analyze(
                        low, high,
                        audit/"voice-aec/M5-playback-coherence-002")

    def test_echo_floor_synthetic_exact_subtraction_reduces_linked_proxy(self):
        """Known echo removal reduces linked power without filtering the mic."""
        rng = np.random.default_rng(903301)
        x = rng.normal(0., .05, 6*RATE)
        floor = rng.normal(0., .0015, 6*RATE)
        mic = floor + .15*x
        spectral = echo_floor.linear_spectral_proxy(
            x, mic, floor)
        self.assertEqual(len(spectral["bands"]), 6)
        good = next(row for row in spectral["bands"]
                    if row["band_hz"] == [1500, 4000])
        self.assertGreater(good["mic_weighted_coherence"], .95)
        self.assertLess(good["residual_mic_weighted_coherence"], .10)
        self.assertGreater(
            good["linked_proxy_before_minus_after_db"], 14.)
        self.assertGreater(good["band_erle_db"], 14.)
        self.assertAlmostEqual(
            spectral["residual_total_rms_dbfs"], aec.db(aec.rms(floor)),
            delta=.001)
        with self.assertRaisesRegex(ValueError, "equal-length"):
            echo_floor.linear_spectral_proxy(x, mic[:-1], floor)
        with self.assertRaisesRegex(ValueError, ">=4 seconds"):
            echo_floor.linear_spectral_proxy(
                x[:RATE], mic[:RATE], floor[:RATE])

    def test_echo_floor_two_existing_takes_hash_guard_and_json_only(self):
        """Original two physical takes stay immutable, with OFF null control."""
        rng = np.random.default_rng(903302)
        ref = rng.normal(0., .05, 20*RATE)
        lag = round(8.13*RATE)
        original = []
        with tempfile.TemporaryDirectory() as temp:
            audit = Path(temp)/".audit"
            digital = audit/"voice-aec/M5-006"
            lo = audit/"voice-aec/M5-level-015-001"
            hi = audit/"voice-aec/M5-level-030-001"
            for folder in (digital, lo, hi):
                folder.mkdir(parents=True)
            ref_path = digital/"reference-sent.wav"
            pcm16_file(ref_path, ref)
            ref_mf = digital/"manifest.json"
            ref_mf.write_text('{"synthetic_source":"fixed"}')
            for folder, volume, coeff in ((lo, .15, .08), (hi, .30, .16)):
                noise = rng.normal(0., .0009, 36*RATE)
                noise[lag:lag+len(ref)] += coeff*ref
                wav = folder/"microphone-aba-raw.wav"
                pcm16_file(wav, noise)
                m = {
                    "schema": "cuewire-M5-continuous-ABA-control-v1",
                    "status": "captured_aba_for_noise_diagnostics",
                    "source_mic_sample_spec": "s16le 1ch 48000Hz",
                    "source_mic_name": "alsa_input.usb-synthetic",
                    "speaker_sink_name": "alsa_output.pci-synthetic",
                    "speaker_play_volume": volume,
                    "reference_source_capture": str(digital),
                    "reference_source_manifest_sha256": aec.digest(ref_mf),
                    "reference_wav_sha256": aec.digest(ref_path),
                    "reference_duration_s": 20.,
                    "requested_speaker_off_before_s": 8,
                    "requested_speaker_off_after_s": 8,
                    "microphone": {"sha256": aec.digest(wav)},
                    "playback_start_after_recorder_launch_s": 8.0,
                }
                (folder/"manifest.json").write_text(json.dumps(m))
            files = [ref_path, ref_mf,
                     lo/"microphone-aba-raw.wav", lo/"manifest.json",
                     hi/"microphone-aba-raw.wav", hi/"manifest.json"]
            original = [aec.digest(p) for p in files]
            corrections = [
                mock.patch.object(echo_floor, "AUDIT", audit.resolve()),
                mock.patch.object(playback_coherence, "AUDIT", audit.resolve()),
                mock.patch.object(echo_floor, "LOW_MANIFEST_SHA",
                                  aec.digest(lo/"manifest.json")),
                mock.patch.object(echo_floor, "HIGH_MANIFEST_SHA",
                                  aec.digest(hi/"manifest.json")),
                mock.patch.object(echo_floor, "LOW_MIC_SHA",
                                  aec.digest(lo/"microphone-aba-raw.wav")),
                mock.patch.object(echo_floor, "HIGH_MIC_SHA",
                                  aec.digest(hi/"microphone-aba-raw.wav")),
                mock.patch.object(echo_floor, "REFERENCE_SHA",
                                  aec.digest(ref_path)),
                mock.patch.object(echo_floor, "SOURCE_MANIFEST_SHA",
                                  aec.digest(ref_mf)),
                mock.patch.object(playback_coherence, "REFERENCE_SHA",
                                  aec.digest(ref_path)),
                mock.patch.object(playback_coherence, "SOURCE_MANIFEST_SHA",
                                  aec.digest(ref_mf)),
            ]
            with (corrections[0], corrections[1], corrections[2],
                  corrections[3], corrections[4], corrections[5],
                  corrections[6], corrections[7], corrections[8],
                  corrections[9]):
                dest = audit/"voice-aec/M5-echo-floor-001"
                report = echo_floor.analyze(lo, hi, dest)
                self.assertFalse(report["voice_preservation_tested"])
                self.assertFalse(report["approved_for_virtual_microphone"])
                self.assertTrue(report[
                    "original_prospective_0_67881_db_gate_still_failed"])
                self.assertEqual(list(dest.glob("*.wav")), [])
                self.assertTrue((dest/"echo-floor.json").is_file())
                self.assertTrue((dest/"SHA256SUMS").is_file())
                self.assertEqual(len(report["low_volume"]["background"][
                    "band_noise_and_null"]), 6)
                self.assertGreater(
                    report["high_volume"]["speech_band_proxies"][
                        "full_fir"]["linked_proxy_before_dbfs"] -
                    report["low_volume"]["speech_band_proxies"][
                        "full_fir"]["linked_proxy_before_dbfs"], 5.5)
                for take in (report["low_volume"], report["high_volume"]):
                    self.assertGreater(
                        take["unfiltered_fullband"]["full_band_erle_db"], 1.)
                    self.assertGreater(
                        take["speech_band_proxies"]["full_fir"][
                            "linked_proxy_suppression_db"], 5.)
                with self.assertRaises(FileExistsError):
                    echo_floor.analyze(lo, hi, dest)
                self.assertEqual(original, [aec.digest(p) for p in files])
                with (hi/"microphone-aba-raw.wav").open("ab") as fd:
                    fd.write(b"tampered")
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    echo_floor.analyze(
                        lo, hi, audit/"voice-aec/M5-echo-floor-002")

    def test_off_floor_windows_distinguish_transient_from_median_shift(self):
        """One high-frequency 0.3s burst must not masquerade as steady floor."""
        rng = np.random.default_rng(451122)
        pcm = rng.normal(0, .0002, 36*RATE)
        a, b = round(2.1*RATE), round(2.4*RATE)
        t = np.arange(b-a) / RATE
        pcm[a:b] += .025*np.sin(2*np.pi*11000*t)
        before = off_windows.split_level(pcm, 2.0, 7.0)
        after = off_windows.split_level(pcm, 30.0, 35.0)
        self.assertEqual(before["num_windows"], 10)
        high_pre = next(x for x in before["band_windows"]
                        if x["band_hz"] == [8000, 16000])
        high_post = next(x for x in after["band_windows"]
                         if x["band_hz"] == [8000, 16000])
        self.assertGreater(
            high_pre["max_half_second_power_dbfs"]-
            high_pre["median_half_second_power_dbfs"], 15)
        self.assertGreater(
            high_pre["maximum_window_fraction_of_phase_power"], .9)
        self.assertEqual(high_pre["count_windows_over_median_plus_6_db"], 1)
        self.assertLess(high_post["maximum_window_fraction_of_phase_power"], .2)
        self.assertLess(abs(
            high_pre["median_half_second_power_dbfs"]-
            high_post["median_half_second_power_dbfs"]), 2)
        with self.assertRaisesRegex(ValueError, "exactly 5s"):
            off_windows.split_level(pcm, 2., 6.)

    def test_off_floor_windows_only_numeric_and_strict_source_hash(self):
        """Two existing captures remain intact, no holdout or audio output."""
        rng = np.random.default_rng(451123)
        with tempfile.TemporaryDirectory() as temp:
            audit = Path(temp)/".audit"
            digital = audit/"voice-aec/M5-006"
            lo = audit/"voice-aec/M5-level-015-001"
            hi = audit/"voice-aec/M5-level-030-001"
            for folder in (digital, lo, hi):
                folder.mkdir(parents=True)
            ref_file = digital/"reference-sent.wav"
            pcm16_file(ref_file, rng.normal(0, .05, 20*RATE))
            ref_manifest = digital/"manifest.json"
            ref_manifest.write_text('{"synthetic":true}')
            for folder, volume in ((lo, .15),(hi, .30)):
                mic_file=folder/"microphone-aba-raw.wav"
                pcm16_file(mic_file,rng.normal(0,.0005,36*RATE))
                meta={
                    "schema":"cuewire-M5-continuous-ABA-control-v1",
                    "status":"captured_aba_for_noise_diagnostics",
                    "source_mic_sample_spec":"s16le 1ch 48000Hz",
                    "source_mic_name":"alsa_input.usb-synthetic",
                    "speaker_sink_name":"alsa_output.pci-synthetic",
                    "speaker_play_volume":volume,
                    "reference_source_capture":str(digital),
                    "reference_source_manifest_sha256":aec.digest(ref_manifest),
                    "reference_wav_sha256":aec.digest(ref_file),
                    "reference_duration_s":20.0,
                    "requested_speaker_off_before_s":8,
                    "requested_speaker_off_after_s":8,
                    "microphone":{"sha256":aec.digest(mic_file)}
                }
                (folder/"manifest.json").write_text(json.dumps(meta))
            files=[ref_file,ref_manifest,
                   lo/"manifest.json",lo/"microphone-aba-raw.wav",
                   hi/"manifest.json",hi/"microphone-aba-raw.wav"]
            old=[aec.digest(p) for p in files]
            with (mock.patch.object(off_windows,"AUDIT",audit.resolve()),
                  mock.patch.object(playback_coherence,"AUDIT",audit.resolve()),
                  mock.patch.object(off_windows,"LOW_MANIFEST_SHA",aec.digest(lo/"manifest.json")),
                  mock.patch.object(off_windows,"HIGH_MANIFEST_SHA",aec.digest(hi/"manifest.json")),
                  mock.patch.object(off_windows,"LOW_MIC_SHA",aec.digest(lo/"microphone-aba-raw.wav")),
                  mock.patch.object(off_windows,"HIGH_MIC_SHA",aec.digest(hi/"microphone-aba-raw.wav")),
                  mock.patch.object(off_windows,"REFERENCE_SHA",aec.digest(ref_file)),
                  mock.patch.object(off_windows,"SOURCE_MANIFEST_SHA",aec.digest(ref_manifest)),
                  mock.patch.object(playback_coherence,"REFERENCE_SHA",aec.digest(ref_file)),
                  mock.patch.object(playback_coherence,"SOURCE_MANIFEST_SHA",aec.digest(ref_manifest))):
                output=audit/"voice-aec/M5-off-floor-windows-001"
                report=off_windows.analyze(lo,hi,output)
                self.assertTrue((output/"off-floor-windows.json").exists())
                self.assertTrue((output/"SHA256SUMS").exists())
                self.assertEqual(list(output.glob("*.wav")),[])
                self.assertFalse(report["aec_fir_fitted_or_modified"])
                self.assertFalse(report["voice_preservation_validated"])
                self.assertTrue(report[
                    "historical_0_67881_db_prospective_gate_remains_failed"])
                self.assertEqual(
                    len(report["high_volume"]["before_after_band_comparison"]),6)
                self.assertEqual(old,[aec.digest(p) for p in files])
                with self.assertRaises(FileExistsError):
                    off_windows.analyze(lo,hi,output)
                with (hi/"microphone-aba-raw.wav").open("ab") as fd:
                    fd.write(b"tamper")
                with self.assertRaisesRegex(ValueError,"SHA256"):
                    off_windows.analyze(
                        lo,hi,audit/"voice-aec/M5-off-floor-windows-002")

    def test_off_spectral_fingerprint_tone_vs_broadband_burst(self):
        """Known 11kHz tone has concentrated spectrum vs broadband noise."""
        rng = np.random.default_rng(674901)
        t = np.arange(RATE//2)/RATE
        stable = rng.normal(0, .0005, RATE//2)
        burst_tone = stable + .012*np.sin(2*np.pi*11000*t)
        burst_broad = stable + rng.normal(0, .012, RATE//2)
        base = off_fingerprint.window_fingerprint(stable)
        tone = off_fingerprint.window_fingerprint(burst_tone)
        broad = off_fingerprint.window_fingerprint(burst_broad)
        self.assertLess(
            abs(tone["highest_welch_psd_frequency_hz"]-11000), 20)
        self.assertGreater(
            tone["top_one_percent_frequency_bins_power_fraction"],
            broad["top_one_percent_frequency_bins_power_fraction"]+.3)
        self.assertLess(tone["spectral_flatness_0_to_1"],
                        broad["spectral_flatness_0_to_1"]*.3)
        self.assertGreater(
            tone["full_highband_power_dbfs"],
            base["full_highband_power_dbfs"]+15)
        self.assertAlmostEqual(
            sum(x["fraction_of_8_16khz_spectral_power"]
                for x in tone["subbands"]), 1, delta=1e-5)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            off_fingerprint.window_fingerprint(stable[:-1])

    def test_off_spectral_fingerprint_source_hashes_and_json_only(self):
        """All 40 OFF-only windows; captures stay unchanged and private."""
        rng = np.random.default_rng(674902)
        with tempfile.TemporaryDirectory() as root:
            audit=Path(root)/".audit"
            digital=audit/"voice-aec/M5-006"
            lo=audit/"voice-aec/M5-level-015-001"
            hi=audit/"voice-aec/M5-level-030-001"
            for folder in (digital,lo,hi):
                folder.mkdir(parents=True)
            ref_file=digital/"reference-sent.wav"
            pcm16_file(ref_file,rng.normal(0,.05,20*RATE))
            ref_mf=digital/"manifest.json"
            ref_mf.write_text('{"synthetic":"reference"}')
            for folder,vol in ((lo,.15),(hi,.30)):
                data=rng.normal(0,.0002,36*RATE)
                if vol==.15:
                    t=np.arange(RATE//2)/RATE
                    data[round(5.5*RATE):round(6.*RATE)] += (
                        .02*np.sin(2*np.pi*11000*t))
                wav=folder/"microphone-aba-raw.wav"
                pcm16_file(wav,data)
                meta={
                    "schema":"cuewire-M5-continuous-ABA-control-v1",
                    "status":"captured_aba_for_noise_diagnostics",
                    "source_mic_sample_spec":"s16le 1ch 48000Hz",
                    "source_mic_name":"alsa_input.usb-synthetic",
                    "speaker_sink_name":"alsa_output.pci-synthetic",
                    "speaker_play_volume":vol,
                    "reference_source_capture":str(digital),
                    "reference_source_manifest_sha256":aec.digest(ref_mf),
                    "reference_wav_sha256":aec.digest(ref_file),
                    "reference_duration_s":20.0,
                    "requested_speaker_off_before_s":8,
                    "requested_speaker_off_after_s":8,
                    "microphone":{"sha256":aec.digest(wav)}
                }
                (folder/"manifest.json").write_text(json.dumps(meta))
            sources=[ref_file,ref_mf,lo/"microphone-aba-raw.wav",
                     lo/"manifest.json",hi/"microphone-aba-raw.wav",
                     hi/"manifest.json"]
            old_hashes=[aec.digest(x) for x in sources]
            with (mock.patch.object(off_fingerprint,"AUDIT",audit.resolve()),
                  mock.patch.object(playback_coherence,"AUDIT",audit.resolve()),
                  mock.patch.object(off_fingerprint,"LOW_MANIFEST_SHA",aec.digest(lo/"manifest.json")),
                  mock.patch.object(off_fingerprint,"HIGH_MANIFEST_SHA",aec.digest(hi/"manifest.json")),
                  mock.patch.object(off_fingerprint,"LOW_MIC_SHA",aec.digest(lo/"microphone-aba-raw.wav")),
                  mock.patch.object(off_fingerprint,"HIGH_MIC_SHA",aec.digest(hi/"microphone-aba-raw.wav")),
                  mock.patch.object(off_fingerprint,"REFERENCE_SHA",aec.digest(ref_file)),
                  mock.patch.object(off_fingerprint,"SOURCE_MANIFEST_SHA",aec.digest(ref_mf)),
                  mock.patch.object(playback_coherence,"REFERENCE_SHA",aec.digest(ref_file)),
                  mock.patch.object(playback_coherence,"SOURCE_MANIFEST_SHA",aec.digest(ref_mf))):
                dest=audit/"voice-aec/M5-off-spectral-fingerprint-001"
                report=off_fingerprint.analyze(lo,hi,dest)
                self.assertTrue((dest/"off-spectral-fingerprint.json").is_file())
                self.assertTrue((dest/"SHA256SUMS").is_file())
                self.assertEqual(list(dest.glob("*.wav")),[])
                self.assertFalse(report["microphone_voice_analyzed"])
                self.assertFalse(report["virtual_mic_approved"])
                self.assertEqual(report["status"],
                                 "diagnostic_after_observed_highband_bursts_not_independent")
                phase=report["volume_015"]["speaker_off"]["before"]
                self.assertEqual(len(phase["windows"]),10)
                self.assertEqual(len(phase["elevated_windows"]),1)
                self.assertEqual(phase["elevated_windows"][0]["mic_wav_time_s"],
                                 [5.5,6.0])
                self.assertEqual(old_hashes,[aec.digest(x) for x in sources])
                with self.assertRaises(FileExistsError):
                    off_fingerprint.analyze(lo,hi,dest)
                with (lo/"microphone-aba-raw.wav").open("ab") as f:
                    f.write(b"tamper")
                with self.assertRaisesRegex(ValueError,"SHA256"):
                    off_fingerprint.analyze(
                        lo,hi,audit/"voice-aec/M5-off-spectral-fingerprint-002")

    def test_speex_baseline_reference_offset_and_bit_exact_pcm(self):
        """Pinned old lag, stream volume and 10ms prepeak are deterministic."""
        rng=np.random.default_rng(777201)
        music=rng.normal(0,.055,20*RATE)
        n=36*RATE
        for lag,volume in ((speex_baseline.LAG_015_SAMPLES,.15),
                           (speex_baseline.LAG_030_SAMPLES,.30)):
            aligned=speex_baseline.render_reference_on_mic_timeline(
                music,n,lag,volume)
            self.assertEqual(len(aligned),n)
            self.assertEqual(aligned.dtype,np.dtype("<i2"))
            start=lag-speex_baseline.PRE_PEAK_SAMPLES
            self.assertTrue(np.all(aligned[:start]==0))
            self.assertTrue(np.all(aligned[start+len(music):]==0))
            np.testing.assert_array_equal(
                aligned[start:start+1000],
                speex_baseline.exact_pcm16(music[:1000]*volume))
        data=(rng.integers(-32000,32000,10001,dtype=np.int16)
              .astype(np.float64)/32768)
        np.testing.assert_array_equal(
            speex_baseline.exact_pcm16(data),
            np.rint(data*32768).astype(np.int16))
        with self.assertRaisesRegex(ValueError,"Invalid pinned"):
            speex_baseline.render_reference_on_mic_timeline(
                music,n,-1,.15)

    def test_speex_baseline_frame_processing_mocked_c_abi(self):
        """Native ABI frame chronology, 480 samples and short final frame."""
        class FakeSpeex:
            def __init__(self):
                self.created=0
                self.destroyed=0
                self.calls=0
                self.sample_rate=-1
            def speex_echo_state_init(self,frame,tail):
                assert (frame,tail)==(480,5760)
                self.created+=1
                return 1988
            def speex_echo_ctl(self,state,request,pointer):
                assert state==1988
                data=ctypes.cast(pointer,ctypes.POINTER(ctypes.c_int))
                if request==24:
                    self.sample_rate=data[0]
                elif request==25:
                    data[0]=self.sample_rate
                elif request==3:
                    data[0]=480
                else:
                    return -1
                return 0
            def speex_echo_cancellation(self,state,rec,play,out):
                assert state==1988
                self.calls+=1
                x=np.ctypeslib.as_array(rec,shape=(480,))
                y=np.ctypeslib.as_array(play,shape=(480,))
                z=np.ctypeslib.as_array(out,shape=(480,))
                z[:]=x-y
            def speex_echo_state_destroy(self,state):
                assert state==1988
                self.destroyed+=1
        fake=FakeSpeex()
        rec=np.full(1001,1000,dtype=np.int16)
        play=np.full(1001,125,dtype=np.int16)
        out,stats=speex_baseline.run_speex_native(
            rec,play,library=fake,library_name="synthetic-test-lib")
        np.testing.assert_allclose(out,np.full(1001,875/32768),atol=1e-12)
        self.assertEqual((fake.created,fake.destroyed,fake.calls),(1,1,3))
        self.assertEqual(stats["loaded_library"],"synthetic-test-lib")
        self.assertEqual(stats["frame_ms"],10)
        self.assertEqual(stats["filter_tail_samples"],5760)
        self.assertEqual(stats["n_frames"],3)
        self.assertTrue(stats["aec_extra_postprocessing"] is False)
        with self.assertRaisesRegex(ValueError,"equal-length"):
            speex_baseline.run_speex_native(rec,play[:-1],library=fake)

    def test_speex_baseline_exact_two_capture_numeric_only(self):
        """No disk WAV output; strict physical source pins; no production pass."""
        rng=np.random.default_rng(777202)
        class TransparentSpeex:
            """Stub native library: passes mic through, explicitly not real Speex."""
            def speex_echo_state_init(self,frame,tail):
                return 2020
            def speex_echo_ctl(self,state,request,pointer):
                ptr=ctypes.cast(pointer,ctypes.POINTER(ctypes.c_int))
                if request==24:
                    self.samplerate=ptr[0]
                elif request==25:
                    ptr[0]=self.samplerate
                elif request==3:
                    ptr[0]=480
                return 0
            def speex_echo_cancellation(self,state,rec,play,out):
                ctypes.memmove(out,rec,960)
            def speex_echo_state_destroy(self,state):
                pass
        with tempfile.TemporaryDirectory() as root:
            audit=Path(root)/".audit"
            digital=audit/"voice-aec/M5-006"
            low=audit/"voice-aec/M5-level-015-001"
            high=audit/"voice-aec/M5-level-030-001"
            for p in (digital,low,high):
                p.mkdir(parents=True)
            ref=digital/"reference-sent.wav"
            wave=rng.normal(0,.05,20*RATE)
            pcm16_file(ref,wave)
            source_manifest=digital/"manifest.json"
            source_manifest.write_text('{"test":true}')
            for folder,gain,lag in (
                (low,.15,speex_baseline.LAG_015_SAMPLES),
                (high,.30,speex_baseline.LAG_030_SAMPLES)):
                mic=rng.normal(0,.001,36*RATE)
                mic[lag:lag+len(wave)]+=gain*.09*wave
                raw=folder/"microphone-aba-raw.wav"
                pcm16_file(raw,mic)
                meta={
                    "schema":"cuewire-M5-continuous-ABA-control-v1",
                    "status":"captured_aba_for_noise_diagnostics",
                    "source_mic_sample_spec":"s16le 1ch 48000Hz",
                    "source_mic_name":"alsa_input.usb-synthetic",
                    "speaker_sink_name":"alsa_output.pci-synthetic",
                    "speaker_play_volume":gain,
                    "reference_source_capture":str(digital),
                    "reference_source_manifest_sha256":aec.digest(source_manifest),
                    "reference_wav_sha256":aec.digest(ref),
                    "reference_duration_s":20.0,
                    "requested_speaker_off_before_s":8,
                    "requested_speaker_off_after_s":8,
                    "microphone":{"sha256":aec.digest(raw)}
                }
                (folder/"manifest.json").write_text(json.dumps(meta))
            originals=[
                ref,source_manifest,
                low/"manifest.json",low/"microphone-aba-raw.wav",
                high/"manifest.json",high/"microphone-aba-raw.wav"
            ]
            old_hashes=[aec.digest(p) for p in originals]
            stub=TransparentSpeex()
            with (mock.patch.object(speex_baseline,"AUDIT",audit.resolve()),
                  mock.patch.object(echo_floor,"AUDIT",audit.resolve()),
                  mock.patch.object(playback_coherence,"AUDIT",audit.resolve()),
                  mock.patch.object(speex_baseline,"LOW_MANIFEST_SHA",aec.digest(low/"manifest.json")),
                  mock.patch.object(speex_baseline,"HIGH_MANIFEST_SHA",aec.digest(high/"manifest.json")),
                  mock.patch.object(speex_baseline,"LOW_MIC_SHA",aec.digest(low/"microphone-aba-raw.wav")),
                  mock.patch.object(speex_baseline,"HIGH_MIC_SHA",aec.digest(high/"microphone-aba-raw.wav")),
                  mock.patch.object(speex_baseline,"REFERENCE_SHA",aec.digest(ref)),
                  mock.patch.object(speex_baseline,"SOURCE_MANIFEST_SHA",aec.digest(source_manifest)),
                  mock.patch.object(playback_coherence,"REFERENCE_SHA",aec.digest(ref)),
                  mock.patch.object(playback_coherence,"SOURCE_MANIFEST_SHA",aec.digest(source_manifest))):
                out=audit/"voice-aec/M5-speex-aec-only-001"
                report=speex_baseline.analyze(
                    low,high,out,speex_loader=lambda:(stub,"mock-only-library"))
                self.assertTrue((out/"speex-aec-only.json").exists())
                self.assertTrue((out/"SHA256SUMS").exists())
                self.assertEqual(list(out.glob("*.wav")),[])
                self.assertFalse(report["voice_preservation_validated"])
                self.assertTrue(report["speex_process_adapts_during_heldout"])
                self.assertEqual(
                    report["library_resolved"],"mock-only-library")
                self.assertTrue(report[
                    "original_prospective_0_67881_db_gate_remains_failed"])
                self.assertAlmostEqual(
                    report["volume_030"]["heldout_speaker_only"][
                        "full_band_erle_db"],0.,delta=.05)
                self.assertEqual(old_hashes,[aec.digest(x) for x in originals])
                with self.assertRaises(FileExistsError):
                    speex_baseline.analyze(
                        low,high,out,speex_loader=lambda:(stub,"mock-only-library"))
                with (high/"microphone-aba-raw.wav").open("ab") as f:
                    f.write(b"tamper")
                with self.assertRaisesRegex(ValueError,"SHA256"):
                    speex_baseline.analyze(
                        low,high,audit/"voice-aec/M5-speex-aec-only-002",
                        speex_loader=lambda:(stub,"mock-only-library"))

    def test_speex_zero_reference_control_drops_all_far_end_frames(self):
        """Real mic signal maintained, but far-end exactly zero throughout."""
        rng=np.random.default_rng(777310)
        x=rng.normal(0,.05,20*RATE)
        mic=rng.normal(0,.0006,36*RATE)
        lag=speex_baseline.LAG_015_SAMPLES
        mic[lag:lag+len(x)]+= .012*x
        class ZeroFarOnly:
            def __init__(self):
                self.calls=0
                self.not_zero=0
            def speex_echo_state_init(self,frame,tail):
                assert (frame,tail)==(480,5760)
                return 3030
            def speex_echo_ctl(self,state,request,ptr):
                data=ctypes.cast(ptr,ctypes.POINTER(ctypes.c_int))
                if request==24: self.rate=data[0]
                elif request==25: data[0]=self.rate
                elif request==3: data[0]=480
                else: return -1
                return 0
            def speex_echo_cancellation(self,state,rec,far,out):
                self.calls+=1
                y=np.ctypeslib.as_array(far,shape=(480,))
                self.not_zero+=int(np.count_nonzero(y))
                ctypes.memmove(out,rec,960)
            def speex_echo_state_destroy(self,state):
                assert state==3030
        fake=ZeroFarOnly()
        result=speex_zero.zero_reference_run(
            mic,x,lag,fake,"synthetic-zero-far")
        self.assertGreater(fake.calls,3500)
        self.assertEqual(fake.not_zero,0)
        self.assertEqual(
            result["native_processor"]["frame_samples"],480)
        self.assertEqual(len(result["heldout_speaker_only"]["bands"]),6)
        self.assertAlmostEqual(
            result["heldout_speaker_only"]["full_band_erle_db"],0.,delta=.05)
        self.assertTrue(result["processed_with_exactly_zero_far_end_pcm"])
        self.assertEqual(len(
            result["speaker_off_source_independent_impact"]),2)
        normal={
          "heldout_speaker_only":{
            "full_band_erle_db":2.5,
            "bands":[dict(row) for row in result["heldout_speaker_only"]["bands"]]
          },
          "speech_band_500_4000hz_proxy":dict(
              result["speech_band_500_4000hz_proxy"]),
          "speaker_off_speex_impact":{
            phase:dict(out)
            for phase,out in result[
                "speaker_off_source_independent_impact"].items()
          },
        }
        normal["speech_band_500_4000hz_proxy"][
            "linked_proxy_suppression_db"]+=2.5
        report=speex_zero.compare(normal,result)
        self.assertAlmostEqual(
            report["wholeband_erle_gain_due_to_real_reference_db"],
            2.5,delta=.05)
        self.assertAlmostEqual(
            report["500_4000hz_linked_suppression_gain_due_to_real_reference_db"],
            2.5,delta=1e-6)
        self.assertEqual(len(report["per_band_speaker_off"]),12)

    def test_speex_zero_reference_immutable_sha_pinned_numeric_only(self):
        """Verified baseline report, synthetic offline inputs and no WAV output."""
        rng=np.random.default_rng(777311)
        class PassSpeex:
            def speex_echo_state_init(self,frame,tail): return 3031
            def speex_echo_ctl(self,state,request,ptr):
                c=ctypes.cast(ptr,ctypes.POINTER(ctypes.c_int))
                if request==24: self.rate=c[0]
                elif request==25: c[0]=self.rate
                elif request==3: c[0]=480
                else: return -1
                return 0
            def speex_echo_cancellation(self,state,rec,far,out):
                ctypes.memmove(out,rec,960)
            def speex_echo_state_destroy(self,state):
                assert state==3031
        with tempfile.TemporaryDirectory() as tmp:
            audit=Path(tmp)/".audit"
            digital=audit/"voice-aec/M5-006"
            low=audit/"voice-aec/M5-level-015-001"
            high=audit/"voice-aec/M5-level-030-001"
            for folder in (digital,low,high):
                folder.mkdir(parents=True)
            ref=digital/"reference-sent.wav"
            music=rng.normal(0,.05,20*RATE)
            pcm16_file(ref,music)
            source_manifest=digital/"manifest.json"
            source_manifest.write_text('{"test":"zero-ref"}')
            for folder,gain,lag in (
                (low,.15,speex_baseline.LAG_015_SAMPLES),
                (high,.30,speex_baseline.LAG_030_SAMPLES)):
                mic=rng.normal(0,.0009,36*RATE)
                mic[lag:lag+len(music)] += .06*gain*music
                wav=folder/"microphone-aba-raw.wav"
                pcm16_file(wav,mic)
                meta={
                    "schema":"cuewire-M5-continuous-ABA-control-v1",
                    "status":"captured_aba_for_noise_diagnostics",
                    "source_mic_sample_spec":"s16le 1ch 48000Hz",
                    "source_mic_name":"alsa_input.usb-test",
                    "speaker_sink_name":"alsa_output.pci-test",
                    "speaker_play_volume":gain,
                    "reference_source_capture":str(digital),
                    "reference_source_manifest_sha256":aec.digest(source_manifest),
                    "reference_wav_sha256":aec.digest(ref),
                    "reference_duration_s":20.0,
                    "requested_speaker_off_before_s":8,
                    "requested_speaker_off_after_s":8,
                    "microphone":{"sha256":aec.digest(wav)}
                }
                (folder/"manifest.json").write_text(json.dumps(meta))
            originals=[
                ref,source_manifest,low/"manifest.json",
                low/"microphone-aba-raw.wav",high/"manifest.json",
                high/"microphone-aba-raw.wav"
            ]
            hashes_before=[aec.digest(p) for p in originals]
            stub=PassSpeex()
            patches=[
                mock.patch.object(speex_baseline,"AUDIT",audit.resolve()),
                mock.patch.object(speex_zero,"AUDIT",audit.resolve()),
                mock.patch.object(playback_coherence,"AUDIT",audit.resolve()),
                mock.patch.object(speex_baseline,"LOW_MANIFEST_SHA",
                                  aec.digest(low/"manifest.json")),
                mock.patch.object(speex_baseline,"HIGH_MANIFEST_SHA",
                                  aec.digest(high/"manifest.json")),
                mock.patch.object(speex_baseline,"LOW_MIC_SHA",
                                  aec.digest(low/"microphone-aba-raw.wav")),
                mock.patch.object(speex_baseline,"HIGH_MIC_SHA",
                                  aec.digest(high/"microphone-aba-raw.wav")),
                mock.patch.object(speex_baseline,"REFERENCE_SHA",
                                  aec.digest(ref)),
                mock.patch.object(speex_baseline,"SOURCE_MANIFEST_SHA",
                                  aec.digest(source_manifest)),
                mock.patch.object(speex_zero,"LOW_MANIFEST_SHA",
                                  aec.digest(low/"manifest.json")),
                mock.patch.object(speex_zero,"HIGH_MANIFEST_SHA",
                                  aec.digest(high/"manifest.json")),
                mock.patch.object(speex_zero,"LOW_MIC_SHA",
                                  aec.digest(low/"microphone-aba-raw.wav")),
                mock.patch.object(speex_zero,"HIGH_MIC_SHA",
                                  aec.digest(high/"microphone-aba-raw.wav")),
                mock.patch.object(speex_zero,"REFERENCE_SHA",aec.digest(ref)),
                mock.patch.object(speex_zero,"SOURCE_MANIFEST_SHA",
                                  aec.digest(source_manifest)),
                mock.patch.object(playback_coherence,"REFERENCE_SHA",
                                  aec.digest(ref)),
                mock.patch.object(playback_coherence,"SOURCE_MANIFEST_SHA",
                                  aec.digest(source_manifest)),
            ]
            with (patches[0],patches[1],patches[2],patches[3],
                  patches[4],patches[5],patches[6],patches[7],
                  patches[8],patches[9],patches[10],patches[11],
                  patches[12],patches[13],patches[14],patches[15],
                  patches[16]):
                base=audit/"voice-aec/M5-speex-aec-only-001"
                speex_baseline.analyze(
                    low,high,base,speex_loader=lambda:(stub,"libspeexdsp.so.1"))
                f=base/"speex-aec-only.json"
                with mock.patch.object(
                        speex_zero,"NATIVE_REF_REPORT_SHA256",
                        aec.digest(f)):
                    dest=audit/"voice-aec/M5-speex-zero-reference-001"
                    report=speex_zero.analyze(
                        low,high,f,dest,
                        speex_loader=lambda:(stub,"libspeexdsp.so.1"))
                    self.assertTrue((dest/"speex-zero-reference.json").is_file())
                    self.assertTrue((dest/"SHA256SUMS").is_file())
                    self.assertEqual(list(dest.glob("*.wav")),[])
                    self.assertTrue(report["zero_farend_pcm_for_entire_duration"])
                    self.assertFalse(report["voice_preservation_tested"])
                    self.assertTrue(report["original_prospective_0_67881_db_gate_remains_failed"])
                    self.assertEqual(len(report[
                        "normal_minus_zero_comparison"]["volume_030"][
                            "per_band_speaker_off"]),12)
                    self.assertEqual(hashes_before,
                                     [aec.digest(p) for p in originals])
                    with self.assertRaises(FileExistsError):
                        speex_zero.analyze(
                            low,high,f,dest,
                            speex_loader=lambda:(stub,"libspeexdsp.so.1"))
                    with (high/"microphone-aba-raw.wav").open("ab") as fd:
                        fd.write(b"tamper")
                    with self.assertRaisesRegex(ValueError,"SHA256"):
                        speex_zero.analyze(
                            low,high,f,audit/"voice-aec/M5-speex-zero-reference-002",
                            speex_loader=lambda:(stub,"libspeexdsp.so.1"))

    def test_speex_known_voice_incremental_gain_snr_math(self):
        """Speech transfer distortion is not conflated with residual music."""
        rng=np.random.default_rng(776010)
        stem=rng.normal(0,.015,3*RATE)
        exact=speex_voice.score_incremental_voice(stem,stem)
        halved=speex_voice.score_incremental_voice(stem,stem*.5)
        self.assertAlmostEqual(
            exact["incremental_voice_transfer_gain"],1.0,delta=1e-7)
        self.assertGreater(exact["incremental_voice_response_snr_db"],200)
        self.assertAlmostEqual(
            halved["incremental_voice_transfer_gain"],.5,delta=1e-7)
        self.assertAlmostEqual(
            halved["incremental_voice_response_snr_db"],6.0206,delta=.002)
        self.assertEqual(len(exact["frequency_bands"]),4)
        with self.assertRaisesRegex(ValueError,">=3s"):
            speex_voice.score_incremental_voice(stem[:RATE],stem[:RATE])
        with self.assertRaisesRegex(ValueError,"silent"):
            speex_voice.score_incremental_voice(np.zeros_like(stem),stem)

    def test_speex_known_voice_real_pcm_delta_is_ground_truth(self):
        """Injected speech is PCM16-difference exact, not an ideal float stem."""
        rng=np.random.default_rng(776011)
        mic=rng.normal(0,.0007,36*RATE)
        voice=rng.normal(0,.01,6*RATE)
        lag=speex_baseline.LAG_015_SAMPLES
        a,b=lag+10*RATE,lag+16*RATE
        mixed,stem,meta=speex_voice.construct_mixture(
            mic,voice,a,b,-50.)
        actual=(mixed[a:b].astype(np.int32)-
                speex_baseline.exact_pcm16(mic)[a:b].astype(np.int32))/32768.
        np.testing.assert_array_equal(stem,actual)
        self.assertEqual(meta["voice_samples"],6*RATE)
        self.assertAlmostEqual(meta["digitized_injected_voice_rms_dbfs"],
                               -50.,delta=.05)
        self.assertFalse(meta["real_mic_or_room_nearend_speech_simulated"])
        baseline=speex_voice.known_stem_interference(
            stem,mixed[a:b].astype(np.float64)/32768)
        self.assertTrue(np.isfinite(baseline["known_stem_snr_db"]))
        with self.assertRaisesRegex(ValueError,"Invalid bounded"):
            speex_voice.construct_mixture(mic,voice,a,b,-10.)
        with self.assertRaisesRegex(ValueError,"too quiet"):
            speex_voice.construct_mixture(
                mic,np.zeros_like(voice),a,b,-50.)

    def test_speex_delta_frontend_cancellation_preserves_exact_pcm_voice(self):
        """Mathematical differential removes common frontend but not music."""
        rng=np.random.default_rng(777601)
        signal_int=rng.integers(-8000,8000,12*RATE,dtype=np.int16)
        mic=signal_int.astype(np.float64)/32768
        echo=rng.normal(0,.0005,len(mic))
        prefiltered=.95*mic
        with_far=prefiltered-echo
        out,meta=speex_delta.delta_from_native(signal_int,with_far,prefiltered)
        np.testing.assert_allclose(out,mic-echo,atol=1e-14,rtol=0)
        self.assertFalse(meta["enabled"])
        protected,policy=speex_delta.delta_from_native(
            signal_int,with_far,prefiltered,guard=True)
        self.assertTrue(policy["only_echo_estimate_is_filtered"])
        self.assertEqual(len(protected),len(mic))
        # The existing zero-phase guard explicitly requires >=10s;
        # do not weaken that production-facing input contract for a test.
        with self.assertRaisesRegex(ValueError,"Invalid echo estimate"):
            speex_delta.delta_from_native(
                signal_int[:6*RATE],with_far[:6*RATE],
                prefiltered[:6*RATE],guard=True)
        speech=rng.normal(0,.012,len(mic))
        voice_pcm=speex_baseline.exact_pcm16(mic+speech)
        raw_stem=(voice_pcm.astype(np.int32)-signal_int.astype(np.int32))/32768
        out_voice,_=speex_delta.delta_from_native(
            voice_pcm, .95*(voice_pcm.astype(np.float64)/32768)-echo,
            .95*(voice_pcm.astype(np.float64)/32768))
        score=speex_voice.score_incremental_voice(raw_stem,out_voice-out)
        self.assertAlmostEqual(
            score["incremental_voice_transfer_gain"],1.0,delta=1e-6)
        self.assertGreater(score["incremental_voice_response_snr_db"],200)
        with self.assertRaisesRegex(ValueError,"identical-length"):
            speex_delta.delta_from_native(
                signal_int,with_far[:-1],prefiltered)

    def test_speex_delta_two_takes_verified_prior_and_numeric_only(self):
        """Native stubs cancel common mic coloration; sources immutable."""
        rng=np.random.default_rng(777602)
        class FakeSpeex:
            def speex_echo_state_init(self,frame,tail):
                assert frame==480 and tail==5760
                return 4120
            def speex_echo_ctl(self,state,request,ptr):
                v=ctypes.cast(ptr,ctypes.POINTER(ctypes.c_int))
                if request==24: self.rate=v[0]
                elif request==25: v[0]=self.rate
                elif request==3: v[0]=480
                else: return -1
                return 0
            def speex_echo_cancellation(self,state,rec,play,out):
                raw=np.ctypeslib.as_array(rec,shape=(480,))
                far=np.ctypeslib.as_array(play,shape=(480,))
                dest=np.ctypeslib.as_array(out,shape=(480,))
                dest[:]=np.clip(
                    np.rint(.94*raw.astype(np.float64) -
                            .2*far.astype(np.float64)),
                    -32768,32767).astype(np.int16)
            def speex_echo_state_destroy(self,state):
                assert state==4120
        with tempfile.TemporaryDirectory() as tmp:
            audit=Path(tmp)/".audit"
            digital=audit/"voice-aec/M5-006"
            low=audit/"voice-aec/M5-level-015-001"
            high=audit/"voice-aec/M5-level-030-001"
            m4=audit/"voice-m4/M4-R0-ALSA-007"
            for p in (digital,low,high,m4):
                p.mkdir(parents=True)
            song=rng.normal(0,.05,20*RATE)
            source=digital/"reference-sent.wav"
            pcm16_file(source,song)
            source_mf=digital/"manifest.json"
            source_mf.write_text('{"synthetic":true}')
            voice_data=np.zeros(12*RATE)
            voice_data[6*RATE:] = rng.normal(0,.008,6*RATE)
            m4_wav=m4/"raw.wav"
            pcm16_file(m4_wav,voice_data)
            m4_manifest={
                "schema":"cuewire-M4-raw-alsa-v1",
                "status":"recorded_unprocessed_raw",
                "source":"physical_alsa_hw",
                "device":"hw:1,2",
                "card_id":"SYNTHETIC",
                "raw_wav":"raw.wav",
                "raw_sha256":aec.digest(m4_wav),
                "processing":{
                    k:False for k in (
                      "aec","denoise","eq","compression",
                      "virtual_mic","normalization","monitoring")
                },
                "measurements":{"rate":RATE,"channels":1,
                                "sample_width_bytes":2,"clipped_samples":0},
            }
            m4_mf=m4/"manifest.json"
            m4_mf.write_text(json.dumps(m4_manifest))
            for folder,vol,lag in ((low,.15,speex_baseline.LAG_015_SAMPLES),
                                   (high,.30,speex_baseline.LAG_030_SAMPLES)):
                mic=rng.normal(0,.0007,36*RATE)
                mic[lag-480:lag-480+len(song)] += .2*vol*song
                mic_wav=folder/"microphone-aba-raw.wav"
                pcm16_file(mic_wav,mic)
                manifest={
                    "schema":"cuewire-M5-continuous-ABA-control-v1",
                    "status":"captured_aba_for_noise_diagnostics",
                    "source_mic_sample_spec":"s16le 1ch 48000Hz",
                    "source_mic_name":"alsa_input.usb-synthetic",
                    "speaker_sink_name":"alsa_output.pci-synthetic",
                    "speaker_play_volume":vol,
                    "reference_source_capture":str(digital),
                    "reference_source_manifest_sha256":aec.digest(source_mf),
                    "reference_wav_sha256":aec.digest(source),
                    "reference_duration_s":20.0,
                    "requested_speaker_off_before_s":8,
                    "requested_speaker_off_after_s":8,
                    "microphone":{"sha256":aec.digest(mic_wav)},
                }
                (folder/"manifest.json").write_text(json.dumps(manifest))
            original_paths=[source,source_mf,m4_wav,m4_mf,
                  low/"manifest.json",low/"microphone-aba-raw.wav",
                  high/"manifest.json",high/"microphone-aba-raw.wav"]
            old_hashes=[aec.digest(p) for p in original_paths]
            src={
                "digital_reference":aec.digest(source),
                "digital_reference_manifest":aec.digest(source_mf),
                "level015_manifest":aec.digest(low/"manifest.json"),
                "level015_raw_mic":aec.digest(low/"microphone-aba-raw.wav"),
                "level030_manifest":aec.digest(high/"manifest.json"),
                "level030_raw_mic":aec.digest(high/"microphone-aba-raw.wav"),
                "m4_voice_manifest":aec.digest(m4_mf),
                "m4_voice_raw":aec.digest(m4_wav),
            }
            original_report={
                "schema":"cuewire-M5-SpeexDSP-incremental-synthetic-nearend-voice-v1",
                "physical_voice_capture_during_music":False,
                "voice_preservation_certified":False,
                "voice_target_rms_dbfs":[-50.0,-39.0],
                "m4_voice_excerpt_start_seconds":6.0,
                "m4_voice_excerpt_duration_seconds":6.0,
                "source_sha256":src,
            }
            prev=audit/"voice-aec/M5-speex-known-voice-001"
            prev.mkdir()
            prev_file=prev/"known-voice-speex.json"
            prev_file.write_text(json.dumps(original_report))
            pins=[
                (speex_delta,"AUDIT",audit.resolve()),
                (playback_coherence,"AUDIT",audit.resolve()),
                (speex_delta,"LOW_MANIFEST_SHA",src["level015_manifest"]),
                (speex_delta,"HIGH_MANIFEST_SHA",src["level030_manifest"]),
                (speex_delta,"LOW_MIC_SHA",src["level015_raw_mic"]),
                (speex_delta,"HIGH_MIC_SHA",src["level030_raw_mic"]),
                (speex_delta,"REFERENCE_SHA",src["digital_reference"]),
                (speex_delta,"SOURCE_MANIFEST_SHA",src["digital_reference_manifest"]),
                (speex_delta,"ORIGINAL_VOICE_REPORT_SHA256",aec.digest(prev_file)),
                (playback_coherence,"REFERENCE_SHA",src["digital_reference"]),
                (playback_coherence,"SOURCE_MANIFEST_SHA",src["digital_reference_manifest"]),
            ]
            with ExitStack() as stack:
                for obj,field,value in pins:
                    stack.enter_context(mock.patch.object(obj,field,value))
                output=audit/"voice-aec/M5-speex-delta-001"
                report=speex_delta.analyze(
                    low,high,m4,prev_file,output,
                    native_loader=lambda:(FakeSpeex(),"fake-native"))
                self.assertTrue((output/"speex-delta.json").is_file())
                self.assertTrue((output/"SHA256SUMS").is_file())
                self.assertEqual(list(output.glob("*.wav")),[])
                self.assertFalse(report["voice_preservation_proven_in_real_doubletalk"])
                self.assertTrue(report[
                    "historical_0_67881_db_prospective_AEC_gate_still_failed"])
                self.assertEqual(old_hashes,[aec.digest(p) for p in original_paths])
                for key in ("speaker_only_and_voice_report_015",
                            "speaker_only_and_voice_report_030"):
                    t=report[key]
                    self.assertEqual(len(t["synthetic_known_voice_cases"]),2)
                    for case in t["synthetic_known_voice_cases"]:
                        plain=case["results"]["reference_difference_raw"][
                            "voice_incremental_transfer"]
                        native=case["results"]["normal_native_speex"][
                            "voice_incremental_transfer"]
                        self.assertGreater(
                            plain["incremental_voice_response_snr_db"],
                            native["incremental_voice_response_snr_db"]+10)
                        self.assertAlmostEqual(
                            plain["incremental_voice_transfer_gain"],1.,delta=.01)
                with self.assertRaises(FileExistsError):
                    speex_delta.analyze(
                        low,high,m4,prev_file,output,
                        native_loader=lambda:(FakeSpeex(),"fake-native"))
                with (low/"microphone-aba-raw.wav").open("ab") as f:
                    f.write(b"tamper")
                with self.assertRaisesRegex(ValueError,"SHA256"):
                    speex_delta.analyze(
                        low,high,m4,prev_file,
                        audit/"voice-aec/M5-speex-delta-002",
                        native_loader=lambda:(FakeSpeex(),"fake-native"))

    def test_listening_ab_common_gain_and_wav_exclusive(self):
        """Explicit user-requested WAVs preserve matched-level A/B comparisons."""
        rng=np.random.default_rng(224401)
        signal=rng.normal(0,.010,6*RATE)
        triple=(signal,signal*.5,signal*.8)
        pcm,meta=listening_ab.matched_pcm16(triple)
        self.assertEqual(len(pcm),3)
        self.assertTrue(meta["same_linear_amplitude_gain_applied_to_all_three"])
        self.assertFalse(meta["per_file_normalization"])
        self.assertEqual(meta["joint_post_gain_target_peak_dbfs"],-3.)
        g=meta["shared_gain_linear"]
        np.testing.assert_allclose(
            pcm[0].astype(np.float64)/32768,
            signal*g,atol=1/32768)
        self.assertAlmostEqual(
            aec.db(aec.rms(pcm[0].astype(np.float64)/32768))-
            aec.db(aec.rms(pcm[1].astype(np.float64)/32768)),
            20*math.log10(2),delta=.002)
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/"demo.wav"
            listening_ab.write_wav(dest,pcm[0])
            with wave.open(str(dest),"rb") as wav:
                self.assertEqual(
                    (wav.getframerate(),wav.getnchannels(),
                     wav.getsampwidth(),wav.getnframes()),
                    (RATE,1,2,6*RATE))
            with self.assertRaises(FileExistsError):
                listening_ab.write_wav(dest,pcm[0])
        with self.assertRaisesRegex(ValueError,"three finite"):
            listening_ab.matched_pcm16(triple[:2])

    def test_listening_ab_exact_source_pins_and_six_private_wavs(self):
        """Two levels, three variants each; nothing auto-played or altered."""
        rng=np.random.default_rng(224402)
        class FakeSpeex:
            def speex_echo_state_init(self,frame,tail):
                assert (frame,tail)==(480,5760)
                return 5567
            def speex_echo_ctl(self,state,request,ptr):
                data=ctypes.cast(ptr,ctypes.POINTER(ctypes.c_int))
                if request==24: self.samplerate=data[0]
                elif request==25: data[0]=self.samplerate
                elif request==3: data[0]=480
                else: return -1
                return 0
            def speex_echo_cancellation(self,state,rec,far,out):
                signal=np.ctypeslib.as_array(rec,shape=(480,))
                speaker=np.ctypeslib.as_array(far,shape=(480,))
                target=np.ctypeslib.as_array(out,shape=(480,))
                target[:]=np.rint(.95*signal.astype(np.float64)-
                                   .18*speaker.astype(np.float64)).astype(np.int16)
            def speex_echo_state_destroy(self,state):
                assert state==5567
        with tempfile.TemporaryDirectory() as temp:
            audit=Path(temp)/".audit"
            music_dir=audit/"voice-aec/M5-006"
            capture=audit/"voice-aec/M5-level-030-001"
            voice=audit/"voice-m4/M4-R0-ALSA-007"
            previous=audit/"voice-aec/M5-speex-delta-001"
            for folder in (music_dir,capture,voice,previous):
                folder.mkdir(parents=True)
            x=rng.normal(0,.015,20*RATE)
            mp=music_dir/"reference-sent.wav"
            pcm16_file(mp,x)
            mm=music_dir/"manifest.json"
            mm.write_text('{"test":true}')
            mic=rng.normal(0,.001,36*RATE)
            lag=speex_baseline.LAG_030_SAMPLES
            mic[lag-480:lag-480+len(x)] += .015*x
            mw=capture/"microphone-aba-raw.wav"
            pcm16_file(mw,mic)
            m={
                "schema":"cuewire-M5-continuous-ABA-control-v1",
                "status":"captured_aba_for_noise_diagnostics",
                "source_mic_sample_spec":"s16le 1ch 48000Hz",
                "source_mic_name":"alsa_input.usb-synthetic",
                "speaker_sink_name":"alsa_output.pci-synthetic",
                "speaker_play_volume":.30,
                "reference_source_capture":str(music_dir),
                "reference_source_manifest_sha256":aec.digest(mm),
                "reference_wav_sha256":aec.digest(mp),
                "reference_duration_s":20.0,
                "requested_speaker_off_before_s":8,
                "requested_speaker_off_after_s":8,
                "microphone":{"sha256":aec.digest(mw)},
            }
            cm=capture/"manifest.json"
            cm.write_text(json.dumps(m))
            v=np.zeros(12*RATE)
            v[6*RATE:]+=rng.normal(0,.006,6*RATE)
            vw=voice/"raw.wav"
            pcm16_file(vw,v)
            vm=voice/"manifest.json"
            vm.write_text(json.dumps({
                "schema":"cuewire-M4-raw-alsa-v1",
                "status":"recorded_unprocessed_raw",
                "source":"physical_alsa_hw",
                "device":"hw:1,2",
                "card_id":"SYNTHETIC",
                "raw_wav":"raw.wav",
                "raw_sha256":aec.digest(vw),
                "processing":{
                    key:False for key in ("aec","denoise","eq",
                    "compression","virtual_mic","normalization","monitoring")},
                "measurements":{"rate":RATE,"channels":1,
                                "sample_width_bytes":2,"clipped_samples":0}
            }))
            original_sources={
                "digital_reference":aec.digest(mp),
                "digital_reference_manifest":aec.digest(mm),
                "level030_manifest":aec.digest(cm),
                "level030_raw_mic":aec.digest(mw),
                "m4_voice_manifest":aec.digest(vm),
                "m4_voice_raw":aec.digest(vw),
            }
            report=previous/"speex-delta.json"
            report.write_text(json.dumps({
                "schema":"cuewire-M5-SpeexDSP-dual-branch-reference-delta-v1",
                "voice_nearend_only_synthetic":True,
                "approved_for_live_mic":False,
                "source_sha256":original_sources,
                "speaker_only_and_voice_report_030":{
                    "historical_fixed_lag_samples":lag}
            }))
            original_files=[mp,mm,mw,cm,vw,vm,report]
            old_hashes=[aec.digest(p) for p in original_files]
            pins=[
                (listening_ab,"AUDIT",audit.resolve()),
                (playback_coherence,"AUDIT",audit.resolve()),
                (listening_ab,"HIGH_MANIFEST_SHA",aec.digest(cm)),
                (listening_ab,"HIGH_MIC_SHA",aec.digest(mw)),
                (listening_ab,"REFERENCE_SHA",aec.digest(mp)),
                (listening_ab,"SOURCE_MANIFEST_SHA",aec.digest(mm)),
                (listening_ab,"DELTA_REPORT_SHA256",aec.digest(report)),
                (playback_coherence,"REFERENCE_SHA",aec.digest(mp)),
                (playback_coherence,"SOURCE_MANIFEST_SHA",aec.digest(mm)),
            ]
            with ExitStack() as stack:
                for obj,field,val in pins:
                    stack.enter_context(mock.patch.object(obj,field,val))
                dest=audit/"voice-aec/M5-listening-001"
                result=listening_ab.export(
                    capture,voice,report,dest,volume=.30,
                    native_loader=lambda:(FakeSpeex(),"fake-lib"))
                self.assertEqual(len(result["files"]),6)
                self.assertFalse(result["automatically_played"])
                self.assertFalse(result["monitoring_or_audio_route_changed"])
                self.assertTrue(result["no_external_microphone_recording"])
                for level in ("voice_-50dbfs","voice_-39dbfs"):
                    files=list((dest/level).glob("*.wav"))
                    self.assertEqual(len(files),3)
                    gains=result["case_settings"][level]["export_shared_gain"]
                    self.assertTrue(
                        gains["same_linear_amplitude_gain_applied_to_all_three"])
                self.assertEqual(old_hashes,[aec.digest(p) for p in original_files])
                with self.assertRaises(FileExistsError):
                    listening_ab.export(capture,voice,report,dest,
                        native_loader=lambda:(FakeSpeex(),"fake-lib"))
                with (capture/"microphone-aba-raw.wav").open("ab") as fh:
                    fh.write(b"tamper")
                with self.assertRaisesRegex(ValueError,"SHA256"):
                    listening_ab.export(
                        capture,voice,report,
                        audit/"voice-aec/M5-listening-002",
                        native_loader=lambda:(FakeSpeex(),"fake-lib"))

    def test_mouse_click_control_separates_phases_and_13khz_bursts(self):
        """Synthetic click-like 13kHz transients in intended M5 mouse phase."""
        rng=np.random.default_rng(130001)
        mic=rng.normal(0., .00016, 44*RATE)
        # Captured within 3s recorder warmup + 2*8s phases.
        for when in (22.25,24.25):
            a=round(when*RATE)
            n=round(.07*RATE)
            t=np.arange(n)/RATE
            mic[a:a+n]+=.018*np.sin(2*np.pi*13000*t)
        phases=[
            {"name":label,"prompt":desc,
             "wall_start_after_launch_s":3+8*i,
             "wall_end_after_launch_s":3+8*(i+1)}
            for i,(label,desc) in enumerate(mouse_control.PHASES)
        ]
        result=mouse_control.analyze_samples(mic,phases)
        quiet=result["quiet_median_8_16khz_dbfs"]
        mouse=result["all_phases"]["mouse_clicks"]
        moving=result["all_phases"]["mouse_move_only"]
        self.assertGreater(mouse["max_highband_dbfs"]-quiet, 15.)
        self.assertGreater(
            mouse["half_second_windows_over_quiet_median_plus_6db"],0)
        self.assertEqual(
            moving["half_second_windows_over_quiet_median_plus_6db"],0)
        loud=max(mouse["windows"],key=lambda w:w["8000_16000_power_dbfs"])
        self.assertGreater(loud["share_of_8_16khz_power_in_12_14khz"],.7)
        self.assertAlmostEqual(loud["peak_frequency_8_16khz_hz"],13000,
                               delta=20)
        with self.assertRaisesRegex(ValueError,"0.5 seconds"):
            mouse_control.analyze_window(mic[:1000])
    def test_chaos_voice_control_music_preflight_and_phase_metrics(self):
        """One local PCM16 vocal music reference; no automatic playback."""
        rng=np.random.default_rng(801125)
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            music=folder/"music.wav"
            pcm16_file(music,rng.normal(0,.015,12*RATE))
            info=chaos.validate_music(music)
            self.assertAlmostEqual(info["duration_s"],12.,delta=.001)
            self.assertEqual(info["channels"],1)
            self.assertEqual(info["sha256"],aec.digest(music))
            text=folder/"jokes.txt"
            text.write_text("\n".join(f"Русская шутка №{i}" for i in range(25)))
            jokes,checksum=chaos.load_jokes(text)
            self.assertEqual(len(jokes),25)
            self.assertEqual(checksum,aec.digest(text))
            pcm=rng.normal(0,.0002,44*RATE)
            pcm[21*RATE:26*RATE] += rng.normal(0,.03,5*RATE)
            wav=folder/"mic.wav"
            pcm16_file(wav,pcm)
            phases=[{
                "name":"quiet_before",
                "start_since_recorder_launch_s":3.,
                "end_since_recorder_launch_s":11.,
            },{
                "name":"mouse_music",
                "start_since_recorder_launch_s":19.,
                "end_since_recorder_launch_s":27.,
            }]
            metrics=chaos.inspect_audio(wav,phases)
            self.assertEqual(metrics["clipped_samples"],0)
            self.assertGreater(
                metrics["phase_stats"]["mouse_music"]["rms_dbfs"],
                metrics["phase_stats"]["quiet_before"]["rms_dbfs"]+20.)
            self.assertTrue(metrics["mic_and_pw_play_not_sample_synchronized"])
            with self.assertRaisesRegex(ValueError,"PCM16"):
                chaos.validate_music(folder/"jokes.txt")

    def test_chaos_mocked_capture_ctrl_c_stops_all_and_saves_one_wav(self):
        """Full user Ctrl+C lifecycle: music OFF for 8s clean voice."""
        with tempfile.TemporaryDirectory() as tmp:
            audit=Path(tmp)/".audit"
            base=audit/"voice-aec"
            base.mkdir(parents=True)
            music=base/"vocal.wav"
            pcm16_file(music,np.zeros(14*RATE))
            jokes=base/"jokes.txt"
            jokes.write_text("\n".join(f"Анекдот {i}: бип" for i in range(25)))
            out=base/"M5-chaos-synthetic-001"
            class FakeProc:
                def __init__(self):
                    self.returncode=None
                def poll(self):
                    return self.returncode
                def wait(self,*args,**kwargs):
                    if self.returncode is None:
                        self.returncode=-15
                    return self.returncode
            processes=[]
            def fake_spawn(cmd,log):
                log.write_text("mock process")
                process=FakeProc()
                processes.append((cmd,process))
                if cmd[0]=="pw-record":
                    pcm16_file(Path(cmd[-1]),np.zeros(48*RATE))
                return process
            def terminate(p):
                p.returncode=-15
            def stop_owned():
                for _,process in processes:
                    terminate(process)
            clock=[0.]
            def time_advance(delta):
                if clock[0]>=46.:
                    raise KeyboardInterrupt()
                clock[0]+=delta
            opts=SimpleNamespace(
                source="alsa_input.mock-physical",
                speaker="alsa_output.mock-physical",
                music=music,volume=.60,jokes=jokes,out=out)
            patches=[
                mock.patch.object(chaos,"AUDIT",audit.resolve()),
                mock.patch.object(capture_runtime,"AUDIT",audit.resolve()),
                mock.patch.object(chaos,"checked_program",return_value="available"),
                mock.patch.object(chaos,"choose_source",
                                  return_value={"name":opts.source}),
                mock.patch.object(chaos,"checked_speaker",
                                  return_value={"name":opts.speaker}),
                mock.patch.object(chaos,"spawn",side_effect=fake_spawn),
                mock.patch.object(chaos,"terminate_group",side_effect=terminate),
                mock.patch.object(chaos,"stop_children",side_effect=stop_owned),
                mock.patch.object(chaos.time,"monotonic",
                                  side_effect=lambda:clock[0]),
                mock.patch.object(chaos.time,"sleep",side_effect=time_advance),
                mock.patch("builtins.print"),
            ]
            with ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                result=chaos.run(opts)
            self.assertEqual(result["status"],"captured_ctrl_c_at_free_stress")
            self.assertTrue(result["manual_ctrl_c"])
            self.assertEqual(len(result["recorded_phases"]),6)
            self.assertEqual([p["name"] for p in result["recorded_phases"]],[
                "quiet_before","mouse_music","keyboard_music",
                "both_music","clean_voice","free_stress"])
            clean=result["recorded_phases"][4]
            self.assertFalse(clean["music_requested"])
            self.assertTrue(clean["clean_voice_reference"])
            self.assertEqual(len(result["playback_software_start_stop_events"]),2)
            self.assertEqual(len([p for p in processes if p[0][0]=="pw-record"]),1)
            self.assertEqual(len([p for p in processes if p[0][0]=="pw-play"]),2)
            self.assertTrue(all(proc.returncode is not None
                                for _,proc in processes))
            self.assertTrue((out/"microphone-chaos-raw.wav").is_file())
            self.assertEqual(list(out.glob("microphone-*.wav")),
                             [out/"microphone-chaos-raw.wav"])
            self.assertTrue((out/"manifest.json").is_file())
            with (out/"SHA256SUMS").open() as f:
                for row in f:
                    hashcode,filename=row.strip().split("  ",1)
                    self.assertEqual(hashcode,aec.digest(out/filename))
            self.assertEqual(result["physical_microphone_analysis"]["clipped_samples"],0)
            self.assertFalse(result["sink_volume_changed"])
    def test_chaos_two_farend_restart_known_synthetic_lags(self):
        """Both independently restarted speaker references are realigned."""
        rng=np.random.default_rng(801127)
        digital=rng.normal(0,.018,(5*RATE,2))
        digital_mean=digital.mean(axis=1)
        mic=rng.normal(0,.0005,58*RATE)
        for launch,delay in ((11.,.121),(44.,.249)):
            i=round((launch+delay)*RATE)
            mic[i:i+len(digital_mean)] += .25*digital_mean
        first=chaos_ref.probe_one(digital,mic,11.)
        second=chaos_ref.probe_one(digital,mic,44.)
        self.assertTrue(first["valid_echo_lag"])
        self.assertTrue(second["valid_echo_lag"])
        self.assertGreater(first["peak_highmid_normalized_correlation"],.1)
        self.assertGreater(second["peak_highmid_normalized_correlation"],.1)
        self.assertAlmostEqual(
            first["lag_after_process_launch_ms"],121.,delta=4.)
        self.assertAlmostEqual(
            second["lag_after_process_launch_ms"],249.,delta=4.)
        self.assertEqual(first["best_channel"],"stereo_arithmetic_mean")

    def test_chaos_reference_probe_sha_pins_new_private_numeric_only(self):
        """All original WAVs, music and manifest protected against mutation."""
        with tempfile.TemporaryDirectory() as tmp:
            audit=Path(tmp)/".audit"
            capture_dir=audit/"voice-aec/M5-chaos-001"
            capture_dir.mkdir(parents=True)
            music=capture_dir/"song.wav"
            rng=np.random.default_rng(801128)
            song=rng.normal(0,.01,(32*RATE,2))
            with wave.open(str(music),"wb") as f:
                f.setnchannels(2);f.setsampwidth(2);f.setframerate(RATE)
                f.writeframes(np.rint(song*32768).astype("<i2").tobytes())
            mic_file=capture_dir/"microphone-chaos-raw.wav"
            pcm16_file(mic_file,np.zeros(60*RATE))
            mf=capture_dir/"manifest.json"
            mf.write_text(json.dumps({
                "schema":"cuewire-M5-chaos-physical-doublestress-v1",
                "status":"captured_ctrl_c_at_free_stress",
                "music_stream_volume":.6,
                "manual_ctrl_c":True,
                "sink_volume_changed":False,
                "raw_wav_file":"microphone-chaos-raw.wav",
                "source_mic_sha256":aec.digest(mic_file),
                "physical_microphone_analysis":{"recorded_frames":60*RATE},
                "music_source":{"path":str(music),"sha256":aec.digest(music),
                                "rate":RATE,"channels":2},
                "playback_software_start_stop_events":[
                    {"instance":1,"music_file_start_s":0.,
                     "start_since_recorder_launch_s":11.,
                     "end_since_recorder_launch_s":35.},
                    {"instance":2,"music_file_start_s":0.,
                     "start_since_recorder_launch_s":44.,
                     "end_since_recorder_launch_s":59.},
                ]
            }))
            sources=(mf,mic_file,music)
            old_hashes=[aec.digest(f) for f in sources]
            with mock.patch.object(chaos_ref,"AUDIT",audit.resolve()), \
                 mock.patch.object(chaos_ref,"ACTUAL_MANIFEST_SHA256",aec.digest(mf)), \
                 mock.patch.object(chaos_ref,"ACTUAL_MIC_SHA256",aec.digest(mic_file)), \
                 mock.patch.object(chaos_ref,"ACTUAL_SONG_SHA256",aec.digest(music)), \
                 mock.patch.object(chaos_ref,"probe_one",side_effect=[
                    {"status":"synthetic_11","valid_echo_lag":True},
                    {"status":"synthetic_44","valid_echo_lag":True}
                 ]) as probe:
                out=audit/"voice-aec/M5-chaos-alignment-001"
                result=chaos_ref.analyze(capture_dir,out)
                self.assertEqual(probe.call_count,2)
                self.assertEqual(
                    result["separate_process_restarts"][0]["estimate"]["status"],
                    "synthetic_11")
                self.assertTrue((out/"music-alignment.json").is_file())
                self.assertTrue((out/"SHA256SUMS").is_file())
                self.assertEqual(list(out.glob("*.wav")),[])
                self.assertEqual([aec.digest(f) for f in sources],old_hashes)
                with self.assertRaises(FileExistsError):
                    chaos_ref.analyze(capture_dir,out)
                with music.open("ab") as f:
                    f.write(b"tamper")
                with self.assertRaisesRegex(ValueError,"SHA256"):
                    chaos_ref.analyze(
                        capture_dir,audit/"voice-aec/M5-chaos-alignment-002")
    def test_chaos_later_music_windows_retain_true_prior_lag_and_detect_drift(self):
        """Known synthetic farend arrival, validation independent of initial window."""
        rng=np.random.default_rng(801129)
        stereo=rng.normal(0,.01,(15*RATE,2))
        music=stereo.mean(axis=1)
        lag=int(.330*RATE)
        observed=rng.normal(0,.00008,17*RATE)
        observed[lag:lag+len(music)] += music*.25
        report=chaos_validate.evaluate(
            stereo,observed,lag,
            ((1.,3.7),(5.,7.7),(9.,11.7)),
            prefiltered=False)
        self.assertEqual(len(report["windows"]),3)
        for stage in report["windows"]:
            scored=stage["channels"]["stereo_arithmetic_mean"]
            self.assertGreater(scored["fixed_lag_abs_normalized_correlation"],.96)
            self.assertLess(abs(scored["best_local_minus_prior_ms"]),.06)
            self.assertLessEqual(
                scored["best_local_abs_normalized_correlation"],1.001)
        # A deliberately wrong prior +47ms is exposed by local search.
        wrong=chaos_validate.lag_score(
            music,observed,1.,3.7,lag+round(.047*RATE))
        self.assertAlmostEqual(wrong["best_local_minus_prior_ms"],
                               -47.,delta=.1)
        self.assertLess(wrong["fixed_lag_abs_normalized_correlation"],.05)
        self.assertGreater(wrong["best_local_abs_normalized_correlation"],.95)
        with self.assertRaisesRegex(ValueError,"too short"):
            chaos_validate.lag_score(music,observed,1.,1.3,lag)

    def test_chaos_real_farend_two_starts_have_clean_speech_zero_reference(self):
        """Two source spans, no far-end in actual physical clean-voice phase."""
        rng=np.random.default_rng(801131)
        stereo=rng.normal(0,.01,(47*RATE,2))
        trials=[
            {"playback_instance":1,"estimate":{
                "valid_echo_lag":True,
                "lag_samples_from_mic_start":round(11.28954167*RATE)}},
            {"playback_instance":2,"estimate":{
                "valid_echo_lag":True,
                "lag_samples_from_mic_start":round(44.10879167*RATE)}},
        ]
        events=[
            {"instance":1,"music_file_start_s":0.,
             "start_since_recorder_launch_s":11.00758,
             "end_since_recorder_launch_s":35.01153},
            {"instance":2,"music_file_start_s":0.,
             "start_since_recorder_launch_s":44.01799,
             "end_since_recorder_launch_s":90.01614},
        ]
        n=round(89.96267*RATE)
        far,details=chaos_real.build_farend_reference(
            stereo,n,trials,events,
            ("stereo_arithmetic_mean","left"),.60)
        self.assertEqual(far.dtype,np.dtype("<i2"))
        self.assertEqual(len(far),n)
        self.assertEqual(len(details["reference_events"]),2)
        self.assertTrue(np.all(far[36*RATE:44*RATE]==0))
        self.assertTrue(np.any(far[12*RATE:18*RATE]!=0))
        self.assertTrue(np.any(far[45*RATE:70*RATE]!=0))
        self.assertTrue(np.all(far[3*RATE:10*RATE]==0))
        # Wall-clock stop timestamps are converted to integer PCM
        # frames; compare to that sample-quantized value, not a higher
        # precision wall-clock time (1/48000 s = 20.83 microseconds).
        self.assertEqual(
            details["reference_events"][0]["reference_end_process_stop_approx_s"],
            round(round(events[0]["end_since_recorder_launch_s"]*RATE)/RATE,7))
        self.assertEqual(
            details["reference_events"][1]["reference_end_process_stop_approx_s"],
            round(n/RATE,7))
        # For the first restart, the quantization discrepancy itself
        # must be strictly smaller than half an audio sample.
        self.assertLess(
            abs(details["reference_events"][0]["reference_end_process_stop_approx_s"]
                - events[0]["end_since_recorder_launch_s"]),
            0.5/RATE + 0.00000005)
        # Missing launch event must fail early, not silently guess a timing.
        malformed=[dict(x) for x in events]
        del malformed[1]["start_since_recorder_launch_s"]
        with self.assertRaisesRegex(ValueError,"start_since_recorder_launch_s"):
            chaos_real.build_farend_reference(
                stereo,n,trials,malformed,
                ("stereo_arithmetic_mean","left"),.60)
        self.assertNotEqual(
            details["reference_events"][0]["pinned_prior_lag_samples"],
            details["reference_events"][1]["pinned_prior_lag_samples"])
        with self.assertRaisesRegex(ValueError,"Unknown stereo"):
            chaos_real.build_farend_reference(
                stereo,n,trials,events,
                ("stereo_arithmetic_mean","invalid"),.60)

    def test_chaos_real_mixed_voice_metric_no_invalid_clean_stem_claim(self):
        """Speaker-reference proxy only when music ON; clean voice compares raw."""
        rng=np.random.default_rng(801132)
        samples=round(8*RATE)
        mic_float=rng.normal(0,.01,samples)
        source=rng.normal(0,.04,samples)
        raw=speex_baseline.exact_pcm16(mic_float)
        far=speex_baseline.exact_pcm16(source)
        original=raw.astype(np.float64)/32768.
        speaker_only={
            "name":"mouse_music",
            "start_since_recorder_launch_s":0.,
            "end_since_recorder_launch_s":8.,
            "music_requested":True,
        }
        variants={
            "original_raw":original,
            "slight_residual":original*.91
        }
        values=chaos_real.numeric_metrics(raw,variants,far,speaker_only)
        self.assertIn("reference_linked_500_4000hz",
            values["processors"]["slight_residual"])
        self.assertEqual(values["processors"]["original_raw"][
            "processed_minus_input_rms_dbfs"],-240.)
        quiet={**speaker_only,"name":"clean_voice","music_requested":False}
        control=chaos_real.numeric_metrics(
            raw,variants,np.zeros_like(far),quiet)
        self.assertNotIn("reference_linked_500_4000hz",
                         control["processors"]["slight_residual"])
        self.assertFalse(control["processors"]["slight_residual"][
            "real_untouched_voice_truth_available"])

    def test_source_sha_guard_and_exact_output_length(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audit = root / ".audit"
            capture_dir = audit / "voice-aec/M5-001"
            capture_dir.mkdir(parents=True)
            x, y, _ = synthetic_pair(9)
            ref_file = capture_dir / "reference-sent.wav"
            mic_file = capture_dir / "microphone-raw.wav"
            pcm16_file(ref_file, x)
            pcm16_file(mic_file, y)
            manifest = {
                "schema": "cuewire-M5-paired-physical-capture-v1",
                "status": "captured_for_offline_aec",
                "reference": {"sha256": aec.digest(ref_file)},
                "microphone": {"sha256": aec.digest(mic_file)},
                "speaker_only_calibration_s": 5
            }
            (capture_dir / "manifest.json").write_text(json.dumps(manifest))
            with mock.patch.object(aec, "AUDIT", audit.resolve()):
                output = audit / "voice-aec/M5-analysis-001"
                report = aec.analyze(capture_dir, output)
                self.assertEqual(report["status"], "estimated_not_listening_approved")
                self.assertEqual(aec.digest(mic_file), report["mic_raw_sha256"])
                self.assertGreater(report["frozen_filter"]["heldout_speaker_only_erle_db"], 1)
                fs, data = aec.wavfile.read(output/"aec-candidate-float32.wav")
                self.assertEqual(fs, RATE)
                self.assertEqual(len(data), len(y))
                self.assertTrue(np.all(np.isfinite(data)))
                with self.assertRaises(FileExistsError):
                    aec.analyze(capture_dir, output)
                diagnostics_dir = audit / "voice-aec/M5-diagnostics-001"
                diag = aec.diagnose(capture_dir, diagnostics_dir)
                self.assertEqual(
                    diag["status"], "diagnostic_only_not_accepted_for_voice")
                self.assertIn("holdout_shift_oracle_grid", diag)
                self.assertIn("passed_production_gate", diag["calibration"])
                self.assertTrue((diagnostics_dir / "diagnostics.json").is_file())
                self.assertTrue((diagnostics_dir / "SHA256SUMS").is_file())
                self.assertEqual(list(diagnostics_dir.glob("*.wav")), [])
                with self.assertRaises(FileExistsError):
                    aec.diagnose(capture_dir, diagnostics_dir)
            with mock.patch.object(spectral, "AUDIT", audit.resolve()):
                spectral_dir = audit / "voice-aec/M5-spectral-001"
                spectral_report = spectral.analyze(capture_dir, spectral_dir)
                self.assertEqual(spectral_report["status"],
                                 "diagnostic_only_not_aec_accepted")
                self.assertEqual(spectral_report["schema"],
                                 "cuewire-M5-spectral-diagnostics-v3")
                self.assertEqual(len(spectral_report[
                    "frequency_selected_lag_diagnostics"]), 5)
                self.assertEqual(list(spectral_dir.glob("*.wav")), [])
                self.assertTrue((spectral_dir / "spectral.json").exists())
                self.assertTrue((spectral_dir / "SHA256SUMS").exists())
                with self.assertRaises(FileExistsError):
                    spectral.analyze(capture_dir, spectral_dir)
            manifest["reference"]["sha256"] = "WRONG"
            (capture_dir / "manifest.json").write_text(json.dumps(manifest))
            with mock.patch.object(aec, "AUDIT", audit.resolve()):
                with self.assertRaisesRegex(ValueError, "Reference SHA256"):
                    aec.analyze(capture_dir, audit/"voice-aec/M5-analysis-002")


if __name__ == "__main__":
    unittest.main()
