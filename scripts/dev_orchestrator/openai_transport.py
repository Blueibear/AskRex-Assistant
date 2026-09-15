from __future__ import annotations

import json
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from rex.credentials import CredentialManager

from .openai_budget import OpenAIUsage
from .schema import agent_result_schema_payload

RESPONSES_URL = "https://api.openai.com/v1/responses"
_BILLING_CODES = {
    "project_spend_limit_exceeded",
    "organization_spend_limit_exceeded",
    "insufficient_quota",
}


@dataclass(frozen=True)
class PreparedOpenAIRequest:
    body: bytes
    input_token_ceiling: int
    model: str
    max_output_tokens: int


@dataclass(frozen=True)
class OpenAITransportResponse:
    output: Mapping[str, Any]
    usage: OpenAIUsage
    request_id: str


class OpenAITransportError(RuntimeError):
    def __init__(
        self,
        category: str,
        *,
        http_status: int | None = None,
        provider_code: str = "",
        request_id: str = "",
    ) -> None:
        self.provider = "openai"
        self.category = category
        self.http_status = http_status
        self.provider_code = provider_code
        self.request_id = request_id
        details = [f"category={category}"]
        if http_status is not None:
            details.append(f"http_status={http_status}")
        if provider_code:
            details.append(f"code={provider_code}")
        if request_id:
            details.append(f"request_id={request_id}")
        super().__init__("OpenAI transport error (" + ", ".join(details) + ")")


def _default_credential_resolver() -> str | None:
    return CredentialManager().get_token("openai")


def _header(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value:
            return str(value)
    return ""


def _safe_error_payload(exc: HTTPError) -> tuple[str, str]:
    request_id = _header(exc.headers, "x-request-id")
    provider_code = ""
    try:
        raw = exc.read()
        payload = json.loads(raw.decode("utf-8"))
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str):
                provider_code = code
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        pass
    return provider_code, request_id


class OpenAIResponsesTransport:
    def __init__(
        self,
        *,
        project_id: str,
        credential_resolver: Callable[[], str | None] | None = None,
        opener: Callable[..., Any] | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        if not project_id.strip():
            raise ValueError("OpenAI project ID is required")
        if timeout_seconds <= 0:
            raise ValueError("OpenAI timeout must be positive")
        self._project_id = project_id.strip()
        self._credential_resolver = credential_resolver or _default_credential_resolver
        self._opener = opener or urlopen
        self._timeout_seconds = timeout_seconds

    def __repr__(self) -> str:
        return f"OpenAIResponsesTransport(timeout_seconds={self._timeout_seconds})"

    def prepare(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        max_output_tokens: int,
        reasoning_effort: str,
    ) -> PreparedOpenAIRequest:
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        body = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "store": False,
            "tools": [],
            "truncation": "disabled",
            "max_output_tokens": max_output_tokens,
            "reasoning": {"effort": reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "askrex_agent_result",
                    "strict": True,
                    "schema": agent_result_schema_payload(openai_strict=True),
                }
            },
        }
        body_bytes = json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return PreparedOpenAIRequest(
            body=body_bytes,
            input_token_ceiling=len(body_bytes),
            model=model,
            max_output_tokens=max_output_tokens,
        )

    @staticmethod
    def _http_category(status: int, provider_code: str) -> str:
        if provider_code in _BILLING_CODES:
            return "billing"
        if status in {401, 403}:
            return "auth"
        if status == 429:
            return "rate_limit"
        if status in {408, 504}:
            return "timeout"
        if 500 <= status <= 599:
            return "transient"
        return "failed"

    def _credential(self) -> str:
        try:
            token = self._credential_resolver()
        except Exception as exc:
            raise OpenAITransportError("auth") from exc
        if not isinstance(token, str) or not token.strip():
            raise OpenAITransportError("auth")
        return token.strip()

    def send(self, prepared: PreparedOpenAIRequest) -> OpenAITransportResponse:
        token = self._credential()
        request = Request(
            RESPONSES_URL,
            data=prepared.body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "OpenAI-Project": self._project_id,
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                request_id = _header(response.headers, "x-request-id")
                raw = response.read()
        except HTTPError as exc:
            provider_code, request_id = _safe_error_payload(exc)
            raise OpenAITransportError(
                self._http_category(exc.code, provider_code),
                http_status=exc.code,
                provider_code=provider_code,
                request_id=request_id,
            ) from exc
        except TimeoutError as exc:
            raise OpenAITransportError("timeout") from exc
        except URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise OpenAITransportError("timeout") from exc
            raise OpenAITransportError("transient") from exc
        except OSError as exc:
            raise OpenAITransportError("transient") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenAITransportError("invalid_output", request_id=request_id) from exc
        return self._parse_completed(payload, request_id=request_id)

    @staticmethod
    def _usage(payload: Mapping[str, Any], request_id: str) -> OpenAIUsage:
        usage = payload.get("usage")
        if not isinstance(usage, Mapping):
            raise OpenAITransportError("invalid_output", request_id=request_id)
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        values = (input_tokens, output_tokens)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values
        ):
            raise OpenAITransportError("invalid_output", request_id=request_id)
        return OpenAIUsage(input_tokens=input_tokens, output_tokens=output_tokens)

    @staticmethod
    def _output_texts(payload: Mapping[str, Any]) -> list[str]:
        texts: list[str] = []
        output = payload.get("output")
        if not isinstance(output, list):
            return texts
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, Mapping) and part.get("type") == "output_text":
                    text = part.get("text")
                    if isinstance(text, str):
                        texts.append(text)
        return texts

    @classmethod
    def _parse_completed(cls, payload: Any, *, request_id: str = "") -> OpenAITransportResponse:
        if not isinstance(payload, Mapping) or payload.get("status") != "completed":
            raise OpenAITransportError("invalid_output", request_id=request_id)
        response_request_id = request_id or str(payload.get("id", ""))
        usage = cls._usage(payload, response_request_id)
        texts = cls._output_texts(payload)
        if len(texts) != 1:
            raise OpenAITransportError("invalid_output", request_id=response_request_id)
        try:
            output = json.loads(texts[0])
        except json.JSONDecodeError as exc:
            raise OpenAITransportError("invalid_output", request_id=response_request_id) from exc
        if not isinstance(output, Mapping):
            raise OpenAITransportError("invalid_output", request_id=response_request_id)
        return OpenAITransportResponse(
            output=dict(output),
            usage=usage,
            request_id=response_request_id,
        )
