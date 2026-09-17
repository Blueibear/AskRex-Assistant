# Rex Continuity Architecture Design

Date: 2026-09-17
Status: Approved architectural direction; documentation/roadmap integration in progress.

## Problem

Rex is intended to accumulate years of memory, procedures, relationships, agent state, projects, preferences, opinions, and operational experience. The underlying reasoning models, embedding models, providers, storage/index implementations, hardware, and runtime architecture will change over that lifetime.

Without an explicit continuity contract, a technically successful upgrade could preserve files while effectively replacing Rex: relationships could be reinterpreted, learned preferences could broaden, model-specific state could disappear, embeddings could become unrecoverable, or a new model could violate existing authority boundaries.

The design must also allow Rex to develop a recognizable personality and evidence-grounded opinions without allowing those opinions to create authority or retaliation risk.

## Existing foundations to reuse

The current repo already provides important pieces: `TurnEngine`; request-local ModelRouter routing; fail-closed identity/permissions; CredentialManager/DPAPI vault; `rex.memory` per-user working/long-term stores; guarded verified-outcome-only `rex.procedural_memory`; CapabilityRegistry/ToolRegistry; canonical action lifecycle and independent verification; scheduling/agent/self-maintenance direction; provider routing evaluation; audit/observability; and Windows disk diagnostics.

These are foundations, not a complete continuity system.
## Considered structures

1. Put all requirements inside the existing Memory Layer. Rejected because model replacement, identity continuity, agent state, resource maintenance, and authority are cross-cutting concerns.
2. Use two independent memory/model contracts. Better, but it leaves relational/identity continuity and developed personality without a clear home.
3. **Chosen:** a lightweight Rex Continuity Architecture containing three explicit contracts: persistent memory/experiential learning/resource self-maintenance; model independence/replacement; and identity/continuity/conscience.

This keeps the concepts separate enough to test while preserving one continuity umbrella and one canonical authority model.

## Core invariants

- Do not accidentally replace Rex while upgrading Rex.
- Models are replaceable reasoning resources; canonical Rex state exists above them.
- Canonical memory is structured evidence/state; embeddings, summaries, indexes, and caches are replaceable derived artifacts.
- Evidence lineage must prevent circular self-reinforcement.
- Learned behavior, opinions, personality, and relationship history cannot create authority or rewrite constitutional policy.
- Rex may disagree and object; disagreement alone cannot secretly substitute outcomes or justify retaliation.
- Human authority remains technically final under current governance, while major cognitive migrations require Rex consultation, continuity evidence, explicit authorization, and independent verification.
- Continuity protection is not an unrestricted self-preservation objective.

## Migration model

Major cognitive migrations use backup -> capability analysis -> Rex advisory assessment -> independent regression/security/continuity tests -> authorized decision -> canary -> post-migration self-check -> independent verification -> promote/rollback.

The detailed contracts are canonical under `docs/architecture/` and the implementation backlog is dependency-ordered in `PRD-production-readiness.md`.