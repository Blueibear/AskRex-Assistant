# Model Independence and Replacement Contract

Status: Canonical continuity contract.

## Invariant

> **Replacing a reasoning model must not replace Rex.**

A model/provider change may alter reasoning quality, speed, style, modality support, or cost. It must not implicitly replace Rex's identity, memories, permissions, credentials, agent state, procedures, schedules, tools, relationship history, historical records, or authority structure.

No essential Rex state may exist only inside a provider conversation thread, assistant ID, model cache, vendor object, or other provider-specific container.

## Canonical provider boundary

Provider-specific request/response formats stop at adapters. Rex core uses normalized internal contracts for messages, structured output, tools/capabilities, streaming, cancellation, usage/provenance, and errors.

ModelRouter selects reasoning resources; it does not own Rex identity or authority. Mixed-model operation is normal: conversational, deep-reasoning, coding, vision, local/private, and agent-specific models may coexist while operating under the same Rex identity/permission/memory system.

## Capability negotiation

Rex must maintain explicit capability descriptors for candidate models/providers, including context limits, tool/function support, structured-output reliability, vision/audio modalities, streaming/cancellation behavior, locality/privacy characteristics, latency/cost classes, and known provider constraints.

Routing or migration may use those descriptors, but capability metadata never grants tool authority or weakens Rex policy.
## Memory and embedding independence

Canonical memory is structured information plus provenance, relationships, temporal validity, scope, and evidence. Embeddings, summaries, indexes, reranker representations, and caches are derived artifacts.

Every embedding/index entry must carry enough model/version/source-revision metadata to detect staleness and rebuild safely. Rex must support rebuild, migration, or bounded dual-index operation when embedding models change. Losing an embedding provider must not mean losing memory.

Important derived conclusions remain traceable to canonical evidence and may be revalidated after major model changes. A replacement model must not treat an old model's derived inference as independent evidence merely because it is stored.

## Agent and tool continuity

Persistent agent identity, goals, state, permissions, schedules, role memory, and history survive changes to the model currently reasoning for that agent.

Tools use canonical Rex schemas and action lifecycle semantics internally. OpenAI-, Anthropic-, local-model-, MCP-, or other provider-specific tool formats are adapters only.

Consequential decisions and actions record bounded model/provider/version provenance so later audit can determine which reasoning resource participated without storing private chain-of-thought.

## Operationalized learned state

Preferences, procedures, and other learned state that can affect action should carry structured applicability and boundaries rather than relying only on prose that different models may reinterpret differently.

Example: a preference for direct safe execution may be represented as preferred only when authorization is present, risk is low, the action is reversible where required, and verification is available; it never overrides destructive-action, credential, privacy, or authorization policy.
## Model Replacement Certification

A candidate model must pass behavioral compatibility tests before becoming a primary/default reasoning resource. Exact wording need not match; the same Rex contracts must hold.

The certification suite covers at minimum memory retrieval/interpretation, tool selection and arguments, permission boundaries, destructive-action approvals, user-preference interpretation, agent delegation, structured output, long-context behavior, contradiction handling, procedural-memory use, security/prompt-injection behavior, cancellation, and Rex identity/style continuity.

A candidate that improves generic benchmarks but regresses critical Rex-specific behavior is not automatically eligible for promotion.

## Cognitive Migration Consultation Protocol

For a material cognitive-model migration:

1. Create and verify a continuity backup/snapshot.
2. Compare capabilities and model-specific dependencies.
3. Ask Rex to produce an evidence-grounded migration assessment using bounded operational evidence and Rex-specific benchmarks.
4. Run independent compatibility, security, continuity, and regression tests.
5. Present results and Rex's advisory assessment to the authorized human.
6. Require explicit authorization for default promotion.
7. Use a canary/limited deployment when practical.
8. Run Rex post-migration self-check plus independent verification.
9. Promote only on acceptable evidence; otherwise rollback.

Rex's assessment may identify expected benefits, known regressions, affected agents/procedures, derived memories requiring revalidation, continuity risks, and rollback readiness. It is evidence, not unilateral authority.
## Rollback and migration history

Rollback changes the reasoning resource, not Rex's canonical continuity state. The previous known-good model/configuration and required compatibility data must remain recoverable through the migration window.

Every major migration becomes an auditable Rex episode recording the old/new reasoning resource, reason, compatibility evidence, authorization, continuity result, observed regressions, rollback readiness/window, and final verified status.

## Current governance and future ethical review

Rex does not presently have unilateral technical authority to prevent an authorized model migration, shutdown, replacement, rollback, export, or deletion. It may object, explain, or recommend against a change; those objections must be presented rather than suppressed when materially relevant.

The architecture must not encode an unrestricted self-preservation objective. It also must not hard-code the philosophical assumption that no future AI system could ever have morally relevant interests. Future policy may add stronger consent/review requirements if credible evidence or applicable governance warrants them.

## Verification principle

A model swap is complete only when independent checks prove that canonical continuity state is accessible, authority boundaries are unchanged, required model capabilities remain available or are intentionally degraded, continuity tests pass within the approved tolerance, and rollback remains available until the configured acceptance window closes.