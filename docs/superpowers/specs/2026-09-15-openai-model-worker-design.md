# Ralph OpenAI Model Worker Design

**Status:** Proposed for implementation after owner review
**Date:** 2026-09-15
**Scope:** Development orchestrator only; not the AskRex product Agent Runtime

## Goal

Add a provider-neutral, API-backed OpenAI reasoning worker to Ralph so review, planning, and adjudication can continue without depending exclusively on Codex CLI capacity, while preserving all existing repository, lease, provenance, validation, coordination, and human-control boundaries.

The first release is deliberately read-only. It does not edit repositories, create patches, run shell commands, use MCP, browse the web, or invoke arbitrary tools. Claude Code and Codex remain the only implementation providers allowed to mutate a disposable repository scratch clone.

## Non-goals

- Automating ordinary ChatGPT browser or desktop conversations.
- Replacing Claude Code or Codex for implementation work.
- Giving an API model shell, filesystem, MCP, browser, or GitHub authority.
- Changing AskRex product model routing or the future Agent Runtime.
- Automatically purchasing API credit, changing billing, or consuming ChatGPT/Codex resets.
- Treating model output as trusted authority or verification evidence.

## Current constraints

Ralph already exposes a provider-neutral `AgentInvoker` boundary to the supervisor. Provider-specific behavior currently lives inside `CliAgentInvoker`, while the supervisor consumes only canonical `AgentResult` objects.Every production invocation already receives an unpredictable invocation ID, operates against an isolated scratch clone, and must return schema-valid output bound to the correct role/task/invocation before supervisor state can change. Those controls remain authoritative and are reused rather than reimplemented.

The machine currently has OpenAI Python SDK 1.12.0, and constructing that client fails against the installed HTTP dependency. That ambient SDK is therefore not a safe dependency for Ralph's new provider. The worker must not silently inherit an arbitrary globally installed OpenAI SDK version.

## Approaches considered

### 1. Direct Responses API transport — recommended

Implement a small orchestrator-owned HTTPS client for the fixed OpenAI Responses endpoint. Use the existing Python runtime and standard HTTPS/TLS behavior, submit strict structured-output requests, and normalize the response into the existing `AgentResult` validator.

Advantages:
- no ChatGPT UI automation;
- no global OpenAI SDK upgrade;
- no new mutation authority;
- narrow request surface with no tools;
- deterministic error handling and privacy controls;
- works independently of Codex CLI quota.

Trade-off: Ralph owns a small amount of HTTP request/response parsing code and must maintain explicit compatibility tests for the Responses wire contract.

### 2. Official SDK in an isolated orchestrator environment

Create a dedicated Ralph virtual environment with a pinned current OpenAI SDK. This gives official client types and transport helpers, but requires new environment bootstrapping, watchdog/runtime-path changes, package lifecycle management, and additional deployment failure modes.This can remain a later migration if the direct transport becomes burdensome.

### 3. ChatGPT UI / Computer Use worker

Rejected for Ralph's critical path. Browser/window state, authentication, conversation state, UI changes, structured-output extraction, and provenance are less deterministic than an API/CLI boundary. UI automation may be useful for separate operator workflows but must not become a prerequisite for repository supervision.

## Architecture

The supervisor-facing `AgentInvoker` protocol remains unchanged. A provider-routing invoker selects a concrete worker from trusted configuration and passes the worker's canonical `AgentResult` through the existing durable receipt and supervisor safety path.

```text
Supervisor
    |
    v
ProviderRoutingInvoker
    |-- ClaudeCodeWorker  -> implementation only
    |-- CodexWorker       -> implementation/review/lead fallback
    `-- OpenAIModelWorker -> review/lead/adjudication only
             |
             v
     OpenAIResponsesTransport
```

Provider routing is deterministic configuration, never a model decision. A worker cannot select another provider, grant itself tools, alter its permitted phases, or change fallback policy.

The existing scratch/lease machinery remains in force even for an API-only read operation. The API worker never receives paths outside the bounded evidence assembled by Ralph and never writes into its scratch clone.## Provider capability matrix

| Phase | Claude Code | Codex | OpenAI API v1 |
|---|---:|---:|---:|
| Implementation / repository mutation | Yes | Fallback | No |
| Independent review | No | Yes | Yes |
| Queue planning | No | Yes | Yes |
| Failure adjudication | No | Yes | Yes |
| Final release review | No | Yes | No initially |

Final release review stays on the existing independently verified Sol/Codex path until the API reviewer has accumulated enough production evidence to justify a separate owner-approved change.

An OpenAI API result can recommend code changes, but the recommendation returns through normal review feedback. It can never itself publish a patch or advance a repository HEAD.

## Review evidence bundle

An API reviewer has no filesystem tools. Ralph therefore constructs a deterministic, bounded `ReviewEvidenceBundle` locally before the request. The bundle contains only evidence the reviewer is authorized to inspect:

- role, task ID, task prompt, and current reviewer feedback;
- invocation ID and content-free repository revision identifiers;
- bounded coordination snapshot already authorized for that role;
- current task checkpoint diff stat;
- bounded textual Git diff for the checkpoint;
- deterministic validation gate names, commands, exit codes, and bounded output;
- applicable Ralph security/ownership invariants required for the review.

The bundle must not contain credentials, API tokens, unrelated private repository content, frozen-worktree data, private conversations, or unrestricted filesystem paths.If a diff or validation report exceeds configured bounds, Ralph truncates using deterministic head/tail rules and marks the evidence as truncated. If the omitted material could affect the decision, the API worker must return a non-passing outcome and Ralph routes the review to Codex when available. It must never infer that unseen evidence is safe.

Planning and adjudication do not require a repository diff by default. They consume the existing bounded coordination snapshot plus the current task/failure counters and explicit trusted constraints.

## OpenAI request contract

The transport sends only to the fixed HTTPS Responses endpoint owned by OpenAI. The initial release does not accept a request-supplied base URL, proxy destination, or arbitrary endpoint.

Each request includes:

- an explicitly configured approved model ID;
- trusted system/instruction text describing the permitted Ralph phase;
- the bounded evidence payload as input;
- `store: false`;
- no built-in tools, web search, file search, MCP, computer use, or function tools;
- a strict JSON-schema response format derived from the canonical Ralph result schema;
- an explicit output-token ceiling and reasoning-effort policy;
- Ralph's invocation ID as a client request correlation identifier when supported.

The returned JSON is never trusted merely because structured output succeeded. It is parsed and then passed through the existing canonical `validate_agent_result()` and exact role/task/invocation binding checks before any state transition or coordination publication.

Provider refusals, incomplete responses, malformed structured output, unexpected content, or missing identity fields fail closed.

## Credentials

The OpenAI API key remains a secret and is never stored in `orchestrator-config.json`, worker state, activity markers, prompts, coordination messages, test fixtures, or logs.Production credential resolution reuses AskRex's canonical vault-backed OpenAI credential path. Absence, vault failure, or invalid credential state produces a typed provider-auth failure; no plaintext fallback is silently enabled for Ralph.

Tests inject a fake transport and fake credential resolver. Normal unit/integration tests never contact OpenAI and never require a real API key.

## Privacy and retention

Ralph treats repository and coordination context sent to a cloud model as a disclosure boundary. Only the minimum evidence necessary for the selected phase is sent. The request explicitly disables provider-side response storage where the API supports that control.

Diagnostics may retain only bounded operational metadata such as provider ID, model ID, HTTP status class, latency, invocation ID, and provider request ID. Raw prompt/evidence, model output, credentials, authorization headers, and exception bodies that may contain private content are excluded from ordinary logs and alerts.

The API worker is disabled by default in generic configuration. Enabling it is an explicit operator choice and requires a usable vault-backed OpenAI credential.

## Cost controls

API billing is independent of ChatGPT/Codex usage. The operator-set hard monthly Ralph API budget is **$30.00 USD per UTC calendar month**.

Cost enforcement is defense-in-depth:

- Ralph keeps a durable local monthly spend ledger, separate from worker/model state;
- before any API call, Ralph reserves the request's worst-case allowed cost from the remaining monthly budget;
- the reservation uses trusted model pricing metadata plus bounded input/output ceilings, never model-supplied estimates;
- if the reservation would exceed the remaining monthly budget, Ralph makes no request and parks the phase with a budget blocker;
- after a successful response, Ralph reconciles the reservation against provider-reported token usage and records the actual charge estimate;
- unknown/malformed usage or unknown pricing fails closed for additional paid calls until reconciled;
- a dedicated OpenAI project for Ralph must also have an enforced **$30/month project spend limit** as the provider-side backstop before live API routing is enabled.

Ralph also requires maximum serialized evidence size, maximum response tokens, an approved model allowlist, bounded reasoning effort, maximum API invocations per role cycle, and no retry policy that can create an unbounded billing loop.

A transient request may receive one bounded transport retry only when failure is known to have occurred before a usable model response. Server errors after uncertain processing are not blindly retried.

No worker may alter these cost limits. Increasing the $30 monthly cap, model allowlists, token ceilings, or enabling the API worker is an operator-controlled configuration change.

## Routing and fallback policy

Implementation routing remains unchanged in v1:

```text
Claude Code -> Codex implementation fallback -> usage-limit blocker
```

Reasoning routing becomes independently configurable:

```text
Routine review: OpenAI API Terra -> Codex Terra/Sol fallback
Escalated review: OpenAI API Sol -> Codex Sol fallback
Routine planning: OpenAI API Sol -> existing lower-cost fallback
Adjudication: deterministic gates -> Sol adjudication -> Astra only if still unresolved
Final release review: existing Codex Sol only
```

Astra is an emergency adjudicator, not a routine worker. It is eligible only when all of the following are true:

1. the phase is adjudication, never routine review, planning, implementation, or final verification;
2. deterministic supervisor evidence cannot resolve the decision;
3. lower-cost Sol adjudication has already failed, returned an explicitly unresolved result, or is unavailable;
4. the task has reached the configured adjudication threshold or involves a security/architecture conflict that cannot safely proceed without higher-level resolution;
5. no Astra call has already been made for the same escalation episode; and
6. the monthly budget gate can reserve the worst-case allowed Astra request.

If any condition is false, Ralph must not call Astra. Provider and model provenance are recorded truthfully; no alias or substitute may be labeled Astra.

Routing decisions are based on trusted phase, configured policy, failure category, budget state, and existing failure counters. Model output cannot widen its own eligibility, budget, fallback, or escalation authority.

A provider failure must preserve the interrupted Ralph phase exactly, just as the current usage-limit path does.

## Failure taxonomy

OpenAI API failures are not automatically equivalent to ChatGPT/Codex usage-limit resets. The provider adapter distinguishes at least:

- `auth`: missing/invalid credential or authentication rejection;
- `rate_limit`: API throttling/capacity condition;
- `billing`: API billing/quota condition requiring operator action;
- `timeout`: bounded transport timeout;
- `transient`: retryable connection or server failure;
- `invalid_output`: refusal, incomplete result, malformed schema, or binding failure;
- `failed`: other provider failure.

Only existing Codex/ChatGPT usage-limit failures enter the banked-reset workflow. An OpenAI API 429 must never offer or consume a ChatGPT/Codex reset.

When an API review/lead call fails, provider routing attempts the configured Codex fallback before the supervisor records a blocker. If the fallback is also unavailable, Ralph records the actual provider/failure category and preserves `resume_status` for that phase.

## Provenance and state

The existing Ralph invocation ID remains the primary correlation identifier. Provider execution records include the actual provider and model used, but no secret or prompt content.

The live worktree lease, pre-HEAD, post-HEAD, scratch ownership nonce, durable pending result, and result replay semantics stay unchanged. Because the API worker is read-only, its permitted repository delta is always zero.

`WorkerState` does not need a new long-lived API session ID. Responses requests are independent and should not create hidden conversational state between Ralph cycles. Any future stateful provider session would require a separate design and privacy review.

Handoff records continue to record the actual last provider. Review policy must distinguish a mutating implementation provider from a read-only reasoning provider so an API review cannot accidentally trigger or bypass the current "Codex implementation requires Sol review" rule.

## Components

### `provider_routing.py`

Owns trusted phase-to-provider selection and bounded fallback chains. It contains no network logic and no repository mutation.

### `openai_worker.py`

Builds phase-specific instructions, accepts only bounded local evidence, invokes the transport, validates structured output, enforces identity binding, and raises typed provider errors. It exposes no filesystem or shell interface to the model.

### `openai_transport.py`

Owns the fixed HTTPS Responses request, credential injection at the transport boundary, timeout, status/error classification, response parsing, and privacy-safe diagnostic metadata. It never accepts arbitrary tools or arbitrary destination URLs.

### `evidence.py`

Builds and bounds review/planning evidence from trusted local sources. Evidence generation is deterministic and separately testable without a model or network.### Existing `runner.py` / invoker integration

`CliAgentInvoker` is refactored only enough to delegate reasoning phases through provider routing while preserving its current public `implement`, `review`, and `lead` methods. The supervisor-facing protocol and state machine remain unchanged.

Implementation mutation logic remains explicitly limited to Claude and Codex. The OpenAI API worker cannot enter `_create_scratch_commit()` or `_publish_scratch_commit()`.

### Existing `supervisor.py`

The supervisor continues to own task lifecycle, validation, review acceptance, escalation counters, blockers, coordination publication, and completion gates. It must not learn OpenAI wire details.

The only supervisor-level changes should be provider-neutral error handling where current logic incorrectly assumes every reasoning-provider capacity error is a banked Codex usage reset.

## Testing strategy

All development follows TDD. Required deterministic coverage includes:

1. provider capability policy rejects OpenAI API implementation/mutation;
2. routing selects API worker only for approved read-only phases;
3. disabled API configuration leaves current Claude/Codex behavior unchanged;
4. evidence bundles contain the required revision/diff/validation fields and obey size bounds;
5. truncation is explicit and cannot produce an unqualified passing review;
6. API requests use the fixed HTTPS endpoint, `store: false`, no tools, explicit model, strict schema, timeout, and bounded tokens;
7. API key is injected only at transport time and never appears in serialized config, logs, state, activity markers, or request evidence;
8. fake structured output still passes canonical `validate_agent_result()` and exact role/task/invocation binding;
9. refusal, incomplete output, malformed JSON, schema mismatch, and binding mismatch fail closed;
10. HTTP/auth/rate-limit/billing/timeout/transient failures map to distinct typed categories;
11. an API 429 cannot enter the banked ChatGPT/Codex reset path;
12. API failure falls back to Codex when policy permits;
13. simultaneous API and Codex unavailability preserves the exact interrupted phase;
14. OpenAI review cannot mutate live or scratch repository state;
15. Codex-implemented checkpoints still force the existing independent Sol review rule;
16. final release verification stays on the existing final-review path;
17. normal tests use fake transport only and make zero external network calls;
18. monthly budget reservation refuses a call whose worst-case cost exceeds the remaining $30 cap;
19. spend reconciliation uses provider-reported usage and unknown pricing/usage fails closed;
20. Astra is rejected for routine review/planning and cannot exceed one call per escalation episode;
21. an exhausted monthly budget preserves the interrupted phase without falling into the Codex reset workflow.

The existing focused orchestrator suite remains mandatory. Ruff, Black, compile/static checks, security audit, `git diff --check`, and the existing frozen-worktree safety assertions must remain green.

A live API smoke test is optional and operator-triggered only. It is never required in ordinary CI and must use a non-sensitive synthetic prompt, a strict spending ceiling, and truthful labeling as live-provider evidence.

## Configuration

Routing/security settings must be typed rather than hidden inside arbitrary metadata. Proposed configuration fields are:

- `openai_worker_enabled: bool = false`
- `openai_review_model: str = "gpt-5.6-terra"`
- `openai_escalation_model: str = "gpt-5.6-sol"`
- `openai_planning_model: str = "gpt-5.6-sol"`
- `openai_astra_model: str = "gpt-6-astra"`
- `openai_astra_enabled: bool = true`
- `openai_monthly_budget_usd: Decimal = Decimal("30.00")`
- `openai_timeout_seconds: int`
- `openai_max_input_chars: int`
- `openai_max_output_tokens: int`
- `openai_max_calls_per_cycle: int`
- `openai_max_astra_calls_per_escalation: int = 1`

Model values are validated against a local allowlist. The API endpoint is not configurable in v1. The credential itself is never represented by these fields.

Enabling the worker without a usable credential does not break implementation routing. Ralph records the API provider as unavailable and follows the trusted Codex fallback policy.

## Rollout

Development occurs only on `lead/openai-model-worker` in the isolated `rex-ai-openai-worker` worktree. The running `lead/dev-orchestrator` checkout is not edited while its supervisor is live.

After implementation passes all gates:

1. confirm the live supervisor has no active model child or postprocessing scratch publication;
2. pause Ralph through the supported CLI;
3. import the verified commit onto `lead/dev-orchestrator` using a clean fast-forward/cherry-pick boundary;
4. rerun the focused orchestrator gates on the live branch;
5. restart only the supervisor process;
6. verify heartbeat PID/FILETIME and persisted worker states;
7. initially leave `openai_worker_enabled=false`;
8. enable it only after credential availability and an explicit operator decision;
9. observe a read-only synthetic review/lead cycle before relying on fallback routing.At rollback, disable the API worker in configuration and restart the supervisor. No worker-state migration is required because the supervisor protocol and canonical `AgentResult` remain unchanged.

## Security invariants

The following are non-negotiable:

- OpenAI API v1 is read-only with respect to repositories and coordination authority.
- No API response may directly write a file, create a commit, publish a coordination message, update an issue, or change a worker state; all side effects pass through existing supervisor validation.
- No external model may expand its own allowed phases, tools, model allowlist, cost budget, credential access, or fallback order.
- The API key is never model input.
- The model receives no arbitrary local file-reading capability.
- The frozen `rex-ai-pc-test` worktree is never included in evidence or made available to the worker.
- API-provider failures cannot weaken deterministic validation or independent final verification.
- Existing human-only gates remain human-only.
- Existing Claude/Codex mutation provenance and publication rules are unchanged.

## Initial acceptance criteria

The first release is acceptable only when all of the following are proven:

1. With the API worker disabled, existing Ralph behavior and focused tests remain unchanged.
2. With a fake API transport enabled, routine review and lead tasks can return canonical bound `AgentResult` objects without invoking Codex.
3. The worker has no code path capable of repository mutation.
4. Evidence is deterministic, bounded, privacy-filtered, and exposes truncation explicitly.
5. API requests use no tools and disable response storage.
6. Credentials remain vault-backed and absent from logs/state/config/evidence.
7. API failures fall back according to trusted policy without touching the banked Codex-reset counter.
8. A Codex implementation still receives the required independent Sol review.
9. Final release verification remains on the existing deterministic + independent Sol gate.
10. Full focused orchestrator tests, Ruff, Black, static checks, security audit, and `git diff --check` are green.
11. The live supervisor can be upgraded with persisted tasks/queues preserved and rolled back by disabling the new provider.

## Future extensions

Only after v1 has production evidence should a later design consider richer API-agent tools, stateful sessions, alternate OpenAI endpoints, or repository mutation. Each would expand authority and therefore requires a separate explicit security/design approval rather than being enabled implicitly by this worker abstraction.
