# AskRex Agent Runtime and MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a persistent least-privilege Agent Runtime and first-class MCP support by extending existing Rex Core authority, capability, action-verification, memory, credential, scheduling, and Ralph/supervisor systems.

**Architecture:** Agents are versioned policy/configuration overlays that always execute through `TurnEngine`. MCP is an external capability provider that normalizes executable tools into the existing `CapabilityRegistry` and uses canonical permission/action/verification policy. Skills become reusable procedures/capability requirements, while executable generation flows through existing Forge/Ralph verification and promotion controls.

**Tech Stack:** Python 3.11, current Rex runtime/capability/action/credential/memory/scheduler architecture, JSON/typed dataclasses or equivalent current repo conventions, pytest, Ruff, Black, mypy, Electron/TypeScript only after backend contracts are stable.

**Spec:** `docs/superpowers/specs/2026-09-14-agent-runtime-mcp-skills-design.md`

## Global Constraints

- Read and follow the current `CLAUDE.md`, repo agent instructions, and shared AskRex coordination protocol before each story.
- Preserve `TurnEngine` as the canonical execution path.
- Preserve the invariant: agent configuration may reduce authority but never create authority.
- Extend `CapabilityRegistry`; do not create a competing MCP registry.
- All mutations use canonical `ToolExecutionLifecycle`/action verification.
- Credentials remain in the canonical vault and never enter prompts, AgentDefinition records, skill records, capability metadata, or logs.
- MCP/remote metadata is untrusted and may never weaken local policy.
- Do not implement product stories directly in the architect/planner worktree after Ralph activation; use Ralph worker/reviewer execution.
- Do not modify the frozen live-test checkout.
- Existing Forge US-116/US-117 remains the canonical promotion/rollback foundation for generated executable capabilities.
- Preserve all existing Ruff, Black, mypy, pytest, security, working-tree, packaging, and CI gates.

---

## Milestone A: Minimal Agent Runtime vertical slice

Target proof:

`Persistent AgentDefinition -> manual invocation -> authority intersection -> TurnEngine -> bounded agent memory -> one existing native capability -> canonical action lifecycle -> verification -> correlated AgentRun audit`

Milestone A is satisfied by S36-S38 plus S46's minimum capability/memory scope work. It does not require MCP, Agent Manager, delegation, or self-extension.

## Milestone B: Minimal MCP vertical slice

Target proof:

`Real MCP server -> connection/session -> tool discovery -> CapabilityRegistry -> caller/agent permission filtering -> action lifecycle/risk/approval -> execution -> verification -> correlated audit`

Milestone B is satisfied by S39-S45 with one deterministic fixture server and one real configured MCP server when external evidence is available.

---

### Task 1: S36 Canonical AgentDefinition, lifecycle, and persistence

**Files:**
- Create: `rex/agents/__init__.py`
- Create: `rex/agents/definition.py`
- Create: `rex/agents/lifecycle.py`
- Create: `rex/agents/store.py`
- Test: `tests/agents/test_definition.py`
- Test: `tests/agents/test_lifecycle.py`
- Test: `tests/agents/test_store.py`
- Modify: `CLAUDE.md` only to document the implemented canonical contract after tests pass.

**Interfaces:**
- Produces: versioned `AgentDefinition`, lifecycle enum/state-transition validator, owner-scoped persistent repository/store.
- Consumes: canonical user validation/runtime paths and current persistence patterns already used elsewhere in Rex.

- [ ] Write failing tests for complete schema round-trip, owner/workspace validation, no raw credential fields, duplicate-name-safe stable IDs, and deterministic serialization.
- [ ] Run the focused tests and verify they fail because `rex.agents` does not exist.
- [ ] Implement the smallest versioned `AgentDefinition` matching ADR-AGENT-RUNTIME-MCP-001 and the source-of-truth checklist.
- [ ] Run focused tests and confirm schema tests pass.
- [ ] Write failing lifecycle tests for allowed transitions, invalid skips, pause, revoke, archive, and reactivation-after-revocation requiring new approval state.
- [ ] Implement the lifecycle validator without embedding runtime/tool behavior.
- [ ] Write failing persistence tests proving records live under the owning user's canonical data root, malformed records fail closed, and two users/agents cannot collide.
- [ ] Implement the minimal store/repository following current atomic-storage conventions.
- [ ] Run focused pytest, mypy, Ruff, Black, `git diff --check`, and the release security audit.
- [ ] Commit as an independently reviewable checkpoint and return `ready_for_review` through Ralph.

**Definition of done:** Agent configuration can be created, validated, approved, activated, paused, revoked, and archived with versioned owner-scoped persistence but no model/tool execution authority.

### Task 2: S37 AgentRun, provenance, and authority-intersection resolver

**Files:**
- Create: `rex/agents/run.py`
- Create: `rex/agents/policy.py`
- Test: `tests/agents/test_run.py`
- Test: `tests/agents/test_policy.py`
- Modify existing permission/audit adapters only where necessary to consume the new resolver; do not duplicate them.

**Interfaces:**
- Consumes: `AgentDefinition` from S36 and current user/workspace/capability policy evidence.
- Produces: `AgentRun`, `AuthorityDecision` or equivalent current-style immutable result, root/parent/correlation identifiers.

- [ ] Write failing tests proving explicit deny wins and an agent `allow` never supplies a missing user permission.
- [ ] Write failing tests for workspace mismatch, risk/approval requirements, revoked agents, and unhealthy/untrusted capability evidence.
- [ ] Implement a pure deterministic authority-intersection function using current policy/permission types.
- [ ] Write failing run-ledger tests for root/parent lineage, content-free audit metadata, and redaction of prompts/secrets/private memory.
- [ ] Implement the minimal AgentRun/provenance representation.
- [ ] Run focused tests plus all current identity/permission/action-lifecycle regressions.
- [ ] Commit and submit to independent review.

**Definition of done:** Tests prove an agent cannot obtain authority absent from its originating principal/current Rex policy, and every future invocation can be correlated without retaining sensitive content.

### Task 3: S38 Manual AgentRuntime invocation through TurnEngine

**Files:**
- Create: `rex/agents/runtime.py`
- Test: `tests/agents/test_runtime.py`
- Modify: only the smallest `rex.runtime`/Assistant/model/capability seams needed to pass request-local agent policy without introducing a second reply pipeline.

**Interfaces:**
- Consumes: S36 `AgentDefinition`, S37 authority/run contracts, current TurnEngine invocation, ModelRouter, CapabilityRetriever, ActionDispatcher, memory services.
- Produces: `AgentRuntime.invoke(...)` and canonical AgentRun-to-Turn correlation.

- [ ] Write failing test showing inactive/paused/revoked agents cannot invoke.
- [ ] Write failing test showing active manual invocation creates AgentRun then enters existing TurnEngine with immutable originating user identity.
- [ ] Write failing tests for read-only capability, mutation requiring approval/verification, deadline/cancellation, and model/provider policy narrowing.
- [ ] Implement the minimal adapter that applies agent overlays and delegates execution to TurnEngine.
- [ ] Prove there is no direct LanguageModel/tool/credential/memory shortcut from `rex.agents.runtime`.
- [ ] Run focused tests plus TurnEngine/Assistant/action regressions and static source guard if one exists.
- [ ] Commit and submit to independent review.

**Definition of done:** an active manually invoked agent uses the same canonical reply/tool/verification pipeline as ordinary Rex and cannot change user identity or widen authority.

### Task 4: S46 minimum per-agent capability and memory scoping for Milestone A

**Files:**
- Modify: `rex/agents/definition.py`, `rex/agents/policy.py`, `rex/agents/runtime.py`
- Create: focused agent-memory namespace adapter only if existing memory APIs cannot express the scope directly.
- Test: `tests/agents/test_capability_bindings.py`
- Test: `tests/agents/test_memory_scope.py`

**Interfaces:**
- Capability binding effect: `allow | deny | require_approval` (exact type/name may follow repo conventions).
- Memory access is a filter/namespace over canonical stores, not a second memory engine.

- [ ] Write failing tests showing installed native capability is unavailable unless explicitly assigned to the agent.
- [ ] Write failing tests proving deny wins and approval-required cannot be silently treated as allow.
- [ ] Write failing tests for same-user different-agent private namespaces, cross-user denial, and explicit workspace-shared access.
- [ ] Implement minimal binding and memory-scope enforcement in the request-local AgentRuntime policy.
- [ ] Add one end-to-end test using an existing safe native read capability and one verifiable mutation fixture.
- [ ] Run full Milestone A focused matrix and regressions.
- [ ] Commit and submit to independent review.

**Milestone A gate:** persistent definition, authority reduction, TurnEngine reuse, scoped memory, restricted native capability, lifecycle verification, and correlated audit all pass independent review.

---

### Task 5: S39 McpServerDefinition and health model

**Files:**
- Create: `rex/mcp/__init__.py`
- Create: `rex/mcp/definition.py`
- Create: `rex/mcp/store.py`
- Create: `rex/mcp/health.py`
- Test: `tests/mcp/test_definition.py`
- Test: `tests/mcp/test_health.py`

- [ ] Write failing tests for stdio vs Streamable HTTP config validation, owner/workspace scope, trust/provenance, auth references, capability digest, and raw-secret rejection.
- [ ] Implement versioned secret-free server definitions using canonical runtime paths/atomic storage.
- [ ] Write failing tests for disabled/configured/connecting/auth-required/healthy/degraded/incompatible/unavailable/stale transitions.
- [ ] Implement truthful health projection without making connection state an authority grant.
- [ ] Run focused gates and independent review.

**Definition of done:** Rex can persist MCP server configuration and truthful health without connecting or storing raw credentials.

### Task 6: S40 stdio transport + S41 Streamable HTTP transport/session primitives

**Files:**
- Create: `rex/mcp/protocol.py`
- Create: `rex/mcp/session.py`
- Create: `rex/mcp/transport_stdio.py`
- Create: `rex/mcp/transport_http.py`
- Test: `tests/mcp/fixtures/` deterministic fixture servers
- Test: `tests/mcp/test_stdio_transport.py`
- Test: `tests/mcp/test_http_transport.py`
- Test: `tests/mcp/test_session.py`

- [ ] Write stdio initialization/version/capability-negotiation tests before implementation.
- [ ] Add failure tests for malformed frames, crash, timeout, cancellation, and undeclared environment-secret inheritance.
- [ ] Implement minimal stdio JSON-RPC/session lifecycle with explicit environment construction.
- [ ] Write Streamable HTTP tests for initialization, protocol header/session handling, JSON and streamed responses, close/reconnect, bounded payloads, cancellation, and non-loopback policy.
- [ ] Implement current standard Streamable HTTP transport without legacy SSE compatibility unless a verified server requires it.
- [ ] Run transport/security tests and independent review.

**Definition of done:** deterministic fixture servers negotiate and fail safely over both standard MCP transports.

### Task 7: S42 discovery/catalog and CapabilityRegistry normalization

**Files:**
- Create: `rex/mcp/discovery.py`
- Create: `rex/mcp/catalog.py`
- Modify: `rex/capabilities/registry.py` only enough to represent MCP provenance/namespaced external IDs safely.
- Test: `tests/mcp/test_discovery.py`
- Test: `tests/mcp/test_registry_projection.py`

- [ ] Write failing tests for tools/resources/prompts discovery, schema bounds, duplicate IDs, malicious metadata, and snapshot removal/change.
- [ ] Write a failing security test proving remote metadata cannot lower a pre-existing local permission/risk/identity/verification classification.
- [ ] Implement stable MCP tool IDs and validated tool snapshot projection into canonical CapabilityRegistry.
- [ ] Keep resources/prompts in protocol-specific context catalogs without executable authority.
- [ ] Add privilege/schema-diff pending-review behavior.
- [ ] Run capability/OpenClaw/retrieval regressions and independent review.

**Definition of done:** approved healthy MCP tools participate in canonical discovery/retrieval while remote metadata cannot widen authority.

### Task 8: S43 MCP execution through action lifecycle

**Files:**
- Create: `rex/mcp/executor.py`
- Modify canonical dispatcher adapter only as required.
- Test: `tests/mcp/test_execution.py`
- Test: `tests/mcp/test_action_lifecycle.py`

- [ ] Write failing tests for call-time permission/binding/schema revalidation.
- [ ] Write failing tests for read completion, mutation verified/unverified, approval requirement, cancellation, malformed response, transport uncertainty, and a server falsely claiming verification.
- [ ] Implement the smallest adapter that calls MCP only after canonical authorization and returns evidence into canonical lifecycle/verification.
- [ ] Run action/tool/OpenClaw regression suites.
- [ ] Commit and submit to independent review.

**Definition of done:** MCP execution cannot bypass Rex permission, risk, approval, cancellation, action truth, or independent verification.

### Task 9: S44 MCP auth and CredentialVault integration

**Files:**
- Create: `rex/mcp/auth.py`
- Modify canonical credential-reference services only where needed.
- Test: `tests/mcp/test_auth.py`

- [ ] Write failing tests for no-auth, bearer reference, auth-required, protected-resource metadata discovery, authorization-server discovery, wrong audience/resource, refresh, revocation, reconnect, cross-user isolation, and redaction.
- [ ] Implement scoped stdio credential injection and current MCP HTTP OAuth profile using vault references only.
- [ ] Prove token passthrough to unrelated downstream resources is rejected.
- [ ] Run credential/security regressions and independent review.

**Definition of done:** MCP authentication works without tokens in config, prompts, logs, AgentDefinitions, or capability metadata.

### Task 10: S45 adversarial MCP security suite and Milestone B

**Files:**
- Create/extend: `tests/mcp/security/`
- Modify protocol/discovery/executor/auth code only when a failing adversarial test proves a defect.

- [ ] Add injection/tool-poisoning/malicious-schema/resource tests.
- [ ] Add server-impersonation/capability-substitution/privilege-expansion tests.
- [ ] Add cross-user/cross-agent leakage tests.
- [ ] Add filesystem/network/environment escape tests for stdio.
- [ ] Add timeout/rate/resource/reconnect/auth-expiry tests.
- [ ] Run all MCP tests plus canonical capability/action/security regressions.
- [ ] Connect one real MCP server only after deterministic tests pass and label that evidence `live_provider` separately.
- [ ] Commit and submit to independent review.

**Milestone B gate:** real/fixture MCP tool enters canonical CapabilityRegistry, caller/agent permission filtering, action lifecycle, approval/risk handling, verification, and audit without a parallel execution path.

---

### Task 11: S47 persistent triggers

**Files:**
- Create: `rex/agents/triggers.py`
- Thin adapters to existing scheduler/automation/event infrastructure only where required.
- Test: `tests/agents/test_triggers.py`

- [ ] Write failing tests for manual, schedule, webhook, email, file/repo, system, Home Assistant, monitoring, and delegation trigger normalization where corresponding infrastructure exists.
- [ ] Verify duplicate delivery/idempotency, pause/revoke, retry, timeout, restart recovery, and source validation.
- [ ] Implement adapters that call AgentRuntime only and never model/tools directly.
- [ ] Run scheduler/automation/event regressions and review.

### Task 12: S48 safe agent-to-agent delegation

**Files:**
- Create: `rex/agents/delegation.py`
- Modify: `rex/agents/runtime.py`, run/audit integration.
- Test: `tests/agents/test_delegation.py`

- [ ] Write failing tests for bounded DelegationEnvelope, target allowlist, authority intersection, context-reference transfer, no credentials, no implicit memory, max depth/concurrency/budget, cycle detection, target revocation, and full lineage.
- [ ] Implement the minimal internal delegation contract.
- [ ] Keep A2A out of scope until this contract passes.
- [ ] Run independent review.

### Task 13: S49-S50 skills migration and abstract capability requirements

**Files:**
- Create/evolve: `rex/skills/definition.py`, registry/store/migration/compatibility adapter.
- Test: `tests/skills/test_definition_v2.py`, `test_legacy_migration.py`, `test_capability_requirements.py`.

- [ ] Write migration tests for existing handler-path skills before changing persistence.
- [ ] Implement non-executable versioned SkillDefinition and treat legacy executable handlers as canonical capabilities/plugins with explicit policy metadata.
- [ ] Add abstract capability requirement resolution through CapabilityRetriever after policy filtering.
- [ ] Test provider substitution, explicit pinning, disabled/unhealthy/unauthorized implementations, version/provenance, and injection resistance.
- [ ] Document deprecation/compatibility timeline.
- [ ] Run independent review.

### Task 14: S51-S53 controlled self-extension integration

**Files:**
- Modify: `rex/capabilities/recovery.py` provider adapters.
- Implement integration to existing Forge US-116/117 only after those stories are verified.
- Create scaffold/template module only after MCP core is stable.
- Test: focused gap/Forge/promotion/rollback/scaffold tests.

- [ ] Prove gap recovery searches enabled/disabled native, approved MCP/OpenClaw, skills/composition, configured external candidates before Forge.
- [ ] Keep resolver advisory/non-executing.
- [ ] Define bounded development proposal into existing Ralph workflow.
- [ ] Verify generated executable candidate begins inactive with manifest/schema/tests/requested authority/provenance/content digest.
- [ ] Enforce privileged approval, permission-expansion reapproval, rollback, and revocation through existing Forge controls.
- [ ] Add standards-based MCP capability scaffold that remains inactive until normal promotion.
- [ ] Run independent security review.

### Task 15: S54-S56 export/management/UI

**Files:**
- Create explicit `rex/mcp/server/` export adapters/policy.
- Create backend Agent/MCP management service.
- Add CLI/API adapters.
- Add Electron IPC/UI only after backend contract tests are green.

- [ ] Write tests proving exported MCP capability set is empty by default.
- [ ] Implement explicit allowlisted export with inbound principal mapping and canonical policy checks.
- [ ] Build backend management service supporting agent lifecycle and MCP add/auth/reconnect/inspect/assign/change approval.
- [ ] Prove CLI/API/config parity before UI.
- [ ] Add thin Electron Agent Manager/MCP management client with renderer-blind credentials and trusted confirmation.
- [ ] Run backend, IPC, TypeScript, Electron smoke, and independent review.

### Task 16: S57 A2A decision and S58 production evidence

**Files:**
- Create an ADR for A2A accept/defer/reject after internal delegation is stable.
- Extend release evidence profile/checklist for Agent Runtime/MCP.

- [ ] Evaluate A2A only against the implemented internal delegation contract; do not make Agent Runtime delivery depend on it.
- [ ] Run full two-user/two-agent, delegation, MCP outage/recovery/auth/schema drift/malicious-content, self-extension promotion/rollback, packaged Electron, and applicable live-provider evidence.
- [ ] Verify retained evidence contains no prompts, transcripts, memory, credentials, or raw private payloads.
- [ ] Run full current release/CI gates on the exact candidate revision.
- [ ] Require zero waived Critical/High findings before advertising Agent Runtime/MCP as production-ready.

**Definition of done:** implemented surfaces have independently verified security/behavior evidence and documentation states only what is actually live.

## Execution order

`S36 -> S37 -> S38 -> S46(minimum)` proves Milestone A.

`S39 -> S40/S41 -> S42 -> S43 -> S44 -> S45` proves Milestone B.

Then `S47 -> S48 -> S49/S50 -> S51/S52/S53 -> S54/S55/S56 -> S57/S58`.

Parallelism is allowed only where dependencies and repository ownership are independent. Workers must report architectural conflicts rather than silently redesign shared contracts.
