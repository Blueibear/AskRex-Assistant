"""Regression tests for explicit identity binding on ``Assistant`` (issue #303).

Policy under test:

- ``Assistant()`` (no ``user_id``) is an *unbound* instance: it never assigns
  ``"default"``, never inherits ``settings.user_id``, and performs no
  user-scoped reads or writes at construction time.
- ``Assistant(user_id="default")`` is a valid, deliberate selection of the
  profile named ``"default"``; it is not an automatic fallback.
- Every private request path (intent routing, cache lookup, early returns,
  history reads/writes, context construction, action/tool dispatch,
  streaming) requires an explicit validated identity — either the bound
  constructor identity or a per-request ``active_user_id`` — and fails
  closed with :class:`~rex.assistant_errors.IdentityRequiredError` when
  neither is available.
- Invalid identities fail canonical validation (``ValueError``); they are
  never sanitized into valid authorization keys.
- Overlapping requests for different users never observe each other's
  identity, history, cache partition, or tool context.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import rex.assistant as assistant_module
from rex.assistant_errors import IdentityRequiredError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dummy_lm_class():
    class DummyLanguageModel:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, prompt=None, *, messages=None, config=None, max_tool_rounds=3):
            return "ok"

    return DummyLanguageModel


def _app_config(tmp_path, **overrides):
    from rex.config import AppConfig

    kwargs = {
        "llm_provider": "transformers",
        "persist_history": False,
        "transcripts_dir": tmp_path / "transcripts",
    }
    kwargs.update(overrides)
    return AppConfig(**kwargs)


def _make_assistant(user_id, history_store=None):
    """Build a minimal Assistant shell without running the heavy ``__init__``.

    ``user_id=None`` models an unbound instance.
    """
    from rex.assistant import Assistant

    a = Assistant.__new__(Assistant)
    a._settings = MagicMock()
    a._settings.max_memory_items = 50
    a._settings.persist_history = False
    a._settings.followups_enabled = False
    a._settings.model_routing = None
    a._settings.transcripts_enabled = False
    a._user_id = user_id
    a._histories = {}
    a._history_limit = 50
    a._plugins = []
    a._history_store = history_store
    a._followup_engine = None
    a._followup_lock = asyncio.Lock()
    a._router = None
    a._response_cache = None
    a._ha_bridge = None
    a._suggestion_engine = None
    a._pattern_entries = {}
    return a


def _wire_fake_dispatch(a, reply_text="dispatched reply", capture=None):
    """Attach a fake intent router + dispatcher so generate_reply reaches
    the response-builder path and returns *reply_text*."""
    from rex.actions.dispatcher import ActionResult
    from rex.intent.router import IntentResult

    ir = MagicMock()
    ir.route.return_value = IntentResult(handled=False, response=None, intent_type=None)
    a._intent_router = ir

    async def _fake_dispatch(intent, ctx, transcript, **kwargs):
        if capture is not None:
            capture.append({"transcript": transcript, **kwargs})
        return ActionResult(success=True, response=reply_text)

    ad = MagicMock()
    ad.dispatch = _fake_dispatch
    a._action_dispatcher = ad
    a._llm = MagicMock()
    a._llm.model_name = "m"
    return ir, ad


class _SpyHistoryStore:
    """Stands in for HistoryStore: records calls, touches no filesystem."""

    def __init__(self, db_path=None):
        self.db_path = db_path
        self.load_calls: list[str] = []
        self.save_calls: list[tuple[str, str, str]] = []
        self.prune_calls: list[str] = []

    def load_history(self, user_id, limit=50, conversation_id=None):
        self.load_calls.append(user_id)
        return []

    def save_turn(self, user_id, role, content, timestamp):
        self.save_calls.append((user_id, role, content))

    def prune(self, user_id, keep_days=30):
        self.prune_calls.append(user_id)
        return 0


# ---------------------------------------------------------------------------
# Constructor binding
# ---------------------------------------------------------------------------


class TestConstructorBinding:
    def test_constructor_without_user_id_is_unbound(self, monkeypatch, tmp_path):
        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        a = assistant_module.Assistant(settings_obj=_app_config(tmp_path))
        assert a.user_id is None
        assert a._user_id is None

    def test_constructor_does_not_assign_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        a = assistant_module.Assistant(settings_obj=_app_config(tmp_path))
        assert a.user_id != "default"
        assert "default" not in a._histories

    def test_constructor_does_not_inherit_settings_user_id(self, monkeypatch, tmp_path):
        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        cfg = _app_config(tmp_path, user_id="settingsuser")
        a = assistant_module.Assistant(settings_obj=cfg)
        assert a.user_id is None

    def test_unbound_construction_does_not_load_persisted_history(self, monkeypatch, tmp_path):
        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        monkeypatch.setattr(assistant_module, "HistoryStore", _SpyHistoryStore)
        cfg = _app_config(tmp_path, persist_history=True, history_db_path=tmp_path / "history.db")
        a = assistant_module.Assistant(settings_obj=cfg)
        store = a._history_store
        # Creating a neutral handle is acceptable; reading rows is not.
        assert store is None or (store.load_calls == [] and store.prune_calls == [])

    def test_bound_construction_preloads_only_own_history(self, monkeypatch, tmp_path):
        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        monkeypatch.setattr(assistant_module, "HistoryStore", _SpyHistoryStore)
        cfg = _app_config(tmp_path, persist_history=True, history_db_path=tmp_path / "history.db")
        a = assistant_module.Assistant(settings_obj=cfg, user_id="james")
        assert a._history_store.load_calls == ["james"]
        assert set(a._history_store.prune_calls) <= {"james"}

    def test_unbound_construction_does_not_init_followup_state(self, monkeypatch, tmp_path):
        import rex.followup_engine as fe

        calls: list[str] = []

        def _spy_init(settings, user_id):
            calls.append(user_id)
            return None, None

        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        monkeypatch.setattr(fe, "init_followup_engine", _spy_init)
        assistant_module.Assistant(settings_obj=_app_config(tmp_path))
        assert calls == []

    def test_bound_construction_inits_followup_state_for_owner(self, monkeypatch, tmp_path):
        import rex.followup_engine as fe

        calls: list[str] = []

        def _spy_init(settings, user_id):
            calls.append(user_id)
            return None, None

        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        monkeypatch.setattr(fe, "init_followup_engine", _spy_init)
        assistant_module.Assistant(settings_obj=_app_config(tmp_path), user_id="default")
        assert calls == ["default"]

    def test_explicit_default_binds(self, monkeypatch, tmp_path):
        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        a = assistant_module.Assistant(settings_obj=_app_config(tmp_path), user_id="default")
        assert a.user_id == "default"

    def test_explicit_named_user_binds(self, monkeypatch, tmp_path):
        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        a = assistant_module.Assistant(settings_obj=_app_config(tmp_path), user_id="james")
        assert a.user_id == "james"

    @pytest.mark.parametrize(
        "bad_id",
        ["", "..", "a/b", "a\\b", "con", "COM1", " james", "james ", "user!", ".hidden"],
    )
    def test_invalid_constructor_ids_fail_canonical_validation(self, monkeypatch, tmp_path, bad_id):
        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        with pytest.raises(ValueError):
            assistant_module.Assistant(settings_obj=_app_config(tmp_path), user_id=bad_id)

    def test_invalid_id_is_never_sanitized(self, monkeypatch, tmp_path):
        """`` james `` must raise — not silently become ``james``."""
        monkeypatch.setattr(assistant_module, "LanguageModel", _make_dummy_lm_class())
        with pytest.raises(ValueError):
            assistant_module.Assistant(settings_obj=_app_config(tmp_path), user_id=" james ")


# ---------------------------------------------------------------------------
# Missing identity fails closed on every private request path
# ---------------------------------------------------------------------------


class TestMissingIdentityFailsClosed:
    def test_generate_reply_fails_before_intent_routing(self):
        a = _make_assistant(user_id=None)
        ir, _ = _wire_fake_dispatch(a)
        with pytest.raises(IdentityRequiredError):
            asyncio.run(a.generate_reply("hello"))
        ir.route.assert_not_called()

    def test_generate_reply_fails_before_cache_lookup(self):
        a = _make_assistant(user_id=None)
        _wire_fake_dispatch(a)
        cache = MagicMock()
        a._response_cache = cache
        with pytest.raises(IdentityRequiredError):
            asyncio.run(a.generate_reply("what is the capital of France"))
        cache.get.assert_not_called()
        cache.put.assert_not_called()

    def test_generate_reply_fails_before_history_recording(self):
        store = MagicMock()
        a = _make_assistant(user_id=None, history_store=store)
        _wire_fake_dispatch(a)
        with pytest.raises(IdentityRequiredError):
            asyncio.run(a.generate_reply("hello"))
        store.save_turn.assert_not_called()
        assert a._histories == {}

    def test_generate_reply_fails_before_intent_early_return_is_recorded(self):
        from rex.intent.router import IntentResult

        store = MagicMock()
        a = _make_assistant(user_id=None, history_store=store)
        ir = MagicMock()
        ir.route.return_value = IntentResult(
            handled=True, response="Hello. How can I help?", intent_type="greeting"
        )
        a._intent_router = ir
        with pytest.raises(IdentityRequiredError):
            asyncio.run(a.generate_reply("hello"))
        store.save_turn.assert_not_called()
        assert a._histories == {}

    def test_generate_reply_fails_before_context_and_dispatch(self):
        a = _make_assistant(user_id=None)
        _, ad = _wire_fake_dispatch(a)
        cb = MagicMock()
        a._context_builder = cb
        with pytest.raises(IdentityRequiredError):
            asyncio.run(a.generate_reply("plan my day"))
        cb.build.assert_not_called()

    def test_stream_reply_fails_closed_without_identity(self):
        from rex.intent.router import IntentResult

        a = _make_assistant(user_id=None)
        ir = MagicMock()
        ir.route.return_value = IntentResult(handled=False, response=None, intent_type=None)
        a._intent_router = ir

        async def _consume():
            async for _ in a.stream_reply("hello"):
                pass

        with pytest.raises(IdentityRequiredError):
            asyncio.run(_consume())
        ir.route.assert_not_called()
        assert a._histories == {}

    def test_record_completion_fails_closed_without_identity(self):
        a = _make_assistant(user_id=None)
        with pytest.raises(IdentityRequiredError):
            a._record_completion("hi", "reply")
        assert a._histories == {}

    def test_history_accessor_fails_closed_without_identity(self):
        a = _make_assistant(user_id=None)
        with pytest.raises(IdentityRequiredError):
            a.history()

    def test_new_based_fixture_cannot_regain_implicit_default(self):
        """An ``Assistant.__new__`` shell with no ``_user_id`` at all must not
        silently operate as the ``default`` user."""
        from rex.assistant import Assistant

        a = Assistant.__new__(Assistant)
        with pytest.raises(IdentityRequiredError):
            _ = a._history
        assert getattr(a, "_histories", {}) in ({}, getattr(a, "_histories", {}))
        assert "default" not in getattr(a, "_histories", {})

    def test_error_message_is_deterministic_and_non_sensitive(self):
        a = _make_assistant(user_id=None)
        _wire_fake_dispatch(a)
        with pytest.raises(IdentityRequiredError) as exc_info:
            asyncio.run(a.generate_reply("hello"))
        message = str(exc_info.value)
        assert message == assistant_module._IDENTITY_REQUIRED_MESSAGE
        for fragment in ("\\", "/", ".db", "token", "secret", "C:"):
            assert fragment not in message


# ---------------------------------------------------------------------------
# Explicit identity works — constructor-bound and per-request
# ---------------------------------------------------------------------------


class TestExplicitIdentityWorks:
    def test_bound_default_generates_normally(self):
        a = _make_assistant(user_id="default")
        _wire_fake_dispatch(a, reply_text="fine")
        reply = asyncio.run(a.generate_reply("tell me a story"))
        assert reply == "fine"
        assert any("tell me a story" in t.text for t in a._history_for("default"))

    def test_bound_named_user_generates_normally(self):
        a = _make_assistant(user_id="james")
        _wire_fake_dispatch(a, reply_text="fine")
        reply = asyncio.run(a.generate_reply("tell me a story"))
        assert reply == "fine"
        assert any("tell me a story" in t.text for t in a._history_for("james"))

    def test_request_identity_works_on_unbound_assistant(self):
        a = _make_assistant(user_id=None)
        capture: list[dict] = []
        _wire_fake_dispatch(a, reply_text="for alice", capture=capture)
        reply = asyncio.run(a.generate_reply("tell me a story", active_user_id="alice"))
        assert reply == "for alice"
        assert capture and capture[0]["user_id"] == "alice"
        assert any("tell me a story" in t.text for t in a._history_for("alice"))
        assert "default" not in a._histories

    def test_explicit_default_is_distinct_from_missing_identity(self):
        bound = _make_assistant(user_id="default")
        _wire_fake_dispatch(bound, reply_text="ok")
        assert asyncio.run(bound.generate_reply("tell me a story")) == "ok"

        unbound = _make_assistant(user_id=None)
        _wire_fake_dispatch(unbound, reply_text="ok")
        with pytest.raises(IdentityRequiredError):
            asyncio.run(unbound.generate_reply("tell me a story"))

    @pytest.mark.parametrize("bad_id", ["..", "a/b", "con", "nul", " alice", "x!y"])
    def test_invalid_active_user_id_fails_canonical_validation(self, bad_id):
        store = MagicMock()
        a = _make_assistant(user_id=None, history_store=store)
        ir, _ = _wire_fake_dispatch(a)
        with pytest.raises(ValueError):
            asyncio.run(a.generate_reply("hello", active_user_id=bad_id))
        ir.route.assert_not_called()
        store.save_turn.assert_not_called()
        assert a._histories == {}

    def test_stream_reply_accepts_request_identity(self):
        from rex.intent.router import IntentResult

        a = _make_assistant(user_id=None)
        ir = MagicMock()
        ir.route.return_value = IntentResult(
            handled=True, response="Direct answer.", intent_type="direct_answer"
        )
        a._intent_router = ir

        async def _consume():
            return [chunk async for chunk in a.stream_reply("hello", active_user_id="alice")]

        chunks = asyncio.run(_consume())
        assert chunks == ["Direct answer."]
        assert any("hello" in t.text for t in a._history_for("alice"))
        assert "default" not in a._histories


# ---------------------------------------------------------------------------
# Attribution: one identity for cache, history, tools, and completion
# ---------------------------------------------------------------------------


class TestSingleIdentityAttribution:
    def test_dispatch_receives_same_user_as_history_and_cache(self):
        from rex.response_cache import ResponseCache

        store = MagicMock()
        a = _make_assistant(user_id=None, history_store=store)
        a._response_cache = ResponseCache(ttl=60.0)
        capture: list[dict] = []
        _wire_fake_dispatch(a, reply_text="answer", capture=capture)

        asyncio.run(a.generate_reply("tell me a story", active_user_id="alice"))

        assert capture[0]["user_id"] == "alice"
        saved_users = {call.args[0] for call in store.save_turn.call_args_list}
        assert saved_users == {"alice"}
        assert a._response_cache.get("tell me a story", user_id="alice") == "answer"
        assert a._response_cache.get("tell me a story", user_id="bob") is None

    def test_intent_handled_response_attributed_to_request_user(self):
        from rex.intent.router import IntentResult

        store = MagicMock()
        a = _make_assistant(user_id="default", history_store=store)
        ir = MagicMock()
        ir.route.return_value = IntentResult(
            handled=True, response="It's noon.", intent_type="time_query"
        )
        a._intent_router = ir

        asyncio.run(a.generate_reply("what o'clock", active_user_id="bob"))

        saved_users = {call.args[0] for call in store.save_turn.call_args_list}
        assert saved_users == {"bob"}
        assert not any("what o'clock" in t.text for t in a._history_for("default"))

    def test_two_users_conflicting_histories_stay_isolated(self):
        a = _make_assistant(user_id=None)
        _wire_fake_dispatch(a, reply_text="noted")

        asyncio.run(a.generate_reply("my pin is 1234", active_user_id="alice"))
        asyncio.run(a.generate_reply("my pin is 9999", active_user_id="bob"))

        alice = a._history_for("alice")
        bob = a._history_for("bob")
        assert any("1234" in t.text for t in alice)
        assert not any("9999" in t.text for t in alice)
        assert any("9999" in t.text for t in bob)
        assert not any("1234" in t.text for t in bob)

    def test_identical_cache_prompts_stay_isolated_between_users(self):
        from rex.response_cache import ResponseCache

        a = _make_assistant(user_id=None)
        a._response_cache = ResponseCache(ttl=60.0)
        _wire_fake_dispatch(a, reply_text="Blue.")
        first = asyncio.run(a.generate_reply("what is my favorite color", active_user_id="alice"))
        assert first == "Blue."

        _wire_fake_dispatch(a, reply_text="Green.")
        second = asyncio.run(a.generate_reply("what is my favorite color", active_user_id="bob"))
        assert second == "Green."


# ---------------------------------------------------------------------------
# Concurrency: overlapping requests for different users
# ---------------------------------------------------------------------------


class TestConcurrentRequestIsolation:
    def test_overlapping_requests_do_not_cross_contaminate(self):
        from rex.actions.dispatcher import ActionResult
        from rex.intent.router import IntentResult

        store = MagicMock()
        a = _make_assistant(user_id=None, history_store=store)
        ir = MagicMock()
        ir.route.return_value = IntentResult(handled=False, response=None, intent_type=None)
        a._intent_router = ir
        a._llm = MagicMock()
        a._llm.model_name = "m"

        started = {"alice": asyncio.Event(), "bob": asyncio.Event()}
        release = asyncio.Event()
        seen: list[tuple[str, str]] = []

        async def _dispatch(intent, ctx, transcript, **kwargs):
            uid = kwargs.get("user_id")
            seen.append((transcript, uid))
            started[uid].set()
            await release.wait()
            return ActionResult(success=True, response=f"reply-for-{uid}")

        ad = MagicMock()
        ad.dispatch = _dispatch
        a._action_dispatcher = ad

        async def _run():
            t_alice = asyncio.create_task(
                a.generate_reply("alice secret question", active_user_id="alice")
            )
            t_bob = asyncio.create_task(
                a.generate_reply("bob secret question", active_user_id="bob")
            )
            await asyncio.wait_for(started["alice"].wait(), timeout=5)
            await asyncio.wait_for(started["bob"].wait(), timeout=5)
            # Both requests are now in flight simultaneously.
            release.set()
            return await asyncio.gather(t_alice, t_bob)

        reply_alice, reply_bob = asyncio.run(_run())

        assert reply_alice == "reply-for-alice"
        assert reply_bob == "reply-for-bob"
        assert dict(seen) == {
            "alice secret question": "alice",
            "bob secret question": "bob",
        }

        alice = a._history_for("alice")
        bob = a._history_for("bob")
        assert any("reply-for-alice" in t.text for t in alice)
        assert not any("reply-for-bob" in t.text for t in alice)
        assert any("reply-for-bob" in t.text for t in bob)
        assert not any("reply-for-alice" in t.text for t in bob)

        saved = {(c.args[0], c.args[2]) for c in store.save_turn.call_args_list}
        assert ("alice", "alice secret question") in saved
        assert ("alice", "reply-for-alice") in saved
        assert ("bob", "bob secret question") in saved
        assert ("bob", "reply-for-bob") in saved
        assert ("alice", "reply-for-bob") not in saved
        assert ("bob", "reply-for-alice") not in saved

    def test_overlapping_requests_do_not_mutate_bound_identity(self):
        from rex.actions.dispatcher import ActionResult
        from rex.intent.router import IntentResult

        a = _make_assistant(user_id="default")
        ir = MagicMock()
        ir.route.return_value = IntentResult(handled=False, response=None, intent_type=None)
        a._intent_router = ir
        a._llm = MagicMock()
        a._llm.model_name = "m"

        observed_during_request: list[object] = []
        release = asyncio.Event()

        async def _dispatch(intent, ctx, transcript, **kwargs):
            observed_during_request.append(a._user_id)
            await release.wait()
            return ActionResult(success=True, response="ok")

        ad = MagicMock()
        ad.dispatch = _dispatch
        a._action_dispatcher = ad

        async def _run():
            task = asyncio.create_task(a.generate_reply("hi there", active_user_id="alice"))
            await asyncio.sleep(0)
            release.set()
            await task

        asyncio.run(_run())

        # The session-bound identity must never be rewritten by a request
        # for another user (shared mutable identity is a cross-user race).
        assert observed_during_request == ["default"]
        assert a._user_id == "default"


# ---------------------------------------------------------------------------
# First-party entrypoint identity resolution
# ---------------------------------------------------------------------------


class TestEntrypointUserResolution:
    def test_explicit_user_wins(self):
        from rex import identity

        assert identity.resolve_entrypoint_user_id(explicit_user="james") == "james"

    def test_explicit_invalid_user_raises(self):
        from rex import identity

        with pytest.raises(ValueError):
            identity.resolve_entrypoint_user_id(explicit_user="../etc")

    def test_session_user_honored(self, monkeypatch):
        from rex import identity

        monkeypatch.setattr(identity, "resolve_active_user", lambda: "sessionuser")
        assert identity.resolve_entrypoint_user_id() == "sessionuser"

    def test_settings_user_id_is_deliberate_entrypoint_choice(self, monkeypatch):
        from rex import identity

        monkeypatch.setattr(identity, "resolve_active_user", lambda: None)
        settings_obj = MagicMock()
        settings_obj.user_id = "cfguser"
        assert identity.resolve_entrypoint_user_id(settings_obj) == "cfguser"

    def test_invalid_settings_user_id_fails_closed(self, monkeypatch):
        from rex import identity

        monkeypatch.setattr(identity, "resolve_active_user", lambda: None)
        settings_obj = MagicMock()
        settings_obj.user_id = "not/valid"
        with pytest.raises(ValueError):
            identity.resolve_entrypoint_user_id(settings_obj)

    def test_defaults_to_default_profile_when_nothing_configured(self, monkeypatch):
        from rex import identity

        monkeypatch.setattr(identity, "resolve_active_user", lambda: None)
        settings_obj = MagicMock()
        settings_obj.user_id = None
        assert identity.resolve_entrypoint_user_id(settings_obj) == "default"
        assert identity.resolve_entrypoint_user_id(None) == "default"
