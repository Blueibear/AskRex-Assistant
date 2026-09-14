# ADR-AGENT-RUNTIME-MCP-001: Rex Agent Runtime and MCP Architecture

Status: Accepted
Date: 2026-09-14
Decision owner: AskRex architect/planner

## Context

AskRex already has canonical runtime, identity, permissions, model routing, capability metadata/retrieval, action verification, credential, memory, scheduling, OpenClaw, procedural-memory, and development-supervisor subsystems. New Agent Runtime, MCP, skills, delegation, and self-extension work must extend these systems without creating a second assistant stack or a second software-development supervisor.

## Decision

1. `TurnEngine` remains the canonical execution path for all user, scheduled, event-driven, API, MCP, and delegated agent work.
2. `AgentDefinition` is the canonical persistent representation of agent configuration and policy. Agent configuration may reduce authority but may never create authority.
3. Effective authority is the intersection of live principal authority, workspace/data authority, agent policy, capability policy, and current Rex security/risk/approval policy.
4. Agents do not own independent model clients, credential stores, memory engines, tool executors, permission systems, or verifiers. They provide request-local policy overlays to Rex Core.
5. `CapabilityRegistry` remains the canonical logical capability model. Native, OpenClaw, MCP, and future executable providers normalize into it while retaining provider-specific execution adapters.
6. MCP is an external capability/interoperability protocol, not Rex's permission system, identity system, credential authority, internal agent bus, or Ralph replacement.
7. MCP client support targets stdio and Streamable HTTP with negotiated protocol capabilities. Legacy HTTP+SSE is compatibility-only if a concrete server requires it.
8. MCP descriptions, schemas, prompts, resources, errors, and results are untrusted inputs. Local Rex policy may become stricter than remote metadata but never weaker because of it.
9. `ToolExecutionLifecycle` and independent verification remain authoritative for executable outcomes, including MCP and delegated work.
10. Skills migrate toward non-executable reusable procedures and capability requirements. Existing executable skill handlers remain compatible through an explicit adapter that treats the handler as an executable capability subject to normal policy.
11. Controlled self-extension reuses `CapabilityGapResolver`, Forge/self-maintenance controls, and the existing Ralph/supervisor development workflow. Generated code never self-authorizes.
12. Production-agent delegation uses Rex internal coordination/run contracts. Future A2A is optional interoperability and is not a prerequisite for internal delegation.
13. Ralph/supervisor remains the development workflow for implementation, review, retry, verification, and controlled self-extension. It is not the product Agent Runtime.
14. Agent Manager is a configuration and observability client over backend contracts. No critical orchestration authority lives only in UI code.

## Canonical AgentDefinition fields

The persistent definition contains stable ID, name, description, purpose, owner user, workspace/project scope, instructions, model/routing/effort policy, skill references, native/MCP capability bindings, memory policy/scope, trigger/schedule/event definitions, resource budgets, approvals, escalation, verification, delegation, retries/failure policy, notifications, audit policy, lifecycle state, version, provenance, and created/updated metadata.

No AgentDefinition contains raw credentials or grants authority not already available to the originating principal.

## Lifecycle

Canonical starting lifecycle:

`DRAFT -> VALIDATED -> APPROVED -> ACTIVE -> PAUSED`

`ACTIVE` or `PAUSED` may transition to `REVOKED`; historical records may transition to `ARCHIVED`. Reactivation after revocation requires a new approval decision. `DRAFT` and `VALIDATED` have no runtime authority. `PAUSED` blocks new triggers/delegations; in-flight operations retain canonical cancellation/action-truth semantics. `REVOKED` removes runtime authority and cancels revocable pending work without falsely claiming rollback for already-dispatched mutations.

## Security invariants

- Agent identity never substitutes for authenticated user/service identity.
- Agent, skill, MCP, plugin, trigger, model output, or delegation cannot widen authority.
- Capability installation never grants it to all agents automatically.
- Cross-user and cross-agent memory access is denied by default.
- Remote metadata cannot lower risk, permission, identity, approval, or verification requirements.
- Credentials never travel in prompts, delegation envelopes, skill definitions, capability metadata, or audit logs.
- Generated executable code is inactive until verified and promoted under current policy.
- Permission expansion on update is a new security decision.
- Scheduled/event triggers enter the same Agent Runtime and TurnEngine path rather than a separate model/tool pipeline.

## Consequences

Positive:
- Reuses mature Rex 2.0 foundations.
- Prevents duplicate registries and policy engines.
- Makes native and MCP tools interoperable at the logical capability layer.
- Preserves auditability and verification across autonomous agents.
- Lets UI, CLI, API, config, and authorized automation produce equivalent agent behavior.

Costs:
- Agent Runtime implementation depends on clean authority-intersection contracts rather than direct tool access.
- Current executable skill records require migration/compatibility handling.
- MCP transport/auth/security work must be first-class rather than delegated implicitly to OpenClaw.
- Self-extension cannot be considered complete until existing Forge promotion/rollback stories are implemented and verified.

## Rejected alternatives

- Replace Rex with OpenClaw/Hyperagent/Agent Zero: rejected because it duplicates or bypasses canonical Rex authority and verification.
- One independent assistant stack per agent: rejected because identity, permissions, memory, credentials, routing, and action truth would drift.
- Use MCP as the internal agent message bus: rejected because MCP capability interoperability and internal durable delegation have different authority/state requirements.
- Convert every mature native Rex capability to MCP: rejected as unnecessary architectural symmetry.
- Build a second self-programming loop: rejected; Forge + Ralph/supervisor is the canonical software-development path.

## Supersession rule

Future workers may refine implementation details but may not introduce a competing runtime, permission system, capability registry, self-extension supervisor, or UI-owned business logic without an explicit superseding ADR approved by the architect/planner.
