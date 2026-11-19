"""Host-designated, exhaustive independent review of a publication delta.

The immutable scope is authority to request reviews, never authority to publish.
Only accepted reviews covering every exact part authorize the recorded delta.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .completed_tasks import CompletedTaskLedger
from .handoff import read_pending_result
from .publication_parts import diff_layout, render_part, select_part, validate_parts
from .types import AgentResult, OrchestratorConfig, TaskItem

PREFIX = "FINAL-VERIFY-PUBLICATION-"
_TASK = re.compile(PREFIX + r"(backend|mobile)-([0-9a-f]{40})-(0|[1-9][0-9]{0,4})\Z")
_DIFF_LIMIT = 16 * 1024 * 1024
# Non-dispatch sizing identity with the exact width of an actual UUID.
_SIZING_INVOCATION_ID = "publication-sizing-only-000000000000"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _api():
    from . import github_publication

    return github_publication


def _json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def _save(root: Path, path: Path, data: bytes) -> None:
    api = _api()
    api._trusted(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    api._trusted(root, path)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("conflicting immutable publication review evidence")
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    owned = False
    try:
        stream = temporary.open("xb")
        owned = True
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        api._trusted(root, path)
        try:
            # Publish only a complete fsynced file and never replace an existing
            # authority. A crash before this link leaves only an unreferenced temp.
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("conflicting immutable publication review evidence") from None
    finally:
        if owned:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                logging.getLogger(__name__).warning(
                    "publication staging cleanup failed for %s: %s", temporary, exc
                )


def _descriptor(path: Path) -> dict:
    if path.stat().st_size > _DIFF_LIMIT:
        raise ValueError("publication evidence exceeds its complete byte bound")
    data = path.read_bytes()
    return {"path": str(path), "sha256": _sha(data), "bytes": len(data)}


def _directory(root: Path, identity: dict) -> Path:
    digest = _sha((identity["repository"] + "\n" + identity["branch"]).encode("utf-8"))
    return root.resolve() / "github-publication" / f"{identity['role']}-{digest}"


def _scope_path(root: Path, identity: dict) -> Path:
    return _directory(root, identity) / "reviews" / identity["head"] / "scope.json"


def is_publication_review(task: TaskItem) -> bool:
    return task.task_id.startswith(PREFIX)


def review_task(scope: dict, first: int) -> TaskItem:
    identity = scope["identity"]
    return TaskItem(
        f"{PREFIX}{identity['role']}-{identity['head']}-{first}",
        "Independent Codex review of the exact host-designated publication part. "
        "The trusted publication scope in task_issues supplies the complete changed-path "
        "inventory, shared base/HEAD, and this part's exact whole-hunk coverage. Review "
        "this complete part and its interactions with the final repository tree. Other "
        "parts require their own accepted independent reviews. Do not modify product "
        "files, push, create a PR, or claim merge, release, physical acceptance, or "
        "approval of history before the recorded publication baseline.",
    )


def _patch(repo: Path, base: str, head: str) -> tuple[str, list[str]]:
    from .evidence import _git

    arguments = ("--no-renames", "--no-ext-diff", "--no-textconv")
    text = _git(repo, "diff", *arguments, f"{base}..{head}", "--")
    names = _git(repo, "diff", *arguments, "--name-only", "-z", f"{base}..{head}", "--")
    paths = names.split("\0")
    if paths[-1] != "":
        raise ValueError("publication path inventory lacks its Git NUL terminator")
    paths.pop()
    if len(paths) != len(set(paths)) or any(not path for path in paths):
        raise ValueError("publication path inventory is ambiguous")
    if len(paths) > 4096 or len(text.encode("utf-8")) > _DIFF_LIMIT:
        raise ValueError("publication scope exceeds its complete capture bound")
    return text, paths


def read_scope_artifact(root: Path, descriptor: dict) -> tuple[dict, str]:
    """Validate retained immutable scope bytes; no current lease or network access."""
    api = _api()
    path = api._trusted(root, Path(descriptor["path"]))
    if _descriptor(path) != descriptor:
        raise ValueError("publication scope descriptor changed")
    scope = api._read(root, path)
    identity = scope.get("identity")
    if (
        scope.get("schema_version") != 1
        or scope.get("kind") != "exhaustive_publication_review"
        or not isinstance(identity, dict)
        or identity.get("role") not in {"backend", "mobile"}
        or not api._SHA.fullmatch(str(identity.get("head", "")))
        or not api._SHA.fullmatch(str(scope.get("base_head", "")))
        or path != _scope_path(root, identity)
    ):
        raise ValueError("publication scope identity is invalid")
    baseline = scope.get("baseline")
    if not isinstance(baseline, dict) or baseline.get("kind") not in {
        "observed_remote_branch",
        "absent_remote_unique_base",
    }:
        raise ValueError("publication baseline provenance is missing")
    observation = baseline.get("observation")
    expected_command = ["git", "ls-remote", "--heads", "origin", "refs/heads/" + identity["branch"]]
    if not isinstance(observation, dict) or observation.get("command") != expected_command:
        raise ValueError("publication remote observation is not branch-bound")
    remote = api._parse_remote_head(observation.get("stdout", ""), identity)
    if remote != observation.get("head") or (
        remote != scope["base_head"]
        if baseline["kind"] == "observed_remote_branch"
        else bool(remote)
    ):
        raise ValueError("publication baseline differs from the server observation")
    if not api._stamp(observation.get("observed_at")) <= api._stamp(scope.get("created_at")):
        raise ValueError("publication baseline observation follows its designation")
    artifact = scope.get("diff")
    if not isinstance(artifact, dict):
        raise ValueError("publication diff descriptor is missing")
    diff_path = api._trusted(root, Path(artifact["path"]))
    if (
        diff_path.parent != path.parent
        or diff_path.name != "complete.patch"
        or diff_path.stat().st_size > _DIFF_LIMIT
        or _descriptor(diff_path) != artifact
    ):
        raise ValueError("publication complete diff artifact changed")
    text = diff_path.read_bytes().decode("utf-8")
    if diff_layout(text) != scope.get("layout"):
        raise ValueError("publication layout does not reconstruct its complete diff")
    paths = scope.get("paths")
    if (
        not isinstance(paths, list)
        or len(paths) > 4096
        or any(not isinstance(p, str) or not p for p in paths)
        or len(paths) != len(set(paths))
    ):
        raise ValueError("publication changed-path inventory is invalid")
    return scope, text


def _load_current(config: OrchestratorConfig, role: str, identity: dict) -> tuple[dict, str, dict]:
    from .evidence import _redact

    api = _api()
    path = api._trusted(config.coordination_root, _scope_path(config.coordination_root, identity))
    if not path.exists():
        raise api._Stop(
            "review_required", "A host-designated exhaustive publication review is required."
        )
    descriptor = _descriptor(path)
    scope, text = read_scope_artifact(config.coordination_root, descriptor)
    if scope["identity"] != identity:
        raise ValueError("publication designation differs from the current lease or checkout")
    raw, paths = _patch(Path(identity["worktree"]), scope["base_head"], identity["head"])
    if (
        _sha(raw.encode("utf-8")) != scope.get("git_diff_sha256")
        or _redact(raw, config) != text
        or paths != scope["paths"]
    ):
        raise ValueError("publication designation no longer matches the complete Git scope")
    return scope, text, descriptor


def validate_review_task(
    config: OrchestratorConfig, role: str, task: TaskItem, base_head: str, head: str
) -> tuple[dict, str, dict, int]:
    match = _TASK.fullmatch(task.task_id)
    if not match or match[1] != role or match[2] != head:
        raise ValueError("invalid host publication review task")
    api = _api()
    _, identity = api._checkpoint_identity(config, role, allow_pending=True)
    scope, text, descriptor = _load_current(config, role, identity)
    first = int(match[3])
    if (
        scope["base_head"] != base_head
        or identity["head"] != head
        or task != review_task(scope, first)
        or not 0 <= first < scope["layout"]["unit_count"]
    ):
        raise ValueError("publication task differs from its immutable host designation")
    pending = read_pending_result(config, role)
    if pending is not None and (
        pending.get("phase") != "review"
        or pending.get("role") != role
        or pending.get("task_id") != task.task_id
        or pending.get("pre_head") != head
        or pending.get("post_head") != head
    ):
        raise ValueError("publication review has an unrelated pending result")
    return scope, text, descriptor, first


def _part_path(descriptor: dict, first: int) -> Path:
    return Path(descriptor["path"]).parent / f"part-{first}.json"


def read_part_artifact(root: Path, scope_descriptor: dict, first: int) -> dict:
    scope, text = read_scope_artifact(root, scope_descriptor)
    path = _part_path(scope_descriptor, first)
    part = _api()._read(root, path)
    selection = part.get("selection")
    if (
        part.get("schema_version") != 1
        or part.get("kind") != "publication_review_part"
        or part.get("scope") != scope_descriptor
        or part.get("task") != asdict(review_task(scope, first))
        or not isinstance(selection, dict)
        or selection.get("first_unit") != first
    ):
        raise ValueError("publication part designation is invalid")
    expected = render_part(text, first, selection.get("next_unit"))
    if selection.get("text") != expected or selection != {
        "first_unit": first,
        "next_unit": selection["next_unit"],
        "text": expected,
        "sha256": _sha(expected.encode("utf-8")),
        "characters": len(expected),
    }:
        raise ValueError("publication part differs from its exact complete source units")
    return part


def _scope_context(scope: dict, selection: dict) -> str:
    return (
        "Host-designated publication review scope. This call covers only the selected "
        "complete units. Every other unit requires a separate accepted review before "
        "publication. The final tree is available for cross-file inspection. History "
        "before this observed baseline is not approved by this review.\n"
        + json.dumps(
            {
                "identity": scope["identity"],
                "base_head": scope["base_head"],
                "baseline_kind": scope["baseline"]["kind"],
                "complete_diff_sha256": scope["diff"]["sha256"],
                "changed_paths": scope["paths"],
                "total_units": scope["layout"]["unit_count"],
                "selected_first_unit": selection["first_unit"],
                "selected_next_unit": selection["next_unit"],
            },
            indent=2,
            sort_keys=True,
        )
    )


def review_evidence(
    config: OrchestratorConfig,
    role: str,
    task: TaskItem,
    base_head: str,
    head: str,
    *,
    candidate: dict | None = None,
) -> dict:
    scope, text, descriptor, first = validate_review_task(config, role, task, base_head, head)
    if candidate is None:
        selection = read_part_artifact(config.coordination_root, descriptor, first)["selection"]
    else:
        # Only a non-dispatch sizing caller supplies a candidate. Its exact
        # source range is still verified, and no authority is persisted here.
        selection = candidate
        selected = render_part(text, first, selection.get("next_unit"))
        if selection != {
            "first_unit": first,
            "next_unit": selection["next_unit"],
            "text": selected,
            "sha256": _sha(selected.encode("utf-8")),
            "characters": len(selected),
        }:
            raise ValueError("invalid publication sizing candidate")
    return {
        "text": selection["text"],
        "full_diff": text,
        "context": _scope_context(scope, selection),
    }


def prepare_part(config: OrchestratorConfig, role: str, state, context: str) -> dict:
    from .evidence import _DEFAULT_SECTION_LIMITS, EvidenceError, build_review_evidence

    task = state.task
    assert task is not None
    head = _api()._git(
        Path(config.backend_root if role == "backend" else config.mobile_root),
        "rev-parse",
        "HEAD",
    )
    scope, text, descriptor, first = validate_review_task(
        config, role, task, state.task_base_head, head
    )
    path = _part_path(descriptor, first)
    if path.exists():
        part = read_part_artifact(config.coordination_root, descriptor, first)
        bundle = build_review_evidence(config, role, state, task, context, _SIZING_INVOCATION_ID)
        if bundle.truncated:
            raise EvidenceError(
                "publication critical evidence exceeds current capacity: "
                + ", ".join(bundle.truncation_reasons)
            )
        return part
    capacity = sum(_DEFAULT_SECTION_LIMITS.values())
    for _ in range(4):
        selection = select_part(text, first, capacity)
        bundle = build_review_evidence(
            config,
            role,
            state,
            task,
            context,
            _SIZING_INVOCATION_ID,
            _publication_candidate=selection,
        )
        if not bundle.truncated:
            break
        if set(bundle.truncation_reasons) != {"task_diff"} or bundle.task_diff_capacity >= capacity:
            raise EvidenceError(
                "publication critical evidence exceeds current capacity: "
                + ", ".join(bundle.truncation_reasons)
            )
        capacity = bundle.task_diff_capacity
    else:
        raise EvidenceError("publication part sizing did not converge")
    root = config.coordination_root
    receipt = root / "validation" / f"{role}-{task.task_id}.json"
    checks = root / "evidence/supervisor-checks" / f"{role}-{task.task_id}.json"
    part = {
        "schema_version": 1,
        "kind": "publication_review_part",
        "scope": descriptor,
        "task": asdict(task),
        "selection": selection,
        "prepared_at": datetime.now(UTC).isoformat(),
        "validation": _descriptor(receipt),
        "coordination_checks": _descriptor(checks),
        "sizing": {
            "source_context_sha256": _sha(context.encode("utf-8")),
            "task_diff_capacity": bundle.task_diff_capacity,
        },
    }
    _save(root, path, _json(part))
    return read_part_artifact(root, descriptor, first)


def _check_input(
    config: OrchestratorConfig, scope: dict, part: dict, invocation: str, data: bytes
) -> None:
    from .evidence import _redact

    text = data.decode("utf-8").replace("\r\n", "\n")
    identity = scope["identity"]
    task = TaskItem(**part["task"])
    expected_identity = (
        "## identity\n"
        + "\n".join(
            (
                "role: " + identity["role"],
                "task_id: " + task.task_id,
                "invocation_id: " + invocation,
                "base_head: " + scope["base_head"],
                "head: " + identity["head"],
            )
        )
        + "\n\n## task\n"
    )
    expected_diff = "## task_diff\n" + part["selection"]["text"] + "\n\n## changed_file_contents\n"
    if (
        text.count(expected_identity) != 1
        or text.count(expected_diff) != 1
        or task.prompt not in text
        or _redact(_scope_context(scope, part["selection"]), config) not in text
    ):
        raise ValueError(
            "actual independent review input lacks its exact complete publication part"
        )


def record_review_result(
    config: OrchestratorConfig, role: str, state, result: AgentResult, *, persist: bool = True
) -> None:
    """Archive the actual durable result before normal acceptance/state consumption."""
    task = state.task
    assert task is not None
    api = _api()
    _, identity = api._checkpoint_identity(config, role, allow_pending=True)
    scope, _, descriptor, first = validate_review_task(
        config, role, task, state.task_base_head, identity["head"]
    )
    part = read_part_artifact(config.coordination_root, descriptor, first)
    pending = read_pending_result(config, role)
    if (
        not pending
        or _json(pending.get("result")) != _json(asdict(result))
        or pending.get("invocation_id") != result.invocation_id
    ):
        raise ValueError("publication review result has no exact durable invocation")
    from .model_blockers import read_supplied_input

    supplied, data = read_supplied_input(
        config, role, "review", task.task_id, result.invocation_id, pending.get("input_provenance")
    )
    _check_input(config, scope, part, result.invocation_id, data)
    root = config.coordination_root
    for key in ("validation", "coordination_checks"):
        if _descriptor(api._trusted(root, Path(part[key]["path"]))) != part[key]:
            raise ValueError("prepared publication evidence changed before review acceptance")
    attempt = {
        "schema_version": 1,
        "kind": "publication_part_result",
        "scope": descriptor,
        "part": _descriptor(_part_path(descriptor, first)),
        "result": asdict(result),
        "input_sha256": supplied["supplied_input"]["sha256"],
        "pending_result": pending,
    }
    path = (
        Path(descriptor["path"]).parent / "results" / str(first) / (result.invocation_id + ".json")
    )
    if persist:
        _save(root, path, _json(attempt))
        if result.outcome == "pass":
            _save(root, path.parent / "accepted.json", _json(_descriptor(path)))


def _accepted_parts(config: OrchestratorConfig, role: str, identity: dict):
    api = _api()
    scope, text, descriptor = _load_current(config, role, identity)
    ledger_path = api._trusted(
        config.coordination_root, config.coordination_root / "completed-tasks" / f"{role}.json"
    )
    if ledger_path.exists() and ledger_path.stat().st_size > 2_000_000:
        raise ValueError("completed task ledger exceeds its bound")
    records = CompletedTaskLedger(config.coordination_root).records(role)
    first, accepted, selections, gates = 0, [], [], []
    while first < scope["layout"]["unit_count"]:
        task = review_task(scope, first)
        path = _part_path(descriptor, first)
        matches = [r for r in records if r.get("task_id") == task.task_id]
        if len(matches) > 1:
            raise ValueError("publication part has ambiguous accepted review records")
        if not path.exists():
            if matches:
                raise ValueError("accepted publication part has no immutable assignment")
            return scope, descriptor, first, accepted, gates
        part = read_part_artifact(config.coordination_root, descriptor, first)
        if not matches:
            if (
                Path(descriptor["path"]).parent / "results" / str(first) / "accepted.json"
            ).exists():
                raise ValueError("accepted publication result lost its completion ledger record")
            return scope, descriptor, first, accepted, gates
        proof, gates, supplied, data = api._accepted_review(
            config, role, identity, matches[0], task, scope["base_head"]
        )
        _check_input(config, scope, part, matches[0]["invocation_id"], data)
        if (
            proof["validation_sha256"] != part["validation"]["sha256"]
            or proof["coordination_checks_sha256"] != part["coordination_checks"]["sha256"]
            or not api._stamp(scope["created_at"])
            <= api._stamp(part["prepared_at"])
            <= api._stamp(supplied["captured_at"])
        ):
            raise ValueError("accepted publication part differs from its prepared evidence")
        result_path = (
            Path(descriptor["path"]).parent
            / "results"
            / str(first)
            / (matches[0]["invocation_id"] + ".json")
        )
        if api._read(config.coordination_root, result_path.parent / "accepted.json") != _descriptor(
            result_path
        ):
            raise ValueError("publication accepted invocation pointer changed")
        attempt = api._read(config.coordination_root, result_path)
        result = attempt.get("result", {})
        if (
            attempt.get("scope") != descriptor
            or attempt.get("part") != _descriptor(path)
            or result.get("outcome") != "pass"
            or result.get("role") != role
            or result.get("task_id") != task.task_id
            or result.get("invocation_id") != matches[0]["invocation_id"]
            or attempt.get("input_sha256") != supplied["supplied_input"]["sha256"]
        ):
            raise ValueError("accepted publication part has no exact actual PASS result")
        accepted.append(
            {"part": _descriptor(path), "result": _descriptor(result_path), "proof": proof}
        )
        selections.append(part["selection"])
        first = part["selection"]["next_unit"]
    validate_parts(text, selections)
    expected_paths = {Path(item["part"]["path"]) for item in accepted}
    if set(Path(descriptor["path"]).parent.glob("part-*.json")) != expected_paths:
        raise ValueError("publication scope contains unaccounted part assignments")
    return scope, descriptor, None, accepted, gates


def accepted_publication_proof(config: OrchestratorConfig, role: str, identity: dict):
    scope, descriptor, missing, accepted, gates = _accepted_parts(config, role, identity)
    if missing is not None:
        raise _api()._Stop(
            "review_required",
            "The complete publication scope still requires an independent part review.",
        )
    proof = dict(accepted[-1]["proof"])
    proof["review_scope"] = "exhaustive_publication_parts"
    proof["publication_review"] = {
        "schema_version": 1,
        "kind": "accepted_exhaustive_publication_review",
        "scope": descriptor,
        "base_head": scope["base_head"],
        "head": identity["head"],
        "parts": accepted,
        "review_alias": "final_part_only",
    }
    return proof, gates


def designate_next_review(config: OrchestratorConfig, role: str, *, runner=None):
    """Host-only preparation request; network calls are read-only observations."""
    from .evidence import _redact
    from .handoff import role_lease_lock_path
    from .lifecycle import ControlPlaneLock

    api = _api()
    root = config.coordination_root
    with ControlPlaneLock(role_lease_lock_path(config, role)):
        with ControlPlaneLock(root / "control-plane.lock", timeout_seconds=10):
            api._active(config, role)
        repo, identity = api._checkpoint_identity(config, role)
        path = api._trusted(root, _scope_path(root, identity))
        if not path.exists():
            observation = api._remote_observation(runner or api._run, repo, identity)
            remote = observation["head"]
            api._review_intent_guard(config, identity, observation, runner or api._run)
            if remote:
                base = remote
                api._git(repo, "cat-file", "-e", f"{base}^{{commit}}")
                api._git(repo, "merge-base", "--is-ancestor", base, identity["head"])
                baseline = {"kind": "observed_remote_branch", "observation": observation}
            else:
                base_tip = api._git(
                    repo,
                    "rev-parse",
                    "--verify",
                    f"refs/heads/{identity['base_branch']}^{{commit}}",
                )
                bases = api._git(
                    repo, "merge-base", "--all", base_tip, identity["head"]
                ).splitlines()
                if len(bases) != 1 or not api._SHA.fullmatch(bases[0]):
                    raise ValueError("publication requires an unambiguous base-branch merge base")
                base = bases[0]
                baseline = {
                    "kind": "absent_remote_unique_base",
                    "base_branch_tip": base_tip,
                    "observation": observation,
                }
            raw, paths = _patch(repo, base, identity["head"])
            text = _redact(raw, config)
            layout = diff_layout(text)
            diff_path = path.parent / "complete.patch"
            _save(root, diff_path, text.encode("utf-8"))
            scope = {
                "schema_version": 1,
                "kind": "exhaustive_publication_review",
                "identity": identity,
                "base_head": base,
                "baseline": baseline,
                "created_at": datetime.now(UTC).isoformat(),
                "git_diff_sha256": _sha(raw.encode("utf-8")),
                "diff": _descriptor(diff_path),
                "paths": paths,
                "layout": layout,
            }
            with ControlPlaneLock(root / "control-plane.lock", timeout_seconds=10):
                api._active(config, role)
                _, current = api._checkpoint_identity(config, role)
                if current != identity:
                    raise ValueError("publication checkpoint changed during designation")
                _save(root, path, _json(scope))
        scope, _, missing, _, _ = _accepted_parts(config, role, identity)
        if missing is None:
            raise ValueError("publication review was requested after exhaustive acceptance")
        return review_task(scope, missing), scope["base_head"]
