# AskRex Agent Runtime, MCP, Skills, and Controlled Self-Extension Design

Status: Approved architecture / implementation to flow through the existing Ralph supervisor workflow
Date: 2026-09-14
Owner: AskRex architect/planner
Applies to: backend/desktop runtime first; mobile remains a thin authenticated client of the canonical AskRex runtime

## 1. Objective

Evolve AskRex from a single-assistant runtime with individually integrated tools into an extensible, general-purpose agent platform without replacing the canonical Rex core.

The core invariant is:

```text
Trigger / user / event
        |
   Agent policy
        |
   TurnEngine
        |
+-------+---------------------------+
|       |                           |
Memory  ModelRouter          Capabilities / Tools
                                |
                          Native / MCP / OpenClaw
                                |
                         Action lifecycle
                                |
                    Verification / audit
```

An agent is a persistent configuration and policy overlay on Rex. It is not a separately implemented assistant and may not own a parallel model client, memory engine, credential store, permission system, tool executor, verifier, or audit pipeline.

MCP is a standardized capability protocol. It is not the agent runtime. Skills are reusable procedures/knowledge. They are not the agent runtime. The Agent Runtime coordinates skills and capabilities through the existing Rex core.

## 2. Existing foundations to reuse

The current repository already provides important canonical pieces. New work must extend them instead of duplicating them:

| Concern | Existing canonical foundation | Agent-platform use |
|---|---|---|
| Turn execution | `rex.runtime`, `TurnEngine`, `TurnContext`, cancellation/status events | Every interactive, scheduled, event-driven, delegated, API, or MCP-invoked agent turn enters here |
| Model routing | `rex.model_router` | Agent policy supplies a request-local routing/budget overlay; no global mutable model switch |
| Capability metadata | `rex.capabilities.registry` | Native, MCP, OpenClaw, and future executable providers normalize into this logical capability model |
| Capability selection | `rex.capabilities.retrieval` | Resolve purpose to permitted, healthy capabilities after policy filtering |
| Gap recovery | `rex.capabilities.recovery.CapabilityGapResolver` | Search installed/approved capabilities before proposing composition or Forge work |
| Executable truth | `rex.actions.lifecycle` and action dispatch/verification | MCP/native/OpenClaw mutations cannot self-declare success |
| OpenClaw | `rex.openclaw.capability_sync`, reconnect/tool adapters | Remains an optional capability provider, not the agent core |
| Credentials | `rex.credential_vault` / credential services | MCP and agent configuration store references/scopes, never raw secrets |
| Memory/privacy | current user-scoped memory, privacy/context rules, procedural memory | Agent scopes are filters/namespaces inside existing user/workspace authority |
| Scheduling/automation | `rex.scheduler`, automation registry and existing event mechanisms | Trigger adapters invoke the same Agent Runtime; no separate scheduler brain |
| Skills | `rex.skills` registry/loader/router | Migrate toward non-executable procedural SkillDefinition; legacy handler skills become compatibility capabilities |
| Self-extension | CapabilityGapResolver plus planned Forge stories | Forge/Ralph perform bounded build/test/verification; generation never grants authority |
| Development verification | shared AskRex supervisor/Ralph workflow | Used for implementing and verifying new executable capability code; not a second production agent runtime |

Important current gaps:

- There is no canonical persistent `AgentDefinition` or Agent Runtime.
- There is no first-class MCP client/server transport implementation. Current MCP references are only recovery candidates.
- `rex.skills` currently mixes procedural skill metadata with executable handler paths and needs a compatibility migration.
- Forge is planned but not yet the authoritative implemented self-extension pipeline.
- Agent-to-agent delegation, run provenance, and per-agent capability policy do not yet exist as first-class runtime contracts.

## 3. Architectural boundaries

### 3.1 Authority hierarchy

Rex core remains authoritative. Effective authority for an agent invocation is the intersection of:

1. authenticated originating user/service-principal authority;
2. current workspace/project scope;
3. the agent's explicit policy and capability grants;
4. the capability's local risk/permission policy;
5. current approval/confirmation state;
6. current health/trust/provenance policy.

An agent policy may make authority narrower. It may never widen the originating principal's authority.

Remote MCP/OpenClaw metadata, skill text, retrieved resources, tool descriptions, tool output, or another agent's request may never modify this hierarchy merely by containing instructions.

### 3.2 Runtime versus development supervisor

The product Agent Runtime owns persistent production agents and delegation. The existing Ralph/development supervisor remains the software-development control plane for implementing, reviewing, testing, and promoting generated or human-requested code changes.

Do not create a second development supervisor. When Agent Runtime self-extension determines that new executable code is required, it hands a bounded development proposal to the existing Forge/Ralph workflow. The resulting candidate returns through verification and promotion gates before it can become a runtime capability.

### 3.3 MCP versus internal coordination versus A2A

Use each protocol for the job it is designed to do:

- **MCP:** tools, resources, prompts, capability discovery, and selected client/server interoperability.
- **Internal Rex coordination:** trusted production-agent delegation, schedules/events, run state, policy, provenance, verification, and existing supervisor/task integration.
- **A2A or a future equivalent:** optional interoperability with independent external agent systems after the internal Agent Runtime is stable.

Do not delay the Agent Runtime waiting for A2A. Do not force MCP to become Rex's internal agent message bus.

## 4. Canonical AgentDefinition

Create one persistent, versioned representation for an agent. The initial contract should contain:

```text
id
name
description
purpose
owner_user_id
workspace_scope
instructions
model_policy
skill_refs
capability_bindings
memory_policy
trigger_definitions
resource_policy
approval_policy
escalation_policy
verification_policy
delegation_policy
retry_policy
notification_policy
audit_policy
lifecycle_status
version
provenance
created_at / updated_at
```

Semantics:

- `owner_user_id` is an authority boundary, not display metadata.
- `workspace_scope` identifies the project/household/business namespace the agent may operate within.
- `instructions` supplement Rex's canonical security/system instructions and cannot override them.
- `model_policy` describes allowed providers/models, local/cloud preference, effort where supported, fallback limits, and budgets. It is request-local.
- `skill_refs` refer to versioned procedural skills.
- `capability_bindings` refer to canonical capability IDs, optional operation/tool IDs, and agent-specific effects (`allow`, `deny`, `require_approval`). Deny wins.
- `memory_policy` specifies readable/writable memory namespaces and retention. No implicit full-user-memory access.
- triggers never execute directly; they invoke `AgentRuntime`.
- resource policy can cap cloud spend, tokens, wall time, tool calls, retries, browser sessions, and concurrency, but never relaxes security.
- delegation policy constrains target agents, maximum depth, permitted context transfer, and budget transfer.
- audit policy controls additional logging, never removal of mandatory security/action audit evidence.

### 4.1 Agent lifecycle

Use an explicit fail-closed lifecycle:

```text
DRAFT -> VALIDATED -> APPROVED -> ACTIVE -> PAUSED
                         |           |        |
                         +-----------+--------+-> REVOKED -> ARCHIVED
```

Rules:

- Only `ACTIVE` agents accept ordinary triggers.
- `DRAFT` and `VALIDATED` agents have no runtime authority.
- Activation requires schema validation, referenced capability/skill resolution, policy validation, and required approval.
- `PAUSED` disables new triggers and delegated work; in-flight work follows cancellation/action-truth rules and may not be falsely reported as rolled back.
- `REVOKED` removes runtime authority and cancels revocable pending work.
- Reactivation after revocation requires a new approval decision; privilege/version changes must be revalidated.
- `ARCHIVED` is historical and non-runnable.

## 5. AgentRuntime execution contract

A single runtime service should expose operations conceptually equivalent to:

```python
invoke(agent_id, invocation) -> AgentRun
pause(agent_id)
resume(agent_id)
revoke(agent_id)
delegate(parent_run, target_agent_id, request) -> AgentRun
```

`invoke()` must:

1. load and validate the active `AgentDefinition`;
2. bind the authenticated principal and trusted invocation origin;
3. calculate effective user/workspace/agent authority;
4. create a correlated agent-run/provenance record;
5. create/extend canonical `TurnContext` without changing user identity authority;
6. apply request-local model/memory/capability/resource policy;
7. enter `TurnEngine`;
8. route all executable actions through canonical capability/action policy and verification;
9. apply retry/escalation rules only where policy permits;
10. record a sanitized terminal run result.

Scheduled, event-driven, delegated, API, MCP, and interactive invocations all use this same path.

## 6. Per-agent capability permissions

MCP/native/OpenClaw access is never inherited simply because a capability is installed.

Support at least:

- provider/server-level enablement;
- capability/tool/operation-level binding;
- `allow`, `deny`, `require_approval` effects;
- risk ceiling and optional read-only restrictions;
- workspace/user constraints.

Effective decision order is fail-closed: explicit deny, principal permission, agent binding, capability local policy, risk/approval requirement, health/trust state.

The canonical Capability Registry remains the source of executable capability identity. Do not build separate registries for each agent or protocol.

## 7. Memory and workspace isolation

Agent state must be persistent but scoped.

- Store private agent state beneath the owning user's canonical data root, partitioned by stable agent ID.
- A workspace-scoped agent may read shared workspace data only when both user policy and workspace policy permit it.
- Cross-agent reads are denied by default. Delegation transfers explicit references or bounded context, not another agent's memory namespace.
- Agent instructions and memories are untrusted content for policy purposes and cannot grant capabilities.
- User identity is never inferred from an agent identity.

## 8. Delegation and provenance

Define a `DelegationEnvelope` carrying only bounded, auditable data:

```text
root_run_id
parent_run_id
originating_user_id
source_agent_id
target_agent_id
objective
approved_context_refs
requested_capability_classes
deadline/resource_budget
correlation_id
```

Delegation rules:

- Target authority is the intersection of originating-user authority, delegated scope, target-agent policy, and current capability policy.
- Credentials are never copied into the envelope.
- Memory is never shared implicitly.
- Delegation has depth/concurrency/budget limits and cycle detection.
- Every delegated action links back to the root run and parent run.
- A target agent may refuse/escalate when its policy or available evidence is insufficient.

## 9. MCP client architecture

Implement MCP as a first-class external capability provider, preferably under a focused `rex/mcp/` package. Do not route it through OpenClaw unless the user explicitly configures OpenClaw as the provider for that server.

### 9.1 Protocol baseline

Target the current stable MCP specification through negotiated initialization rather than hard-coding feature assumptions. Standard transports are:

- stdio for local subprocess servers;
- Streamable HTTP for remote/independent servers.

The older HTTP+SSE transport is legacy. Add a compatibility adapter only if a real configured server requires it.

Persist negotiated protocol version, server implementation metadata, capabilities, session information, and schema/version hashes in connection state.

### 9.2 Initial supported server features

Phase the client so the first stable release supports:

1. tools list/call;
2. resources list/read, plus list-changed/subscribe only when both sides support it;
3. prompts list/get;
4. structured logging and capability/list-change notifications where useful.

MCP client features with broader authority should be explicit later capabilities:

- roots;
- sampling (especially tool-enabled sampling);
- elicitation;
- experimental durable tasks.

Do not automatically expose these merely because a server requests them. MCP tasks are experimental in the current stable specification and must not replace Rex's canonical AgentRun/delegation state machine.

### 9.3 MCP connection model

Define persistent `McpServerDefinition` records containing:

```text
server_id / display_name
transport configuration
command/args OR endpoint
auth reference
owner user/workspace
enabled state
trust/provenance
protocol/version constraints
requested local roots/network/filesystem boundaries
assigned agents
health state
last negotiated protocol/server metadata
capability snapshot digest
created/updated metadata
```

Raw secrets are not stored in this definition.

### 9.4 Discovery and normalization

On successful initialization, discover and schema-validate supported server metadata. Normalize executable tools into the existing `CapabilityRegistry` with stable IDs such as `mcp:<server-id>:tool:<tool-name>`.

Resources and prompts should have protocol-specific catalogs linked to the server and may be surfaced as context providers, but they do not become executable authority merely by existing.

Local Rex security metadata is authoritative. Remote metadata may increase caution but may never lower local risk, remove permissions, disable verification, or grant agent access.

When a server's capability snapshot changes:

- compute schema/permission/risk diffs;
- keep removed capabilities disabled;
- treat material privilege expansion or incompatible schema change as pending review;
- never silently inherit broader requested access.

## 10. MCP execution and security

All MCP content is untrusted by default, including tool descriptions, schemas, prompts, resources, errors, and results.

Required controls:

- strict JSON-RPC/schema validation and bounded payload sizes;
- argument validation against the negotiated tool schema;
- local permission/risk/approval checks before `tools/call`;
- cancellation/deadline propagation from the current turn;
- connection and call timeouts, bounded retries, and rate/resource limits;
- no implicit filesystem/network roots;
- sanitized logging and audit;
- output validation where a structured schema exists;
- independent post-action verification for mutations when Rex can verify the external state;
- prompt-injection/tool-poisoning adversarial tests;
- server identity/provenance and capability-snapshot tracking;
- no server result can alter system/security instructions or self-promote its action status.

For local stdio servers, launch with a minimal environment assembled from explicit config plus narrowly scoped credentials. Never inherit the full Rex/user process environment by default.

For local Streamable HTTP servers, bind loopback by default. Any listener Rex exposes must validate origin where applicable, authenticate appropriately, and require explicit non-loopback configuration.

## 11. MCP authentication and credentials

Integrate with the existing credential vault.

### stdio

Credentials are injected only into the launched server's explicitly declared environment variables and remain out of model context/config/logs.

### HTTP

Support bearer/token auth and the current MCP OAuth authorization profile. The implementation must support protected-resource metadata discovery, authorization-server discovery, audience/resource binding, refresh/revocation, and scoped credential storage. Never pass an MCP access token through to a downstream service as if it were that service's token.

Store tokens and refresh material per owning user + MCP server + authorization server. Agent definitions reference the server/capability; they do not contain tokens.

## 12. MCP server/export architecture

Rex may expose selected capabilities through an MCP server, but only through an explicit export policy.

- Export canonical capability adapters, not internal Python objects.
- Map each inbound authenticated service/user principal to Rex identity and scopes.
- Re-run canonical permission, risk, approval, action-lifecycle, and verification checks on every call.
- Default export set is empty.
- Never export raw memory stores, credential APIs, system prompts, policy internals, arbitrary filesystem access, arbitrary agent access, or unrestricted shell execution.
- Agent invocation can be exported only as an explicit capability naming an allowed agent/template and allowed invocation scope.
- Preserve action/run provenance from external MCP request to canonical audit record.

## 13. Skills and plugins

### 13.1 SkillDefinition

A skill is reusable procedural knowledge, not executable integration authority. Evolve `rex.skills` toward a versioned `SkillDefinition` with fields such as:

```text
id / name / description / version
instructions or procedure
required_capability_interfaces
optional_capability_interfaces
input/output expectations
owner/workspace scope
provenance/trust
status
```

A skill may say "read the support email, find the order, compare tracking, draft a reply" while capabilities perform `email.read`, `order.find`, and `email.reply`.

### 13.2 Legacy executable skills

Current handler-path skills are executable plugins in practice. Preserve compatibility through an adapter, but do not perpetuate the conflation. Normalize their executable handler into a canonical capability with explicit permissions/risk/provenance, and let a procedural skill reference that capability if needed.

### 13.3 Capability interfaces

Allow skills and agents to depend on abstract capability purposes such as `email.read` or `order.lookup` when practical. `CapabilityRetriever` resolves those to healthy, permitted concrete implementations. Provider-specific requirements remain possible where behavior is not interchangeable.

## 14. Controlled self-extension

Reuse the existing gap-recovery and planned Forge architecture:

```text
Capability gap
  -> enabled native/registered capabilities
  -> approved installed MCP/OpenClaw capabilities
  -> approved reusable skills/compositions
  -> configured external capability candidates
  -> no safe match
  -> bounded development proposal
  -> Forge/Ralph candidate implementation + tests
  -> static/security analysis
  -> sandboxed execution/evaluation
  -> supervisor verification
  -> human approval when policy requires
  -> versioned registration/promotion
  -> explicit agent assignments
```

Trust tiers:

1. non-executable knowledge/procedural skill candidate;
2. generated executable candidate with zero runtime authority;
3. verified sandboxed/read-only candidate eligible for policy-controlled promotion;
4. privileged capability requiring explicit owner approval unless a pre-existing owner policy authorizes that exact authority class.

File/host mutation, shell, network write, messaging, purchases/refunds, secrets, infrastructure, security configuration, account management, and physical-device control are privileged.

A promotion approval applies to a specific content/version digest and requested authority. A new version requesting broader permissions is a new security decision.

## 15. Triggers and automation

Support trigger definitions for schedule, webhook, incoming email, file/repo event, system event, Home Assistant event, monitoring condition, manual invocation, and authorized-agent delegation.

Each trigger adapter:

1. authenticates/validates its source;
2. resolves an active agent and owning user/workspace;
3. generates an idempotency/correlation key;
4. calls `AgentRuntime.invoke()`;
5. never calls a model/tool directly.

Use existing scheduler/automation infrastructure wherever possible. Trigger failure, duplicate delivery, retry, and cancellation semantics must be explicit and auditable.

## 16. Budgets, retries, escalation, and verification

Agent resource policy may include maximum cloud spend, tokens, wall time, tool calls, browser sessions, retry attempts, and concurrency. Security and privacy policy always outrank cost policy.

Escalate when authority is insufficient, evidence conflicts, confidence is below policy, a required capability is unavailable, risk requires approval, verification repeatedly fails, a resource limit is reached, or a user judgment is required.

Escalation payloads should contain what was attempted, evidence/status, why the agent cannot safely continue, and the minimum next user decision. They must not contain credentials or unnecessary private content.

Tool-call success is not goal completion. Mutation outcomes continue to use Rex's canonical action lifecycle and independent verification.

## 17. Observability and audit

Add an `AgentRun`/run-ledger projection correlated with existing Turn/Action identifiers. Track, with redaction:

- timestamp and stable run/correlation IDs;
- originating user and trusted source;
- requesting/delegating/target agent IDs;
- root/parent delegation chain;
- selected model/provider and bounded resource usage;
- selected skills and capabilities;
- MCP server/capability/version/schema snapshot identifiers;
- permission/approval decision labels;
- action lifecycle and verification result;
- retry/escalation/cancellation outcome;
- terminal status/duration.

Do not retain raw prompts, secrets, private memory content, or unrestricted tool payloads merely for observability.

## 18. Agent templates

Templates provide defaults only. Instantiating a template creates an ordinary `AgentDefinition` that then validates and follows the same lifecycle.

Initial useful templates may include research, customer support, development, monitoring, personal assistant, marketing, maintenance, security, and content. No template receives special runtime code or implicit authority.

## 19. Agent Manager and MCP management UI

The future Electron UI is a client of backend contracts, never the orchestration owner.

Agent Manager should support:

- create/edit/clone/pause/revoke/archive agents;
- choose template and model/resource policy;
- assign skills and capabilities;
- assign MCP servers/tools with granular permission effects;
- configure memory/workspace scope, triggers, budgets, approvals, escalation, verification, and delegation;
- show validation state, health, run history, provenance, versions, and audit summaries.

MCP management should support add/remove/enable/disable/authenticate/reconnect, inspect negotiated server features/tools/resources/prompts, approve capability changes, assign to agents, and view health/provenance/version/risk.

The same backend contracts must be usable by config migration, CLI, API, and authorized automation.

## 20. Security invariants

The implementation is unacceptable if any of these become possible:

- agent identity substitutes for user identity;
- one agent reads another user's or agent's private memory without an explicit authorized scope;
- installing an MCP server grants it to all agents;
- remote metadata lowers local risk/permission requirements;
- MCP resources/prompts/tool text can rewrite core policy;
- stdio child processes inherit all host secrets by default;
- OAuth tokens are exposed to model context or passed through to unrelated upstream APIs;
- generated code activates itself;
- an update silently broadens permissions;
- an MCP/native/OpenClaw mutation bypasses action lifecycle/verification;
- a scheduled/event trigger enters a separate model/tool pipeline;
- a delegated agent receives broader authority than both the origin user and target policy permit;
- the Agent Manager UI contains business/orchestration logic unavailable through backend contracts.

## 21. Current MCP/A2A standards decision

For MCP implementation, follow negotiated current stable protocol semantics rather than freezing a custom subset. As of this design, the stable MCP specification defines stdio and Streamable HTTP as standard transports; Streamable HTTP replaced the older HTTP+SSE transport. HTTP authorization follows the MCP OAuth profile, including protected-resource metadata and resource/audience binding. Features are negotiated during initialization.

MCP 2025-11-25 adds experimental durable tasks and richer sampling/tool capabilities. Rex must treat those as optional negotiated features and must not replace `AgentRun`, TurnEngine, approval, or policy with them.

A2A is suitable for later interoperability with independent external agents. It is not a prerequisite for internal Rex delegation and must not delay the Agent Runtime.

## 22. Delivery sequence

Use these dependency-ordered phases, adjusting only when the actual tree proves an existing component already satisfies the contract:

1. **Foundations:** AgentDefinition/lifecycle/run ledger, policy intersection, persistence, architecture tests.
2. **MCP core:** stdio + Streamable HTTP connection/session/version negotiation, discovery, registry normalization, health.
3. **MCP security/auth/execution:** credential vault/OAuth, permission gates, action lifecycle, adversarial tests.
4. **Persistent Agent Runtime:** TurnEngine invocation, model/memory/capability/resource overlays, triggers/schedules/escalation.
5. **Skills migration:** procedural SkillDefinition, legacy executable-skill adapter, abstract capability dependencies.
6. **Controlled self-extension:** integrate gap resolver with installed MCP/skills and complete Forge/Ralph build/promotion/rollback.
7. **Delegation:** permission-safe agent-to-agent delegation and provenance; then evaluate A2A adapter.
8. **MCP server/export:** explicit allowlisted Rex capability exposure.
9. **Management surfaces:** backend CLI/API/config parity, then Agent/MCP Manager UI.
10. **Production evidence:** security/adversarial suite, regression suite, packaged-runtime proof, docs, audit/observability proof.

## 23. Explicit non-goals

Do not replace Rex with Hyperagent, Agent Zero, OpenClaw, or another agent framework. Do not create a per-agent assistant stack. Do not rewrite mature native tools solely to make them MCP tools. Do not make OpenClaw mandatory. Do not implement A2A before the internal contracts are stable. Do not expose every MCP client feature in the first increment. Do not add a proprietary protocol layer where MCP already supplies the needed interoperability.

## 24. Acceptance at architecture level

This design is integrated only when the authoritative roadmap/checklist/backlog and Ralph implementation plan reference it, and implementation work preserves all existing quality gates. Product code changes must be performed by isolated Ralph workers/reviewers under the shared coordination protocol, not directly in the architect/planner worktree.
