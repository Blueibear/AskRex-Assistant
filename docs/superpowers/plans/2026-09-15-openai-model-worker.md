# Ralph OpenAI Model Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only OpenAI Responses API reasoning worker to Ralph with deterministic provider routing, a hard local $30/month API budget, provider-side project-cap prerequisite, and Astra reserved for one last-resort adjudication attempt only.

**Architecture:** Keep `Supervisor` and its `AgentInvoker` protocol provider-neutral. Add a routing invoker around the existing Claude/Codex CLI invoker and a new read-only OpenAI worker. All paid requests pass through a durable pre-reservation budget ledger before the fixed Responses API transport; only schema-valid, identity-bound results enter the existing durable handoff/result-replay path.

**Tech Stack:** Python 3.11, stdlib HTTPS/JSON, `Decimal`, existing `AtomicJsonStore`, existing DPAPI-backed `CredentialManager`, pytest, Ruff, Black.

**Spec:** `docs/superpowers/specs/2026-09-15-openai-model-worker-design.md`

## Global Constraints

- API spend is capped at **$30.00 USD per UTC calendar month**. No code path may raise this cap automatically or through model output.
- ChatGPT/Codex banked-reset accounting remains separate from OpenAI API billing and rate limits.
- OpenAI API v1 is read-only: no repository mutation, shell, GitHub, MCP, web, computer, file-search, or function tools.
- Claude remains the preferred implementer and Codex remains the only implementation fallback.
- Final release verification remains the existing independent Codex Sol path. The API worker must never service `FINAL-VERIFY-*`.
- Astra is never routine planning, routine review, implementation, or final verification. It is eligible only after deterministic evidence and Sol adjudication remain unresolved, at most once per escalation episode.
- Astra may be enabled, but routing must enforce every emergency-adjudication prerequisite and the one-call-per-escalation limit before it is eligible.
- Live API routing stays disabled until an operator confirms a dedicated OpenAI project has an enforced $30/month project spend limit.
- Secrets never enter config, prompts, evidence, logs, activity markers, tests, or coordination files.
- Normal tests must make zero external network calls.
- Preserve lease, scratch, provenance, durable pending-result replay, coordination safety, and frozen-worktree protections.

---
### Task 1: Typed OpenAI policy configuration and fail-closed activation

**Files:**
- Modify: `scripts/dev_orchestrator/types.py`
- Modify: `scripts/dev_orchestrator/cli.py`
- Modify: `scripts/dev_orchestrator/routing.py`
- Test: `tests/scripts/test_dev_orchestrator_cli.py`
- Test: `tests/scripts/test_dev_orchestrator_routing.py`

**Interfaces:**
- Produces typed `OrchestratorConfig` fields for OpenAI enablement, models, ceilings, project identity, and project-limit confirmation.
- Produces `validate_openai_policy(config)` and phase/model constants consumed by later routing and budget tasks.

- [ ] **Step 1: Write failing config round-trip tests**

Add tests proving API routing defaults disabled/fail-closed, the budget is exactly `Decimal("30.00")`, Astra is configured but emergency-gated, and save/load preserves all fields without serializing a credential.

```python
assert config.openai_worker_enabled is False
assert config.openai_monthly_budget_usd == Decimal("30.00")
assert config.openai_astra_enabled is True
assert config.openai_project_hard_limit_confirmed is False
assert "api_key" not in json.dumps(_config_payload(config)).lower()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_cli.py tests/scripts/test_dev_orchestrator_routing.py`
Expected: FAIL because the OpenAI policy fields and validator do not exist yet.
- [ ] **Step 3: Add the typed configuration fields**

Use `Decimal`, not float, and validate model IDs against a local allowlist. Keep API routing disabled by default; Astra may be configured but remains unreachable except through the emergency adjudication gate.

```python
openai_worker_enabled: bool = False
openai_review_model: str = "gpt-5.6-terra"
openai_escalation_model: str = "gpt-5.6-sol"
openai_planning_model: str = "gpt-5.6-sol"
openai_astra_model: str = "gpt-6-astra"
openai_astra_enabled: bool = True
openai_monthly_budget_usd: Decimal = Decimal("30.00")
openai_project_id: str = ""
openai_project_hard_limit_confirmed: bool = False
openai_timeout_seconds: int = 120
openai_max_input_chars: int = 120_000
openai_max_output_tokens: int = 4_000
openai_max_calls_per_cycle: int = 4
openai_max_astra_calls_per_escalation: int = 1
```

`validate_openai_policy()` must reject a monthly budget above `$30.00`, unsupported model IDs, non-positive ceilings, `openai_max_astra_calls_per_escalation != 1`, and live enablement without both project ID and hard-limit confirmation.

- [ ] **Step 4: Persist decimal values as strings and add an explicit operator confirmation command**

Add `confirm-openai-project-limit --project-id <id> --monthly-usd 30.00`; reject every amount other than exactly `30.00`. This records operator confirmation only; it must not attempt to modify OpenAI billing settings itself.

- [ ] **Step 5: Run focused tests and commit**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_cli.py tests/scripts/test_dev_orchestrator_routing.py`
Expected: PASS.

Commit: `git commit -am "feat(orchestrator): add openai worker policy config"`
### Task 2: Durable $30/month OpenAI API budget ledger

**Files:**
- Create: `scripts/dev_orchestrator/openai_budget.py`
- Modify: `scripts/dev_orchestrator/cli.py`
- Test: `tests/scripts/test_dev_orchestrator_openai_budget.py`

**Interfaces:**
- `OpenAIUsage(input_tokens: int, output_tokens: int)` represents provider-reported billable usage.
- `BudgetReservation(reservation_id: str, month: str, model: str, reserved_usd: Decimal)` is durable before network dispatch.
- `OpenAIBudgetLedger.reserve(...)`, `.reconcile(...)`, and `.mark_uncertain(...)` are the only paid-request accounting boundary.

- [ ] **Step 1: Write RED tests for reservation, reconciliation, UTC rollover, crash persistence, and fail-closed unknowns**

Cover: first reservation; multiple reservations; exact `$30.00` boundary; refusal at `$30.000001`; reservation survives a new ledger instance; unresolved reservation continues to consume its full amount; UTC month rollover starts a new bucket without deleting history; unknown model pricing rejects before dispatch; malformed/missing usage leaves the full reservation charged and blocks future paid calls.

```python
with pytest.raises(OpenAIBudgetExceeded):
    ledger.reserve(
        model="gpt-5.6-sol",
        input_token_ceiling=1_000_000,
        output_token_ceiling=1_000_000,
    )
```

- [ ] **Step 2: Run the budget tests and verify RED**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_openai_budget.py`
Expected: FAIL because the ledger does not exist.
- [ ] **Step 3: Implement trusted pricing and Decimal-only arithmetic**

Use only verified API prices. Astra is priced, but the routing gate still prevents routine use.

```python
CONSERVATIVE_PRICE_PER_MILLION = {
    # (max input/cache-write rate, standard output rate)
    "gpt-5.6-terra": (Decimal("2.50"), Decimal("12.00")),
    "gpt-5.6-sol": (Decimal("5.00"), Decimal("20.00")),
    "gpt-6-astra": (Decimal("12.50"), Decimal("50.00")),
}
LONG_CONTEXT_THRESHOLD_TOKENS = 272_000
LONG_INPUT_MULTIPLIER = Decimal("2")
LONG_OUTPUT_MULTIPLIER = Decimal("1.5")
HARD_MONTHLY_CAP_USD = Decimal("30.00")
```

Calculate worst-case reservation from input/output token ceilings before the call. For the HTTP payload, conservatively use the UTF-8 byte length of all model-visible request text/schema as the input-token ceiling, never a model-supplied estimate. If that ceiling exceeds 272K tokens, apply the 2x input and 1.5x output long-context multipliers. Treat all input at the maximum cache-write/input rate for reservation so caching behavior can never make the local estimate too low.

- [ ] **Step 4: Persist reservations atomically under a dedicated lock**

Store at `<coordination_root>/usage/openai-api-spend.json` using `AtomicJsonStore`. Serialize all monetary values as decimal strings. Use `ControlPlaneLock(<coordination_root>/openai-api-budget.lock)` around read-modify-write so backend/mobile cannot race the cap.

Ledger records must include only month, reservation ID, model, reserved/actual USD, token counts, status, and timestamps. Never include prompt/evidence/output content.

- [ ] **Step 5: Reconcile only from valid provider usage**

`reconcile()` must require non-negative integer `input_tokens` and `output_tokens`, recompute a conservative post-call charge estimate from provider-reported usage using the same maximum applicable rates/multipliers, and fail closed if that estimate exceeds the reservation. It may overestimate the invoice, never underestimate it. `mark_uncertain()` preserves the reservation as spent-risk rather than refunding it.

- [ ] **Step 6: Expose privacy-safe status**

Extend `render_status()` with current UTC month, configured cap, charged/reserved total, and remaining API allowance. Do not expose request content or credentials.

- [ ] **Step 7: Run tests and commit**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_openai_budget.py tests/scripts/test_dev_orchestrator_cli.py`
Expected: PASS.

Commit: `git add scripts/dev_orchestrator/openai_budget.py scripts/dev_orchestrator/cli.py tests/scripts/test_dev_orchestrator_openai_budget.py tests/scripts/test_dev_orchestrator_cli.py && git commit -m "feat(orchestrator): enforce openai api budget"`
### Task 3: Fixed, tool-free OpenAI Responses transport

**Files:**
- Create: `scripts/dev_orchestrator/openai_transport.py`
- Modify: `scripts/dev_orchestrator/schema.py`
- Test: `tests/scripts/test_dev_orchestrator_openai_transport.py`

**Interfaces:**
- `agent_result_schema_payload() -> dict[str, Any]` returns the canonical schema without duplicating schema authority.
- `PreparedOpenAIRequest(body: bytes, input_token_ceiling: int, model: str, max_output_tokens: int)` contains the exact secret-free request body before network dispatch.
- `OpenAITransportResponse(output: Mapping[str, Any], usage: OpenAIUsage, request_id: str)` contains only parsed structured output plus usage metadata.
- `OpenAIResponsesTransport.prepare(...) -> PreparedOpenAIRequest` is pure and network-free; `send(prepared) -> OpenAITransportResponse` is the only network boundary.

- [ ] **Step 1: Write RED request-contract tests around an injected HTTP opener**

Assert the exact destination is `https://api.openai.com/v1/responses`; `store` is `false`; `tools` is absent or `[]`; `truncation` is `disabled`; model/output/reasoning ceilings are explicit; structured output uses the canonical agent-result JSON schema; and the `Authorization` plus `OpenAI-Project` headers are injected only at send time.

```python
assert request.full_url == "https://api.openai.com/v1/responses"
assert body["store"] is False
assert body.get("tools", []) == []
assert body["truncation"] == "disabled"
assert body["text"]["format"]["type"] == "json_schema"
assert body["text"]["format"]["strict"] is True
```

Also assert API key/project ID never appear in `repr(transport)`, exceptions, or captured diagnostic metadata.

- [ ] **Step 2: Write RED response/error tests**

Cover completed structured output, missing usage, incomplete/refused output, malformed JSON, 401/403 auth, ordinary 429 rate limit, project/org spend-limit error codes as `billing`, timeout, connection failure, and 5xx transient failure. Error objects expose only provider, category, HTTP status class/code, and request ID; never raw response bodies.
- [ ] **Step 3: Run transport tests and verify RED**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_openai_transport.py`
Expected: FAIL because the transport does not exist.

- [ ] **Step 4: Implement stdlib HTTPS transport with injected credential resolver**

Default credential resolution is `CredentialManager().get_token("openai")`. Tests inject a resolver and opener. Do not silently enable plaintext credential fallback. `prepare()` must not resolve credentials; `send()` injects them only after the caller has reserved budget.

Build the request using the current Responses contract:

```python
body = {
    "model": model,
    "instructions": instructions,
    "input": input_text,
    "store": False,
    "tools": [],
    "truncation": "disabled",
    "max_output_tokens": max_output_tokens,
    "reasoning": {"effort": reasoning_effort},
    "text": {"format": {"type": "json_schema", "name": "askrex_agent_result", "strict": True, "schema": schema}},
}
```

Do not implement automatic paid retries in v1. A transport failure returns to provider routing, which can use the existing Codex fallback without risking duplicate billing.

- [ ] **Step 5: Parse only completed output and provider-reported usage**

Require response `status == "completed"`, extract exactly one structured agent-result object, and require integer `usage.input_tokens` / `usage.output_tokens`. Missing or malformed usage must be surfaced so the budget reservation remains charged as uncertain.

- [ ] **Step 6: Run tests and commit**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_openai_transport.py tests/scripts/test_dev_orchestrator_storage.py`
Expected: PASS.

Commit: `git add scripts/dev_orchestrator/openai_transport.py scripts/dev_orchestrator/schema.py tests/scripts/test_dev_orchestrator_openai_transport.py && git commit -m "feat(orchestrator): add openai responses transport"`
### Task 4: Revision-bound review evidence and validation receipts

**Files:**
- Create: `scripts/dev_orchestrator/evidence.py`
- Modify: `scripts/dev_orchestrator/types.py`
- Modify: `scripts/dev_orchestrator/supervisor.py`
- Modify: `scripts/dev_orchestrator/validation.py`
- Test: `tests/scripts/test_dev_orchestrator_evidence.py`
- Test: `tests/scripts/test_dev_orchestrator_supervisor.py`

**Interfaces:**
- Add `WorkerState.task_base_head: str = ""` so a review can cover the complete current task, not only the latest checkpoint commit.
- `ValidationReceipt` binds gate evidence to role, task ID, base HEAD, reviewed HEAD, commands, exit codes, and bounded outputs.
- `ReviewEvidenceBundle` exposes only bounded authorized text plus `truncated` and `truncation_reasons`.

- [ ] **Step 1: Write RED state tests for stable task-base revision tracking**

When a task is dequeued or assigned, record the current leased HEAD once. Preserve it through implementation retries and review changes. Clear it only when the task is completed/replaced. Final-repair tasks get a fresh base HEAD.

```python
assert assigned.task_base_head == lease_head
assert reviewing.task_base_head == assigned.task_base_head
assert retry.task_base_head == assigned.task_base_head
```

- [ ] **Step 2: Write RED validation-receipt tests**

Extend successful `run_iteration_validation()` results to contain bounded gate evidence. Persist a receipt only after all configured gates pass. A receipt must be rejected when role, task, base HEAD, or current HEAD no longer matches.

- [ ] **Step 3: Write RED evidence-bundle tests**

Build a bundle containing role, task ID/prompt/feedback, invocation ID, base/current revisions, bounded coordination snapshot, diff stat, bounded `git diff <base>..<head> --`, and the matching validation receipt. Assert credentials, frozen-worktree paths/content, unrelated filesystem paths, and raw private conversation data are absent.
- [ ] **Step 4: Verify truncation cannot silently pass review**

Use deterministic head/tail truncation per section and record every omitted section. `ReviewEvidenceBundle.truncated=True` must force the OpenAI worker to return or synthesize `changes_required`/`blocked_system` rather than `pass`; provider routing may then use Codex for full read-only inspection.

```python
@dataclass(frozen=True)
class ReviewEvidenceBundle:
    text: str
    truncated: bool
    truncation_reasons: tuple[str, ...]
    base_head: str
    head: str
```

- [ ] **Step 5: Run focused tests and verify RED**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_evidence.py tests/scripts/test_dev_orchestrator_supervisor.py`
Expected: FAIL on missing state/evidence/receipt behavior.

- [ ] **Step 6: Implement state tracking, receipt persistence, and bounded evidence**

Store receipts under `<coordination_root>/validation/<role>-<task-id>.json` using `AtomicJsonStore`. Evidence building may execute only fixed read-only Git commands with argument arrays; it must not accept model-supplied command text.

The bundle builder must fail closed on dirty/revision mismatch or missing successful validation receipt instead of sending stale evidence to the API.

- [ ] **Step 7: Run tests and commit**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_evidence.py tests/scripts/test_dev_orchestrator_supervisor.py tests/scripts/test_dev_orchestrator_safety.py`
Expected: PASS.

Commit: `git add scripts/dev_orchestrator/evidence.py scripts/dev_orchestrator/types.py scripts/dev_orchestrator/supervisor.py scripts/dev_orchestrator/validation.py tests/scripts/test_dev_orchestrator_evidence.py tests/scripts/test_dev_orchestrator_supervisor.py tests/scripts/test_dev_orchestrator_safety.py && git commit -m "feat(orchestrator): bind review evidence to task revisions"`
### Task 5: Read-only OpenAI worker with reservation-before-send and durable result replay

**Files:**
- Create: `scripts/dev_orchestrator/scratch.py`
- Create: `scripts/dev_orchestrator/openai_worker.py`
- Modify: `scripts/dev_orchestrator/runner.py`
- Modify: `scripts/dev_orchestrator/handoff.py`
- Test: `tests/scripts/test_dev_orchestrator_openai_worker.py`
- Test: `tests/scripts/test_dev_orchestrator_lease.py`
- Test: `tests/scripts/test_dev_orchestrator_safety.py`

**Interfaces:**
- Move the existing temporary scratch-root and detached-clone helpers into `scratch.py`; `runner.py` and `openai_worker.py` both reuse them without changing Claude/Codex behavior.
- `OpenAIModelWorker.review(...)` and `.lead(..., phase: Literal["plan", "adjudicate"], model: str)` return canonical `AgentResult` objects only.
- The worker composes `prepare -> reserve -> send -> reconcile -> validate_agent_result -> binding check -> advance_handoff` in that order.

- [ ] **Step 1: Write characterization tests before extracting scratch helpers**

Prove the current CLI invoker still creates detached disposable clones, never gives reviewers a writable live checkout, preserves a clean live repository, and publishes only implementation commits. These tests must pass before and after extraction.

- [ ] **Step 2: Extract the scratch helpers without behavior change**

Move `_temporary_scratch_root()`, `_remove_scratch_tree()`, and `_clone_scratch_repo()` into `scratch.py`; import them back into `runner.py`. Run the existing routing/safety/lease suite immediately.

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_routing.py tests/scripts/test_dev_orchestrator_safety.py tests/scripts/test_dev_orchestrator_lease.py`
Expected: PASS before adding the API worker.

- [ ] **Step 3: Write RED worker tests**

Assert the API worker receives only bounded evidence from the disposable snapshot; reserves budget before `send()` is entered; never sends when reservation fails; reconciles valid usage; keeps the full reservation on missing/malformed usage; validates the canonical schema and exact role/task/invocation binding; and rejects a repository change occurring during the read-only invocation.
- [ ] **Step 4: Add OpenAI as an explicitly read-only handoff provider**

In `advance_handoff()`, accept `provider == "openai"` only when `post_head == pre_head`. It may write a durable `pending_result`, but it may never verify or publish invocation commits.

```python
elif provider == "openai":
    if post_head != pre_head:
        raise HandoffRequired("read-only OpenAI invocation changed repository HEAD")
```

Add a test proving an OpenAI provider record cannot smuggle a repository delta through the handoff path.

- [ ] **Step 5: Implement the worker transaction**

Hold the existing per-role lease lock; validate handoff; snapshot the clean leased HEAD; clone the exact revision into a disposable read-only evidence snapshot; prepare the secret-free request; reserve its worst-case cost; then call `send()`.

On success, reconcile usage before accepting the model result. Then run `validate_agent_result()`, exact role/task/invocation binding, and the existing `validate_agent_updates()` safety gate before writing a durable pending result through `advance_handoff()`.

If the transport result is uncertain after dispatch, call `mark_uncertain()` before propagating a typed error. Never refund an uncertain paid request.

- [ ] **Step 6: Run focused tests and commit**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_openai_worker.py tests/scripts/test_dev_orchestrator_routing.py tests/scripts/test_dev_orchestrator_safety.py tests/scripts/test_dev_orchestrator_lease.py`
Expected: PASS.

Commit: `git add scripts/dev_orchestrator/scratch.py scripts/dev_orchestrator/openai_worker.py scripts/dev_orchestrator/runner.py scripts/dev_orchestrator/handoff.py tests/scripts/test_dev_orchestrator_openai_worker.py tests/scripts/test_dev_orchestrator_routing.py tests/scripts/test_dev_orchestrator_safety.py tests/scripts/test_dev_orchestrator_lease.py && git commit -m "feat(orchestrator): add read-only openai worker"`
### Task 6: Deterministic provider routing and emergency-only Astra

**Files:**
- Create: `scripts/dev_orchestrator/provider_routing.py`
- Modify: `scripts/dev_orchestrator/runner.py`
- Modify: `scripts/dev_orchestrator/cli.py`
- Modify: `scripts/dev_orchestrator/openai_budget.py`
- Test: `tests/scripts/test_dev_orchestrator_provider_routing.py`
- Test: `tests/scripts/test_dev_orchestrator_routing.py`

**Interfaces:**
- `ProviderRoutingInvoker` implements the existing `AgentInvoker` protocol and owns all phase-to-provider fallback order.
- `CliAgentInvoker.lead()` becomes a generic read-only **Sol** planner/adjudicator; `_lead_prompt()` must stop claiming to be Astra.
- `OpenAIBudgetLedger.has_astra_attempt(episode_key)` makes the one-Astra limit crash-safe.

- [ ] **Step 1: Write the complete routing matrix as RED parameterized tests**

Required routes:

```text
implement                 -> Claude -> Codex implementation fallback
routine review            -> API Terra -> Codex Terra fallback
escalated review          -> API Sol -> Codex Sol fallback
Codex-implemented review  -> API Sol -> Codex Sol fallback
planning                  -> API Sol -> Codex Sol fallback
adjudication              -> API Sol -> Codex Sol fallback -> optional API Astra
FINAL-VERIFY-*             -> Codex Sol only
```

With `openai_worker_enabled=false`, review/planning/adjudication must reproduce the existing Codex behavior except routine planning now uses Sol, never Astra.

- [ ] **Step 2: Write explicit negative Astra tests**

Prove Astra is never selected for implementation, planning, routine/escalated review, final verification, a Sol `assign`, a Sol `done`, a Sol blocker, a Sol successful/pass-equivalent adjudication, or a task below `astra_adjudication_after`.
- [ ] **Step 3: Define one conservative escalation episode key**

Use a content-free key derived from `role`, `task_id`, and `task_base_head`. This intentionally treats the entire task revision span as one escalation episode, which is stricter than allowing retries to mint fresh Astra eligibility.

```python
episode_key = hashlib.sha256(
    f"{role}\0{task.task_id}\0{state.task_base_head}".encode("utf-8")
).hexdigest()
```

An Astra reservation is recorded with `purpose="astra_adjudication"` and this key before dispatch. Any prior Astra reservation/attempt for the key makes Astra permanently ineligible for that episode, including after restart or uncertain billing.

- [ ] **Step 4: Permit Astra only after a Sol adjudication returns explicitly unresolved**

Use `AgentResult.outcome == "failed"` as the only v1 unresolved signal eligible for Astra. Provider transport errors do not themselves prove that the reasoning problem needs Astra; first exhaust the trusted Sol fallback chain. If Sol cannot produce a valid result at all, park with the real provider/blocker reason rather than automatically spending on Astra.

Then require all of: adjudication phase; failure threshold met; Astra enabled; trusted Astra pricing present; no prior Astra attempt for episode; and budget reservation succeeds. If reservation fails, propagate a budget blocker without sending a request.

- [ ] **Step 5: Remove routine Astra identity from CLI planning**

Change `_lead_prompt()` from `Act as Astra` to a provider-neutral lead/planner instruction and change `CliAgentInvoker.lead()` to use `SOL_MODEL`. There must be no `ASTRA_MODEL` reference in ordinary CLI planning/review code.

- [ ] **Step 6: Run routing tests and commit**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_provider_routing.py tests/scripts/test_dev_orchestrator_routing.py`
Expected: PASS, including max-one-Astra and all negative-path assertions.

Commit: `git add scripts/dev_orchestrator/provider_routing.py scripts/dev_orchestrator/runner.py scripts/dev_orchestrator/cli.py scripts/dev_orchestrator/openai_budget.py tests/scripts/test_dev_orchestrator_provider_routing.py tests/scripts/test_dev_orchestrator_routing.py && git commit -m "feat(orchestrator): route reasoning providers safely"`
### Task 7: Provider-neutral failure handling without reset-accounting leakage

**Files:**
- Modify: `scripts/dev_orchestrator/supervisor.py`
- Modify: `scripts/dev_orchestrator/usage.py`
- Test: `tests/scripts/test_dev_orchestrator_supervisor.py`
- Test: `tests/scripts/test_dev_orchestrator_usage.py`
- Test: `tests/scripts/test_dev_orchestrator_provider_routing.py`

- [ ] **Step 1: Write RED tests for API failure categories**

Cover `auth`, `rate_limit`, `billing`, `budget`, `timeout`, `transient`, `invalid_output`, and `failed`. Assert none can decrement, offer, reserve, or mention ChatGPT/Codex banked resets merely because the provider is OpenAI API.

```python
assert state.blocker_kind != "usage_limit"
assert config.banked_resets_remaining == original_resets
assert not any("banked reset" in p.read_text().lower() for p in alerts)
```

- [ ] **Step 2: Guard the legacy usage-limit path by provider**

`handle_usage_limit()` remains exclusively for Claude/Codex CLI allowance state. If an `AgentInvocationError(provider="openai", kind="usage_limit", ...)` somehow reaches the supervisor, treat it as an invalid provider classification and fail closed rather than entering reset handling.

- [ ] **Step 3: Preserve the exact interrupted phase for API blockers**

Map `review -> REVIEWING`, `lead/plan -> PLANNING`, and adjudication back to the state from which adjudication was entered. Budget/billing/auth conditions requiring operator action become `BLOCKED_USER`; transient/invalid-output/system failures become `BLOCKED_SYSTEM` with a resume phase and no hidden state advance.

- [ ] **Step 4: Prove Codex fallback usage limits still use the existing reset flow**

If API review fails and the configured Codex fallback then returns a real Codex `usage_limit`, the final error provider is `codex`; the existing banked-reset workflow remains valid. This keeps API billing separate without disabling legitimate Codex reset handling.

- [ ] **Step 5: Run focused tests and commit**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_supervisor.py tests/scripts/test_dev_orchestrator_usage.py tests/scripts/test_dev_orchestrator_provider_routing.py`
Expected: PASS.

Commit: `git add scripts/dev_orchestrator/supervisor.py scripts/dev_orchestrator/usage.py tests/scripts/test_dev_orchestrator_supervisor.py tests/scripts/test_dev_orchestrator_usage.py tests/scripts/test_dev_orchestrator_provider_routing.py && git commit -m "fix(orchestrator): separate api billing from reset limits"`
### Task 8: Runtime wiring, activation guard, and operator documentation

**Files:**
- Modify: `scripts/dev_orchestrator/cli.py`
- Modify: `CLAUDE.md`
- Modify: `docs/claude/DEVELOPMENT_ORCHESTRATOR.md`
- Modify: `docs/superpowers/specs/2026-09-15-openai-model-worker-design.md` only if implementation reveals a verified contract correction
- Test: `tests/scripts/test_dev_orchestrator_cli.py`
- Test: `tests/scripts/test_dev_orchestrator_supervisor.py`

- [ ] **Step 1: Write RED construction/activation tests**

Prove active cycles construct `ProviderRoutingInvoker` with the existing CLI invoker plus OpenAI worker, while observe-only cycles still invoke nothing. Prove API routing cannot become live unless project ID and hard-limit confirmation validate successfully.

- [ ] **Step 2: Preserve final verification as Codex Sol only**

Add an integration-style fake-router test around `_done_claim()` showing `FINAL-VERIFY-*` bypasses the OpenAI worker even when API routing is enabled and budget remains. The invoked provider/model must be Codex / `gpt-5.6-sol`.

- [ ] **Step 3: Update orchestrator documentation to match actual behavior**

Remove statements that Astra selects ordinary queue work or performs final completion judgment. Document:
- Claude -> Codex implementation fallback;
- API Terra/Sol -> Codex fallback for read-only reasoning;
- final release review = Codex Sol only;
- Astra = API-only emergency adjudication after unresolved Sol, max one per task escalation episode;
- local `$30/month` reservation ledger plus dedicated OpenAI project `$30/month` enforced limit;
- API billing/rate limits never consume ChatGPT/Codex reset accounting;
- API provider disabled by default until operator activation prerequisites are met.

- [ ] **Step 4: Document operator activation sequence without storing secrets**

The sequence is: create/use dedicated OpenAI project; configure an **enforced** `$30.00/month` project spend limit in OpenAI; ensure the household vault contains the OpenAI API credential; record project ID + hard-limit confirmation through Ralph CLI; then explicitly enable the API worker. Do not put the API key in commands, docs, config, or coordination files.

- [ ] **Step 5: Run focused tests and commit**

Run: `py -3.11 -m pytest -q tests/scripts/test_dev_orchestrator_cli.py tests/scripts/test_dev_orchestrator_supervisor.py tests/scripts/test_dev_orchestrator_provider_routing.py`
Expected: PASS.

Commit: `git add scripts/dev_orchestrator/cli.py CLAUDE.md docs/claude/DEVELOPMENT_ORCHESTRATOR.md tests/scripts/test_dev_orchestrator_cli.py tests/scripts/test_dev_orchestrator_supervisor.py tests/scripts/test_dev_orchestrator_provider_routing.py && git commit -m "docs(orchestrator): document bounded openai routing"`
### Task 9: Full verification, optional live smoke, and controlled rollout

**Files:**
- Modify as needed only for verified fixes from the gates below.
- Do not modify the live `lead/dev-orchestrator` worktree during implementation/verification on this branch.

- [ ] **Step 1: Run the complete deterministic orchestrator suite**

Run: `py -3.11 -m pytest -q tests/scripts -k dev_orchestrator`
Expected: all orchestrator tests PASS with no live API calls.

- [ ] **Step 2: Run source quality/security gates**

Run, in order:

```powershell
py -3.11 -m ruff check scripts/dev_orchestrator tests/scripts
py -3.11 -m black --check scripts/dev_orchestrator tests/scripts
py -3.11 -m compileall -q scripts/dev_orchestrator
py -3.11 scripts/security_audit.py --release-gate
git diff --check origin/master...HEAD
git status --short
```

Expected: all commands succeed; `git status --short` is clean after committed work; tests generated no tracked artifacts.

- [ ] **Step 3: Verify the critical negative invariants directly**

Run focused tests proving: API implementation is impossible; API worker disabled preserves existing behavior; API 429 never enters banked-reset logic; final verification never routes to API; budget cannot exceed $30; unknown pricing/usage fails closed; Astra never routes outside unresolved adjudication; the same escalation episode cannot make a second Astra call; and repository HEAD cannot change during an OpenAI invocation.

- [ ] **Step 4: Commit/push verified implementation branch**

Use Conventional Commits. Push only `lead/openai-model-worker`; do not merge or mutate `master`/`lead/dev-orchestrator` from an agent session.

- [ ] **Step 5: Optional operator-triggered live API smoke only after the provider-side cap is confirmed**

Use a synthetic, non-sensitive read-only payload and Terra, never Astra. The smoke must pass through the same local budget ledger and carry a very small request ceiling. Label it `live_provider` evidence. This smoke is optional and never a CI requirement.

- [ ] **Step 6: Controlled integration into the live orchestrator**

Pause the live supervisor, confirm no active model child/postprocessing scratch, import the verified implementation commit into `lead/dev-orchestrator` at a clean boundary, rerun focused gates there, restart only the supervisor, verify heartbeat PID/FILETIME and persisted worker states, and leave `openai_worker_enabled=false` until the dedicated project limit + credential prerequisites are confirmed.

- [ ] **Step 7: First live routing observation**

After explicit operator enablement, observe one read-only routine review/planning cycle. Confirm actual provider/model provenance, ledger reservation/reconciliation, unchanged repository state, and no change to banked-reset accounting. Do not deliberately trigger Astra merely to test it; deterministic fake tests are sufficient for the emergency path.

## Execution Handoff

Preferred execution mode is `superpowers:subagent-driven-development`, one task at a time, with `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before each completion claim.

The implementation worker must remain on `lead/openai-model-worker` (or a fresh isolated descendant worktree for delegated subtasks), re-read `CLAUDE.md` and the shared coordination protocol before coding, and stop rather than weaken any budget, lease, credential, privacy, final-review, or Astra gate in order to make a test pass.

Do not enable live API routing as part of ordinary implementation. Activation is a separate operator action after the branch is verified and the dedicated OpenAI project hard limit is confirmed.