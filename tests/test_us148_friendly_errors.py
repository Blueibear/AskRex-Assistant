"""Tests for US-148: Friendly, actionable error messages for missing dependencies."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# AC1: friendly_import_error — includes package name and install command
# ---------------------------------------------------------------------------


def test_friendly_import_error_includes_package_name() -> None:
    """friendly_import_error message mentions the package name."""
    from rex.dep_errors import friendly_import_error

    exc = ImportError("No module named 'openai'")
    result = friendly_import_error("openai", "pip install openai", exc)
    assert "openai" in str(result)


def test_friendly_import_error_includes_install_command() -> None:
    """friendly_import_error message includes the install command."""
    from rex.dep_errors import friendly_import_error

    exc = ImportError("No module named 'sounddevice'")
    result = friendly_import_error("sounddevice", "pip install sounddevice", exc)
    assert "pip install sounddevice" in str(result)


def test_friendly_import_error_chains_original_exception() -> None:
    """friendly_import_error chains the original exception as __cause__."""
    from rex.dep_errors import friendly_import_error

    original = ImportError("No module named 'foo'")
    result = friendly_import_error("foo", "pip install foo", original)
    assert result.__cause__ is original


def test_friendly_import_error_returns_import_error_type() -> None:
    """friendly_import_error returns an ImportError instance."""
    from rex.dep_errors import friendly_import_error

    exc = ImportError("missing")
    result = friendly_import_error("pkg", "pip install pkg", exc)
    assert isinstance(result, ImportError)


def test_friendly_import_error_message_has_install_hint() -> None:
    """Message contains 'Install' keyword directing user to fix."""
    from rex.dep_errors import friendly_import_error

    exc = ImportError("missing")
    result = friendly_import_error("requests", "pip install requests", exc)
    assert "Install" in str(result) or "install" in str(result)


def test_friendly_import_error_message_contains_install_cmd_verbatim() -> None:
    """Install command is present verbatim in the error message."""
    from rex.dep_errors import friendly_import_error

    cmd = "pip install 'rex[full]'"
    exc = ImportError("missing")
    result = friendly_import_error("rex", cmd, exc)
    assert cmd in str(result)


# ---------------------------------------------------------------------------
# AC1: is_connection_error — detects various connection failure types
# ---------------------------------------------------------------------------


def test_is_connection_error_connection_refused() -> None:
    from rex.dep_errors import is_connection_error

    assert is_connection_error(ConnectionRefusedError("refused")) is True


def test_is_connection_error_connection_error_subclass() -> None:
    from rex.dep_errors import is_connection_error

    assert is_connection_error(ConnectionError("generic")) is True


def test_is_connection_error_value_error_returns_false() -> None:
    from rex.dep_errors import is_connection_error

    assert is_connection_error(ValueError("not a connection error")) is False


def test_is_connection_error_runtime_error_returns_false() -> None:
    from rex.dep_errors import is_connection_error

    assert is_connection_error(RuntimeError("something else")) is False


def test_is_connection_error_connect_error_by_name() -> None:
    """ConnectError (httpx-style) detected by class name without importing httpx."""
    from rex.dep_errors import is_connection_error

    class ConnectError(Exception):
        pass

    assert is_connection_error(ConnectError("connect failed")) is True


def test_is_connection_error_broken_pipe() -> None:
    from rex.dep_errors import is_connection_error

    assert is_connection_error(BrokenPipeError()) is True


# ---------------------------------------------------------------------------
# AC2: LM Studio connection → friendly message
# ---------------------------------------------------------------------------


def test_lm_studio_connection_error_is_runtime_error() -> None:
    """LMStudioConnectionError is a RuntimeError subclass."""
    from rex.dep_errors import LMStudioConnectionError

    exc = LMStudioConnectionError("Rex can't reach LM Studio at http://localhost:1234.")
    assert isinstance(exc, RuntimeError)


def test_lm_studio_connection_error_message_format() -> None:
    """LMStudioConnectionError message matches expected pattern."""
    from rex.dep_errors import LMStudioConnectionError

    url = "http://localhost:1234"
    exc = LMStudioConnectionError(f"Rex can't reach LM Studio at {url}. Is LM Studio running?")
    msg = str(exc)
    assert "Rex can't reach LM Studio" in msg
    assert url in msg
    assert "Is LM Studio running?" in msg


def test_language_model_generate_raises_lm_studio_error_on_connection_refused() -> None:
    """LanguageModel.generate converts ConnectionRefusedError → LMStudioConnectionError."""
    from rex.dep_errors import LMStudioConnectionError
    from rex.llm_client import LanguageModel

    # Build a minimal mock config
    mock_config = MagicMock()
    mock_config.llm_provider = "openai"
    mock_config.openai_model = "local-model"
    mock_config.llm_model = "local-model"
    mock_config.openai_api_key = "test-key"  # pragma: allowlist secret -- error fixture
    mock_config.anthropic_api_key = None
    mock_config.ollama_api_key = None
    mock_config.ollama_base_url = "http://localhost:11434"
    mock_config.ollama_use_cloud = False
    mock_config.openai_base_url = "http://localhost:1234"
    mock_config.llm_max_tokens = 256
    mock_config.llm_temperature = 0.7
    mock_config.llm_top_p = 1.0
    mock_config.llm_top_k = 50
    mock_config.llm_seed = 42

    lm = LanguageModel.__new__(LanguageModel)
    lm.config = mock_config
    lm.provider = "openai"
    lm.model_name = "local-model"
    lm._tools = []
    lm._tool_functions = {}
    lm._openai_client = None
    lm._anthropic_client = None

    from rex.llm_client import GenerationConfig

    lm.generation = GenerationConfig(
        max_new_tokens=256,
        temperature=0.7,
        top_p=1.0,
        top_k=50,
        seed=42,
    )

    # Mock strategy to raise ConnectionRefusedError
    mock_strategy = MagicMock()
    mock_strategy.generate.side_effect = ConnectionRefusedError("Connection refused")
    lm.strategy = mock_strategy

    with pytest.raises(LMStudioConnectionError) as exc_info:
        lm.generate("Hello")

    assert "Rex can't reach LM Studio" in str(exc_info.value)
    assert "http://localhost:1234" in str(exc_info.value)
    assert "Is LM Studio running?" in str(exc_info.value)


def test_language_model_generate_raises_lm_studio_error_includes_url() -> None:
    """The LMStudioConnectionError message includes the configured base_url."""
    from rex.dep_errors import LMStudioConnectionError
    from rex.llm_client import GenerationConfig, LanguageModel

    url = "http://192.168.1.10:1234"
    mock_config = MagicMock()
    mock_config.llm_provider = "openai"
    mock_config.openai_model = "local-model"
    mock_config.llm_model = "local-model"
    mock_config.openai_api_key = "test-key"  # pragma: allowlist secret
    mock_config.anthropic_api_key = None
    mock_config.ollama_api_key = None
    mock_config.ollama_base_url = "http://localhost:11434"
    mock_config.ollama_use_cloud = False
    mock_config.openai_base_url = url
    mock_config.llm_max_tokens = 256
    mock_config.llm_temperature = 0.7
    mock_config.llm_top_p = 1.0
    mock_config.llm_top_k = 50
    mock_config.llm_seed = 42

    lm = LanguageModel.__new__(LanguageModel)
    lm.config = mock_config
    lm.provider = "openai"
    lm.model_name = "local-model"
    lm._tools = []
    lm._tool_functions = {}
    lm._openai_client = None
    lm._anthropic_client = None
    lm.generation = GenerationConfig(
        max_new_tokens=256, temperature=0.7, top_p=1.0, top_k=50, seed=42
    )

    mock_strategy = MagicMock()
    mock_strategy.generate.side_effect = ConnectionRefusedError("refused")
    lm.strategy = mock_strategy

    with pytest.raises(LMStudioConnectionError) as exc_info:
        lm.generate("Hello")

    assert url in str(exc_info.value)


def test_language_model_generate_non_connection_error_propagates() -> None:
    """Non-connection errors from strategy.generate are NOT wrapped."""
    from rex.llm_client import GenerationConfig, LanguageModel

    mock_config = MagicMock()
    mock_config.llm_provider = "openai"
    mock_config.openai_model = "local-model"
    mock_config.llm_model = "local-model"
    mock_config.openai_api_key = "test-key"  # pragma: allowlist secret
    mock_config.anthropic_api_key = None
    mock_config.ollama_api_key = None
    mock_config.ollama_base_url = "http://localhost:11434"
    mock_config.ollama_use_cloud = False
    mock_config.openai_base_url = "http://localhost:1234"
    mock_config.llm_max_tokens = 256
    mock_config.llm_temperature = 0.7
    mock_config.llm_top_p = 1.0
    mock_config.llm_top_k = 50
    mock_config.llm_seed = 42

    lm = LanguageModel.__new__(LanguageModel)
    lm.config = mock_config
    lm.provider = "openai"
    lm.model_name = "local-model"
    lm._tools = []
    lm._tool_functions = {}
    lm._openai_client = None
    lm._anthropic_client = None
    lm.generation = GenerationConfig(
        max_new_tokens=256, temperature=0.7, top_p=1.0, top_k=50, seed=42
    )

    mock_strategy = MagicMock()
    mock_strategy.generate.side_effect = ValueError("model not found")
    lm.strategy = mock_strategy

    with pytest.raises(ValueError, match="model not found"):
        lm.generate("Hello")


def test_language_model_connection_error_without_base_url_propagates() -> None:
    """Connection error without openai_base_url is NOT wrapped as LMStudioConnectionError."""
    from rex.llm_client import GenerationConfig, LanguageModel

    mock_config = MagicMock()
    mock_config.llm_provider = "openai"
    mock_config.openai_model = "gpt-4"
    mock_config.llm_model = "gpt-4"
    mock_config.openai_api_key = "sk-real-key"  # pragma: allowlist secret -- error fixture
    mock_config.anthropic_api_key = None
    mock_config.ollama_api_key = None
    mock_config.ollama_base_url = "http://localhost:11434"
    mock_config.ollama_use_cloud = False
    mock_config.openai_base_url = None  # No custom base_url → not LM Studio
    mock_config.llm_max_tokens = 256
    mock_config.llm_temperature = 0.7
    mock_config.llm_top_p = 1.0
    mock_config.llm_top_k = 50
    mock_config.llm_seed = 42

    lm = LanguageModel.__new__(LanguageModel)
    lm.config = mock_config
    lm.provider = "openai"
    lm.model_name = "gpt-4"
    lm._tools = []
    lm._tool_functions = {}
    lm._openai_client = None
    lm._anthropic_client = None
    lm.generation = GenerationConfig(
        max_new_tokens=256, temperature=0.7, top_p=1.0, top_k=50, seed=42
    )

    mock_strategy = MagicMock()
    mock_strategy.generate.side_effect = ConnectionRefusedError("refused")
    lm.strategy = mock_strategy

    with pytest.raises(ConnectionRefusedError):
        lm.generate("Hello")


# ---------------------------------------------------------------------------
# AC3: No raw tracebacks in normal operation
# ---------------------------------------------------------------------------


def test_wrap_entrypoint_prints_error_message_to_stderr(
    capsys: pytest.CaptureFixture,
) -> None:
    """wrap_entrypoint prints Error: <message> to stderr without traceback."""
    import os

    from rex.exception_handler import wrap_entrypoint

    @wrap_entrypoint
    def fn() -> int:
        raise RuntimeError("something went wrong")

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REX_DEBUG", None)
        with pytest.raises(SystemExit):
            fn()

    captured = capsys.readouterr()
    assert "something went wrong" in captured.err
    assert "Traceback" not in captured.err


def test_wrap_entrypoint_no_raw_traceback_for_import_error(
    capsys: pytest.CaptureFixture,
) -> None:
    """wrap_entrypoint suppresses raw traceback for ImportError in normal mode."""
    import os

    from rex.exception_handler import wrap_entrypoint

    @wrap_entrypoint
    def fn() -> int:
        raise ImportError("No module named 'foo'")

    os.environ.pop("REX_DEBUG", None)
    with pytest.raises(SystemExit):
        fn()

    captured = capsys.readouterr()
    assert "No module named 'foo'" in captured.err
    assert "Traceback" not in captured.err


def test_wrap_entrypoint_debug_mode_shows_traceback(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In debug mode (REX_DEBUG=1), wrap_entrypoint prints full traceback to stderr."""
    from rex.exception_handler import wrap_entrypoint

    monkeypatch.setenv("REX_DEBUG", "1")

    @wrap_entrypoint
    def fn() -> int:
        raise RuntimeError("debug error")

    with pytest.raises(SystemExit):
        fn()

    captured = capsys.readouterr()
    assert "Traceback" in captured.err
    assert "debug error" in captured.err


def test_wrap_entrypoint_hints_debug_flag_in_normal_mode(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal mode output tells user to run with REX_DEBUG=1 for details."""
    from rex.exception_handler import wrap_entrypoint

    monkeypatch.delenv("REX_DEBUG", raising=False)

    @wrap_entrypoint
    def fn() -> int:
        raise RuntimeError("oops")

    with pytest.raises(SystemExit):
        fn()

    captured = capsys.readouterr()
    assert "REX_DEBUG" in captured.err


# ---------------------------------------------------------------------------
# is_debug_mode
# ---------------------------------------------------------------------------


def test_is_debug_mode_when_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from rex.dep_errors import is_debug_mode

    monkeypatch.delenv("REX_DEBUG", raising=False)
    assert is_debug_mode() is False


def test_is_debug_mode_when_set_to_1(monkeypatch: pytest.MonkeyPatch) -> None:
    from rex.dep_errors import is_debug_mode

    monkeypatch.setenv("REX_DEBUG", "1")
    assert is_debug_mode() is True


def test_is_debug_mode_when_set_to_true(monkeypatch: pytest.MonkeyPatch) -> None:
    from rex.dep_errors import is_debug_mode

    monkeypatch.setenv("REX_DEBUG", "true")
    assert is_debug_mode() is True


def test_is_debug_mode_when_set_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    from rex.dep_errors import is_debug_mode

    monkeypatch.setenv("REX_DEBUG", "0")
    assert is_debug_mode() is False
