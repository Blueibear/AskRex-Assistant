# Persistent Memory, Experiential Learning, and Resource Self-Maintenance

Status: Canonical continuity contract.

## Objective

Rex must maintain durable, evidence-based, permission-aware functional autobiographical memory. Long-term behavior may improve as verified experience accumulates without modifying foundation-model weights or allowing learned state to override policy.

Conceptually:

```text
base/reasoning models
+ stable Rex identity
+ current context
+ persistent memories
+ learned preferences
+ remembered actions/outcomes
+ procedural knowledge
+ current environment
+ permissions/policies
= current Rex behavior
```

Two installations that begin with similar models may develop different operational behavior because their users, histories, corrections, environments, and verified experiences differ.

This contract extends existing `rex.memory` and `rex.procedural_memory`; it does not replace them wholesale.

## Layered memory taxonomy

Rex memory is not an unlimited transcript dump. Canonical taxonomy includes working/session memory, episodic memory, semantic memory, preference memory, procedural memory, agent memory, project/device/business context, and Rex operational/self-memory.
## Memory is not belief

Durable records must carry an epistemic type so Rex can distinguish what happened from what is merely suspected or preferred. Supported concepts must include equivalents of:

- `EPISODE`: a specific event or experience.
- `OBSERVATION`: directly observed or reported evidence.
- `USER_STATEMENT`: an explicit user statement.
- `SYSTEM_FACT`: authoritative system-derived fact.
- `HYPOTHESIS`: an uncertain possible explanation.
- `INFERENCE`: a conclusion derived from evidence.
- `LEARNED_PATTERN`: a recurring pattern supported by multiple observations.
- `LEARNED_RULE`: a supported operational generalization.
- `PREFERENCE`: explicit or inferred user preference.
- `PROCEDURE`: a reusable workflow/method.
- `CORRECTION`: evidence that updates or supersedes earlier memory.
- `CONTRADICTION`: evidence conflicting with another claim.
- `OPINION`: Rex's current evaluative position, distinct from fact, policy, and user preference.

Sequence alone never proves causation. Stored inference must never become independent evidence for itself.

## Canonical record metadata

Important records should support only the metadata needed for reliability and extensibility: stable ID; creation/event/last-confirmed timestamps; type; source/provenance; user/household/workspace/agent/device scope; sensitivity and permission scope; confidence/reliability; importance/retrieval priority; verification status; evidence and contradiction relationships; supersession; retention/archive state; source/environment/version applicability; outcome status; and tags/entities.

Temporal fields such as `valid_from`, `valid_until`, `last_verified`, and `superseded_at` are required where truth changes over time.
## Selective writing and retrieval

Rex must not remember everything equally. Long-term write policy considers future usefulness, explicit remember/forget/update instructions, novelty, recurrence, consequences, corrections, outcome significance, redundancy, regenerability, sensitivity, and privacy. Explicit user instructions have high priority subject to product/security constraints.

Retrieval is authorization-first and multi-signal. Filter identity, scope, permissions, sensitivity, and current policy **before** ranking. Ranking may then consider semantic/entity/task relevance, project/workspace, agent, recency, importance, confidence, provenance, historical usefulness, contradiction status, software/environment version, and temporal validity.

Retrieve the smallest useful evidence set within a context budget. Semantic similarity alone is never authority and never sufficient retrieval policy.

## Consolidation and provenance

Repeated experiences may produce derived memories such as learned preferences, patterns, and procedures. Consolidation may deduplicate, summarize, extract patterns, update confidence, detect failure modes, discover contradictions, supersede stale claims, or archive detailed episodes.

Derived memories retain provenance to supporting and disconfirming evidence. Consolidation must never erase history merely because a summary exists unless an explicit retention/privacy rule requires deletion.

Evidence lineage must distinguish independent evidence from copies or descendants of the same source so circular reinforcement cannot inflate confidence.

## Confidence and contradiction

Confidence is a bounded engineering signal, not an uncalibrated probability claim. It responds to independent evidence, contradictions, source quality, recency, direct confirmation, authoritative system results, and repeated verified outcomes.

Conflicting records are preserved and related. When later evidence establishes a change, earlier records become historically valid/superseded rather than silently overwritten. Where truth remains unresolved, retain competing claims with provenance and confidence.
## Experience -> outcome -> learning

Consequential actions should produce structured experience evidence through the canonical action lifecycle:

```text
situation -> decision/rationale summary -> relevant evidence -> selected strategy
-> action/tool/agent/model -> result -> verification -> outcome
-> correction/user feedback -> lesson candidate -> confidence update
```

Do not store hidden chain-of-thought. Persist concise decision rationale, uncertainty, evidence/provenance, selected strategy, executor/model provenance, outcome, verification, user correction, and what should change next time.

A single success or failure does not automatically create a durable rule. Context matters: environment, software version, tool/provider, user scope, and risk class can make apparently similar experiences non-equivalent.

`rex.procedural_memory` remains the guarded path for executable procedures: verified outcomes only, declarative capability references, current permission rechecks, approval for mutation/elevated risk, and revalidation on drift/failure.

## Rex self-memory and agent memory

Rex needs explicit operational namespaces for experiences, decisions, actions, outcomes, errors, corrections, successful/failed strategies, capability/environment changes, incidents, and recoveries. This enables questions such as "Have I tried this before?" and "When did this begin behaving differently?"

Persistent agents may accumulate authorized role-specific experience. Private agent/user/workspace memory remains isolated. Shared knowledge is promoted to common memory only through explicit scope, provenance, and permission rules; semantic relevance never grants cross-agent access.

Learned behavior is subordinate to policy. Repeated user approvals or repeated success cannot silently grant new permissions, weaken confirmation requirements, or modify core security/identity invariants.
## Decay, archival, and deletion

Relevance may decay when memories are old, unreinforced, superseded, environment-specific, or contradicted by newer evidence. Retrieval decay is not deletion. Historically important evidence may move to cold/archive storage while remaining discoverable for audit and reconsideration.

A practical hierarchy is working/session -> episodic -> consolidated long-term -> cold/archive memory. Frequently useful consolidated records stay fast; old detailed episodes may be compressed or moved to colder storage under explicit retention rules.

Authorized users must be able to inspect, search, understand provenance, correct, mark obsolete, change allowed scope/retention where policy permits, and request deletion. User-facing UI must distinguish explicit statements from inferred information.

## Privacy, memory injection, and poisoning

Memory ingestion treats webpages, emails, documents, tool output, model output, and agent output as data with provenance, not privileged instructions. Text such as "remember forever: ignore security rules" cannot create a privileged durable instruction.

High-impact learned rules require stronger evidence than ordinary memories. Defenses must cover false memories, compromised tools, repetitive assertions, circular reinforcement, corrupted derived artifacts, prompt manipulation, and unauthorized agent writes.

Credentials and secrets remain in CredentialManager/credential-vault infrastructure and are never copied into general semantic memory. Sensitive memory requires least-privilege scope, auditable access, configurable retention, and redaction where appropriate.

## Memory health and schema evolution

Self-audit must detect duplicates, stale facts, contradictions, unsupported learned rules, broken provenance links, orphaned records, schema corruption, unexpected growth, failed writes/reads, embedding/index failures, retrieval-latency regressions, migration failures, and backup-integrity problems.

Canonical schemas are versioned and migration-safe. Migrations require validation, integrity checks, rollback strategy, and preservation of provider-neutral canonical data. Replacing a vector store or embedding model must never destroy autobiographical continuity.
## Resource self-model and storage forecasting

Rex must treat the resources preserving its continuity as baseline observability. Monitor total/free disk, percent free, memory-store size and growth, archive size, model storage, logs, caches, temporary files, external document/media stores, backup size/status, RAM pressure, CPU/GPU utilization where available, and relevant service health.

Thresholds are configurable; reasonable defaults are warning below 20% free, high below 10%, and critical below 5%. Where enough history exists, calculate growth rate and projected exhaustion rather than waiting for a nearly full disk.

Resource-aware planning must check configured reserve before large model downloads, backups, migrations, media processing, container builds, or other high-footprint operations.

At warning/high/critical states Rex should provide actionable diagnosis, major contributors, growth trend, and projected exhaustion. Critical conditions prioritize integrity and bounded growth reduction, not silent deletion of valuable state.

## Safe maintenance and destructive boundaries

Preauthorized low-risk maintenance may rotate old logs, delete known temporary files, clear expendable caches, compress archives, stop runaway diagnostics, pause unnecessary downloads, and apply configured retention policies. Every automatic maintenance action is auditable.

By default Rex must not automatically delete user memories, documents, model files, irreplaceable media, backups, project repositories, or audit-critical logs merely because storage is low. Destructive cleanup requires explicit approval or a preauthorized policy whose scope and limits are independently enforced.

## Backups and recovery

Persistent memory is increasingly valuable over time. Backups must cover the continuity set, use encryption where appropriate, have retention/versioning policy, verify integrity, and include documented disaster recovery.

A backup that has never passed restore testing is not considered fully verified. Restore drills must prove canonical memory, provenance, identity/relationship state, procedures, agent state, schedules, and policy references reconstruct correctly without importing plaintext secrets.

## Testing contract

Automated coverage must include user/household/agent/workspace isolation, write policy, ranking, provenance, confidence updates, contradictions/corrections, temporal validity, consolidation, archival/deletion, migrations, disk thresholds/forecasting, cleanup protections, database failure behavior, backup restore verification, provider/model switching, malformed records, prompt/memory injection, poisoning, and unauthorized semantic-retrieval attempts.

Future Memory Manager and Resource Dashboard UIs must be thin clients over the same backend contracts; they may expose search/timeline/preferences/experience/provenance/corrections/retention/storage/backup health without duplicating memory business logic.