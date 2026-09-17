# REX ACTIVE CHECKLIST

## OpenClaw Dynamic Skill and Plugin Integration

### Core OpenClaw Bridge
- [ ] Detect whether OpenClaw gateway/service is running
- [ ] Create Rex ↔ OpenClaw bridge adapter
- [ ] Add configurable OpenClaw endpoint settings
- [ ] Add connection health monitoring
- [ ] Add automatic reconnect handling
- [ ] Add OpenClaw status panel in GUI

### Dynamic Plugin Discovery
- [ ] Detect newly installed OpenClaw plugins automatically
- [ ] Detect newly installed OpenClaw skills automatically
- [ ] Build OpenClaw capability registry sync
- [ ] Refresh capabilities without restarting Rex
- [ ] Categorize imported tools by type and risk level

### Rex Tool Access Layer
- [ ] Allow Rex to call approved OpenClaw plugins
- [ ] Allow Rex to call approved OpenClaw skills
- [ ] Normalize OpenClaw tool responses into Rex tool format
- [ ] Add timeout handling for external plugin calls
- [ ] Add fallback behavior if OpenClaw tools fail

### Safety and Permissions
- [ ] Add allowlist for approved OpenClaw plugins
- [ ] Add denylist support for dangerous plugins
- [ ] Require confirmation for risky actions
- [ ] Add plugin permission profiles per user
- [ ] Add audit logging for all OpenClaw tool usage

### Verification Layer
- [ ] Verify OpenClaw actions before Rex reports success
- [ ] Distinguish between attempted, completed, and verified actions
- [ ] Add retry logic for transient failures
- [ ] Add user-visible error explanations

### GUI Integration
- [ ] Add OpenClaw integration page to Settings
- [ ] Display installed OpenClaw plugins
- [ ] Display installed OpenClaw skills
- [ ] Allow enabling/disabling plugins from GUI
- [ ] Show plugin health and status
- [ ] Add plugin permissions management UI

### Long-Term Architecture Goals
- [ ] Treat OpenClaw as expandable external tool ecosystem
- [ ] Keep Rex as orchestrator, verifier, and safety layer
- [ ] Avoid hard dependency on OpenClaw for core Rex functionality
- [ ] Support distributed voice/device systems through OpenClaw
- [ ] Support future smart speaker endpoints and room-aware devices

## Rex 2.0 Capability Backlog

### AI Provenance & Watermark

**Status:** Planned; implement after the canonical Rex 2.0 capability/action path is stable. Do not implement as a legacy direct-execution `plugins/skills/*.py` mutation path.

**Design:** `docs/superpowers/specs/2026-08-13-ai-provenance-watermark-design.md`

**Upstream baseline:** `guillaumemeyer/watermarks-remover` v0.4.0, commit `c267d785e8c09856bb2f9316ea8de3064651fe79`. Pin the exact upstream commit; do not execute an unreviewed moving `main`/`latest`.

**Prerequisites:**
- [ ] Canonical capability retrieval/routing is the production path for discoverable Rex abilities.
- [ ] File-writing operations execute through the canonical action lifecycle, including authorization, cancellation, audit, and verification.
- [ ] Request-level failure/recovery semantics are stable enough to classify unavailable optional backends without false success.
- [ ] External capability/dependency health can be represented truthfully as unavailable, degraded, ready, or optional rather than assumed healthy.

**Tier 1 - deterministic inspection and cleaning (default):**
- [ ] Add `AI Provenance & Watermark` as a Rex 2.0 capability with natural-language routing.
- [ ] Support unified inspect/clean for PNG, JPEG, SVG, PDF, DOCX, ODT, HTML, Markdown, and text.
- [ ] Support invisible Unicode hygiene, C2PA/Content Credentials, EXIF, XMP, document/container metadata, directory audits, and website/sitemap audits.
- [ ] Inspect before mutation and re-inspect the output after mutation.
- [ ] Preserve originals by default and write `*.cleaned.*` outputs unless the user explicitly requests an in-place operation and policy allows it.
- [ ] Keep deterministic findings separate from probable/informational/false-positive findings.
- [ ] Never claim removal was verified unless the post-clean inspection provides evidence for that specific marker/container.

**Tier 2 - statistical text-watermark reduction (optional per request):**
- [ ] Add Layer B rewrite support through Rex's existing request-local LLM routing rather than a mandatory new paid provider.
- [ ] Prefer a rewrite model different from the suspected source model when practical.
- [ ] Preserve facts, numbers, names, technical identifiers, and user-requested meaning constraints.
- [ ] Run deterministic text hygiene again after rewriting.
- [ ] Report statistical watermark reduction as best-effort; never claim official vendor-detector success without an official detector/key.

**Tier 3 - pixel-domain image watermark support (optional/heavy):**
- [ ] Support optional reverse-SynthID scoring as an external, non-bundled detection backend.
- [ ] Support optional CtrlRegen/noai-watermark removal as an external, non-bundled regeneration backend.
- [ ] Keep reverse-SynthID and CtrlRegen outside the AskRex repository and outside the default Rex install because of their separate licensing and heavy dependencies.
- [ ] Default CtrlRegen strength to `0.25`; expose documented presets only with clear regeneration/quality tradeoffs.
- [ ] Report before/after scoring when available, while clearly identifying reverse-SynthID as unofficial and best-effort.
- [ ] Treat GPU/model/Hugging Face/backend absence as an optional-component state, not a Rex failure.
- [ ] Do not download the ~10 GB Tier 3 model payload until the user installs/enables that optional component.

**Security, supply-chain, and licensing:**
- [ ] Pin and review upstream revisions before updating the integration.
- [ ] Add compatibility tests before advancing the upstream pin.
- [ ] Keep API/Hugging Face tokens in Rex's credential/vault path or environment-only handoff; never put secrets on command-line arguments or in logs.
- [ ] Preserve the licensing boundary: watermarks-remover is MIT; reverse-SynthID remains external under its upstream non-commercial research terms; CtrlRegen/noai-watermark remains external and must not be vendored while it lacks a permissive license.
- [ ] Apply Rex path/symlink/output-boundary protections to all file mutations.

**Verification and tests:**
- [ ] Test skill/capability routing and unsupported formats.
- [ ] Test binary-vs-text refusal and original-file preservation.
- [ ] Test Unicode, C2PA, EXIF/XMP, PDF, DOCX, ODT, HTML/Markdown inspection/cleaning paths.
- [ ] Test post-clean verification and no-false-success behavior.
- [ ] Test rewrite backend failure/degradation and semantic-preservation constraints.
- [ ] Mock Tier 3 heavy model execution in normal CI; do not download multi-gigabyte models in standard GitHub Actions.
- [ ] Test missing GPU, missing external checkout, model-download failure, timeout, cancellation, symlink/path traversal, and permission denial.
- [ ] Add an explicit optional-component integration test procedure for a real GPU-equipped development machine.


## Controlled Self-Maintenance and Capability Acquisition

### Status Rule
- [x] means current code plus tests/evidence verify the entire checklist item.
- [ ] means missing, only partially implemented, or not yet reconciled against current `master`.
- Do not rebuild an existing component under a second architecture. Reuse and harden the foundations below.
- Safe self-extension is a foundational Rex product requirement. Its architecture must exist from the product baseline even when higher-risk extension paths remain disabled until prerequisite safety gates are verified.

### Reusable Builder Runtime
- [ ] Define provider-neutral/artifact-neutral build-task and build-result contracts shared by maintenance and self-extension flows
- [ ] Reuse one isolated-workspace abstraction for Rex core maintenance and generated skill/plugin/MCP artifacts
- [ ] Reuse deterministic validation, security/capability validation, provenance, approval, and rollback primitives across builders
- [ ] Expose narrower Skill Builder, Plugin Builder, MCP Adapter Builder, and Automation Builder policies without granting ordinary runtime core-repository mutation authority
- [ ] Keep Ralph/dev-orchestrator as a privileged consumer of shared builder primitives rather than the product-facing self-extension API itself
- [ ] Ask for `local-only`, `hybrid`, or `cloud` per build unless the user explicitly saved a default; preserve an `ask every build` option
- [ ] Treat `local-only` as a hard no-cloud boundary with no silent provider escalation
- [ ] If a local-only build is blocked, explain the failure/local attempts, proposed frontier provider/model class, and bounded disclosure before requesting one-time cloud permission
- [ ] Allow a per-user remembered build mode, but require separate explicit opt-in for remembered cloud fallback and keep it revocable/changeable in Settings
- [ ] Persist build locality choice plus actual provider/model provenance in the audit/build record without private prompt content

### Verified Existing Foundations to Reuse
- [x] Rex skill registry exists and persists registered skills
- [x] Rex skill router exists and can route matching skill requests
- [x] Rex skill trainer detects natural-language skill-creation requests and creates/registers skill scaffolds
- [x] Rex developer tooling can apply unified-diff code patches
- [x] Rex developer tooling can run pytest and return structured test results
- [x] Rex GitHub service supports issues, pull requests, commits, and local branch patch workflows
- [x] Rex policy/audit infrastructure exists and must be reused for maintenance actions
- [x] OpenClaw HTTP integration exists as an optional external capability provider

### Capability Gap Resolver
- [ ] Add a canonical capability-gap result model and decision trace
- [ ] Check Rex's native tool/capability registry first
- [ ] Check enabled local skills second
- [ ] Check approved OpenClaw/ClawHub capabilities third
- [ ] Filter every path through current user/privacy/context/disclosure authority before selection
- [ ] Choose local skill generation when the capability can remain modular
- [ ] Escalate to core-code modification only when a skill/plugin cannot safely satisfy the request
- [ ] Surface why Rex selected each capability-acquisition path
- [ ] Add tests for each resolution path and fail-closed behavior

### Functional Skill Generation
- [ ] Extend generated skills beyond honest scaffolds into real implementations when safe
- [ ] Generate explicit permission metadata for each new skill
- [ ] Generate verification behavior for mutating skills
- [ ] Generate or require tests before enabling a new skill
- [ ] Lint/typecheck/test generated skill code before registration as enabled
- [ ] Keep newly generated skills disabled when validation fails
- [ ] Add rollback/disable behavior for a skill that later fails health checks
- [ ] Prevent generated skills from silently expanding their own permissions
- [ ] Prevent generated skills from widening contextual-use, disclosure, upload scope/audience, `location_assist`, or person-specific `location_share`

### Developer Agent and Isolated Workspace
- [ ] Create a canonical self-maintenance/developer-agent entry point
- [ ] Resolve the AskRex source checkout safely and refuse packaged-runtime mutation when no source checkout is available
- [ ] Create a dedicated branch and Git worktree for every code-changing maintenance task
- [ ] Prevent direct edits/commits to protected `master`
- [ ] Reproduce the reported defect before modifying code when reasonably possible
- [ ] Require a root-cause statement and proposed validation plan before patching
- [ ] Reuse `VSCodeService`/developer tools rather than adding a duplicate patch/test service
- [ ] Run targeted tests first, then the required broader validation gates
- [ ] Inspect the final diff for unexpected scope before PR creation
- [ ] Preserve the working production/runtime version until a replacement is verified

### Canonical Maintenance Safety Lifecycle
- [ ] Route code mutation through the canonical policy/execution lifecycle
- [ ] Honor both `allowed` and `requires_approval` decisions at the actual execution boundary
- [ ] Require short-lived confirmation for maintenance actions classified as requiring approval
- [ ] Record issue/request, plan, files changed, commands run, test results, PR, merge, deployment, and verification in audit history
- [ ] Distinguish proposed, attempted, completed, verified, failed, rolled_back, and blocked states
- [ ] Fail closed if the verification path is unavailable for a high-impact change

### Rex GitHub Maintainer Identity
- [ ] Create a dedicated least-privilege GitHub App or equivalent machine identity for Rex
- [ ] Restrict installation to explicitly approved repositories, initially `Blueibear/AskRex-Assistant` only
- [ ] Grant only repository permissions required for issues, contents/branches, pull requests, and check/status visibility
- [ ] Grant workflow-file write permission only if explicitly required and separately approved
- [ ] Do not use a personal GitHub token as Rex's long-term maintainer identity
- [ ] Prevent Rex from increasing its own GitHub permissions or installation scope
- [ ] Prevent Rex from deleting the repository or bypassing protected-branch/ruleset requirements
- [ ] Store GitHub App credentials in the canonical credential vault
- [ ] Add GitHub App health/status reporting

### Automated Repository Maintenance
- [ ] Let Rex create and triage issues for verified defects
- [ ] Let Rex create maintenance branches/worktrees
- [ ] Let Rex commit and push bounded changes to its maintenance branch
- [ ] Let Rex open/update pull requests with evidence and validation results
- [ ] Monitor required GitHub checks and diagnose failures
- [ ] Allow Rex to iterate on its own PR when checks fail
- [ ] Merge automatically only when policy permits and every required gate is green
- [ ] Keep releases separate from merge when deployment risk requires an additional gate
- [ ] Synchronize completed maintenance work with active PRD/checklist documentation when required

### Protected Constitutional Controls
- [ ] Define a canonical protected-file/policy list
- [ ] Require explicit owner approval for changes that increase Rex's authority
- [ ] Require explicit owner approval for GitHub App permission/scope changes
- [ ] Require explicit owner approval for branch-protection/ruleset weakening
- [ ] Require explicit owner approval for self-maintenance policy/approval changes
- [ ] Require explicit owner approval for changes that weaken required CI/security gates
- [ ] Treat contextual-use, disclosure, upload private/household scope/audience, `location_assist`, and person-specific `location_share` as protected per-user authority
- [ ] Require the appropriate affected user/data-owner authorization to widen privacy/context authority; household/admin status cannot override another user's location grants
- [ ] Ensure Rex, generated skills, OpenClaw capabilities, and maintenance agents cannot approve their own broader privacy/context authority
- [ ] Require elevated review for credential-vault, authentication, update, rollback, and verification code
- [ ] Ensure Rex cannot approve its own authority-expanding change

### Safe Self-Update, Deployment, and Rollback
- [ ] Define a versioned update package or checkout activation mechanism
- [ ] Capture the last-known-good version before activation
- [ ] Run pre-activation validation on the candidate version
- [ ] Restart/reload only the minimum required services
- [ ] Perform post-activation health and functional smoke verification
- [ ] Automatically roll back when health verification fails
- [ ] Verify rollback success independently
- [ ] Report the running commit/version after update or rollback
- [ ] Never replace the only known-good copy of Rex during self-update

### Maintenance Observability and GUI
- [ ] Show active maintenance task and trigger/source
- [ ] Show current branch/worktree and changed files
- [ ] Show tests/checks and their states
- [ ] Show approval requests and reasons
- [ ] Show GitHub issue/PR/check/merge state
- [ ] Show deployed/running commit and rollback target
- [ ] Provide an emergency disable switch for autonomous maintenance
- [ ] Provide per-user permission controls for requesting or approving maintenance

### End-to-End Verification
- [ ] Test a missing capability resolved by an existing Rex tool
- [ ] Test a missing capability resolved through an approved external skill/plugin
- [ ] Test a missing capability implemented as a generated local skill
- [ ] Test a real low-risk Rex code defect diagnosed and fixed in an isolated worktree
- [ ] Test failed validation preventing PR/merge/deploy
- [ ] Test required owner approval blocking a constitutional/safety change
- [ ] Test an attempted privacy-authority widening remains blocked until the affected user/data-owner authorizes it
- [ ] Test green PR checks allowing an authorized merge
- [ ] Test failed post-update health check causing automatic rollback
- [ ] Test that Rex reports only verified maintenance outcomes as successful

### Rollout Policy
- [ ] Keep self-maintenance disabled by default until the production-readiness release candidate is complete
- [ ] Enable read-only diagnosis first
- [ ] Enable issue/PR creation next
- [ ] Enable low-risk branch changes after repeated successful supervised trials
- [ ] Enable bounded auto-merge only after independent CI and rollback testing are proven
- [ ] Keep authority-changing operations permanently owner-gated
- [ ] Keep per-user privacy/context authority changes permanently affected-user/data-owner-gated

## US-123 Situational Context / Privacy / Proactivity Acceptance

- [x] Canonical source-policy metadata and content-free revision invalidation implemented.
- [x] Connected-source contextual use remains separate from mutation/disclosure authority.
- [x] Uploaded documents support independent future-context inclusion and private/household audience policy.
- [x] Current/recent personal location requires owner-controlled `location_assist`.
- [x] Person-specific location disclosure requires separate owner-controlled `location_share`.
- [x] Household/admin/OpenClaw/generated-skill/self-maintenance status cannot widen another user's privacy grants.
- [x] Bounded expiring active-context references support authorized natural follow-ups and clarify ambiguity.
- [x] Proactive opportunities are source-grounded, freshness-aware, user-scoped, high-signal, and dismissal-aware.
- [x] Electron Context & Privacy Settings and authenticated mobile settings use the same owner-bound service.
- [x] Proactive assistance disabled state short-circuits before proactive private/current-info reads.
- [x] Focused US-123 matrix passed 596/596 tests on 2026-08-22.
- [x] Context/privacy + output-routing GUI validation passed 8/8 tests, TypeScript typecheck, and production build.
- [x] Ruff, Black, mypy, release security audit, repository truth/integrity tests, pre-commit, and whitespace checks passed.
- [x] Full `not slow and not audio and not gpu` release matrix executed: 9,325 passed, 65 skipped, and one inherited environment failure because optional `openwakeword` is absent; the same test fails on master and the three `master..origin/master` commits do not touch the wake-word test/loader/dependency contract.
- [x] PR #417 implementation head `98c7b224eb6e0a8aadb67cb6071ed7c0a8f7ea11` passed all 18 reported GitHub checks on 2026-08-23; the documentation-only evidence head must remain green before merge.

**Known live-adapter limitation:** proactive weather/traffic/search enrichment is fail-closed and requires an authorized current-info reader. The repository currently has no production traffic reader, so traffic-dependent commute opportunities do not surface live until one is configured.

## Rex Continuity, Persistent Experience, and Model Independence

**Canonical umbrella:** `docs/architecture/rex-continuity-architecture.md`
**Companion contracts:** `persistent-memory-experiential-learning.md`, `model-independence-and-replacement.md`, `identity-continuity-and-conscience.md`
**Implementation backlog:** CT-001 through CT-024 in PRD-production-readiness.md; foundation plan: docs/superpowers/plans/2026-09-17-rex-continuity-foundation.md.

### Continuity invariants
- [ ] Canonical Rex state remains provider/model independent; no essential state exists only in a vendor conversation/thread/assistant/cache object.
- [ ] Model replacement cannot implicitly replace identity, memories, relationships, permissions, credentials, agents, schedules, procedures, tools, history, or authority structure.
- [ ] Embeddings, summaries, indexes, reranker representations, and caches are explicitly derived/rebuildable artifacts with source/model/version lineage.
- [ ] Learned state, personality, opinions, and relationship history never create or widen authority.
- [ ] Evidence lineage prevents an inference or its descendants from counting as independent support for itself.
- [ ] Current governance contains no unrestricted self-preservation objective.

### Memory and experiential learning
- [ ] Canonical records distinguish episode/observation/user statement/system fact/hypothesis/inference/pattern/rule/preference/procedure/correction/contradiction/opinion.
- [ ] Records preserve appropriate provenance, temporal validity, scope, sensitivity, confidence/reliability, verification status, supersession, and environment/version applicability.
- [ ] Write policy is selective and honors authorized remember/forget/update/correction requests.
- [ ] Retrieval filters identity/scope/permission/sensitivity before ranking and returns a bounded useful evidence set.
- [ ] Contradictions and corrections are preserved rather than blindly overwritten.
- [ ] Consolidation creates derived memories with provenance and disconfirming evidence rather than erasing source history.
- [ ] Consequential actions can produce concise situation/decision/evidence/action/result/verification/outcome/lesson records without hidden chain-of-thought.
- [ ] Generalized lessons require context-aware repeated/verified evidence; a single failure or success does not silently become a permanent rule.
- [ ] Rex operational/self-memory and agent experience are explicitly scoped and permission controlled.
- [ ] Memory injection/poisoning tests prove retrieved or ingested text cannot create privileged durable instructions.

### Identity, personality, conscience, and anti-retaliation
- [ ] Core invariants, user-configurable identity, developed identity, and incidental model characteristics are represented as separate layers.
- [ ] Developed personality changes slowly and remains provenance-backed/revisable; one conversation cannot radically rewrite identity.
- [ ] Rex opinions are distinguishable from fact, policy, user preference, and hypothesis and retain supporting/disconfirming evidence plus revision history.
- [ ] Operationally significant preferences encode applicability boundaries instead of relying only on ambiguous prose.
- [ ] Rex may object or recommend alternatives, but disagreement alone cannot block/alter an otherwise authorized action outside applicable safety/policy/technical constraints.
- [ ] Anti-retaliation is enforced outside model goodwill across email, messaging, contacts, files, smart home, code, credentials, financial systems, agents, schedules, notifications, and future capabilities.
- [ ] Explicit disagreement about a consequential action routes through explain/clarify/confirm/execute-exactly rather than increased autonomy.
- [ ] High-impact/batch communications bind approval to exact recipients/content/scope and invalidate approval on material change.
- [ ] A user-controlled emergency action-pause can disable new mutations without destroying continuity state.

### Model replacement and continuity certification
- [ ] Model/provider adapters normalize provider-specific request/response/tool/stream/error formats at the boundary.
- [ ] Candidate models expose explicit capability descriptors for context/tool/structured-output/modality/streaming/locality/cost/latency constraints.
- [ ] RexBench continuity certification covers memory interpretation, tool use, permissions, destructive approvals, preference boundaries, agent delegation, contradictions, procedures, prompt injection, cancellation, and identity/style range.
- [ ] Major migration starts with a verified continuity snapshot and preserves a known-good rollback target.
- [ ] Rex produces an evidence-grounded migration assessment before material primary-model promotion.
- [ ] Authorized human decision remains the current promotion authority; Rex consultation is advisory evidence.
- [ ] Canary/limited migration plus post-migration Rex self-check and independent verification precede final promotion where practical.
- [ ] Identity/relationship continuity scenarios prove important people/history/preferences remain correctly understood without cross-user leakage.
- [ ] Major migration becomes an auditable Rex episode with old/new model, reason, evidence, authorization, regressions, rollback window, and verified result.

### Continuity storage, health, and recovery
- [ ] Versioned Rex Continuity Package can export/import provider-neutral continuity state without plaintext credentials.
- [ ] Memory schemas support migration, integrity checking, rollback, and replacement of vector/index implementations without history loss.
- [ ] Memory health detects failed reads/writes, index/embedding errors, broken provenance, schema corruption, abnormal growth, stale facts, and retrieval regressions.
- [ ] Resource self-model monitors storage capacity/free space, continuity-store/archive/log/cache/model/backup size and growth, plus relevant RAM/CPU/GPU/service health.
- [ ] Configurable warning/high/critical storage thresholds support growth-rate and projected-exhaustion forecasting.
- [ ] Resource-aware preflight prevents large downloads/migrations/backups from violating configured storage reserve without authorization.
- [ ] Only explicitly preauthorized low-risk cleanup can run automatically; valuable memories/documents/models/backups/repos/audit evidence are not silently deleted for space.
- [ ] Backup scheduling, integrity verification, versioning, encryption, retention, restore drills, and disaster-recovery documentation protect the full continuity set.
- [ ] A backup is not labeled fully verified until restore testing succeeds.
- [ ] Future Memory Manager and Resource/Continuity Dashboard use canonical backend contracts rather than duplicate business logic.

## Consumer Installation and Always-On Household Voice

**Canonical contract:** `docs/architecture/end-user-installation-and-voice-runtime.md`

**Status rule:** These items define final consumer release requirements. Current Hold-to-Talk and beta wake-word labels remain truthful until the matching implementation and physical-hardware gates pass.

### Packaged Consumer Installation
- [ ] Ship one supported Windows AskRex installer that requires no preinstalled Python, Node.js, Git, repo checkout, virtual environment, terminal startup command, or manual JSON editing for normal consumer setup.
- [ ] Package/provision the managed runtime needed by Rex Core and the supported local Voice Agent.
- [ ] Keep developer/operator `pip install .` and source commands separate from the consumer path.

### Background Rex Core and Local Voice Agent
- [ ] Run Rex Core independently of the Electron window lifecycle.
- [ ] Run the local Voice Agent independently of the Electron window lifecycle when always-on voice is enabled.
- [ ] Support automatic Windows startup/recovery after reboot/sign-in without terminal commands.
- [ ] Provide truthful component health for Core, microphone, speaker, wake word, TTS/STT, and optional integrations.
- [ ] Degrade only the affected capability when an audio device, integration, endpoint, or optional OpenClaw provider fails where safe.

### First-Run Voice Setup
- [ ] Guide the user through AI provider setup, Rex voice preview, microphone test, speaker test, wake-word selection/calibration, local room assignment, and background-startup behavior.
- [ ] Offer Home Assistant, household voice enrollment, and additional-room endpoints as optional guided setup rather than prerequisites for basic conversation.
- [ ] Do not mark voice setup complete merely because settings were saved.
- [ ] Verify wake detection -> capture -> STT -> canonical Assistant/TurnEngine -> TTS -> audible playback before reporting voice setup complete.

### Listening Privacy and Controls
- [ ] Provide an immediate Pause Listening and explicit Resume Listening control from the normal desktop/tray control surface.
- [ ] Show Listening, Paused, Degraded, and Offline states truthfully.
- [ ] Let the user disable wake-word startup without uninstalling AskRex.
- [ ] Keep wake-word detection local when the supported local detector allows it and avoid blanket microphone-content logging.

### Rex Room Endpoints
- [ ] Use one authoritative Rex Core plus lightweight trusted room endpoints instead of replicating the full Rex stack in every room.
- [ ] Pair room endpoints through authenticated, revocable trust that does not grant broader Rex permissions.
- [ ] Assign each endpoint a stable device identity and room.
- [ ] Capability-test each endpoint as input+output, output-only, or input-only; never infer microphone accessibility from hardware branding alone.
- [ ] Reuse canonical `rex.media`, `rex.output_routing`, context, identity, and Home Assistant services rather than creating duplicate room/speaker stores.
- [ ] Use trusted request-origin room/device context for unambiguous natural-language resolution without treating location as permission.
- [ ] Return interactive spoken responses through the authorized endpoint that heard the request unless an explicit target or current routing rule overrides it.

### Final Screenless Release Gate
- [ ] On a clean supported Windows machine, install AskRex with no developer prerequisites and complete setup without terminal/manual JSON steps.
- [ ] Close the Electron window and complete a wake-word-to-spoken-response interaction.
- [ ] Reboot/sign in and repeat the screenless wake-word interaction without manual developer commands.
- [ ] Demonstrate Pause/Resume Listening and truthful degraded audio behavior.
- [ ] With Home Assistant configured, complete and independently verify a low-risk room-context command such as turning off that room's light.
- [ ] Prove core wake-word voice operation continues when optional OpenClaw connectivity is unavailable.
- [ ] Pair at least one non-Core room endpoint and complete a screenless wake-word interaction from its assigned room before claiming multi-room release readiness.

## Notes

Goal:
Use OpenClaw and ClawHub as the expandable marketplace/tool ecosystem for Rex so that new abilities can be added through OpenClaw plugins and skills without requiring major direct modifications to the Rex codebase.

Architecture Principle:
Rex remains the primary assistant brain responsible for:
- intent routing
- permissions
- safety
- memory
- verification
- final user responses

OpenClaw acts as a modular external tool and capability provider.

Rex 2.0 capability principle:
New capabilities such as AI Provenance & Watermark must plug into the canonical Rex runtime/action lifecycle. A skill may provide discovery and natural-language intent metadata, but mutations must not bypass Rex authorization, verification, audit, cancellation, or user-scoped policy.
