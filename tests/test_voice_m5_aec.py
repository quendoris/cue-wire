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

    def test_cli_import_help_offline(self):
        for script in ("voice_m5_capture.py", "voice_m5_aec.py"):
            proc = subprocess.run([sys.executable, str(TOOLS / script), "--help"],
                                  capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

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
            manifest["reference"]["sha256"] = "WRONG"
            (capture_dir / "manifest.json").write_text(json.dumps(manifest))
            with mock.patch.object(aec, "AUDIT", audit.resolve()):
                with self.assertRaisesRegex(ValueError, "Reference SHA256"):
                    aec.analyze(capture_dir, audit/"voice-aec/M5-analysis-002")


if __name__ == "__main__":
    unittest.main()
