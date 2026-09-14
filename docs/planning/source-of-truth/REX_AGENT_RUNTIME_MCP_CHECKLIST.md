# REX Agent Runtime, MCP, Skills, and Self-Extension Checklist

Status: Source-of-truth companion to `REX_Unified_Build_Spec_UPDATED.md` and `REX_ACTIVE_CHECKLIST.md`
Effective: 2026-09-14 owner reprioritization
Canonical design: `docs/superpowers/specs/2026-09-14-agent-runtime-mcp-skills-design.md`
Implementation backlog: `docs/planning/AGENT_RUNTIME_MCP_INTEGRATED_BACKLOG.md`

## Operating rule

This is not a side project and does not authorize a parallel assistant stack. Every item below extends the existing Rex architecture and inherits all current production-readiness, privacy, security, testing, CI, coordination, and truthfulness gates.

The architect/planner defines contracts and stories. Product-code implementation flows through isolated Ralph workers/reviewers under the shared AskRex coordination protocol. The frozen live-test worktree remains test-only.

## Existing foundations to preserve

- [x] Canonical `TurnEngine` / `TurnContext` / turn events exist and are shared by supported interfaces.
- [x] Model routing exists and supports request-local route decisions/fallback evidence.
- [x] Canonical `CapabilityRegistry` exists with sealed local security metadata.
- [x] Capability retrieval filters permission, identity, enabled/configured state, health, and risk before ranking.
- [x] `CapabilityGapResolver` exists and already models native/OpenClaw/MCP/OpenAPI/composition/Forge search ordering without granting authority.
- [x] Canonical action lifecycle distinguishes planned/authorized/attempted/completed/verified/unverified/failed/cancelled.
- [x] Credential vault architecture exists; secrets are not supposed to live in prompts/tool metadata.
- [x] User/privacy/memory boundaries and private data roots exist.
- [x] Scheduler/automation/event infrastructure exists and should be adapted, not replaced.
- [x] Dynamic OpenClaw capability sync/reconnect exists as an optional provider.
- [x] Procedural memory requires verified action evidence and rechecks current permissions.
- [x] Basic `rex.skills` registry/loader/router exists, but currently conflates procedural skills and executable handlers.
- [ ] Forge build/promotion/rollback stories are fully implemented and verified. Do not assume planned Forge behavior is live until its existing PRD stories are green.

## A. Canonical AgentDefinition and lifecycle

- [ ] Define one versioned `AgentDefinition` contract; do not scatter agent config across scheduler, UI, model router, and tool settings.
- [ ] Include stable agent ID, name, purpose, owner user, workspace/project scope, instructions, model policy, skills, capability bindings, memory policy, triggers, budgets, approvals, escalation, verification, delegation, retries, notifications, audit policy, lifecycle, version, provenance, timestamps.
- [ ] Agent policy can narrow but never widen originating user/service-principal authority.
- [ ] Define fail-closed lifecycle: draft -> validated -> approved -> active -> paused/revoked -> archived, with explicit transition rules.
- [ ] Only active agents accept ordinary triggers/delegations.
- [ ] Privilege/version changes require revalidation; wider authority requires a new security decision.
- [ ] Agent persistence uses canonical data roots and user/workspace ownership rules.
- [ ] Tests cover schema/version migration, invalid references, lifecycle transitions, revocation, and ownership isolation.

## B. Agent Runtime execution

- [ ] Add one canonical Agent Runtime service on top of Rex core, not a second assistant.
- [ ] Interactive, scheduled, event, API, MCP, and delegated invocation all enter the same Agent Runtime and then `TurnEngine`.
- [ ] Extend trusted turn/run provenance with agent/run identifiers without allowing agent identity to replace user identity.
- [ ] Apply model, memory, capability, resource, approval, retry, escalation, and verification policies as request-local overlays.
- [ ] No agent may call an LLM, credential store, memory store, tool executor, or integration behind `TurnEngine`/canonical policy.
- [ ] Pause/revoke/cancellation obey existing mutation truth rules; cancellation is not rollback.
- [ ] Tests prove cross-agent isolation, cross-user isolation, cancellation, retries, resource limits, and verification behavior.

## C. Per-agent capability permissions

- [ ] Installation/registration of a capability does not grant it to any agent automatically.
- [ ] Support explicit server/provider-level and capability/tool/operation-level bindings.
- [ ] Support `allow`, `deny`, and `require_approval`; deny wins.
- [ ] Effective authority is the intersection of principal, workspace, agent, capability, risk/approval, health, and trust policy.
- [ ] Agent bindings reference canonical capability IDs; do not create one tool registry per agent.
- [ ] Remote descriptions/schemas/results can never lower Rex's local risk, permission, identity, or verification requirements.

## D. Agent memory/workspace isolation

- [ ] Agent private state is partitioned by owning user + stable agent ID.
- [ ] Workspace-shared data requires explicit user/workspace permission.
- [ ] Cross-agent memory access is denied by default.
- [ ] Delegation transfers explicit bounded context references, not another agent's memory namespace.
- [ ] Agent instructions/memory content are untrusted for authority purposes.
- [ ] Tests cover same-name agents, user isolation, workspace isolation, expired references, and revocation.

## E. Agent triggers and automation

- [ ] Support normalized triggers for schedule, webhook, incoming email, file/repo event, system event, Home Assistant event, monitoring condition, manual request, and authorized agent delegation.
- [ ] Every trigger authenticates/validates source, resolves user/workspace, derives idempotency/correlation identity, and invokes Agent Runtime.
- [ ] No trigger adapter may call the model or tools directly.
- [ ] Reuse existing scheduler/automation/event infrastructure where practical.
- [ ] Define duplicate-delivery, retry, timeout, cancellation, pause, and revocation behavior.

## F. Delegation and agent coordination

- [ ] Define a bounded `DelegationEnvelope` preserving root run, parent run, origin user, source agent, target agent, objective, allowed context refs, budget/deadline, and correlation ID.
- [ ] Target authority is an intersection; delegation can never transfer broader permissions.
- [ ] Credentials are never placed in delegation messages.
- [ ] Memory is never shared implicitly.
- [ ] Add maximum depth, concurrency/budget limits, and cycle detection.
- [ ] Trace all actions/verification back to the originating run/user.
- [ ] Reuse existing Rex task/event/supervisor concepts; do not build a second software-development supervisor.
- [ ] Ralph remains the code-build/review verifier for self-extension; production Agent Runtime remains product runtime.
- [ ] Evaluate A2A only after internal delegation contracts are stable.

## G. MCP client foundation

- [ ] Add first-class MCP support independent of OpenClaw.
- [ ] Support standard stdio transport for local subprocess servers.
- [ ] Support standard Streamable HTTP transport for remote/independent servers.
- [ ] Treat older HTTP+SSE as compatibility-only and add it only for a verified need.
- [ ] Perform MCP initialization/version/capability negotiation and persist the negotiated protocol/server metadata.
- [ ] Support tools list/call, resources list/read, and prompts list/get in the first stable slice.
- [ ] Support list-change/resource subscription/logging only when negotiated and useful.
- [ ] Keep roots, sampling, elicitation, and experimental durable tasks opt-in behind explicit policy; do not expose them automatically.
- [ ] MCP tasks must not replace Rex AgentRun, TurnEngine, or approval/verification state.

## H. MCP registry normalization and change control

- [ ] Persist versioned `McpServerDefinition` records without raw secrets.
- [ ] Track transport, owner/workspace, trust/provenance, auth reference, version constraints, health, assigned agents, negotiated metadata, and capability snapshot digest.
- [ ] Normalize executable MCP tools into the canonical `CapabilityRegistry` under stable namespaced IDs.
- [ ] Keep MCP resources/prompts in protocol-specific catalogs linked to the server; existence alone grants no execution authority.
- [ ] Validate metadata/schema before registration.
- [ ] Capability removal disables stale executable entries.
- [ ] Material schema/risk/permission/auth changes become pending review rather than silently widening access.
- [ ] Preserve provenance/version/audit history across updates and rollback.

## I. MCP security and execution

- [ ] Treat all MCP descriptions, schemas, prompts, resources, errors, and outputs as untrusted input unless explicitly trusted.
- [ ] Add strict JSON-RPC/schema validation, bounded payload sizes, timeouts, cancellation, retry and rate/resource controls.
- [ ] Validate arguments against negotiated tool schema before execution.
- [ ] Recheck user/workspace/agent/capability/risk/approval policy at call time.
- [ ] Run MCP mutations through canonical action lifecycle and independent verification where practical.
- [ ] Local stdio servers receive a minimal explicit environment, not all Rex/user process secrets.
- [ ] Filesystem and network scope default closed and are explicitly granted.
- [ ] Add prompt-injection, tool-poisoning, malicious-schema/resource, capability-substitution, cross-user, and cross-agent adversarial tests.
- [ ] An MCP response can never change Rex security policy or self-promote action success.

## J. MCP authentication and credential handling

- [ ] Integrate all MCP credentials with the existing credential vault.
- [ ] Agent/server config stores credential references, never tokens/passwords.
- [ ] Support scoped token/bearer auth as required by configured servers.
- [ ] Implement the current MCP HTTP OAuth profile including protected-resource metadata and authorization-server discovery.
- [ ] Enforce resource/audience binding and prohibit token passthrough to unrelated upstream APIs.
- [ ] Support refresh, revocation, reconnect, and auth-required health states.
- [ ] Scope stored auth material by user + MCP server + authorization server/workspace as required.
- [ ] Redact auth data from logs/audits/model context.

## K. MCP server/export support

- [ ] Default exported Rex MCP capability set is empty.
- [ ] Export only explicit canonical capability adapters with an allowlist/export policy.
- [ ] Map inbound authenticated principal to Rex identity/scopes and re-run canonical authorization for every call.
- [ ] Do not export raw memory stores, credentials, system prompts, policy internals, unrestricted filesystem/shell, or arbitrary agents.
- [ ] Explicit agent invocation exports name the permitted agent/template and invocation scope.
- [ ] Preserve external request -> Agent/Turn/Action/Audit correlation.
- [ ] Non-loopback service exposure requires explicit configuration and appropriate auth/network protections.

## L. Skills and plugin architecture

- [ ] Define versioned non-executable `SkillDefinition` for procedural knowledge, strategy, workflow, and capability dependencies.
- [ ] Skills may require abstract capability interfaces such as `email.read` where compatibility permits.
- [ ] `CapabilityRetriever` resolves abstract needs only after permission/health/risk filtering.
- [ ] Preserve current handler-based skill records through a compatibility adapter, but classify executable handlers as capabilities/plugins rather than procedural skills.
- [ ] Track skill owner/workspace, provenance/trust, version, status, dependencies, and compatibility.
- [ ] Skill content cannot grant permissions or embed raw credentials.
- [ ] Test versioning, dependency resolution, disabled capabilities, provider substitution, and legacy migration.

## M. Controlled self-extension

- [ ] CapabilityGapResolver searches approved installed native/MCP/OpenClaw/skill/composition options before proposing code generation.
- [ ] Non-executable generated skill candidates are versioned and validated before use.
- [ ] Executable generation produces an inactive candidate with manifest, schemas, requested authority, provenance, tests, and documentation.
- [ ] Reuse/complete the existing Forge stories for constrained build/test/security analysis; do not create another builder.
- [ ] Use the existing Ralph/supervisor workflow for code implementation/review/verification.
- [ ] Low-risk read-only candidates may be eligible for policy-controlled promotion only after all automated gates pass.
- [ ] Filesystem/host mutation, shell, network write, messaging, purchases/refunds, secrets, infrastructure/security/account/device control require explicit owner approval unless an existing owner policy authorizes that exact authority class.
- [ ] Promotion/approval is bound to a version/content digest and declared authority.
- [ ] Permission expansion on update requires new approval.
- [ ] Rollback/revocation remains available and auditable.

## N. Capability-development template

- [ ] Provide a simple Rex-approved scaffold/workflow for a new standard MCP capability/tool package.
- [ ] Generated package includes protocol boilerplate, schemas, tests, security policy/permissions, manifest, version/provenance, and README.
- [ ] Prefer standard MCP SDK/contracts over proprietary wrappers.
- [ ] Scaffold output remains inactive until normal verification/promotion.

## O. Resource policy, escalation, and verification

- [ ] Agent policy can cap allowed providers/models, local/cloud preference, cloud spend, tokens, wall time, tool calls, retries, concurrency, and browser/session use.
- [ ] Cost policy never overrides security/privacy/capability requirements.
- [ ] Escalate on insufficient authority, conflicting/low-confidence data, missing capability, higher risk, required approval, ambiguous policy, repeated verification failure, exhausted budget, or required human judgment.
- [ ] Escalation states what happened, what was attempted, relevant evidence/status, why it cannot safely continue, and the minimum next action.
- [ ] Tool call success is never equated with goal completion.

## P. Observability and auditing

- [ ] Add canonical AgentRun/run-ledger projection correlated to Turn/Action identifiers.
- [ ] Track originating user/source, requesting/delegating/target agent, root/parent chain, selected model/provider, bounded resource usage, skills/capabilities, MCP server/version/schema snapshot, decision labels, action/verification state, retries/escalation/cancellation, terminal status/duration.
- [ ] Redact credentials, raw prompts, private memory, and unnecessary raw tool payloads.
- [ ] Support tracing one task through delegated agents and MCP/native/OpenClaw actions.
- [ ] Health distinguishes configured, connected, authenticated, compatible, healthy, degraded, stale, and unavailable states truthfully.

## Q. Templates and management surfaces

- [ ] Agent templates only provide defaults and instantiate ordinary `AgentDefinition` records.
- [ ] No hard-coded runtime special case for Nasteeshirts, research, development, monitoring, personal, marketing, maintenance, security, or content templates.
- [ ] Backend contracts support create/edit/validate/approve/activate/pause/revoke/archive before UI work.
- [ ] CLI/API/config paths use the same backend service and validation rules.
- [ ] Future Electron Agent Manager is a configuration/status client, not an orchestration engine.
- [ ] MCP manager supports add/remove/enable/disable/auth/reconnect/inspect/approve/assign/health/log/provenance/version/risk.
- [ ] Routine management requires no manual YAML/JSON editing, while advanced config remains possible.

## R. Production evidence and quality gates

- [ ] Unit tests cover AgentDefinition, lifecycle, policy intersection, persistence, model/resource policy, memory isolation, triggers, escalation, delegation, verification, and limits.
- [ ] MCP tests cover connection/disconnection, negotiation, tools/resources/prompts, malformed protocol/schema/output, timeout/cancel, auth/OAuth expiry, permission denial/approval, malicious metadata/resources, isolation, registration/update/recovery.
- [ ] Self-extension tests cover gap detection, candidate generation, sandbox, verification, trust levels, approvals, registration, versioning, rollback, and permission expansion.
- [ ] Existing Ruff/Black/mypy/pytest/security/working-tree/packaging/CI gates remain intact.
- [ ] Evidence labels deterministic/mock, local runtime, live-provider, packaged artifact, mobile/device, and physical-hardware proof separately.
- [ ] Documentation reflects implemented state; planned features are never advertised as live.
