# US-124 Background Rex Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make packaged Windows AskRex own a persistent, Electron-independent Rex Core plus signed-in-user Voice Agent that survive GUI close, auto-start at sign-in, expose truthful bounded health, and support a real screenless voice turn through the canonical Assistant/TurnEngine path.

**Architecture:** Add a small per-user background supervisor started by Windows Task Scheduler. The supervisor owns two separate managed-Python child processes: a loopback-only Rex Core RPC server that owns the canonical `Assistant`, and a Voice Agent that owns microphone/speaker/wake-word/STT/TTS and uses an `Assistant`-compatible proxy to send voice turns to Core. Electron is only a bootstrap/status/control surface: it may ensure the scheduled task exists and start the supervisor, but the background processes are detached and do not depend on a renderer window or Electron lifetime.

**Tech Stack:** Python 3.11 stdlib (`asyncio`, `socket`, `subprocess`, `secrets`, `json`, `pathlib`, `msvcrt`/`fcntl`), existing `rex.assistant.Assistant`, `rex.runtime.invocation.turn_invocation`, canonical `rex.voice_loop.build_voice_loop`, Electron/TypeScript main process, Windows `schtasks.exe`, existing managed Python runtime and GitHub Actions packaging gates.

**Spec:** `docs/architecture/end-user-installation-and-voice-runtime.md`

## Global Constraints

- The desktop/mobile apps are control and interaction surfaces, not runtime lifecycle owners.
- Core remains authoritative for orchestration, identity, permissions, memory, context, models, tools, integrations, scheduling, verification, and final responses.
- Voice Agent owns interactive-user audio only; do not put microphone ownership in a Windows service/system session.
- Use the existing managed Python 3.11 packaged runtime; no new paid service and no new external runtime dependency.
- OpenClaw remains optional. Its absence must never block Core/Voice-Agent startup.
- Health/status payloads must be content-free: no microphone audio, transcript, prompt, memory, credential, tool result, or raw user identity.
- Use canonical `Assistant`/TurnEngine and canonical voice-loop code. Do not create a second assistant pipeline, permission system, verification system, or room/user store.
- One repeated failing component may be retried only with bounded backoff; do not create an infinite restart loop that presents false health.
- Windows startup commands must use absolute packaged paths. Normal users must not need a terminal or source checkout.
- US-124 may prove background lifecycle and one local screenless voice path; first-run calibration UX, room pairing, privacy-control UI, and final physical clean-install acceptance remain US-125 through US-130 unless explicitly required by a US-124 acceptance criterion.

---

## File Structure

### New Python package: `rex/background/`

- `rex/background/types.py` — content-free lifecycle enums/dataclasses and JSON serialization.
- `rex/background/paths.py` — canonical runtime state paths beneath `ASKREX_RUNTIME_DIR` / packaged userData.
- `rex/background/lock.py` — stdlib single-instance file lock with Windows and POSIX implementations.
- `rex/background/core_server.py` — loopback Core request server that owns one canonical `Assistant` for the resolved user.
- `rex/background/core_client.py` — small authenticated JSON-lines client and `CoreAssistantProxy` used by the voice loop.
- `rex/background/voice_agent.py` — canonical Voice Agent entrypoint; builds `build_voice_loop()` around the proxy and owns only audio/wake/STT/TTS.
- `rex/background/supervisor.py` — owns Core + Voice Agent subprocess lifecycle, bounded restart policy, and aggregate health snapshot.
- `rex/background/windows_startup.py` — deterministic Task Scheduler command construction/registration/removal using absolute managed-runtime paths.
- `rex/background/cli.py` — internal packaged entrypoint for `supervisor`, `core`, `voice-agent`, `status`, `stop`, `install-startup`, `remove-startup`.
- `rex/background/__init__.py` — narrow public exports only.

### Existing files to modify

- `rex/cli.py` + `rex/commands/core.py` (or a focused new `rex/commands/background.py`) — register a developer/operator `rex background` command without changing normal default chat behavior.
- `gui/src/main/backgroundRuntime.ts` — resolve absolute packaged Python/runtime paths, ensure scheduled startup, and launch the supervisor detached when needed.
- `gui/src/main/index.ts` — call background bootstrap after identity resolution; Electron close remains UI-only.
- `gui/src/main/tray.ts` — do not stop Core/Voice Agent when the user closes the window; retain explicit GUI quit semantics without claiming background Rex is stopped.
- `gui/src/main/bridgeResolver.ts` — expose packaged managed-Python/runtime-root path helpers instead of duplicating path logic.
- `gui/package.json` — keep Voice runtime profile and ensure packaged runtime includes the background package through the wheel; no additional npm dependency.
- `scripts/verify_electron_package_contents.py` — assert the packaged runtime contains `pythonw.exe`, AskRex background modules, and Voice dependencies required by the agent.
- `PRD-production-readiness.md` and `docs/archive/progress/progress-production-readiness.txt` — record only verified US-124 evidence.
- `README.md`, `INSTALL.md`, `RUNNING.md`, `SURFACE-CLASSIFICATION.md`, `CLAUDE.md` — update wording only after behavior is proven.

### Tests

- `tests/background/test_types.py`
- `tests/background/test_lock.py`
- `tests/background/test_core_protocol.py`
- `tests/background/test_supervisor.py`
- `tests/background/test_windows_startup.py`
- `tests/background/test_voice_agent.py`
- `gui/tests/backgroundRuntime.test.ts`
- extend packaged-artifact verifier tests where they already live.

---

### Task 1: Content-free lifecycle model, paths, and duplicate-start lock

**Files:**
- Create: `rex/background/__init__.py`
- Create: `rex/background/types.py`
- Create: `rex/background/paths.py`
- Create: `rex/background/lock.py`
- Create: `tests/background/test_types.py`
- Create: `tests/background/test_lock.py`

**Interfaces:**
- Produces `HealthState(str, Enum)` values: `ready`, `paused`, `degraded`, `unavailable`, `failed`, `starting`, `stopped`.
- Produces `ComponentHealth(component: str, state: HealthState, detail_code: str | None, observed_at: float, pid: int | None)`.
- Produces `RuntimeHealth(core: ComponentHealth, voice_agent: ComponentHealth, supervisor_pid: int, observed_at: float)`.
- Produces `BackgroundPaths.from_runtime_root(Path)` with `state_dir`, `core_endpoint_file`, `health_file`, `stop_file`, `supervisor_lock`.
- Produces `SingleInstanceLock(path: Path)` context manager; a second live acquisition raises `AlreadyRunningError`.

- [ ] **Step 1: Write failing lifecycle serialization tests**

```python
from rex.background.types import ComponentHealth, HealthState


def test_component_health_wire_payload_is_content_free():
    health = ComponentHealth(
        component="voice_agent",
        state=HealthState.DEGRADED,
        detail_code="microphone_unavailable",
        observed_at=10.0,
        pid=321,
    )
    assert health.to_dict() == {
        "component": "voice_agent",
        "state": "degraded",
        "detail_code": "microphone_unavailable",
        "observed_at": 10.0,
        "pid": 321,
    }
    assert "transcript" not in health.to_dict()
    assert "user_id" not in health.to_dict()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pytest -q tests/background/test_types.py`
Expected: import failure because `rex.background` does not exist.

- [ ] **Step 3: Implement lifecycle types and canonical paths**

Use frozen dataclasses and explicit `to_dict()` methods. `BackgroundPaths.from_runtime_root()` must resolve the supplied path and create no files as a side effect.

- [ ] **Step 4: Write failing duplicate-lock tests**

```python
from rex.background.lock import AlreadyRunningError, SingleInstanceLock


def test_second_lock_is_rejected(tmp_path):
    path = tmp_path / "runtime.lock"
    with SingleInstanceLock(path):
        with pytest.raises(AlreadyRunningError):
            with SingleInstanceLock(path):
                pass
```

Add a subprocess-based test proving the lock releases after process exit; do not rely only on two lock objects in one process.

- [ ] **Step 5: Implement stdlib cross-platform lock**

On Windows use `msvcrt.locking()` against a one-byte lock file; on POSIX use `fcntl.flock(..., LOCK_EX | LOCK_NB)`. Keep the file handle alive for lock ownership and release in `__exit__`/`close()`.

- [ ] **Step 6: Run focused tests**

Run: `pytest -q tests/background/test_types.py tests/background/test_lock.py`
Expected: PASS.

- [ ] **Step 7: Commit**

Commit message: `feat(runtime): add background lifecycle primitives`

---

### Task 2: Authenticated loopback Rex Core protocol

**Files:**
- Create: `rex/background/core_server.py`
- Create: `rex/background/core_client.py`
- Create: `tests/background/test_core_protocol.py`

**Interfaces:**
- `CoreEndpoint(host: str, port: int, token: str, pid: int)` stored in `core_endpoint_file` with mode-restricted best effort and atomic replace.
- `CoreServer(assistant_factory, user_id, paths, host="127.0.0.1", port=0)`.
- Requests are one JSON object per TCP connection, capped at 1 MiB, with fields `token`, `type`, and type-specific payload.
- Supported request types in US-124: `health`, `turn`, `shutdown`.
- `turn` accepts `transcript: str`, `voice_mode: bool`, and optional content-free `origin_device_id`; Core calls canonical `Assistant.generate_reply()` under `turn_invocation(TurnSource.VOICE, device_id=origin_device_id)`.
- `CoreClient.from_endpoint_file(path)`.
- `CoreAssistantProxy(user_id, client, origin_device_id=None)` exposes async `generate_reply(transcript, *, voice_mode=False, **kwargs) -> str` so existing `VoiceLoop` can use it without changes to the turn pipeline.

- [ ] **Step 1: Write protocol RED tests**

Cover:
1. invalid token -> generic unauthorized error and no Assistant call;
2. oversized/malformed request -> bounded failure;
3. `health` -> content-free ready payload;
4. `turn` -> exactly one `Assistant.generate_reply()` call with `voice_mode=True` and VOICE invocation provenance;
5. proxy returns Core reply;
6. endpoint metadata never contains transcript/user ID.

- [ ] **Step 2: Run RED**

Run: `pytest -q tests/background/test_core_protocol.py`
Expected: import failures for missing server/client.

- [ ] **Step 3: Implement minimal loopback protocol**

Use stdlib `asyncio.start_server()` or `socketserver`; bind only `127.0.0.1`. Generate `secrets.token_urlsafe(32)` per Core start. Write endpoint metadata atomically after the listener is ready; delete it during clean shutdown if it still belongs to this PID/token.

Do not serialize TurnEvents, credentials, memory, tool results, or Assistant internals onto this private lifecycle channel. US-124 needs only the final spoken response string for the local Voice Agent.

- [ ] **Step 4: Run focused protocol tests**

Run: `pytest -q tests/background/test_core_protocol.py`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat(runtime): add local Rex Core protocol`

---

### Task 3: Voice Agent process using the canonical voice loop

**Files:**
- Create: `rex/background/voice_agent.py`
- Create: `tests/background/test_voice_agent.py`
- Modify only if required: `rex/voice_loop.py` and `rex/voice/builder.py` — prefer no changes; `CoreAssistantProxy` is designed to satisfy the existing assistant seam.

**Interfaces:**
- `build_voice_agent(user_id: str, paths: BackgroundPaths, *, activation_mode="wake-word", origin_device_id=None)`.
- The Voice Agent resolves the current Core endpoint, constructs `CoreAssistantProxy`, and passes it to canonical `build_voice_loop()`.
- Audio initialization failures map to content-free health codes such as `microphone_unavailable`, `speaker_unavailable`, `wakeword_unavailable`, or `core_unavailable`.
- A missing OpenClaw dependency/configuration is not a Voice Agent startup error.

- [ ] **Step 1: Write RED tests**

Use fakes; do not require physical audio in unit tests.

```python
async def test_voice_agent_builds_canonical_loop_with_core_proxy(monkeypatch, tmp_path):
    ...
    assert captured["assistant"].user_id == "james"
    assert captured["activation_mode"] == "wake-word"
```

Add tests proving Core-unavailable maps to `degraded`, and microphone initialization failure does not mutate Core state.

- [ ] **Step 2: Run RED**

Run: `pytest -q tests/background/test_voice_agent.py`
Expected: missing module failure.

- [ ] **Step 3: Implement Voice Agent entrypoint**

Load runtime config through existing config APIs, resolve exact user identity passed by the supervisor, build the canonical voice loop, and await `loop.run()`. Do not instantiate a local `Assistant`; all response generation must cross `CoreAssistantProxy`.

- [ ] **Step 4: Run focused voice-agent tests plus canonical voice regressions**

Run: `pytest -q tests/background/test_voice_agent.py tests/test_voice_loop*.py`
Expected: PASS with physical-audio markers still skipped according to existing policy.

- [ ] **Step 5: Commit**

Commit message: `feat(runtime): add background Voice Agent`

---

### Task 4: Supervisor, bounded restart, health snapshot, and internal CLI

**Files:**
- Create: `rex/background/supervisor.py`
- Create: `rex/background/cli.py`
- Create: `tests/background/test_supervisor.py`
- Create: `rex/commands/background.py`
- Modify: `rex/cli.py`

**Interfaces:**
- `ComponentSpec(name, argv, required, max_restarts=3, restart_window_seconds=60.0)`.
- `RuntimeSupervisor(paths, core_spec, voice_spec, poll_interval=0.25)`.
- Supervisor state machine:
  - acquire single-instance lock;
  - remove stale stop/endpoint files that belong to dead processes;
  - start Core;
  - wait bounded time for Core endpoint/health;
  - start Voice Agent;
  - write aggregate `RuntimeHealth` atomically;
  - restart a crashed child only within bounded policy;
  - repeated failure -> `failed` for that component without pretending healthy;
  - Core failure degrades Voice Agent to `core_unavailable`; Voice Agent failure leaves Core running;
  - stop request terminates Voice Agent then Core and exits cleanly.
- `python -m rex.background.cli supervisor --runtime-root ... --user ...`
- `python -m rex.background.cli status --runtime-root ...` returns machine-readable JSON with no private content.
- `rex background ...` is developer/operator convenience only; packaged startup invokes the module directly with absolute paths.

- [ ] **Step 1: Write supervisor RED tests**

Cover GUI-independent process lifetime using fake child processes:
1. duplicate supervisor start is rejected;
2. Core starts before Voice Agent;
3. Voice crash restarts without Core restart;
4. repeated Voice crash becomes failed after bounded attempts;
5. Core crash causes Voice degradation and Core bounded restart;
6. stop is Voice-first then Core;
7. aggregate health contains only allowed fields.

- [ ] **Step 2: Run RED**

Run: `pytest -q tests/background/test_supervisor.py`
Expected: missing supervisor failure.

- [ ] **Step 3: Implement supervisor and CLI**

Use `subprocess.Popen` with `close_fds=True`. Packaged Windows launch paths are supplied by the startup layer; the supervisor itself must not guess repository paths. Signal handling should set the same orderly-stop event used by the stop-file path.

- [ ] **Step 4: Run lifecycle suite**

Run: `pytest -q tests/background`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat(runtime): supervise Core and Voice Agent`

---

### Task 5: Windows automatic startup and Electron bootstrap independent of window lifetime

**Files:**
- Create: `rex/background/windows_startup.py`
- Create: `tests/background/test_windows_startup.py`
- Create: `gui/src/main/backgroundRuntime.ts`
- Create: `gui/tests/backgroundRuntime.test.ts`
- Modify: `gui/src/main/bridgeResolver.ts`
- Modify: `gui/src/main/index.ts`
- Modify: `gui/src/main/tray.ts`

**Interfaces:**
- `build_schtasks_create_command(task_name, pythonw_path, runtime_root, user_id) -> list[str]`.
- Task trigger is `ONLOGON` for the signed-in user, because interactive audio must run in that user session.
- Task action uses absolute packaged `pythonw.exe` and absolute runtime root; no shell script, current working directory, repo checkout, or PATH Python.
- `install_startup(...)`, `remove_startup(...)`, and `query_startup(...)` wrap `schtasks.exe` via argument arrays, never `shell=True`.
- Electron `ensureBackgroundRuntime(sessionIdentity)`:
  - packaged Windows only;
  - resolves managed `python.exe`/`pythonw.exe` from `process.resourcesPath/python` and userData runtime root;
  - calls the internal Python startup-registration command;
  - starts supervisor detached only when status reports it is not already running;
  - calls `child.unref()` so Electron/window closure is not ownership.
- Dev Electron does not silently install a Windows task.

- [ ] **Step 1: Write Windows command-construction RED tests**

Assert exact argument-array properties rather than localized `schtasks` stdout:
- absolute `pythonw.exe` path required;
- absolute runtime root required;
- user ID quoted as data in the task action, not interpolated into a shell command;
- `ONLOGON` trigger;
- no `shell=True` path.

- [ ] **Step 2: Run RED**

Run: `pytest -q tests/background/test_windows_startup.py`
Expected: missing module failure.

- [ ] **Step 3: Implement startup registration**

Fail closed on non-Windows for mutation commands; query/build helpers remain unit-testable cross-platform.

- [ ] **Step 4: Write Electron RED tests**

Test:
1. packaged Windows resolves `resources/python/pythonw.exe` and absolute userData runtime root;
2. dev mode never registers Task Scheduler;
3. spawned supervisor uses `detached: true`, `windowsHide: true`, and is `unref()`ed;
4. closing/hiding the BrowserWindow never invokes a background-runtime stop path;
5. explicit GUI Quit does not falsely log/claim that Core/Voice Agent stopped.

- [ ] **Step 5: Implement Electron bootstrap**

Call bootstrap after `resolveElectronSessionIdentity()` succeeds and before renderer availability is treated as proof of Rex readiness. Bootstrap failure is logged as a background-runtime degraded condition; it must not prevent text GUI startup.

- [ ] **Step 6: Run GUI checks**

Run: `cd gui && npm test -- --runInBand` if supported by the current Vitest config; otherwise `cd gui && npm test`.
Run: `cd gui && npm run typecheck && npm run build`.
Expected: PASS.

- [ ] **Step 7: Commit**

Commit message: `feat(runtime): bootstrap background Rex on Windows`

---

### Task 6: Packaged-artifact proof, documentation truth, and US-124 evidence

**Files:**
- Modify: `scripts/verify_electron_package_contents.py`
- Modify/add existing verifier tests under `tests/`
- Modify: `PRD-production-readiness.md`
- Modify: `docs/archive/progress/progress-production-readiness.txt`
- Modify: `README.md`
- Modify: `INSTALL.md`
- Modify: `RUNNING.md`
- Modify: `SURFACE-CLASSIFICATION.md`
- Modify: `CLAUDE.md`
- Modify only if needed: `.github/workflows/electron-smoke.yml` or the existing Windows packaged-artifact workflow

**Interfaces / evidence:**
- Artifact verifier must prove `resources/python/pythonw.exe` exists, `rex.background` imports from the managed runtime, and the Voice profile still contains the canonical audio/STT dependencies.
- Add a packaged-runtime lifecycle smoke that launches the built managed runtime from its absolute artifact path in a test mode using fake Core/Voice child commands; it must prove duplicate-start prevention, detached supervisor survival after the Electron smoke process exits, status readability, and orderly stop.
- Physical microphone/wake-word/reboot evidence is **not** fabricated here; document it as still required by US-130 unless the CI environment actually provides the needed hardware/sign-in behavior.

- [ ] **Step 1: Write artifact-verifier RED assertions**

Require background modules and `pythonw.exe`; run verifier against a fixture missing one required item and prove failure.

- [ ] **Step 2: Implement artifact verifier and Windows smoke**

The smoke must use the packaged managed runtime path, not machine Python. Child component fakes may be used for CI lifecycle mechanics; label this evidence `packaged Windows artifact / deterministic child fakes`, not physical voice evidence.

- [ ] **Step 3: Run full relevant validation locally/CI equivalent**

Run at minimum:

```text
pytest -q tests/background
pytest -m "not slow and not audio and not gpu"
ruff check .
black --check --diff rex/ tests/ bridge/ *.py
mypy rex --ignore-missing-imports
cd gui && npm test
cd gui && npm run typecheck
cd gui && npm run build
python scripts/security_audit.py
```

Then run the existing Windows packaged Electron workflow on the exact PR head.

- [ ] **Step 4: Update docs conservatively**

Only claim what the branch proves. Required wording boundary:
- background runtime lifecycle is implemented/tested if the artifact test passes;
- wake-word remains beta until physical audio acceptance passes;
- no claim that clean-install/reboot/screenless release gate is complete unless US-130 evidence exists.

Mark US-124 acceptance boxes only where exact code/tests/artifact evidence supports them. If a criterion still depends on physical hardware, leave it unchecked and state the blocker explicitly rather than weakening the criterion.

- [ ] **Step 5: Commit**

Commit message: `docs(runtime): record US-124 verification evidence`

- [ ] **Step 6: Open/update PR and independently verify exact-head checks**

Create a draft PR targeting `master`. After all checks are green on the exact final head, perform an independent diff review against the spec and US-124 criteria. Do not merge a partially-proven story merely because unit tests pass.

---

## Plan Self-Review

### Spec coverage

- GUI-close independence: Tasks 4–5.
- Explicit Core + Voice Agent lifecycle ownership: Tasks 2–4.
- Automatic Windows reboot/sign-in startup with absolute packaged paths: Task 5 plus artifact proof in Task 6.
- Bounded health states: Tasks 1 and 4.
- Graceful component degradation and OpenClaw independence: Tasks 3–4.
- GUI-close survival, orderly shutdown, restart, duplicate-start, degraded behavior tests: Task 4 and Task 5.
- Real installed-path/package evidence: Task 6.
- Documentation truth and `CLAUDE.md`: Task 6.
- Canonical Assistant/TurnEngine and voice-loop reuse: Tasks 2–3.

### YAGNI / deferred deliberately

This plan does **not** add multi-room pairing, new Home Assistant entity mapping, custom satellite hardware, iPhone always-on listening, a new model/provider system, a second permission store, or a new streaming protocol. Those do not solve US-124 and belong to later stories.
