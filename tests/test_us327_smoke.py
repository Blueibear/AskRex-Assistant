"""US-327: End-to-end smoke test -- CLI boot and chat.

Verifies that ``rex doctor`` exits 0 and ``rex chat --no-tts`` produces
output for piped stdin.  Both tests are CI-safe: no GPU, no microphone,
and no external API keys are required (heavy checks are monkeypatched).
"""

from __future__ import annotations

import argparse
import io
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from rex.doctor import CheckResult, Status, run_diagnostics

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(name: str) -> CheckResult:
    return CheckResult(name=name, status=Status.OK, message="ok (mocked)")


# ---------------------------------------------------------------------------
# Doctor smoke tests
# ---------------------------------------------------------------------------


def _patch_all_checks(monkeypatch) -> None:
    """Patch every individual check in rex.doctor to return OK."""
    import rex.doctor as dm

    monkeypatch.setattr(dm, "check_python_version", lambda: _ok("Python"))
    monkeypatch.setattr(dm, "check_package_installation", lambda: _ok("Pkg"))
    monkeypatch.setattr(dm, "check_config_file", lambda root: _ok("Config"))
    monkeypatch.setattr(dm, "check_config_types", lambda root: _ok("ConfigTypes"))
    monkeypatch.setattr(dm, "check_env_file", lambda root: _ok("Env"))
    monkeypatch.setattr(dm, "check_credential_vault_backend", lambda: _ok("Vault"))
    monkeypatch.setattr(dm, "check_environment_variables", lambda: _ok("APIKeys"))
    monkeypatch.setattr(dm, "check_config_permissions", lambda root: _ok("Perms"))
    monkeypatch.setattr(dm, "check_audio_input_device", lambda: _ok("AudioIn"))
    monkeypatch.setattr(dm, "check_audio_output_device", lambda: _ok("AudioOut"))
    monkeypatch.setattr(dm, "check_smart_speakers", lambda: _ok("Speakers"))
    monkeypatch.setattr(dm, "check_lm_studio_reachability", lambda: _ok("LMStudio"))
    monkeypatch.setattr(dm, "check_ffmpeg_for_tts", lambda: _ok("FFmpeg"))
    monkeypatch.setattr(dm, "check_wakeword_config", lambda: _ok("WakeWord"))
    monkeypatch.setattr(dm, "check_stt_backend", lambda: _ok("STT"))
    monkeypatch.setattr(dm, "check_stt_warmup", lambda stt=None: _ok("STTWarmup"))
    monkeypatch.setattr(dm, "check_warm_runtime", lambda: _ok("WarmRuntime"))
    monkeypatch.setattr(dm, "check_gpu_availability", lambda: _ok("GPU"))
    monkeypatch.setattr(dm, "check_xtts_transformers_compat", lambda: _ok("XTTS"))
    monkeypatch.setattr(dm, "check_core_dependencies", lambda: [_ok("CoreDep")])
    monkeypatch.setattr(dm, "check_external_dependencies", lambda: [_ok("Git")])


class TestDoctorSmoke:
    """run_diagnostics() returns 0 when all checks succeed."""

    def test_exits_zero_with_mocked_checks(self, monkeypatch, capsys):
        _patch_all_checks(monkeypatch)
        exit_code = run_diagnostics()
        assert exit_code == 0
        assert "Rex is ready to use." in capsys.readouterr().out

    def test_exits_one_when_any_check_errors(self, monkeypatch, capsys):
        _patch_all_checks(monkeypatch)
        import rex.doctor as dm

        monkeypatch.setattr(
            dm,
            "check_audio_input_device",
            lambda: CheckResult(name="AudioIn", status=Status.ERROR, message="no mic"),
        )
        exit_code = run_diagnostics()
        assert exit_code == 1

    def test_ready_message_absent_on_failure(self, monkeypatch, capsys):
        _patch_all_checks(monkeypatch)
        import rex.doctor as dm

        monkeypatch.setattr(
            dm,
            "check_stt_backend",
            lambda: CheckResult(name="STT", status=Status.ERROR, message="not installed"),
        )
        run_diagnostics()
        out = capsys.readouterr().out
        assert "Rex is NOT ready" in out


# ---------------------------------------------------------------------------
# Chat --no-tts smoke tests
# ---------------------------------------------------------------------------


class TestChatNoTtsSmoke:
    """cmd_chat with --no-tts produces output for piped stdin input."""

    def _run_chat(self, monkeypatch, capsys, stdin_text: str, mock_reply: str) -> tuple[int, str]:
        """Helper: run cmd_chat with faked stdin and mocked LLM."""
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))

        mock_assistant = MagicMock()
        mock_assistant.generate_reply = AsyncMock(return_value=mock_reply)

        with (
            patch("rex.logging_utils.configure_logging"),
            patch("rex.services.initialize_services"),
            patch("rex.plugins.load_plugins", return_value=[]),
            patch("rex.plugins.shutdown_plugins"),
            patch("rex.assistant.Assistant", return_value=mock_assistant),
        ):
            from rex.cli import cmd_chat

            args = argparse.Namespace(no_tts=True)
            exit_code = cmd_chat(args)

        return exit_code, capsys.readouterr().out

    def test_chat_produces_non_empty_output(self, monkeypatch, capsys):
        exit_code, out = self._run_chat(monkeypatch, capsys, "hello\n", "Hello from Rex!")
        assert exit_code == 0
        assert "Hello from Rex!" in out

    def test_chat_exits_zero_on_eof(self, monkeypatch, capsys):
        exit_code, _ = self._run_chat(monkeypatch, capsys, "hello\n", "Hi!")
        assert exit_code == 0

    def test_chat_empty_line_skipped(self, monkeypatch, capsys):
        """Empty lines produce a prompt reminder, not an LLM reply."""
        exit_code, out = self._run_chat(monkeypatch, capsys, "\n", "should not appear")
        assert exit_code == 0
        assert "should not appear" not in out

    def test_no_tts_flag_accepted_by_parser(self):
        """--no-tts is a recognised flag on the chat subcommand."""
        from rex.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["chat", "--no-tts"])
        assert args.no_tts is True

    def test_no_tts_flag_defaults_to_false(self):
        """--no-tts defaults to False when not supplied."""
        from rex.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["chat"])
        assert args.no_tts is False
