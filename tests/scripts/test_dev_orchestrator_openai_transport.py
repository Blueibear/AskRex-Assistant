from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from scripts.dev_orchestrator.openai_budget import OpenAIUsage
from scripts.dev_orchestrator.openai_transport import (
    RESPONSES_URL,
    OpenAIResponsesTransport,
    OpenAITransportError,
)
from scripts.dev_orchestrator.schema import agent_result_schema_payload


class FakeResponse:
    def __init__(self, payload: dict, *, headers: dict[str, str] | None = None) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _agent_result() -> dict:
    return {
        "outcome": "pass",
        "summary": "review clean",
        "next_action": "",
        "needs_user": False,
        "blocker_reason": "",
    }


def _completed_response(*, usage: dict | None = None, text: str | None = None) -> dict:
    return {
        "id": "resp_test",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text if text is not None else json.dumps(_agent_result()),
                    }
                ],
            }
        ],
        "usage": usage if usage is not None else {"input_tokens": 123, "output_tokens": 45},
    }


def test_prepare_is_secret_free_tool_free_and_schema_bound() -> None:
    transport = OpenAIResponsesTransport(
        project_id="proj-test",
        credential_resolver=lambda: "secret-token",
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )

    prepared = transport.prepare(
        model="gpt-5.6-terra",
        instructions="Review safely.",
        input_text="bounded evidence",
        max_output_tokens=4000,
        reasoning_effort="medium",
    )
    body = json.loads(prepared.body)

    assert prepared.model == "gpt-5.6-terra"
    assert prepared.max_output_tokens == 4000
    assert prepared.input_token_ceiling >= len(prepared.body)
    assert body["store"] is False
    assert body["tools"] == []
    assert body["truncation"] == "disabled"
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["schema"] == agent_result_schema_payload(openai_strict=True)
    assert b"secret-token" not in prepared.body
    assert b"proj-test" not in prepared.body


def test_send_injects_headers_only_at_network_boundary() -> None:
    captured = {}

    def opener(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(_completed_response(), headers={"x-request-id": "req_test"})

    transport = OpenAIResponsesTransport(
        project_id="proj-test",
        credential_resolver=lambda: "secret-token",
        opener=opener,
        timeout_seconds=17,
    )
    prepared = transport.prepare(
        model="gpt-5.6-sol",
        instructions="Judge.",
        input_text="evidence",
        max_output_tokens=1000,
        reasoning_effort="high",
    )

    result = transport.send(prepared)
    request = captured["request"]
    assert request.full_url == RESPONSES_URL
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert request.get_header("Openai-project") == "proj-test"
    assert captured["timeout"] == 17
    assert result.output == _agent_result()
    assert result.usage == OpenAIUsage(input_tokens=123, output_tokens=45)
    assert result.request_id == "req_test"


def test_transport_repr_and_errors_do_not_expose_credentials() -> None:
    transport = OpenAIResponsesTransport(
        project_id="proj-secretish",
        credential_resolver=lambda: "sk-super-secret",
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    prepared = transport.prepare(
        model="gpt-5.6-terra",
        instructions="Review.",
        input_text="safe",
        max_output_tokens=100,
        reasoning_effort="low",
    )

    assert "sk-super-secret" not in repr(transport)
    assert "proj-secretish" not in repr(transport)
    with pytest.raises(OpenAITransportError) as exc_info:
        transport.send(prepared)
    text = str(exc_info.value)
    assert "sk-super-secret" not in text
    assert "proj-secretish" not in text
    assert exc_info.value.provider == "openai"
    assert exc_info.value.category == "transient"


def _http_error(status: int, payload: dict, request_id: str = "req_error") -> HTTPError:
    headers = {"x-request-id": request_id}
    return HTTPError(
        RESPONSES_URL,
        status,
        "provider error",
        headers,
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (401, "invalid_api_key", "auth"),
        (403, "forbidden", "auth"),
        (429, "rate_limit_exceeded", "rate_limit"),
        (429, "project_spend_limit_exceeded", "billing"),
        (429, "organization_spend_limit_exceeded", "billing"),
        (500, "server_error", "transient"),
    ],
)
def test_http_errors_are_typed_without_raw_body(status: int, code: str, expected: str) -> None:
    payload = {"error": {"code": code, "message": "raw provider secret detail"}}
    transport = OpenAIResponsesTransport(
        project_id="proj-test",
        credential_resolver=lambda: "secret-token",
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(_http_error(status, payload)),
    )
    prepared = transport.prepare(
        model="gpt-5.6-terra",
        instructions="Review.",
        input_text="safe",
        max_output_tokens=100,
        reasoning_effort="low",
    )

    with pytest.raises(OpenAITransportError) as exc_info:
        transport.send(prepared)
    exc = exc_info.value
    assert exc.category == expected
    assert exc.http_status == status
    assert exc.request_id == "req_error"
    assert "raw provider secret detail" not in str(exc)


@pytest.mark.parametrize(
    "payload",
    [
        {**_completed_response(), "usage": None},
        {**_completed_response(), "status": "incomplete"},
        {
            **_completed_response(),
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "refusal", "refusal": "cannot comply"}],
                }
            ],
        },
        _completed_response(text="not-json"),
    ],
)
def test_invalid_completed_output_or_usage_fails_closed(payload: dict) -> None:
    transport = OpenAIResponsesTransport(
        project_id="proj-test",
        credential_resolver=lambda: "secret-token",
        opener=lambda *args, **kwargs: FakeResponse(payload),
    )
    prepared = transport.prepare(
        model="gpt-5.6-terra",
        instructions="Review.",
        input_text="safe",
        max_output_tokens=100,
        reasoning_effort="low",
    )

    with pytest.raises(OpenAITransportError) as exc_info:
        transport.send(prepared)
    assert exc_info.value.category == "invalid_output"


def test_timeout_and_missing_credential_are_typed() -> None:
    timed_out = OpenAIResponsesTransport(
        project_id="proj-test",
        credential_resolver=lambda: "secret-token",
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("slow")),
    )
    prepared = timed_out.prepare(
        model="gpt-5.6-terra",
        instructions="Review.",
        input_text="safe",
        max_output_tokens=100,
        reasoning_effort="low",
    )
    with pytest.raises(OpenAITransportError, match="timeout") as exc_info:
        timed_out.send(prepared)
    assert exc_info.value.category == "timeout"

    no_key = OpenAIResponsesTransport(
        project_id="proj-test",
        credential_resolver=lambda: None,
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    with pytest.raises(OpenAITransportError) as missing:
        no_key.send(prepared)
    assert missing.value.category == "auth"


def test_schema_payload_is_a_copy_of_the_canonical_schema() -> None:
    first = agent_result_schema_payload()
    second = agent_result_schema_payload()
    first["type"] = "changed"
    assert second["type"] == "object"
    assert "$schema" in second


def test_openai_schema_projection_matches_strict_supported_subset() -> None:
    transport = OpenAIResponsesTransport(
        project_id="proj-test",
        credential_resolver=lambda: "secret-token",
        opener=lambda *args, **kwargs: None,
    )
    prepared = transport.prepare(
        model="gpt-5.6-terra",
        instructions="Review.",
        input_text="safe",
        max_output_tokens=100,
        reasoning_effort="low",
    )
    schema = json.loads(prepared.body)["text"]["format"]["schema"]

    assert "$schema" not in schema
    assert "allOf" not in schema
    assert set(schema["required"]) == set(schema["properties"])
    forbidden = {"allOf", "not", "dependentRequired", "dependentSchemas", "if", "then", "else"}

    def assert_supported(node):
        if isinstance(node, dict):
            assert forbidden.isdisjoint(node)
            for value in node.values():
                assert_supported(value)
        elif isinstance(node, list):
            for value in node:
                assert_supported(value)

    assert_supported(schema)


def test_transport_does_not_retry_paid_requests_automatically() -> None:
    calls = 0

    def opener(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise _http_error(500, {"error": {"code": "server_error"}})

    transport = OpenAIResponsesTransport(
        project_id="proj-test",
        credential_resolver=lambda: "secret-token",
        opener=opener,
    )
    prepared = transport.prepare(
        model="gpt-5.6-terra",
        instructions="Review.",
        input_text="safe",
        max_output_tokens=100,
        reasoning_effort="low",
    )

    with pytest.raises(OpenAITransportError):
        transport.send(prepared)
    assert calls == 1


def test_malformed_usage_is_invalid_output() -> None:
    payload = _completed_response(usage={"input_tokens": "123", "output_tokens": 45})
    transport = OpenAIResponsesTransport(
        project_id="proj-test",
        credential_resolver=lambda: "secret-token",
        opener=lambda *args, **kwargs: FakeResponse(payload),
    )
    prepared = transport.prepare(
        model="gpt-5.6-terra",
        instructions="Review.",
        input_text="safe",
        max_output_tokens=100,
        reasoning_effort="low",
    )
    with pytest.raises(OpenAITransportError) as exc_info:
        transport.send(prepared)
    assert exc_info.value.category == "invalid_output"
