"""M7 terminal console tests. Never access real mic, PipeWire or user socket."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import cuewire_console as ui

STATUS = (
    "OK mic=alsa_input.usb-test_mic "
    "input=cuewire.mix.123 "
    "virtual_source=cuewire.microphone.123 "
    "monitor=bluez_output.headphones_1 "
    "active_cue=Some(5)"
)


class MockSocket:
    def __init__(self, response: bytes):
        self.response = response
        self.received: list[bytes] = []
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.endpoint = self.root / ui.SOCKET_NAME
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.endpoint))
        os.chmod(self.endpoint, 0o600)
        self.server.listen(1)
        self.thread: threading.Thread | None = None

    def start(self):
        def serve():
            with self.server:
                conn, _ = self.server.accept()
                with conn:
                    packet = bytearray()
                    while b"\n" not in packet:
                        part = conn.recv(128)
                        if not part:
                            break
                        packet.extend(part)
                    self.received.append(bytes(packet))
                    conn.sendall(self.response)
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.server.close()
        if self.thread:
            self.thread.join(timeout=1.0)
        self.temp.cleanup()


class OperatorConsoleTests(unittest.TestCase):
    def test_preview_is_pure_and_no_device_or_socket_is_contacted(self):
        out = io.StringIO()
        with patch.object(ui, "request", side_effect=AssertionError("network")), \
             patch.object(ui, "validate_socket_location",
                          side_effect=AssertionError("filesystem")), \
             redirect_stdout(out):
            self.assertEqual(ui.main(["--preview"]), 0)
        self.assertIn("PREVIEW ONLY", out.getvalue())
        self.assertIn("not loaded in runtime", out.getvalue())

    def test_status_v01_is_parsed_without_activating_DSP(self):
        status = ui.parse_status(STATUS)
        self.assertTrue(status.online)
        self.assertEqual(status.cue, "Some(5)")
        self.assertIn("no live M6 DSP", status.mode)
        self.assertIn("cuewire.microphone.123", ui.render(status))

    def test_status_rejects_malicious_or_incomplete_replies(self):
        for value in (
            "OK mic=x input=y virtual_source=z monitor=w",
            "OK mic=x input=y virtual_source=z monitor=w active_cue=None bogus=a",
            "OK mic=x input=y virtual_source=z monitor=w active_cue=None\x1b[2J",
            "ERROR not running",
        ):
            with self.subTest(value=value), self.assertRaises(ui.ConsoleError):
                ui.parse_status(value)

    def test_remote_commands_have_no_shell_or_multicommand_path(self):
        for allowed in ("status", "stop", "quit", "play 0", "play 5", "play 9"):
            self.assertEqual(ui.operator_command(allowed), ("remote", allowed))
        for invalid in ("play 10", "play -1", "play 1; rm -rf /",
                        "run", "set-default", "play 0\nquit", "record", "keycode"):
            with self.subTest(value=invalid), self.assertRaises(ui.ConsoleError):
                ui.operator_command(invalid)
        self.assertEqual(ui.operator_command("exit"), ("local", "exit"))

    def test_existing_user_owned_socket_only_and_exact_command(self):
        mock = MockSocket((STATUS + "\n").encode()).start()
        try:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(mock.root)}):
                path = ui.validate_socket_location()
                self.assertEqual(path, mock.endpoint)
            self.assertEqual(ui.request("status", mock.endpoint), STATUS)
            self.assertEqual(mock.received, [b"status\n"])
        finally:
            mock.close()

    def test_refuses_socket_permissions_and_symlink(self):
        mock = MockSocket(b"OK test\n").start()
        try:
            environment = {"XDG_RUNTIME_DIR": str(mock.root)}
            os.chmod(mock.endpoint, 0o666)
            with self.assertRaisesRegex(ui.ConsoleError, "mode 0600"):
                ui.validate_socket_location(environment)
            os.chmod(mock.endpoint, 0o600)
            rootlink = mock.root / "linked.sock"
            rootlink.symlink_to(mock.endpoint)
            self.assertTrue(rootlink.is_symlink())
            self.assertTrue(stat.S_ISSOCK(mock.endpoint.lstat().st_mode))
            # A replaced expected socket path is NOT accepted.
            mock.endpoint.unlink()
            mock.endpoint.symlink_to(rootlink)
            with self.assertRaisesRegex(ui.ConsoleError, "not a real Unix socket"):
                ui.validate_socket_location(environment)
        finally:
            mock.close()

    def test_invalid_utf8_or_terminal_escape_rejected(self):
        for packet in (b"OK bad\x1b[2J\n", b"OK \xff\n", b"OK no newline"):
            mock = MockSocket(packet).start()
            try:
                with self.assertRaises(ui.ConsoleError):
                    ui.request("status", mock.endpoint)
            finally:
                mock.close()

    def test_quit_needs_confirmation_and_exit_never_stops_runtime(self):
        out = io.StringIO()
        with patch.object(ui.sys.stdin, "isatty", return_value=True), \
             patch.object(ui, "read_snapshot",
                          return_value=ui.Snapshot(False, "OFFLINE")), \
             patch.object(ui, "request",
                          side_effect=AssertionError("must not contact engine")), \
             patch("builtins.input",
                   side_effect=["quit", "NO", "exit"]), \
             redirect_stdout(out):
            self.assertEqual(ui.interactive(), 0)
        self.assertIn("Cancelled", out.getvalue())
        self.assertIn("left untouched", out.getvalue())

    def test_offline_once_fails_closed_without_side_effects(self):
        with patch.object(ui, "read_snapshot",
                          return_value=ui.Snapshot(False, "OFFLINE")), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(ui.main(["--once"]), 2)

    def test_visible_does_not_emit_terminal_escape(self):
        self.assertNotIn("\x1b", ui.visible("OK\x1b[2J", 80))
        self.assertLessEqual(len(ui.visible("a"*100, 12)), 12)


if __name__ == "__main__":
    unittest.main()
