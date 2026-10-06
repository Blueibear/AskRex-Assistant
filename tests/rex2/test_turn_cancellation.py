from __future__ import annotations

import asyncio

import pytest

from rex.runtime.cancellation import (
    TurnCancelledError,
    current_turn_cancellation,
)
from rex.runtime.events import EventKind
from rex.runtime.turn import (
    AuthorizationSnapshotRef,
    ResponseMode,
    TurnContext,
    TurnScope,
    TurnSource,
)
from rex.runtime.turn_engine import TurnEngine


def _context(user_id: str = "james") -> TurnContext:
    return TurnContext.create(
        user_id=user_id,
        scope=TurnScope.USER,
        source=TurnSource.ELECTRON,
        device_id="desktop-1",
        response_mode=ResponseMode.SCREEN,
        authorization=AuthorizationSnapshotRef("policy:v1", f"permissions:{user_id}:v1"),
    )


def test_cancellation_is_idempotent_and_preserves_first_reason() -> None:
    context = _context()

    assert context.cancellation.cancel("caller_cancelled") is True
    assert context.cancellation.cancel("replacement_turn") is False
    assert context.cancellation.cancelled is True
    assert context.cancellation.reason == "caller_cancelled"

    with pytest.raises(TurnCancelledError, match="caller_cancelled"):
        context.cancellation.raise_if_cancelled()


def test_each_turn_owns_an_isolated_cancellation_scope() -> None:
    james = _context("james")
    cole = _context("cole")

    james.cancellation.cancel("barge_in")

    assert james.cancellation.cancelled is True
    assert cole.cancellation.cancelled is False
    assert james.cancellation is not cole.cancellation


def test_cancelled_before_dispatch_skips_operation_and_emits_one_terminal() -> None:
    context = _context()
    observed = []
    called = False
    context.cancellation.cancel("stale_request")

    def operation(_events):
        nonlocal called
        called = True
        return "must-not-run"

    with pytest.raises(TurnCancelledError, match="stale_request"):
        TurnEngine().execute(context, operation, on_event=observed.append)

    assert called is False
    assert [event.kind for event in observed] == [
        EventKind.TURN_STARTED,
        EventKind.CANCELLED,
    ]
    assert sum(event.is_terminal for event in observed) == 1
    assert observed[-1].details["reason"] == "stale_request"


@pytest.mark.asyncio
async def test_cancellation_during_async_work_emits_cancelled_not_failed() -> None:
    context = _context()
    observed = []

    async def operation(_events):
        assert current_turn_cancellation() is context.cancellation
        await asyncio.sleep(0)
        context.cancellation.cancel("barge_in")
        context.cancellation.raise_if_cancelled()
        return "stale-output"

    with pytest.raises(TurnCancelledError, match="barge_in"):
        await TurnEngine().execute_async(context, operation, on_event=observed.append)

    assert observed[-1].kind is EventKind.CANCELLED
    assert sum(event.is_terminal for event in observed) == 1
    assert current_turn_cancellation() is None


@pytest.mark.asyncio
async def test_engine_cancel_turn_is_identity_bound_and_interrupts_wait() -> None:
    from rex.runtime.cancellation import await_with_cancellation

    engine = TurnEngine()
    context = _context("james")
    observed = []
    entered = asyncio.Event()

    async def operation(_events):
        entered.set()
        await await_with_cancellation(asyncio.sleep(30))
        return "stale-output"

    task = asyncio.create_task(engine.execute_async(context, operation, on_event=observed.append))
    await entered.wait()

    assert engine.cancel_turn(context.turn_id, user_id="cole", reason="wrong-user") is False
    assert engine.cancel_turn(context.turn_id, user_id="james", reason="replacement_turn") is True
    with pytest.raises(TurnCancelledError, match="replacement_turn"):
        await asyncio.wait_for(task, timeout=1.0)
    assert observed[-1].kind is EventKind.CANCELLED


@pytest.mark.asyncio
async def test_cancellation_during_model_generation_ignores_stale_output() -> None:
    from threading import Event
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from rex.actions.dispatcher import ActionDispatcher
    from rex.intent.router import IntentResult

    entered = Event()
    release = Event()
    llm = MagicMock()

    def generate(*_args, **_kwargs):
        entered.set()
        release.wait(5)
        return "stale model output"

    llm.generate.side_effect = generate
    builder = MagicMock()
    package = SimpleNamespace(messages=[], prompt="prompt")
    builder.build.return_value = package
    result_handler = MagicMock()
    result_handler.process = AsyncMock(return_value="stale model output")
    dispatcher = ActionDispatcher(
        context_builder=builder,
        llm=llm,
        result_handler=result_handler,
    )
    context = _context()
    engine = TurnEngine()
    observed = []

    async def operation(events):
        return await dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type=None),
            package,
            "explain this",
            user_id="james",
            turn_events=events,
        )

    task = asyncio.create_task(engine.execute_async(context, operation, on_event=observed.append))
    assert await asyncio.to_thread(entered.wait, 1.0)
    assert engine.cancel_turn(context.turn_id, user_id="james", reason="replacement_turn")
    try:
        with pytest.raises(TurnCancelledError, match="replacement_turn"):
            await asyncio.wait_for(task, timeout=0.5)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    assert observed[-1].kind is EventKind.CANCELLED
    assert result_handler.process.await_count == 0


def test_cancellation_during_read_only_tool_work_stops_wait(monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from rex.tools.execution import ToolExecutionLifecycle

    monkeypatch.setattr("rex.tools.execution.get_audit_logger", lambda: MagicMock())
    entered = Event()
    release = Event()

    def handler():
        entered.set()
        release.wait(5)
        return {"value": "stale"}

    tool = SimpleNamespace(
        name="slow_read",
        handler=handler,
        operation="read",
        risk="safe",
        requires_identity=False,
        required_args=(),
        verifier=None,
    )
    lifecycle = ToolExecutionLifecycle()
    engine = TurnEngine()
    context = _context()

    def operation(_events):
        return lifecycle.execute(tool, {}, timeout_seconds=5.0)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(engine.execute, context, operation)
        assert entered.wait(1.0)
        assert engine.cancel_turn(context.turn_id, user_id="james", reason="stale_read")
        try:
            with pytest.raises(TurnCancelledError, match="stale_read"):
                future.result(timeout=0.5)
        finally:
            release.set()
            try:
                future.result(timeout=2.0)
            except BaseException:
                pass


def test_cancellation_after_mutation_dispatch_is_attempted_unverified(monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from rex.tools.execution import ToolExecutionLifecycle

    monkeypatch.setattr("rex.tools.execution.get_audit_logger", lambda: MagicMock())
    entered = Event()
    release = Event()
    holder = {}

    def handler():
        entered.set()
        release.wait(5)
        return {"ok": True}

    tool = SimpleNamespace(
        name="slow_mutation",
        handler=handler,
        operation="mutation",
        risk="safe",
        requires_identity=True,
        required_args=(),
        verifier=None,
    )
    lifecycle = ToolExecutionLifecycle()
    engine = TurnEngine()
    context = _context()

    def operation(_events):
        holder["result"] = lifecycle.execute(
            tool,
            {},
            context={"user_id": "james", "request_id": "cancel-mutation"},
            timeout_seconds=5.0,
        )
        return holder["result"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(engine.execute, context, operation)
        assert entered.wait(1.0)
        assert engine.cancel_turn(context.turn_id, user_id="james", reason="transport_lost")
        try:
            with pytest.raises(TurnCancelledError, match="transport_lost"):
                future.result(timeout=0.5)
        finally:
            release.set()
            try:
                future.result(timeout=2.0)
            except BaseException:
                pass

    assert holder["result"].status == "attempted_unverified"
    assert holder["result"].success is False


def test_openclaw_retry_stops_after_turn_cancellation(monkeypatch) -> None:
    from requests.exceptions import ConnectionError as RequestsConnectionError

    from rex.openclaw.http_client import OpenClawClient
    from rex.runtime.cancellation import turn_cancellation_scope

    context = _context()
    client = OpenClawClient("http://127.0.0.1:18789", "token", max_retries=3)
    calls = 0

    def request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        context.cancellation.cancel("openclaw_cancelled")
        raise RequestsConnectionError("gateway unavailable")

    monkeypatch.setattr(client._session, "request", request)
    monkeypatch.setattr("rex.openclaw.http_client.time.sleep", lambda _seconds: None)

    with turn_cancellation_scope(context.cancellation):
        with pytest.raises(TurnCancelledError, match="openclaw_cancelled"):
            client.get("/tools")
    assert calls == 1


def test_openclaw_stream_stops_before_stale_chunk(monkeypatch) -> None:
    from types import SimpleNamespace

    from rex.openclaw.http_client import OpenClawClient
    from rex.runtime.cancellation import turn_cancellation_scope

    context = _context()
    client = OpenClawClient("http://127.0.0.1:18789", "token")
    response = SimpleNamespace(
        status_code=200,
        text="",
        iter_lines=lambda **_kwargs: iter(
            [
                'data: {"choices":[{"delta":{"content":"first"}}]}',
                'data: {"choices":[{"delta":{"content":"stale"}}]}',
            ]
        ),
    )
    monkeypatch.setattr(client._session, "post", lambda *_args, **_kwargs: response)

    with turn_cancellation_scope(context.cancellation):
        chunks = client.post_stream("/v1/chat/completions")
        assert next(chunks) == "first"
        context.cancellation.cancel("stream_replaced")
        with pytest.raises(TurnCancelledError, match="stream_replaced"):
            next(chunks)


@pytest.mark.asyncio
async def test_tts_provider_wait_is_cancelled_without_stale_fallback() -> None:
    from threading import Event

    from rex.voice.tts import TextToSpeech

    entered = asyncio.Event()
    tts = TextToSpeech.__new__(TextToSpeech)
    tts._provider = "edge"
    # This fixture bypasses ``__init__`` to isolate the provider boundary.
    # Preserve the constructor's disabled-router state so ``speak`` reaches
    # the deliberately slow provider below.
    tts._speech_router = None
    tts._edge_voice = "test"
    tts._tts_output_device = None
    tts._xtts_init_error = None
    tts._speaking = Event()
    tts._clean_text = lambda text: text
    tts._settings_int = lambda _name, default: default

    async def slow_edge(_text, *, request_started_at):
        del request_started_at
        entered.set()
        await asyncio.sleep(30)
        return {"path_used": "stale-edge"}

    tts._speak_edge = slow_edge
    context = _context()
    engine = TurnEngine()

    task = asyncio.create_task(engine.execute_async(context, lambda _events: tts.speak("hello")))
    try:
        # A bounded wait turns a fixture/setup regression into a useful
        # failure instead of allowing the release suite to hang forever.
        await asyncio.wait_for(entered.wait(), timeout=0.5)
        assert engine.cancel_turn(context.turn_id, user_id="james", reason="barge_in")

        with pytest.raises(TurnCancelledError, match="barge_in"):
            await asyncio.wait_for(task, timeout=0.5)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert tts.is_speaking() is False


@pytest.mark.asyncio
async def test_external_task_cancellation_emits_canonical_cancelled_terminal() -> None:
    context = _context()
    engine = TurnEngine()
    observed = []
    entered = asyncio.Event()

    async def operation(_events):
        entered.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(engine.execute_async(context, operation, on_event=observed.append))
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert context.cancellation.cancelled is True
    assert observed[-1].kind is EventKind.CANCELLED
    assert observed[-1].details["reason"] == "task_cancelled"
    assert sum(event.is_terminal for event in observed) == 1


@pytest.mark.asyncio
async def test_cancelled_delivery_does_not_release_stale_second_sentence() -> None:
    from unittest.mock import MagicMock

    from rex.assistant import Assistant

    context = _context()
    engine = TurnEngine()
    observed = []
    delivered = []
    assistant = Assistant.__new__(Assistant)

    async def sink(chunk: str) -> None:
        delivered.append(chunk)
        if len(delivered) == 1:
            context.cancellation.cancel("stale_delivery")

    async def operation(events):
        await assistant._deliver_safe_response(
            "First sentence. Second sentence.",
            turn_events=events,
            response_sink=sink,
            latency_trace=MagicMock(),
            stream_started_ns=None,
        )
        return "unused"

    with pytest.raises(TurnCancelledError, match="stale_delivery"):
        await engine.execute_async(context, operation, on_event=observed.append)

    assert delivered == ["First sentence."]
    assert observed[-1].kind is EventKind.CANCELLED


@pytest.mark.asyncio
async def test_cancellation_during_retrieval_enrichment_ignores_stale_result() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from rex.actions.dispatcher import ActionDispatcher
    from rex.intent.router import IntentResult

    entered = asyncio.Event()
    release = asyncio.Event()
    builder = MagicMock()
    package = SimpleNamespace(messages=[], prompt="prompt")
    builder.build.return_value = package
    llm = MagicMock()
    llm.generate.return_value = "base answer"
    result_handler = MagicMock()
    result_handler.process = AsyncMock(return_value="base answer with stale enrichment")

    async def retrieve(_transcript: str) -> list[str]:
        entered.set()
        await release.wait()
        return ["stale enrichment"]

    dispatcher = ActionDispatcher(
        context_builder=builder,
        llm=llm,
        result_handler=result_handler,
        run_plugins_fn=retrieve,
    )
    context = _context()
    engine = TurnEngine()

    async def operation(events):
        return await dispatcher.dispatch(
            IntentResult(handled=False, response=None, intent_type=None),
            package,
            "research this",
            user_id="james",
            turn_events=events,
        )

    task = asyncio.create_task(engine.execute_async(context, operation))
    await entered.wait()
    assert engine.cancel_turn(context.turn_id, user_id="james", reason="query_replaced")

    try:
        with pytest.raises(TurnCancelledError, match="query_replaced"):
            await asyncio.wait_for(task, timeout=0.5)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    assert result_handler.process.await_count == 0


def test_cancelled_mutation_exception_remains_attempted_unverified(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from rex.tools.execution import ToolExecutionLifecycle

    monkeypatch.setattr("rex.tools.execution.get_audit_logger", lambda: MagicMock())
    context = _context()

    def handler():
        context.cancellation.cancel("transport_lost")
        raise RuntimeError("connection dropped after possible write")

    tool = SimpleNamespace(
        name="uncertain_mutation",
        handler=handler,
        operation="mutation",
        risk="safe",
        requires_identity=True,
        required_args=(),
        verifier=None,
    )
    lifecycle = ToolExecutionLifecycle()
    engine = TurnEngine()

    def operation(_events):
        return lifecycle.execute(
            tool,
            {},
            context={"user_id": "james", "request_id": "cancel-exception"},
            timeout_seconds=1.0,
        )

    with pytest.raises(TurnCancelledError, match="transport_lost"):
        engine.execute(context, operation)

    result = lifecycle.execute(
        tool,
        {},
        context={"user_id": "james", "request_id": "cancel-exception"},
        timeout_seconds=1.0,
    )
    assert result.status == "attempted_unverified"
    assert result.success is False
