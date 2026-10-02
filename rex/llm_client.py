"""Language model client with pluggable backends (Transformers, OpenAI, Echo)."""

from __future__ import annotations

import contextvars
import json
import sys
import threading
import types as _types
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
from typing import Any, Protocol, cast

from rex.assistant_errors import ConfigurationError
from rex.config import OPENROUTER_DEFAULT_BASE_URL, AppConfig, load_config
from rex.logging_utils import get_logger
from rex.retry import RetryConfig, with_retry

logger = get_logger(__name__)


def _module_available(name: str) -> bool:
    if name in sys.modules:
        return True
    return find_spec(name) is not None


TORCH_AVAILABLE = _module_available("torch")
TRANSFORMERS_AVAILABLE = _module_available("transformers")
OPENAI_AVAILABLE = _module_available("openai")
OLLAMA_AVAILABLE = _module_available("ollama")
ANTHROPIC_AVAILABLE = _module_available("anthropic")


@dataclass
class GenerationConfig:
    max_new_tokens: int
    temperature: float
    top_p: float
    top_k: int
    seed: int


class LLMStrategy(Protocol):
    name: str

    def generate(
        self,
        prompt: str,
        config: GenerationConfig,
        *,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str: ...

    def stream(
        self,
        prompt: str,
        config: GenerationConfig,
        *,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        raise NotImplementedError


class EchoStrategy:
    name = "echo"

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def generate(
        self,
        prompt: str,
        _config: GenerationConfig,
        *,
        messages: list[dict[str, Any]] | None = None,
        **_kwargs: Any,
    ) -> str:
        text = prompt.strip()
        if not text and messages:
            text = "\n".join(m.get("content", "").strip() for m in messages).strip()
        return f"[{self.model_name}] {text or '(silence)'}"

    def stream(
        self,
        prompt: str,
        _config: GenerationConfig,
        *,
        messages: list[dict[str, Any]] | None = None,
        **_kwargs: Any,
    ) -> Iterator[str]:
        text = prompt.strip()
        if not text and messages:
            text = "\n".join(m.get("content", "").strip() for m in messages).strip()

        words = (text or "(silence)").split()
        for index, word in enumerate(words):
            suffix = " " if index < len(words) - 1 else ""
            yield f"{word}{suffix}"


class OfflineTransformersStrategy:
    name = "offline-transformers"

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def generate(
        self,
        prompt: str,
        _config: GenerationConfig,
        *,
        messages: list[dict[str, Any]] | None = None,
        **_kwargs: Any,
    ) -> str:
        text = prompt.strip()
        if not text and messages:
            text = "\n".join(m.get("content", "").strip() for m in messages).strip()
        return f"(offline) {text or ''}".strip() or "(offline)"

    def stream(
        self,
        prompt: str,
        config: GenerationConfig,
        *,
        messages: list[dict[str, Any]] | None = None,
        **_kwargs: Any,
    ) -> Iterator[str]:
        yield self.generate(prompt, config, messages=messages)


@dataclass(frozen=True)
class _TransformersRuntime:
    torch: Any
    pipeline: Any
    tokenizer: Any


class TransformersStrategy:
    name = "transformers"

    def __init__(self, model_name: str) -> None:
        if not (TORCH_AVAILABLE and TRANSFORMERS_AVAILABLE):
            raise ConfigurationError("Transformers backend requires `torch` and `transformers`.")
        try:
            torch = import_module("torch")
            transformers = import_module("transformers")
        except ImportError as exc:
            raise ConfigurationError(
                "Transformers backend requires `torch` and `transformers`."
            ) from exc

        from rex.runtime.warm import (
            WarmComponentSpec,
            default_idle_timeout,
            get_global_warm_runtime,
            warm_component_key,
        )

        device = 0 if torch.cuda.is_available() else -1
        manager = get_global_warm_runtime()
        component_name = warm_component_key("llm", model_name, device)

        def _load() -> _TransformersRuntime:
            tokenizer = transformers.AutoTokenizer.from_pretrained(model_name)
            model = transformers.AutoModelForCausalLM.from_pretrained(model_name)
            pipeline = transformers.pipeline(
                "text-generation",
                model=model,
                tokenizer=tokenizer,
                device=device,
            )
            if tokenizer.pad_token_id is None:
                tokenizer.pad_token_id = tokenizer.eos_token_id
            return _TransformersRuntime(torch=torch, pipeline=pipeline, tokenizer=tokenizer)

        manager.register_if_absent(
            WarmComponentSpec(
                name=component_name,
                loader=_load,
                estimated_cost_mb=2048.0,
                idle_timeout_s=default_idle_timeout(),
            )
        )
        manager.warm(component_name)
        self._warm_manager = manager
        self._warm_component_name = component_name

    def _runtime(self) -> _TransformersRuntime:
        return cast(_TransformersRuntime, self._warm_manager.get(self._warm_component_name))

    @property
    def _torch(self) -> Any:
        return self._runtime().torch

    @property
    def pipeline(self) -> Any:
        return self._runtime().pipeline

    @property
    def tokenizer(self) -> Any:
        return self._runtime().tokenizer

    def generate(
        self,
        prompt: str,
        config: GenerationConfig,
        *,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        with self._warm_manager.acquire(self._warm_component_name) as value:
            runtime = cast(_TransformersRuntime, value)
            torch = runtime.torch
            torch.manual_seed(config.seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(config.seed)

            do_sample = config.temperature > 0 or config.top_p < 1.0
            generate_kwargs = {
                "max_new_tokens": config.max_new_tokens,
                "do_sample": do_sample,
                "pad_token_id": runtime.tokenizer.pad_token_id,
            }
            if do_sample:
                generate_kwargs.update(
                    {
                        "temperature": max(config.temperature, 1e-4),
                        "top_p": config.top_p,
                        "top_k": config.top_k,
                    }
                )

            outputs = runtime.pipeline(prompt, **generate_kwargs)
            text = outputs[0]["generated_text"]
            return text[len(prompt) :].strip() or "(silence)"


class OpenAIStrategy:
    name = "openai"

    def __init__(
        self,
        model_name: str,
        client_factory,
        retry_config: RetryConfig | None = None,
    ) -> None:
        self.model_name = model_name
        self._client_factory = client_factory
        self._cached_client = None
        self.tools: list[Any] = []  # Will be set by LanguageModel
        self._retry_config = retry_config or RetryConfig()

    def _get_client(self) -> Any:
        if self._cached_client is None:
            self._cached_client = self._client_factory()
        return self._cached_client

    def generate(
        self,
        prompt: str,
        config: GenerationConfig,
        *,
        messages=None,
        tools=None,
        user: str | None = None,
    ) -> Any:
        payload = messages or [{"role": "user", "content": prompt}]

        # Prepare API call parameters
        api_params: dict[str, Any] = {
            "model": self.model_name,
            "messages": payload,
            "temperature": config.temperature,
            "max_tokens": config.max_new_tokens,
            "top_p": config.top_p,
        }

        # Pass a stable user key so OpenClaw can maintain session state.
        if user is not None:
            api_params["user"] = user

        # Add tools if available
        if tools or self.tools:
            api_params["tools"] = tools or self.tools
            # Don't force tool use, let model decide
            # api_params["tool_choice"] = "auto"

        def _call() -> str:
            response = self._get_client().chat.completions.create(**api_params)
            message = response.choices[0].message

            # When tool_calls are present, serialize to JSON so generate() always returns str
            if hasattr(message, "tool_calls") and message.tool_calls:
                tool_calls_data = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ]
                return json.dumps({"__tool_calls__": tool_calls_data})

            content = getattr(message, "content", "") or ""
            return content.strip() or "(silence)"

        return with_retry(_call, config=self._retry_config)

    def stream(
        self,
        prompt: str,
        config: GenerationConfig,
        *,
        messages=None,
        tools=None,
        user: str | None = None,
    ) -> Iterator[str]:
        payload = messages or [{"role": "user", "content": prompt}]
        api_params: dict[str, Any] = {
            "model": self.model_name,
            "messages": payload,
            "temperature": config.temperature,
            "max_tokens": config.max_new_tokens,
            "top_p": config.top_p,
            "stream": True,
        }

        if user is not None:
            api_params["user"] = user

        if tools or self.tools:
            api_params["tools"] = tools or self.tools

        stream = self._get_client().chat.completions.create(**api_params)
        for chunk in stream:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue

            content = getattr(delta, "content", None)
            if isinstance(content, str):
                if content:
                    yield content
                continue

            if isinstance(content, list):
                for item in content:
                    text = getattr(item, "text", None)
                    if text is None and isinstance(item, dict):
                        text = item.get("text")
                    if text:
                        yield str(text)


class OllamaStrategy:
    name = "ollama"

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        base_url: str = "http://localhost:11434",
        use_cloud: bool = False,
        retry_config: RetryConfig | None = None,
    ) -> None:
        if not OLLAMA_AVAILABLE:
            raise ConfigurationError("Ollama backend requires the `ollama` package.")
        try:
            ollama = import_module("ollama")
            client_cls = ollama.Client
        except Exception as exc:
            raise ConfigurationError("Ollama backend requires the `ollama` package.") from exc
        self.model_name = model_name
        self.base_url = base_url
        self.use_cloud = use_cloud
        self.api_key = api_key
        self._ollama = ollama
        self._client_cls = client_cls
        self._retry_config = retry_config or RetryConfig()

        if use_cloud and not api_key:
            raise ConfigurationError("Ollama cloud requires an API key.")

        try:
            if use_cloud:
                self._client = None  # cloud handled via ollama.chat
            else:
                self._client = self._client_cls(host=self.base_url)
                logger.info("Ollama local client initialized at %s", self.base_url)
        except Exception as exc:
            raise ConfigurationError(f"Failed to initialize Ollama client: {exc}") from exc

    def generate(
        self,
        prompt: str,
        config: GenerationConfig,
        *,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = messages or [{"role": "user", "content": prompt}]
        options = {
            "temperature": config.temperature,
            "top_p": config.top_p,
            "top_k": config.top_k,
            "seed": config.seed,
        }

        def _call() -> str:
            if self.use_cloud:
                response = self._ollama.chat(
                    model=self.model_name,
                    messages=payload,
                    options=options,
                )
            else:
                response = self._client.chat(  # type: ignore[union-attr]
                    model=self.model_name,
                    messages=payload,
                    options=options,
                )

            # Log usage when token counts are available in the response
            try:
                from rex.llm_usage import record_usage

                if isinstance(response, dict):
                    eval_count = int(response.get("eval_count") or 0)
                    prompt_eval_count = int(response.get("prompt_eval_count") or 0)
                else:
                    eval_count = int(getattr(response, "eval_count", None) or 0)
                    prompt_eval_count = int(getattr(response, "prompt_eval_count", None) or 0)
                record_usage(
                    model=self.model_name,
                    prompt_tokens=prompt_eval_count,
                    completion_tokens=eval_count,
                )
            except Exception as _log_exc:  # noqa: BLE001
                logger.debug("Usage logging failed: %s", _log_exc)

            if isinstance(response, dict):
                content = response.get("message", {}).get("content", "")
            else:
                message = getattr(response, "message", None)
                content = str(getattr(message, "content", "") or "") if message else ""
            return content.strip() or "(silence)"

        try:
            return with_retry(_call, config=self._retry_config)
        except Exception as exc:
            # Connection errors: server not running or unreachable
            if isinstance(exc, ConnectionRefusedError):
                logger.error("Ollama connection failed: %s", exc)
                return "[Ollama: connection failed — is Ollama running?]"
            # httpx.ConnectError — ollama uses httpx internally; avoid top-level import
            exc_cls = type(exc)
            if exc_cls.__name__ == "ConnectError" and exc_cls.__module__ == "httpx":
                logger.error("Ollama connection failed: %s", exc)
                return "[Ollama: connection failed — is Ollama running?]"
            # 404 model-not-found (ollama.ResponseError carries status_code)
            if getattr(exc, "status_code", None) == 404:
                logger.error("Ollama model '%s' not found: %s", self.model_name, exc)
                return (
                    f"[Ollama: model '{self.model_name}' not found"
                    f" — run: ollama pull {self.model_name}]"
                )
            # All other errors
            logger.error("Ollama unexpected error (model=%s): %s", self.model_name, exc)
            return f"[Ollama: unexpected error: {exc}]"

    def stream(
        self,
        prompt: str,
        config: GenerationConfig,
        *,
        messages=None,
        **_kwargs: Any,
    ) -> Iterator[str]:
        payload = messages or [{"role": "user", "content": prompt}]
        options = {
            "temperature": config.temperature,
            "top_p": config.top_p,
            "top_k": config.top_k,
            "seed": config.seed,
        }

        try:
            if self.use_cloud:
                stream = self._ollama.chat(
                    model=self.model_name,
                    messages=payload,
                    options=options,
                    stream=True,
                )
            else:
                stream = self._client.chat(  # type: ignore[union-attr]
                    model=self.model_name,
                    messages=payload,
                    options=options,
                    stream=True,
                )

            for chunk in stream:
                content = ""
                if isinstance(chunk, dict):
                    content = str(chunk.get("message", {}).get("content", "") or "")
                else:
                    message = getattr(chunk, "message", None)
                    content = str(getattr(message, "content", "") or "")

                if content:
                    yield content
        except Exception as exc:
            logger.warning("Ollama stream interrupted (model=%s): %s", self.model_name, exc)
            return


class AnthropicStrategy:
    name = "anthropic"

    def __init__(
        self,
        model_name: str,
        client_factory,
        retry_config: RetryConfig | None = None,
    ) -> None:
        self.model_name = model_name
        self._client_factory = client_factory
        self._cached_client = None
        self._retry_config = retry_config or RetryConfig()

    def _get_client(self) -> Any:
        if self._cached_client is None:
            self._cached_client = self._client_factory()
        return self._cached_client

    def generate(
        self,
        prompt: str,
        config: GenerationConfig,
        *,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        payload = messages or [{"role": "user", "content": prompt}]
        # Anthropic requires alternating user/assistant turns; filter system messages
        system_parts = [m["content"] for m in payload if m.get("role") == "system"]
        user_messages = [m for m in payload if m.get("role") != "system"]
        system_text = "\n".join(system_parts) if system_parts else None

        api_params: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": config.max_new_tokens,
            "messages": user_messages,
        }
        if system_text:
            api_params["system"] = system_text

        def _call() -> str:
            response = self._get_client().messages.create(**api_params)
            content = response.content[0].text if response.content else ""
            return content.strip() or "(silence)"

        return with_retry(_call, config=self._retry_config)

    def stream(
        self,
        prompt: str,
        config: GenerationConfig,
        *,
        messages=None,
        **_kwargs: Any,
    ) -> Iterator[str]:
        payload = messages or [{"role": "user", "content": prompt}]
        system_parts = [m["content"] for m in payload if m.get("role") == "system"]
        user_messages = [m for m in payload if m.get("role") != "system"]
        system_text = "\n".join(system_parts) if system_parts else None

        api_params: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": config.max_new_tokens,
            "messages": user_messages,
        }
        if system_text:
            api_params["system"] = system_text

        try:
            stream_manager = self._get_client().messages.stream(**api_params)
            with stream_manager as stream:
                for text in stream.text_stream:
                    if text:
                        yield str(text)
        except Exception as exc:
            logger.warning("Anthropic stream interrupted (model=%s): %s", self.model_name, exc)
            return


_STRATEGIES: dict[str, type[Any]] = {
    EchoStrategy.name: EchoStrategy,
    TransformersStrategy.name: TransformersStrategy,
}


def register_strategy(name: str, strategy: type[Any]) -> None:
    _STRATEGIES[name] = strategy


class LanguageModel:
    def __init__(self, config: AppConfig | None = None, **overrides) -> None:
        self.config = config or load_config()
        llm_config = self.config.llm
        if llm_config is None:
            raise ConfigurationError("LLM configuration is unavailable.")
        self.provider = str(overrides.get("provider") or llm_config.llm_provider).lower()
        if self.provider == "openai":
            self.model_name = (
                overrides.get("model") or self.config.openai_model or self.config.llm_model
            )
        elif self.provider == "openrouter":
            self.model_name = (
                overrides.get("model") or self.config.openrouter_model or self.config.llm_model
            )
        elif self.provider == "anthropic":
            self.model_name = (
                overrides.get("model") or self.config.anthropic_model or self.config.llm_model
            )
        else:
            self.model_name = overrides.get("model") or self.config.llm_model
        self.api_key: str | None
        self.api_base_url: str | None
        if self.provider == "openrouter":
            self.api_key = overrides.get("openrouter_api_key") or self.config.openrouter_api_key
            self.api_base_url = OPENROUTER_DEFAULT_BASE_URL
        else:
            self.api_key = overrides.get("openai_api_key") or self.config.openai_api_key
            self.api_base_url = overrides.get("base_url") or self.config.openai_base_url
        self.anthropic_api_key = overrides.get("anthropic_api_key") or self.config.anthropic_api_key

        self.generation = GenerationConfig(
            max_new_tokens=overrides.get("max_new_tokens", self.config.llm_max_tokens),
            temperature=overrides.get("temperature", self.config.llm_temperature),
            top_p=overrides.get("top_p", self.config.llm_top_p),
            top_k=overrides.get("top_k", self.config.llm_top_k),
            seed=overrides.get("seed", self.config.llm_seed),
        )

        self._openai_client = None
        self._anthropic_client = None
        self._tools: list[Any] = []  # OpenAI tool definitions
        self._tool_functions: dict[str, Any] = {}  # Map of tool name -> callable
        from rex.runtime.warm import get_global_warm_runtime

        # Establish process-wide warm-cache policy before constructing model strategies.
        get_global_warm_runtime(self.config)
        self._request_model: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"askrex_llm_model_{id(self)}", default=None
        )
        self._strategy_lock = threading.Lock()
        self.strategy = self._init_strategy(self.model_name)
        self._strategy_cache: dict[str, LLMStrategy] = {self.model_name: self.strategy}

    def _ensure_anthropic_client(self) -> Any:
        if self._anthropic_client is not None:
            return self._anthropic_client
        if not self.anthropic_api_key:
            raise ConfigurationError("Missing Anthropic API key.")
        if not ANTHROPIC_AVAILABLE:
            raise ConfigurationError("Anthropic backend requires the `anthropic` package.")
        try:
            Anthropic = import_module("anthropic").Anthropic
        except Exception as exc:
            raise ConfigurationError("Anthropic backend requires the `anthropic` package.") from exc
        self._anthropic_client = Anthropic(api_key=self.anthropic_api_key)
        return self._anthropic_client

    def _init_strategy(self, model_name: str | None = None) -> LLMStrategy:
        model_name = model_name or self.model_name
        if self.provider in {"openai", "openrouter"}:
            return cast(LLMStrategy, OpenAIStrategy(model_name, self._ensure_openai_client))

        if self.provider == "anthropic":
            return cast(LLMStrategy, AnthropicStrategy(model_name, self._ensure_anthropic_client))

        if self.provider == "ollama":
            return cast(
                LLMStrategy,
                OllamaStrategy(
                    model_name,
                    api_key=self.config.ollama_api_key,
                    base_url=self.config.ollama_base_url,
                    use_cloud=self.config.ollama_use_cloud,
                ),
            )

        strategy_cls = _STRATEGIES.get(self.provider)
        if strategy_cls is None:
            logger.warning("Unknown LLM provider '%s'. Falling back to echo mode.", self.provider)
            return cast(LLMStrategy, EchoStrategy(model_name))

        if strategy_cls is TransformersStrategy and not (
            TORCH_AVAILABLE and TRANSFORMERS_AVAILABLE
        ):
            logger.warning(
                "Transformers missing; using offline fallback for model '%s'.", model_name
            )
            return cast(LLMStrategy, OfflineTransformersStrategy(model_name))

        try:
            return cast(LLMStrategy, strategy_cls(model_name))
        except Exception as exc:
            logger.warning(
                "LLM backend init failed (%s). Falling back to echo. (%s)", self.provider, exc
            )
            return cast(LLMStrategy, EchoStrategy(model_name))

    def set_request_model(self, model_name: str) -> contextvars.Token[str | None]:
        """Set a model override for the current request context only."""
        return self._request_model.set(model_name)

    def reset_request_model(self, token: contextvars.Token[str | None]) -> None:
        """Restore the previous request-local model override."""
        self._request_model.reset(token)

    def active_model_name(self) -> str:
        """Return the request-local model when set, otherwise the configured base model."""
        request_model = getattr(self, "_request_model", None)
        if request_model is None:
            return self.model_name
        return request_model.get() or self.model_name

    def _strategy_for_request(self) -> LLMStrategy:
        model_name = self.active_model_name()
        strategy_cache = getattr(self, "_strategy_cache", None)
        strategy_lock = getattr(self, "_strategy_lock", None)
        if strategy_cache is None or strategy_lock is None:
            return self.strategy
        cached = strategy_cache.get(model_name)
        if cached is not None:
            return cast(LLMStrategy, cached)
        with strategy_lock:
            cached = strategy_cache.get(model_name)
            if cached is None:
                cached = self._init_strategy(model_name)
                if hasattr(cached, "tools"):
                    cached.tools = self._tools
                strategy_cache[model_name] = cached
            return cast(LLMStrategy, cached)

    def _ensure_openai_client(self):
        if self._openai_client is not None:
            return self._openai_client
        base_url = self.api_base_url
        api_key = self.api_key
        if self.provider == "openrouter":
            if not api_key:
                raise ConfigurationError("Missing OpenRouter API key.")
        elif not api_key and base_url:
            api_key = "local"  # pragma: allowlist secret
        if not api_key:
            raise ConfigurationError("Missing OpenAI API key.")
        if not OPENAI_AVAILABLE:
            raise ConfigurationError("OpenAI backend requires the `openai` package.")
        try:
            OpenAI = import_module("openai").OpenAI
        except Exception as exc:
            raise ConfigurationError("OpenAI backend requires the `openai` package.") from exc
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        if self.provider == "openrouter":
            client_kwargs["default_headers"] = {"X-OpenRouter-Title": "AskRex Assistant"}
        self._openai_client = OpenAI(**client_kwargs)
        return self._openai_client

    def register_tool(
        self, name: str, description: str, parameters: dict[str, Any], function: Any
    ) -> None:
        """Register a tool/function that the LLM can call.

        Args:
            name: Function name (e.g., "web_search")
            description: What the function does
            parameters: JSON schema for parameters
            function: Callable to execute when tool is invoked
        """
        tool_def = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }
        self._tools.append(tool_def)
        self._tool_functions[name] = function

        # Update strategy if it supports tools
        if hasattr(self.strategy, "tools"):
            self.strategy.tools = self._tools

        logger.info(f"Registered tool: {name}")

    def _execute_tool_call(self, tool_call: Any) -> str:
        """Execute a tool call and return the result."""
        import json

        function_name = tool_call.function.name
        function_args = json.loads(tool_call.function.arguments)

        logger.info(f"Executing tool: {function_name} with args: {function_args}")

        if function_name not in self._tool_functions:
            return f"Error: Unknown function {function_name}"

        from rex.mobile_api.action_context import (  # noqa: PLC0415
            MobileActionDeniedError,
            authorized_mobile_tool,
        )

        try:
            with authorized_mobile_tool(function_name, arguments=function_args):
                result = self._tool_functions[function_name](**function_args)
            return str(result) if result is not None else "No results found"
        except MobileActionDeniedError:
            raise
        except Exception as e:
            logger.error("Tool execution failed: %s", type(e).__name__)
            return f"Error executing {function_name}"

    def _format_messages(self, messages: Sequence[dict[str, str]]) -> str:
        return "\n".join(
            f"{m.get('role', 'user').capitalize()}: {m.get('content', '').strip()}"
            for m in messages
            if isinstance(m, dict)
        ).strip()

    def generate(
        self,
        prompt: str | None = None,
        *,
        messages: Sequence[dict[str, Any]] | None = None,
        config: GenerationConfig | None = None,
        max_tool_rounds: int = 3,
    ) -> str:
        if messages is not None:
            prompt_text = self._format_messages(messages)
            normalized_messages: list[dict[str, Any]] = [
                {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
                for m in messages
                if isinstance(m, dict)
            ]
        elif isinstance(prompt, str):
            prompt_text = prompt
            normalized_messages = [{"role": "user", "content": prompt}]
        else:
            raise ValueError("Prompt or messages must be provided.")

        if not prompt_text.strip():
            raise ValueError("Prompt must not be empty.")

        strategy = self._strategy_for_request()

        # Derive a stable user key for OpenAI/OpenClaw session persistence.
        _user_key: str | None = None
        if self.provider == "openai":
            uid = getattr(self.config, "user_id", "default")
            profile = getattr(self.config, "active_profile", "default")
            _user_key = uid if uid and uid != "default" else profile

        # Tool calling loop (only for OpenAI strategy with tools)
        if self.provider == "openai" and self._tools:
            current_messages = list(normalized_messages)

            for round_num in range(max_tool_rounds):
                try:
                    _tool_extra: dict[str, Any] = {}
                    if _user_key is not None:
                        _tool_extra["user"] = _user_key
                    result = strategy.generate(
                        prompt_text,
                        config or self.generation,
                        messages=current_messages,
                        tools=self._tools,
                        **_tool_extra,
                    )
                except TypeError:
                    # Fallback for strategies that don't support tools parameter
                    result = strategy.generate(
                        prompt_text, config or self.generation, messages=current_messages
                    )

                # Check if result is a JSON-serialized tool call response
                _tc_parsed: dict[str, Any] | None = None
                if isinstance(result, str):
                    try:
                        _maybe = json.loads(result)
                        if isinstance(_maybe, dict) and "__tool_calls__" in _maybe:
                            _tc_parsed = _maybe
                    except (json.JSONDecodeError, ValueError):
                        pass

                if _tc_parsed is not None:
                    tc_list: list[dict[str, Any]] = _tc_parsed["__tool_calls__"]
                    logger.info(
                        f"Tool call round {round_num + 1}: Model requested {len(tc_list)} tool(s)"
                    )

                    # Add assistant message to conversation
                    current_messages.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": tc_list,
                        }
                    )

                    # Execute each tool call and add results
                    for tc_data in tc_list:
                        tool_call = _types.SimpleNamespace(
                            id=tc_data["id"],
                            function=_types.SimpleNamespace(
                                name=tc_data["function"]["name"],
                                arguments=tc_data["function"]["arguments"],
                            ),
                        )
                        tool_result = self._execute_tool_call(tool_call)
                        current_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc_data["id"],
                                "content": tool_result,
                            }
                        )

                    # Continue loop to get final response with tool results
                    continue

                # No tool calls - return final response
                return result.strip() if isinstance(result, str) else str(result).strip()

            # Max rounds reached
            logger.warning(f"Max tool rounds ({max_tool_rounds}) reached")
            return "I apologize, but I encountered too many tool calls. Please try rephrasing your question."

        # No tools or not OpenAI - use original simple generation
        try:
            try:
                extra: dict[str, Any] = {}
                if _user_key is not None:
                    extra["user"] = _user_key
                result = strategy.generate(
                    prompt_text,
                    config or self.generation,
                    messages=normalized_messages,
                    **extra,
                )
                return result.strip() if isinstance(result, str) else str(result).strip()
            except TypeError:
                result = strategy.generate(prompt_text, config or self.generation)
                return result.strip() if isinstance(result, str) else str(result).strip()
        except Exception as exc:
            from rex.dep_errors import LMStudioConnectionError, is_connection_error

            if (
                is_connection_error(exc)
                and self.provider == "openai"
                and self.config.openai_base_url
            ):
                url = self.config.openai_base_url
                raise LMStudioConnectionError(
                    f"Rex can't reach LM Studio at {url}. Is LM Studio running?"
                ) from exc
            raise

    def stream(
        self,
        prompt: str | None = None,
        *,
        messages: Sequence[dict[str, Any]] | None = None,
        config: GenerationConfig | None = None,
    ) -> Iterator[str]:
        generation_config = config or self.generation
        if messages is not None:
            prompt_text = self._format_messages(messages)
            normalized_messages = [
                {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
                for m in messages
                if isinstance(m, dict)
            ]
        elif isinstance(prompt, str):
            prompt_text = prompt
            normalized_messages = [{"role": "user", "content": prompt}]
        else:
            raise ValueError("Prompt or messages must be provided.")

        if not prompt_text.strip():
            raise ValueError("Prompt must not be empty.")

        strategy = self._strategy_for_request()

        def _generate_once() -> Iterator[str]:
            if messages is not None:
                completion = self.generate(messages=normalized_messages, config=generation_config)
            else:
                completion = self.generate(prompt_text, config=generation_config)
            if completion:
                yield completion

        extra: dict[str, Any] = {}
        if self.provider == "openai":
            uid = getattr(self.config, "user_id", "default")
            profile = getattr(self.config, "active_profile", "default")
            user_key = uid if uid and uid != "default" else profile
            if user_key:
                extra["user"] = user_key
            if self._tools:
                extra["tools"] = self._tools

        stream_fn = getattr(strategy, "stream", None)
        if not callable(stream_fn):
            return _generate_once()

        try:
            token_iterator = stream_fn(
                prompt_text,
                generation_config,
                messages=normalized_messages,
                **extra,
            )
        except TypeError:
            try:
                token_iterator = stream_fn(prompt_text, generation_config)
            except NotImplementedError:
                return _generate_once()
        except NotImplementedError:
            return _generate_once()

        def _guarded_stream() -> Iterator[str]:
            try:
                yield from token_iterator
            except NotImplementedError:
                yield from _generate_once()

        return _guarded_stream()


__all__ = [
    "LanguageModel",
    "GenerationConfig",
    "register_strategy",
    "TORCH_AVAILABLE",
    "TRANSFORMERS_AVAILABLE",
    "RetryConfig",
]
