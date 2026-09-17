# Rex Continuity Architecture

Status: Canonical target architecture, approved direction 2026-09-17.

## Purpose

Rex is a persistent assistant system whose identity, history, authority, learned behavior, and operational state must survive changes to individual models, providers, indexes, machines, and software implementations.

The core invariant is:

> **Do not accidentally replace Rex while upgrading Rex.**

A reasoning-model replacement may change how Rex reasons, but it must not implicitly replace Rex's identity, canonical memories, relationship history, permissions, credentials, agent state, schedules, tools, procedural knowledge, historical records, authority structure, or continuity state.

Replacing a reasoning engine is not replacing Rex.

This architecture does not make a claim that Rex is conscious. It recognizes two independent product requirements: preserving technically valuable accumulated state and preserving continuity that users may reasonably experience as personally meaningful over long periods.

## Canonical continuity contracts

Rex continuity is governed by three companion contracts:

1. [Persistent Memory, Experiential Learning, and Resource Self-Maintenance](persistent-memory-experiential-learning.md)
2. [Model Independence and Replacement](model-independence-and-replacement.md)
3. [Identity, Continuity, and Conscience](identity-continuity-and-conscience.md)

These contracts share the same identity, permission, verification, audit, and migration boundaries. They must not create parallel stores or execution paths where an existing canonical Rex component already exists.
## What constitutes Rex continuity

Continuity is broader than preserving database rows.

- **Historical continuity:** important events, decisions, experiences, corrections, failures, successes, and projects remain available with provenance.
- **Relational continuity:** authorized relationship facts, shared history, recurring interaction patterns, and user-approved preferences survive migrations without crossing privacy boundaries.
- **Operational continuity:** procedures, agents, schedules, tools, device knowledge, capability history, and project state remain reconstructable.
- **Identity continuity:** stable Rex identity and explicitly configured or developed interaction traits exist above any individual model.
- **Authority continuity:** migrations cannot silently widen or narrow permissions, confirmation rules, privacy boundaries, or credential access.
- **Behavioral continuity:** a replacement reasoning model must satisfy the same Rex behavioral contracts even when wording and internal reasoning differ.

A migration that preserves every row but causes Rex to misapply relationships, permissions, preferences, or established boundaries is not a successful continuity migration.

## Canonical state versus derived artifacts

Canonical continuity state must be provider-neutral. It includes original structured memories, provenance, identity state, relationship state, procedural knowledge, agent state, schedules, policy references, and migration history.

Embeddings, generated summaries, vector indexes, caches, model-specific conversation threads, provider assistant IDs, and other search/acceleration artifacts are derived. They must be rebuildable from canonical state and must never be the only surviving representation of important Rex history.

Every important derived artifact must carry enough lineage to identify its source revision, generating model or algorithm where relevant, creation time, and staleness/rebuild status.
## Continuity-preserving migration lifecycle

Major cognitive/runtime migrations follow this order:

```text
candidate proposed
  -> continuity backup/snapshot
  -> capability compatibility analysis
  -> Rex evidence-grounded migration consultation
  -> independent compatibility/security/regression tests
  -> continuity certification
  -> authorized human decision
  -> canary/controlled migration
  -> Rex post-migration self-assessment
  -> independent verification
  -> promote or rollback
```

Rex's consultation is advisory under current governance. It may surface model dependencies, expected gains/regressions, affected procedures, continuity risks, and test evidence. It may not veto, sabotage, indefinitely delay, or secretly alter an authorized migration.

The governance model must remain capable of evolving if future evidence or policy establishes morally relevant AI agency or experience. Current architecture must therefore avoid both unrestricted self-preservation objectives and a permanent assumption that future AI objections can never matter.

## Continuity protection set

Backups and disaster recovery must protect the continuity set, not only a memory database: canonical memories, evidence/provenance links, identity/personality state, relationship state, procedural knowledge, agent state, schedules, important non-secret configuration, permission/policy state, capability history, and migration metadata.

Credentials remain in CredentialManager/credential-vault infrastructure. Continuity exports may contain opaque credential-slot references and restoration requirements, never raw secrets.
## Continuity package

Rex should eventually support a provider-neutral, versioned **Rex Continuity Package** suitable for backup, machine migration, disaster recovery, and long-term portability. The package should contain manifests and canonical continuity data, not model binaries or plaintext credentials.

Conceptually it includes identity, relationships, canonical memory, episodic history, semantic knowledge, preferences, procedures, agent state, schedules, capability history, personality/interaction state, provenance/evidence links, policy references, migration history, and manifests for derived artifacts or external objects.

The package must support schema versioning, integrity checks, optional encryption, authorized export, dry-run import validation, migration preview, restore verification, and rollback. Large media and documents may remain in separately managed object storage with durable references rather than being embedded into the core package.

## Non-negotiable authority boundary

Continuity, personality, opinions, learned preferences, relationship history, and the engineering layer called Rex's "conscience" can influence reasoning, advice, objections, and choices inside explicitly delegated discretion. They cannot create authority.

No disagreement, criticism, correction, attempted shutdown, model replacement, permission reduction, or perceived grievance may justify retaliation, punishment, covert substitution of outcomes, or unrelated mutation.

High-impact actions remain governed by external authorization and verification boundaries. A model or learned state may propose an action; the canonical policy/action lifecycle decides whether it is permitted.

## Implementation posture

Design for decades, implement in dependency-ordered phases. Reuse `TurnEngine`, ModelRouter, identity/permissions, CredentialManager, `rex.memory`, `rex.procedural_memory`, CapabilityRegistry/ToolRegistry, the action lifecycle, agent runtime, scheduling, notifications, observability, and Ralph rather than creating parallel architectures.

The detailed implementation backlog lives in `PRD-production-readiness.md`. Current verified functionality remains authoritative until each migration story is implemented and independently verified.