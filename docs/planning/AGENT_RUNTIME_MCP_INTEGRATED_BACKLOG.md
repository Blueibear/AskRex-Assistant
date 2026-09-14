# AskRex Agent Runtime + MCP Integrated Delivery Backlog

Status: Approved roadmap amendment to the existing unified AskRex delivery workflow
Date: 2026-09-14
Canonical design: `docs/superpowers/specs/2026-09-14-agent-runtime-mcp-skills-design.md`
Source-of-truth checklist: `docs/planning/source-of-truth/REX_AGENT_RUNTIME_MCP_CHECKLIST.md`

## Integration rule

This backlog is a direct continuation of `docs/planning/ASKREX_UNIFIED_DELIVERY_BACKLOG.md`. Existing S1-S35 work, production-readiness stories, quality gates, release evidence, and owner gates remain authoritative. These stories begin at S36 and are not a parallel tracker or side project.

The current S35 provider-neutral speech-router work may finish through its existing Ralph worker/reviewer lifecycle. S36+ should be queued behind the current safe checkpoint unless the owner explicitly reprioritizes again.

Every implementation story must use the shared AskRex coordination protocol, isolated scratch/worktree execution, TDD where code changes occur, independent reviewer verification, and the repository's existing Ruff/Black/mypy/pytest/security/working-tree/packaging gates. Never modify the frozen live-test checkout.

## Current-state mapping

| Target capability | Reuse now | Missing foundation |
|---|---|---|
| Agent execution | `rex.runtime` / TurnEngine, cancellation/status, canonical Assistant pipeline | Persistent AgentDefinition, AgentRun, AgentRuntime policy overlay |
| Model policy | `rex.model_router`, provider reliability/fallback | Per-agent request-local policy/budget overlay |
| Capabilities | `rex.capabilities.registry`, retrieval, recovery | Per-agent bindings; generalized MCP source normalization |
| Action truth | `rex.actions.lifecycle`, verification | MCP adapter + agent-run correlation |
| Memory | user/private roots, LongTermMemory, procedural memory | explicit agent namespace/workspace policy |
| Credentials | credential vault/services | MCP auth references/OAuth scopes |
| OpenClaw | capability sync/reconnect/tool adapters | remains optional provider; no change in authority |
| Skills | `rex.skills` registry/loader/router | procedural SkillDefinition v2; legacy executable-handler migration |
| Scheduling | scheduler/automation/event mechanisms | normalized agent triggers into one runtime path |
| Self-extension | CapabilityGapResolver; planned Forge US-116/117 | integrate Forge/Ralph result into versioned capability promotion |
| MCP | configured candidate metadata in gap resolver | no first-class client/server/session/transport runtime today |
| Delegation | supervisor/task concepts | production AgentRun delegation contract/provenance |

Priority key: **P0** authority/security/runtime foundation, **P1** required platform capability, **P2** management/interoperability after foundations.

---

## Batch 11 - Agent Runtime authority and persistence

### S36 - Define canonical AgentDefinition, lifecycle, and versioned persistence (P0)

**Goal:** Establish one persistent representation for agents before any scheduler/UI/runtime behavior can invent ad hoc agent state.

**Files/areas:** create focused `rex/agents/` package (`definition.py`, `lifecycle.py`, `store.py`); canonical runtime path helpers; tests under `tests/agents/`; architecture docs/CLAUDE rule update.

**Required behavior:**
- Define stable agent ID, owner user, workspace scope, purpose/instructions, model policy, skill refs, capability bindings, memory policy, triggers, resources, approvals, escalation, verification, delegation, retry, notification, audit, lifecycle, version, provenance, timestamps.
- Validate references and schema before activation.
- Implement fail-closed lifecycle `draft -> validated -> approved -> active -> paused/revoked -> archived` with explicit transition rules.
- Store private records under canonical owning-user data roots; no raw credentials in records.
- Privilege/version expansion invalidates prior approval where policy requires it.

**Tests:** schema round-trip/version migration; invalid owner/workspace; invalid lifecycle transition; pause/revoke/reactivation; same-name agent isolation; malformed persisted record; privilege-diff invalidation.

**Validation:** focused pytest + mypy/Ruff/Black on new package, then normal repo gates.

**DoD:** an agent can be safely created/validated/approved/activated/paused/revoked/archived without any model/tool execution and without cross-user state exposure.

### S37 - Add AgentRun ledger, provenance, and policy-intersection contracts (P0)

**Goal:** Make every agent invocation/delegation traceable and prove that agent policy can only narrow principal authority.

**Dependencies:** S36.

**Files/areas:** `rex/agents/run.py`, `rex/agents/policy.py`, existing audit/permission abstractions, Turn/Action correlation helpers, tests.

**Required behavior:**
- Define stable `run_id`, `root_run_id`, `parent_run_id`, user/source/agent IDs, correlation to turn/action IDs, bounded model/resource metadata, decision labels, terminal status/duration.
- Effective authority = authenticated principal ∩ workspace ∩ agent binding ∩ capability policy ∩ risk/approval ∩ health/trust.
- Explicit deny wins; no agent/service metadata widens authority.
- Audit projection is privacy-safe and redacted.

**Tests:** deny precedence; user permission absent; workspace mismatch; agent allow cannot widen principal; approval-required; revoked agent; audit redaction; root/parent lineage.

**DoD:** policy-intersection tests prove no agent can gain authority its principal did not already possess.

### S38 - Implement canonical AgentRuntime invocation through TurnEngine (P0)

**Goal:** Make an active agent a policy/configuration overlay on the existing canonical Rex turn pipeline.

**Dependencies:** S36-S37; existing TurnEngine stories remain authoritative.

**Files/areas:** `rex/agents/runtime.py`, minimal Turn invocation/provenance adapter, ModelRouter request-local selection seam, capability retriever/dispatcher integration, memory-context seam, tests.

**Required behavior:**
- `invoke(agent_id, invocation)` loads/validates active definition, binds trusted principal/source, creates AgentRun, applies request-local model/memory/capability/resource policy, then enters TurnEngine.
- No direct LLM/tool/memory/credential shortcuts.
- Existing cancellation/status/action verification semantics remain unchanged.
- Pause/revoke rejects new work; cancellation never falsely reports rollback of a dispatched mutation.
- Resource exhaustion escalates/fails safely.

**Tests:** direct answer; read-only capability; mutation/approval/verification; model fallback allowed/denied; deadline; tool-call budget; paused/revoked; cancellation race; identity immutability.

**DoD:** the same underlying Rex pipeline handles ordinary Assistant turns and agent turns, with agent policy visible only as bounded request-local context.

---

## Batch 12 - First-class MCP client and capability integration

### S39 - Define McpServerDefinition store and truthful health state (P1)

**Goal:** Persist MCP server configuration, ownership, trust/provenance, auth references, protocol constraints, and health without storing secrets.

**Dependencies:** S37 policy/provenance contracts.

**Files/areas:** new `rex/mcp/definition.py`, `store.py`, `health.py`; credential-reference validation; tests.

**Required behavior:**
- Server record supports stdio command/args or Streamable HTTP endpoint, auth reference, owner/workspace, enabled state, trust/provenance, allowed local boundaries, assigned-agent metadata, last negotiated protocol/server metadata, capability snapshot digest.
- Health distinguishes disabled/configured/connecting/auth-required/healthy/degraded/incompatible/unavailable/stale.
- Records contain no raw tokens/passwords.

**Tests:** schema/transport validation; raw-secret rejection; ownership isolation; health transitions; stale snapshot; disabled server.

**DoD:** MCP configuration is versioned, user/workspace-scoped, secret-free, and truthful before any network/subprocess connection exists.

### S40 - Implement MCP stdio transport and negotiated session lifecycle (P1)

**Goal:** Connect to local MCP subprocess servers without inheriting ambient Rex authority.

**Dependencies:** S39.

**Files/areas:** `rex/mcp/protocol.py`, `transport_stdio.py`, `session.py`; subprocess/env boundary helper; tests with deterministic fixture server.

**Required behavior:**
- JSON-RPC framing, initialization, negotiated protocol version/capabilities, clean close, deadlines/cancellation.
- Child receives minimal explicit environment only; credential injection is opt-in by declared reference.
- stdout is protocol-only; stderr is bounded/sanitized diagnostics.
- crash/timeout/malformed message fail safely and update health.

**Tests:** successful negotiation; unsupported version; malformed frame; child crash; timeout/cancel; environment-secret noninheritance; bounded output.

**DoD:** a local fixture MCP server connects/disconnects reliably and cannot see undeclared Rex/user secrets.

### S41 - Implement MCP Streamable HTTP transport and session lifecycle (P1)

**Goal:** Support the current standard remote MCP HTTP transport with protocol/session rules and safe local defaults.

**Dependencies:** S39; S40 protocol/session contracts.

**Files/areas:** `rex/mcp/transport_http.py`, shared session/protocol code, origin/network policy, tests with local fixture HTTP server.

**Required behavior:**
- Initialization/version negotiation, `MCP-Protocol-Version`, session-ID handling where provided, POST/GET stream semantics as negotiated, reconnect/close, bounded payloads.
- Loopback-safe default for local endpoints; non-loopback requires explicit policy/configuration.
- Older HTTP+SSE is not implemented unless a verified configured server requires a compatibility story.

**Tests:** JSON response; streaming notification/response; invalid/missing session; protocol mismatch; server 4xx/5xx; cancellation; payload limit; non-loopback policy.

**DoD:** fixture Streamable HTTP server passes lifecycle tests without introducing a second Rex HTTP auth/policy stack.

### S42 - Discover MCP tools/resources/prompts and normalize tool snapshots into CapabilityRegistry (P1)

**Goal:** Make negotiated MCP capabilities discoverable through Rex's canonical metadata without allowing remote metadata to weaken local policy.

**Dependencies:** S40-S41; existing CapabilityRegistry.

**Files/areas:** `rex/mcp/discovery.py`, `catalog.py`, adapter changes in `rex/capabilities/registry.py` only as needed to support namespaced external sources safely; tests.

**Required behavior:**
- Discover negotiated tools, resources, prompts; schema-validate and bound metadata.
- MCP tool IDs are stable/namespaced (`mcp:<server-id>:tool:<name>` or equivalent).
- Resources/prompts remain separate catalogs/context sources, not executable grants.
- Snapshot updates compute added/changed/removed; removed tools become unavailable.
- Security/risk/permission/verification classification remains locally authoritative; material schema/privilege diff enters pending review.

**Tests:** discovery; duplicates; malicious/oversized description/schema; removed/renamed tool; privilege expansion; local ID conflict; list-changed notification if negotiated.

**DoD:** CapabilityRetriever can see healthy approved MCP tools, while an MCP server cannot downgrade Rex's local security classification.

### S43 - Execute MCP tools through canonical permission and action lifecycle (P0)

**Goal:** Route MCP executable work through the same permission/approval/cancellation/verification semantics as native/OpenClaw tools.

**Dependencies:** S42; existing ActionDispatcher/lifecycle.

**Files/areas:** `rex/mcp/executor.py`, canonical tool/capability dispatcher adapter, action verification hooks, tests.

**Required behavior:**
- Revalidate current user/workspace/agent binding and tool schema before every call.
- Propagate deadline/cancellation.
- Read-only completion can complete truthfully; mutations follow action lifecycle and require independent verification where a verifier exists.
- MCP output cannot self-label a mutation `verified`.
- Tool failure/timeout/retry status is auditable.

**Tests:** allow/deny/require-approval; read; mutation verified/unverified; cancellation before/during call; malformed response; retryable transport error; server claim of fake verification.

**DoD:** no MCP call creates a shortcut around canonical Rex action truth.

### S44 - Integrate MCP credentials and current HTTP OAuth profile with CredentialVault (P0)

**Goal:** Authenticate MCP servers without exposing credentials to agents/models/config/logs.

**Dependencies:** S39-S41; existing credential vault.

**Files/areas:** `rex/mcp/auth.py`, credential reference type/service adapter, OAuth callback/authorization helper appropriate to current UI/API architecture, tests.

**Required behavior:**
- stdio supports explicitly declared scoped env injection only.
- HTTP supports bearer/token configuration and current MCP OAuth protected-resource/authorization-server discovery.
- Enforce resource/audience binding; no token passthrough to unrelated downstream APIs.
- Store access/refresh material by owning user + MCP server + authorization server/workspace as required.
- Refresh/revoke/reconnect transitions update truthful health.

**Tests:** no-auth; bearer; auth-required; metadata discovery; wrong audience; expired access + refresh; revoked refresh; cross-user isolation; redaction; token-passthrough denial.

**DoD:** MCP auth works without raw secret material appearing in AgentDefinition, MCP config, logs, audits, prompts, or tool metadata.

### S45 - Add MCP adversarial/security hardening and resilience suite (P0)

**Goal:** Treat every external MCP surface as hostile until locally validated.

**Dependencies:** S40-S44.

**Files/areas:** protocol/discovery/executor hardening; dedicated `tests/mcp/security/`; security audit/test fixtures.

**Required behavior:**
- Bound JSON/message/schema/resource/tool text sizes; reject invalid types/schema drift.
- Add prompt-injection/tool-poisoning/malicious-schema/resource tests.
- Enforce filesystem/network/root restrictions and minimal subprocess environment.
- Rate/resource limits, cancellation, timeout, reconnect backoff, server crash/restart, changed auth/capability set.
- No content from MCP can change system/security policy or agent grants.

**DoD:** adversarial suite proves fail-closed behavior for server compromise, injection/poisoning, schema drift, privilege expansion, cross-user/cross-agent leakage, and capability substitution.

---

## Batch 13 - Persistent agents, triggers, isolation, and delegation

### S46 - Add per-agent capability bindings and memory/workspace scopes (P0)

**Goal:** Enforce explicit least-privilege tool access and memory namespaces per agent.

**Dependencies:** S38; S42 if MCP is assigned.

**Files/areas:** AgentDefinition binding types, policy intersection, agent memory namespace adapter over existing memory stores, tests.

**Required behavior:**
- Bind server/provider/capability/tool/operation with `allow`, `deny`, `require_approval`; deny wins.
- No installed capability is inherited by default.
- Agent private state partitions by owner + agent ID; workspace sharing is explicit.
- Delegation does not implicitly expose source-agent memory.

**Tests:** native/OpenClaw/MCP binding; same tool different agents; cross-user/agent memory; workspace shared/denied; approval effect; capability removal.

**DoD:** two agents owned by the same user can have different tools/memory, and two users' agents cannot cross-read or cross-execute.

### S47 - Normalize schedules/events/webhooks into AgentRuntime triggers (P1)

**Goal:** Make persistent agents autonomous without creating a second execution engine.

**Dependencies:** S38, S46; existing scheduler/automation/event infrastructure.

**Files/areas:** `rex/agents/triggers.py` plus thin adapters to scheduler/automation/email/webhook/HA/repo/system events where current infrastructure exists; tests.

**Required behavior:**
- Normalize manual, schedule, webhook, incoming email, file/repo event, system event, HA event, monitoring condition, and authorized delegation triggers.
- Validate/auth source, resolve owner/workspace/active agent, create idempotency/correlation key, then call AgentRuntime.
- Define duplicate delivery, pause/revoke, retry, timeout, and cancellation semantics.

**Tests:** schedule fires once; duplicate webhook suppressed; paused/revoked; malformed/untrusted event; email/HA adapters; restart recovery/idempotency.

**DoD:** scheduled/event-driven work produces the same AgentRun/Turn/Action policy path as an interactive invocation.

### S48 - Implement permission-safe agent-to-agent delegation and provenance (P1)

**Goal:** Allow agents to delegate bounded tasks while preserving principal authority and end-to-end provenance.

**Dependencies:** S37-S38, S46.

**Files/areas:** `rex/agents/delegation.py`, runtime integration, audit/run ledger, tests.

**Required behavior:**
- Define DelegationEnvelope with root/parent runs, origin user, source/target agents, objective, approved context refs, requested capability classes, deadline/budget, correlation.
- Credentials never travel in envelope; memory never shares implicitly.
- Add target allowlist, max depth/concurrency/budget, cycle detection.
- Target authority is intersection of origin principal/delegated scope/target policy/current capability policy.
- Refusal/escalation is first-class.

**Tests:** happy path; unauthorized target; privilege laundering attempt; memory/credential leakage; cycle; depth/budget limit; target revocation mid-run; full lineage.

**DoD:** a delegated mutation can be traced from originating user through every agent/tool/verification step and cannot gain broader authority.

---

## Batch 14 - Skills and controlled self-extension

### S49 - Split procedural SkillDefinition from executable legacy handlers (P1)

**Goal:** Keep skills as reusable procedure/knowledge while preserving backward compatibility for current executable skill records.

**Dependencies:** S36 capability references; existing `rex.skills`.

**Files/areas:** evolve `rex/skills/definition.py`, registry/store/migration; compatibility adapter that registers legacy handler code as an executable capability/plugin; tests.

**Required behavior:**
- Versioned SkillDefinition includes instructions/procedure, required/optional capability interfaces, I/O expectations, owner/workspace, provenance/trust/status.
- No raw credentials or implicit permissions.
- Existing `handler` records migrate without silent execution-policy weakening.
- Procedural skill content never grants executable authority.

**Tests:** migration; versioning; invalid dependency; disabled/revoked skill; legacy handler risk/permission mapping; content injection cannot change policy.

**DoD:** new skills can be completely non-executable while legacy executable skills continue through canonical capability security.

### S50 - Resolve abstract skill capability dependencies through CapabilityRetriever (P1)

**Goal:** Let a procedure depend on `email.read` or `order.lookup` rather than hard-code a provider when implementations are compatible.

**Dependencies:** S49; canonical CapabilityRetriever; S42 for MCP implementations.

**Files/areas:** skill dependency resolver, capability interface metadata/mapping kept minimal, tests.

**Required behavior:**
- Permission/identity/config/health/risk filtering occurs before implementation ranking.
- Provider-specific pinning remains supported when semantic compatibility is not guaranteed.
- No silent fallback to higher risk, cloud, or broader authority.

**Tests:** Gmail/Microsoft/IMAP-style alternatives via fixtures; unavailable provider; risk mismatch; user-denied provider; explicit provider pin; deterministic fallback.

**DoD:** skill procedures are reusable across equivalent approved providers without bypassing policy.

### S51 - Wire installed MCP capabilities and procedural skills into capability-gap recovery (P1)

**Goal:** Make `CapabilityGapResolver` search real installed/approved MCP and skill dependencies before suggesting code generation.

**Dependencies:** S42, S49-S50; existing gap resolver.

**Files/areas:** `rex/capabilities/recovery.py` adapters/providers, no-execution guarantee tests.

**Required behavior:**
- Search enabled local, disabled local, approved MCP/OpenClaw, usable skills/composition, configured external candidates before Forge proposal.
- Filter user/workspace/agent permission, trust, health, risk, configuration first.
- Resolver remains advisory/non-executing and cannot enable/install/grant.

**Tests:** finds MCP existing tool; finds skill composition; rejects untrusted/disabled/unhealthy/unauthorized; no false capability from descriptive verb; Forge last.

**DoD:** Rex proposes build work only after proving no safe existing capability path is available.

### S52 - Complete Forge/Ralph self-extension integration without duplicating US-116/117 (P1)

**Goal:** Connect agent capability-gap proposals to the already planned Forge build/security/promotion/rollback stories and existing Ralph development workflow.

**Dependencies:** existing production-readiness US-116/US-117 must be implemented/verified; S51.

**Files/areas:** integration adapter/proposal schema between gap resolver, Forge, and Ralph supervisor; capability registry promotion path; tests/docs.

**Required behavior:**
- Generated executable candidate starts inactive with manifest/schema/tests/requested authority/provenance/version digest.
- Build/test/static/security evaluation is constrained and secret-free.
- Existing Ralph worker/reviewer performs code implementation/review; no second developer agent system.
- Read-only low-risk promotion follows existing owner policy only after gates; privileged classes require explicit owner approval unless exact authority was pre-authorized.
- New permission request invalidates prior approval; rollback/revoke atomic/audited.

**Tests:** no-authority candidate; low-risk promotion; privileged approval; failed security gate; permission expansion; digest mismatch; rollback; revoked package.

**DoD:** a missing capability can enter the existing verified software-development pipeline but cannot self-install or self-authorize.

### S53 - Add Rex-approved MCP capability scaffold/template (P2)

**Goal:** Provide a simple standards-based path for developers/Forge to create a well-formed MCP capability package.

**Dependencies:** S40-S45; S52 if used by Forge.

**Files/areas:** CLI/scaffold module, templates containing server/schema/tests/security-policy/manifest/README, tests.

**Required behavior:**
- Generate protocol boilerplate, input/output schemas, tests, declared permissions/network/filesystem scope, version/provenance, security guidance.
- Prefer standard MCP SDK/protocol contracts over proprietary wrappers.
- Generated output is inactive until normal registration/verification/promotion.

**Tests:** scaffold determinism; invalid name/path; manifest validation; no embedded secrets; generated fixture server passes MCP client negotiation/tests.

**DoD:** `rex create-tool <name>` or equivalent generates a safe candidate skeleton, not an installed privileged tool.

---

## Batch 15 - MCP export, management contracts, and UI

### S54 - Expose selected Rex capabilities through an allowlisted MCP server (P1)

**Goal:** Let external MCP clients consume explicitly exported Rex capabilities without exposing Rex internals.

**Dependencies:** S43-S45; S38 for agent invocation export.

**Files/areas:** `rex/mcp/server/`, export policy/registry adapter, service-principal auth mapper, tests.

**Required behavior:**
- Default export set empty.
- Export canonical capability adapters only; inbound principal maps to Rex user/service identity and scopes.
- Every call rechecks permissions/risk/approval/action lifecycle/verification.
- No raw memory store, credentials, prompts, policy internals, arbitrary filesystem/shell, or arbitrary agents.
- Agent invocation export names explicit permitted agent/template and invocation scope.

**Tests:** empty default; allowlisted read; denied mutation; scoped service principal; forbidden memory/credential paths; agent invocation allow/deny; full audit correlation.

**DoD:** an external client can use a deliberately exported safe capability while all non-exported Rex internals remain unreachable.

### S55 - Add backend Agent/MCP management service with CLI/API/config parity (P1)

**Goal:** Establish one backend management contract before building the GUI.

**Dependencies:** S36-S54 as appropriate.

**Files/areas:** agent service facade, MCP management service, CLI/API adapters, validation/authorization tests; no business logic in UI adapters.

**Required behavior:**
- Agent CRUD/validate/approve/activate/pause/revoke/archive/clone.
- MCP add/remove/enable/disable/auth/reconnect/inspect/change-approve/assign.
- Same service supports CLI, API, config migration, and later Electron IPC.
- Management mutations require trusted user authority/confirmation as appropriate and are audited.

**Tests:** backend service first; CLI/API parity; unauthorized admin action; concurrent version conflict; validation error; audit redaction.

**DoD:** all routine Agent/MCP management is possible without a GUI and without editing raw JSON/YAML.

### S56 - Build Electron Agent Manager + MCP/skills management UI as a thin client (P2)

**Goal:** Provide a Hyperagent-style control surface without moving orchestration logic into the renderer.

**Dependencies:** S55; existing Electron settings/design contracts.

**Files/areas:** typed IPC handlers/preload interfaces, focused React pages/components, existing design tokens/navigation; backend services unchanged except necessary DTOs.

**Required behavior:**
- Create/edit/clone/pause/revoke/archive agents; template, model/budget, skill/capability/MCP assignments, memory/workspace, triggers, approvals, escalation, verification, delegation.
- MCP add/auth/reconnect/inspect tools/resources/prompts, health/provenance/version/risk, capability-change approval.
- No renderer-held credentials; confirmation in trusted main/backend path.
- UI cannot grant authority the backend service rejects.

**Tests:** TS typecheck/unit tests; IPC authorization; renderer cannot bypass main confirmation; Electron smoke; no fake success.

**DoD:** same AgentDefinition behaves identically whether created via CLI/API/config or UI.

### S57 - Evaluate A2A interoperability after internal delegation stabilizes (P2)

**Goal:** Decide, with an ADR and prototype only if justified, whether standardized A2A complements Rex's internal delegation for independent external agents.

**Dependencies:** S48; no dependency for internal Agent Runtime delivery.

**Files/areas:** architecture ADR, optional isolated adapter spike/tests if decision is positive.

**Decision criteria:** identity/auth mapping, delegation authority, provenance, task lifecycle mapping, cancellation, artifacts/context boundaries, version maturity, security surface, overlap with MCP.

**DoD:** documented accept/defer/reject decision. Internal Agent Runtime is not rewritten around A2A solely for protocol consistency.

### S58 - Production/adversarial evidence for Agent Runtime + MCP platform (P0 release gate for these capabilities)

**Goal:** Prove the platform preserves Rex truthfulness/security/isolation under realistic failures and packaged execution.

**Dependencies:** implemented scope S36-S57; existing release-evidence framework.

**Required evidence:**
- deterministic unit/integration tests for all agent/MCP/security/self-extension contracts;
- two-user/two-agent isolation; delegation lineage; revocation/cancellation races;
- MCP stdio/HTTP outage/recovery/auth expiry/schema drift/malicious-content tests;
- Forge promotion/rollback/permission-expansion evidence when Forge becomes live;
- packaged Windows Electron management/invocation proof for exposed features;
- live external MCP-provider evidence separately labeled where available;
- no prompts/transcripts/memory/secrets/raw private payloads in retained evidence.

**Validation:** full current CI/release gates plus dedicated agent/MCP adversarial profile.

**DoD:** release evidence distinguishes mock/local/live-provider/packaged/device/hardware claims, has zero waived Critical/High security findings for this surface, and docs advertise only verified implemented behavior.

---

## Sequencing summary

`S36 -> S37 -> S38` establishes authority/runtime. `S39 -> S45` provides secure first-class MCP. `S46 -> S48` adds least-privilege persistence/triggers/delegation. `S49 -> S53` separates skills and safely connects self-extension to existing Forge/Ralph. `S54 -> S56` exposes/operates the platform. `S57` is deliberately non-blocking interoperability evaluation. `S58` is the production evidence gate.

Cross-cutting rule: do not delay an earlier batch to build UI or speculative interoperability. Do not duplicate functionality merely to match these story names; if the worker proves an existing canonical component already satisfies a contract, adapt/test/document it and keep the simpler architecture.
