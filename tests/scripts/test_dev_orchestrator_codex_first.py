"""Fast provider-order tests that never start real agents or Git workers."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.dev_orchestrator import runner
from scripts.dev_orchestrator.runner import AgentInvocationError, CliAgentInvoker
from scripts.dev_orchestrator.types import TaskItem, WorkerState


def invoker_stub(tmp_path: Path, execute):
    agent = object.__new__(CliAgentInvoker)
    agent.config = SimpleNamespace(coordination_root=tmp_path, claude_cloud_mode="disabled")
    agent._repo = lambda role: tmp_path
    agent._cloud_task_allowed = lambda task: False
    agent._execute_agent = execute
    agent._finish = lambda provider, result: (provider, result)
    return agent


def run(agent):
    return agent.implement(
        "backend", WorkerState("backend"), TaskItem("B-TEST", "Implement"), "context", "sonnet"
    )


def test_codex_first_without_claude_or_api(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "read_cloud_session", lambda *_: None)
    calls = []

    def execute(command, repo, role, provider, invocation_id, **kwargs):
        calls.append((command, provider))
        return "ok"

    assert run(invoker_stub(tmp_path, execute)) == ("codex", "ok")
    assert len(calls) == 1
    assert calls[0][0][0].lower().startswith("codex")
    assert calls[0][1] == "codex"
    assert 'forced_login_method="chatgpt"' in calls[0][0]


@pytest.mark.parametrize("failure", ["auth", "usage_limit"])
def test_claude_fallback_only_on_codex_access_failure(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(runner, "read_cloud_session", lambda *_: None)
    calls = []

    def execute(command, repo, role, provider, invocation_id, **kwargs):
        calls.append(provider)
        if provider == "codex":
            raise AgentInvocationError("codex", failure, "temporary")
        return "claude-ok"

    assert run(invoker_stub(tmp_path, execute)) == ("claude", "claude-ok")
    assert calls == ["codex", "claude"]


def test_no_fallback_for_unsafe_codex_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "read_cloud_session", lambda *_: None)
    calls = []

    def execute(command, repo, role, provider, invocation_id, **kwargs):
        calls.append(provider)
        raise AgentInvocationError(provider, "invalid_state", "unsafe")

    with pytest.raises(AgentInvocationError, match="invalid_state"):
        run(invoker_stub(tmp_path, execute))
    assert calls == ["codex"]


def test_existing_cloud_writer_blocks_new_local_writer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner, "read_cloud_session", lambda *_: SimpleNamespace(session_id="existing")
    )

    def execute(*args, **kwargs):
        raise AssertionError("must not start competing worker")

    with pytest.raises(AgentInvocationError, match="active cloud session"):
        run(invoker_stub(tmp_path, execute))


def test_both_subscription_providers_unavailable_blocks_without_api(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "read_cloud_session", lambda *_: None)
    calls = []

    def execute(command, repo, role, provider, invocation_id, **kwargs):
        calls.append(provider)
        raise AgentInvocationError(provider, "usage_limit", "limit")

    with pytest.raises(AgentInvocationError) as error:
        run(invoker_stub(tmp_path, execute))
    assert error.value.provider == "claude"
    assert calls == ["codex", "claude"]


def test_existing_cloud_session_is_polled_without_competing_codex(tmp_path):
    def execute(*args, **kwargs):
        raise AssertionError("must not start a local writer")

    agent = invoker_stub(tmp_path, execute)
    agent._cloud_task_allowed = lambda task: True
    agent._cloud_implementation_state = lambda *args: "existing-cloud-session"
    assert run(agent) == "existing-cloud-session"
