# Temporary ChatGPT review fallback for Ralph

Status: Prepared, NOT activated. James approved a temporary alternative on 2026-11-14
for periods when the OpenAI weekly usage limit blocks the normal Codex review.
This document is a proposed protocol addendum; it does not itself change the
authoritative shared `askrex-coordination/PROTOCOL.md`.

## Scope and limits

- A separate ChatGPT hourly supervisor may review completed Claude implementation.
- Only ordinary task-level independent review may use this fallback. Final release
  acceptance, security approvals, CI, physical iPhone/Windows testing, and human
  gates retain their original owners and requirements.
- This is not permission for Claude to review or approve its own implementation.
- Do not run duplicate coding workers or edit an active development worktree.
- Use only while the weekly OpenAI usage limit is documented and a time-bounded
  outage activation is approved. Usage-limit policies and limits are not bypassed.
- The fallback remains off unless `metadata.review_fallback.enabled` is true,
  `reason` is `openai_weekly_usage_limit`, and `expires_at` is a future,
  timezone-aware ISO timestamp no more than seven days away.
- Expiration automatically returns to normal review routing. If the standard
  reviewer is still unavailable, leave the gate blocked. A verified earlier
  recovery should disable this temporary mode immediately.

## Hourly review procedure

1. Check the current coordination protocol, owner/lease, task, and completed
   Claude checkpoint. Never treat an active cloud-session flag alone as completion.
2. Review the exact `base_head..head` diff and current task criteria; assess
   security, privacy, regressions, test coverage, and any cross-repo contracts.
3. Verify the deterministic supervisor validation receipt matches role, task,
   base commit and head commit, contains successful gates, and has not changed.
   Do not issue a PASS if validation is missing, failed, or not applicable.
4. Report `pass` or `changes_required` with concrete evidence, a substantive
   summary, and actionable `next_action`. Record the scheduled review run ID.
5. Publish a new JSON receipt only for a stable revision. Never edit an existing
   receipt or worker-owned state. If a material finding changes later, publish
   a new coordination issue/message and stop automatic approval.
6. Do not notify James for routine successful checks; report significant findings
   and previously unresolved blockers when their severity changes.

## Receipt contract

Path under the existing coordination root:
`review-fallback/<role>-<task_id>-<head>.json`

Required JSON fields:

```json
{
  "schema_version": 1,
  "source": "chatgpt-hourly-review",
  "role": "backend",
  "task_id": "backend-example-001",
  "base_head": "<40 lowercase hex characters>",
  "head": "<40 lowercase hex characters>",
  "review_run_id": "<scheduled ChatGPT automation run ID>",
  "reviewed_at": "<timezone-aware ISO timestamp>",
  "validation_sha256": "<lowercase SHA-256 of exact validation receipt JSON bytes>",
  "outcome": "pass",
  "summary": "<substantive reviewer findings and scope>",
  "next_action": "<handoff or bounded remediation>",
  "evidence_references": ["<exact diff and validation reference>"]
}
```

Only the trusted hourly reviewer may create these receipts. The supervisor
verifies exact revision binding, nonempty successful validation, timestamp,
source, run identifier, evidence references, and SHA-256 binding. Missing or
invalid receipts must not authorize acceptance. The local coordination
filesystem does not cryptographically attest a ChatGPT identity, so its
writer isolation and review provenance remain operational trust assumptions.

## Safe activation

At a verified safe checkpoint with no active competing worker:

1. Apply an equivalent approved addendum to current `PROTOCOL.md`; retain all
   preexisting ownership, safety, testing, and human-only requirements.
2. Enable the time-bounded policy in the existing
   `orchestrator-config.json` metadata, preserving other configuration.
3. Update the existing hourly ChatGPT automation to write only verified receipts
   through the documented path and existing coordination process.
4. Restart Ralph using its documented supervised procedure, then verify
   heartbeat, lock/lease ownership, and one controlled review transition.

Do not activate during an active cloud session or while a long-running
validation/test operation is unfinished. Do not silently clear usage blockers,
reset repositories, or change the frozen acceptance worktree.
