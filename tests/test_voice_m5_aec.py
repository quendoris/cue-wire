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
        for script in ("voice_m5_capture.py", "voice_m5_aec.py"):
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
