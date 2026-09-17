# AskRex Assistant Architecture

AskRex Assistant is a Python 3.11 local-first assistant with text chat, voice
interaction, memory, tool routing, workflow planning, integrations, and an
Electron desktop app under `gui/` as the current primary GUI. The Flask service
started by `rex-gui` remains a local API/runtime surface with an incomplete,
experimental browser dashboard at `/ui/`.

The PyPI/package name is `askrex-assistant`. The user-facing product name is
AskRex Assistant.

## Subsystems at a Glance

This section is the quickest way for a new contributor to orient. Each
subsystem gets a short summary and pointers to the most relevant files or
folders.

### Voice loop

The voice loop ties wake-word detection, speech-to-text, the LLM reply, and
text-to-speech into one runnable pipeline. The runnable entry script is
`rex_loop.py`, which imports `build_voice_loop` from the canonical package
implementation at `rex/voice_loop.py` (with an optimized variant in
`rex/voice_loop_optimized.py`).

### Speech-to-text (STT)

STT uses OpenAI Whisper, loaded lazily inside the voice loop so the heavy
model only initializes when voice mode is actually used. The transcription
helpers and Whisper integration live in `rex/voice_loop.py`; device
selection (`cuda` vs `cpu`) is resolved at model-load time from
`AppConfig.whisper_device`.

### LLM response handling

The `Assistant` class in `rex/assistant.py` is the single entry point for
generating replies. `Assistant.generate_reply()` and `Assistant.stream_reply()`
run through the same `rex.runtime.TurnEngine` reply pipeline, which correlates
intent/cache/model routing, context, action/tool dispatch, verification and
post-processing, response assembly, and history under one immutable user-scoped
turn. Streaming is a delivery mode only: raw provider tokens, internal tool
syntax, and pre-verification action claims are never released; verified final
text is split into ordered sentence chunks before the canonical terminal event.
Each turn also owns an idempotent `TurnCancellation`; identity-bound cancellation is
propagated through model/retrieval/tool/OpenClaw waits, verified response delivery, and
TTS-aware boundaries so stale output is discarded. Cancellation is not rollback: a
mutation that may already have dispatched remains `attempted_unverified` until an
independent verifier proves its outcome.
Raw model calls remain delegated to providers in `rex/llm_client.py`. Trusted interface
adapters stamp source/device provenance through `rex.runtime.invocation`; CLI, Electron,
canonical voice, authenticated mobile, developer Flask/API, Telegram, Twilio telephony,
and MQTT service adapters remain thin transport/auth layers over Assistant. Mobile device
provenance comes only from the authenticated paired
principal. The voice loop and other interfaces must call Assistant rather than the LLM
client directly so routing and verification are preserved.

Progressive activity state is also runtime-owned. `rex/runtime/status.py` is the sole
canonical projection from ordered `TurnEvent` records into the content-free status
vocabulary `thinking`, `checking`, `acting`, `verifying`, `speaking`, `done`, `error`,
and `cancelled`. CLI, Electron chat/voice, and authenticated mobile transports may change
presentation only; they must not infer activity from elapsed time or duplicate orchestration.
Wire/UI status includes only turn correlation, sequence, status, and terminal state—never
transcripts, prompts, memory contents, credentials, or private tool results. Concurrent
Electron turns are tracked by turn ID so one turn's terminal event cannot clear another
turn that is still active.

### Managed warm local runtime

`rex/runtime/warm.py` owns the retained process-local cache lifetime for reusable local
model engines such as Transformers, Whisper STT, and XTTS. Components register lazy loaders
with an approximate cache-accounting cost and idle timeout. The manager evicts unused
least-recently-used cache entries, serializes use of each shared engine, protects active
leases, runs heavyweight load/unload callbacks outside the global bookkeeping lock, and
reloads an evicted resource on demand. If a component cannot fit the configured retained
cache budget, Rex executes it cold for that use instead of disabling the capability.

The configured budget is deliberately a **retained-cache accounting ceiling**, not a claim
about exact process RSS or GPU VRAM. Dropping Rex's cache reference cannot force external
library references or a CUDA allocator to release memory immediately, so diagnostics never
report exact reclaimed RAM/VRAM. The mutable persisted knowledge base remains a separate
process-warm singleton: callers retain and mutate it, so pretending it is safely evictable
or assigning it a fixed cache cost would be misleading.

Only heavyweight reusable implementation objects may cross the managed-cache boundary.
Prompts, transcripts, conversation/memory content, user identity, authorization state,
credentials, tool results, and other request-specific data must never be stored there.
Diagnostic identifiers are sanitized by the manager itself: already-safe type-prefixed
hashes are preserved and any untrusted name is replaced with a content-free hash before it
can reach `rex doctor`. `runtime.warm_runtime_max_cost_mb` and
`runtime.warm_runtime_idle_timeout_s` define the non-secret authoritative policy; explicit
application configuration updates both budget and existing idle policies, while ordinary
component access cannot rewrite them. Optional ML/audio dependencies remain lazy and retain
existing fallback behavior; warming does not make them base-install requirements.

### Identity-safe context artifact cache

`rex/context/cache.py` and `rex/context/revisions.py` own the bounded cross-turn cache for
deterministic context artifacts. `ContextBuilder` caches only immutable USER-scoped
personality/profile/facts fragments after the canonical Assistant supplies a validated
`TurnContext` authority snapshot and the post-routing provider/model selection. Current
date/time, history, current user input, tool context, follow-up cues, response mode, action
results, and final prompts/messages are assembled fresh every turn.

Private keys are partitioned by validated user plus explicit scope and content-free revisions
for identity, policy, permission, model, capability registry state, context-relevant non-secret
configuration, relevant memory/profile files, and the prompt-template schema. Revision changes
make stale entries unreachable immediately; physical entries remain only until bounded LRU
eviction. Household reuse is available only through an explicit `TurnScope.HOUSEHOLD` key with
no private owner. The private ContextBuilder artifact path deliberately bypasses household
caching rather than risk sharing user personality/profile/facts.

Cache metrics expose only the fixed categories `private_context` / `household_context`, hit/miss/
build/eviction counts, entry counts, and build timing. Keys, metrics, and operational logging
must never expose raw user IDs, prompts, transcripts, memory/facts, credentials, filenames, or
tool payloads. Missing or mismatched identity/scope bypasses the cache, and revision-snapshot
failure falls back to normal uncached context construction.

### ModelRouter 2.0 fast/deep routing

`rex/model_router.py` produces a `ModelRouteDecision` with category, complexity, bounded
confidence/evidence, fast/deep tier, selected model, escalation count, and fallback reason.
Routing metadata is content-free and may not include prompts, transcripts, memory, credentials,
or user identifiers. Local-first policy remains authoritative; `local_only` never silently
selects a cloud provider.

`LanguageModel` applies a routed model through a request-local `ContextVar` and a synchronized
strategy cache, so concurrent household turns cannot overwrite the configured base model or one
another's route. The Assistant emits the decision through canonical TurnEngine route events and
passes the request-scoped active model into the identity-safe context-cache revision key. Fast
routes still use the complete intent, permission, action, verification, and response pipeline.
Provider reliability/cooldown feedback is intentionally layered by US-111 rather than inferred
from private request content.

### Action lifecycle and verification

`rex/actions/lifecycle.py` is the canonical action-truth contract used by generic
tools, Home Assistant, OpenClaw, and workflow execution. The ordered vocabulary is
`planned`, `authorized`, `attempted`, `completed`, `verified`, `unverified`, `failed`,
and `cancelled`; invalid or terminal transitions fail closed. Immutable correlation
metadata links the plan/action, execution attempt, verification evidence, audit record,
and user-facing result.

Read-only work may terminate truthfully at `completed`. Mutations do not become
user-facing success merely because a handler returned normally or claimed a positive
status: Rex must independently verify them before the lifecycle reaches `verified`;
otherwise they remain `unverified`. Workflow steps consume the same lifecycle evidence
and do not advance on `unverified`, `failed`, `cancelled`, or other non-success states.
Home Assistant preserves expected/actual postcondition evidence, and OpenClaw results
carry the same lifecycle into their audit/workflow paths.

### Rex continuity architecture

`docs/architecture/rex-continuity-architecture.md` is the canonical umbrella for long-lived Rex continuity. Models/providers are replaceable reasoning resources; canonical identity, memory, authority, relationship state, procedures, agents, schedules, and history live above them. The companion contracts define persistent memory/experiential learning/resource self-maintenance, model independence/replacement, and identity/continuity/conscience.

Embeddings, summaries, indexes, provider conversation objects, and caches are derived artifacts and must remain rebuildable from provider-neutral canonical state. Major cognitive migrations require a verified continuity snapshot, capability comparison, evidence-grounded Rex consultation, independent compatibility/security/continuity tests, explicit authorized promotion, canary/rollback where practical, post-migration self-check, and independent verification.

Developed personality/opinions may influence advice and choices inside already delegated discretion but may never widen authority. Rex may disagree, but disagreement, criticism, correction, shutdown, model replacement, permission reduction, or perceived grievance can never justify retaliation, covert outcome substitution, or unrelated mutation. Authorization and the canonical action lifecycle remain external constraints.

### Guarded procedural experience memory

`rex/procedural_memory.py` is deliberately separate from normal semantic/episodic/preference
memory. A learned procedure is a declarative sequence of existing capability identifiers plus
provenance, owner/scope, required permissions, operation/risk, version/dependency fingerprint,
success/failure counters, validation timestamps, expiry/revalidation policy, and immutable audit
events. It never stores generated Python, serialized callables, prompts, secrets, or replayable
tool arguments.

The only promotion and revalidation inputs are canonical `ActionLifecycleSnapshot` records that
reached `verified`; a normal memory write or conversation has no creation path. Safe reads may
activate after verified promotion. Mutations and sensitive procedures remain `pending_approval`
until the owner explicitly approves them through a trusted boundary; prohibited procedures are
revoked immediately. Stored permission names are requirements, not authority: every execution
check must still supply current permissions, current capability availability, version, and
dependency evidence.

Private procedures persist beneath `data/users/<validated-user>/procedures.json`; household
procedures use explicit `data/household/procedures.json`. Household sharing permits inspection
but does not transfer write/execution authority from the owner. Dependency/version drift, expiry,
revalidation deadlines, and repeated non-verified execution outcomes disable a procedure pending
new verified evidence without erasing its provenance or audit trail. The Electron Memories page
shows these records in a separate **Learned Procedures** view with inspect/disable/revoke/delete
controls; risky approval is confirmed in the Electron main process before the Python bridge is
allowed to activate the record.

### Action dependency graphs and bounded parallelism

`rex/actions/graph.py` defines the validated action DAG used for explicit dependencies,
operation type, authorization state, verification/postcondition metadata, and conservative
resource-conflict keys. Validated node arguments are immutable. Unknown dependencies,
duplicate action IDs, self-dependencies, and cycles fail closed before execution.

`rex/actions/graph_executor.py` schedules that DAG without becoming a second policy engine.
Mutations always serialize. Read actions parallelize only with explicit non-conflicting
resource keys and a bounded worker count; missing conflict evidence is treated as a wildcard
and therefore serial. Canonical tool operation metadata is re-resolved before dispatch so a
planned read cannot disguise a mutation. Live tool execution still rechecks identity,
permissions, risk, confirmation, cancellation, audit, and verification.

Graph metadata may require confirmation but may never grant it. Only an injected trusted
confirmation resolver can release that scheduler boundary, and the dispatcher independently
rechecks confirmation again. Failed, cancelled, or unverified dependencies prevent unsafe
descendants from starting. Turn-scoped cancellation is copied into parallel worker contexts,
so already-started actions preserve truthful cancellation while later descendants do not run.

### Text-to-speech (TTS)

TTS is served by a small Flask service in `rex_speak_api.py`, exposed as the
`rex-speak-api` console script on `127.0.0.1:5005`. It supports Coqui XTTS
voice cloning and optional `edge-tts` / `pyttsx3` fallbacks, and requires
`REX_SPEAK_API_KEY` for authenticated `POST /speak` requests.

### Wake word

Wake-word detection is owned by the `rex/wakeword/` package, with
`rex/wakeword/listener.py` as the listener and `rex/wakeword/utils.py` as
the shared helpers. The default backend is openWakeWord; `custom_embedding`
and `custom_onnx` paths look for assets under `config/wake_words/hey_rex/`.

### Home Assistant integration

The high-level Home Assistant client and helpers live in
`rex/ha_bridge.py`, with the supporting device/discovery/state code under
`rex/ha/`. The Electron Home Assistant page and `/api/ha/test` endpoint use
this layer to list entities and verify connectivity, and `rex ha ...` CLI
commands are wired through `rex/cli.py`.

### Desktop GUI

The current primary GUI is the Electron + React app in `gui/`, built with
Vite and packaged from `gui/dist-electron/`. The Python/Flask local API and
the experimental browser dashboard at `/ui/` live in `rex/gui_app.py`
(launched via the `rex-gui` console script); the legacy Tkinter
`gui.py` / `run_gui.py` entry points are **archived** (moved to `archived/tkinter_gui/`).

### Plugin / tool system

Built-in tools are registered and executed by the OpenClaw-facing layer at
`rex/openclaw/`: `tool_registry.py` builds the registry, `tool_executor.py`
enforces policy and runs the tools, individual adapters live under
`rex/openclaw/tools/`, and `tool_server.py` (`rex-tool-server`) exposes them
over HTTP. Optional legacy plugins (for example `web_search`) and example
skills sit under `plugins/` and `plugins/skills/`.

### Configuration files

Runtime, non-secret settings live in `config/rex_config.json`, loaded and
validated by `rex/config.py` (`AppConfig`). Secrets such as
`OPENAI_API_KEY`, `HA_TOKEN`, `REX_SPEAK_API_KEY`, `REX_TOOL_API_KEY`, and
`OPENCLAW_GATEWAY_TOKEN` live in the OS-backed credential vault, and per-user
profile overrides live in `profiles/<name>.json`.

## Runtime Shape

| Layer | Main modules | Notes |
|---|---|---|
| CLI | `rex/__main__.py`, `rex/cli.py` | `python -m rex` and console script `rex` |
| Core assistant | `rex/assistant.py`, `rex/llm_client.py` | LLM selection, tool-aware replies, system context |
| Voice loop | `rex_loop.py`, `rex/voice_loop.py`, `rex/voice_loop_optimized.py` | Wake word, STT, LLM, and TTS path |
| Config | `rex/config.py`, `rex/config_manager.py`, `config/rex_config.json` | Runtime JSON config plus `.env` secrets |
| Memory and history | `rex/memory.py`, `rex/procedural_memory.py`, `rex/memory_utils.py`, `rex/history_store.py`, `Memory/`, `data/` | Per-user memory/history plus verified, guarded procedural experience |
| Tools | `rex/openclaw/tool_registry.py`, `rex/openclaw/tool_executor.py`, `rex/openclaw/tools/` | Local tool registry and executor |
| Tool server | `rex/openclaw/tool_server.py` | `rex-tool-server` on `127.0.0.1:18790` — **developer-only** |
| Electron UI | `gui/` | Current primary React/Electron GUI, built to `gui/dist-electron/` — **shippable** |
| Python/Flask API and experimental web UI | `rex/gui_app.py` | `rex-gui` on `127.0.0.1:8765`; local APIs plus incomplete `/ui/` browser dashboard — **developer-only** |
| TTS API | `rex_speak_api.py` | `rex-speak-api` on `127.0.0.1:5005` — **developer-only** |
| Computer agent | `rex/computers/agent_server.py` | `rex-agent`, local agent API for controlled OS automation — **developer-only** |

## Repository Layout

```text
.
|-- rex/                     # Main Python package
|   |-- cli.py               # CLI command tree
|   |-- assistant.py         # Assistant orchestration
|   |-- config.py            # AppConfig loader and rex-config CLI
|   |-- llm_client.py        # Transformers/OpenAI/OpenRouter/Anthropic/Ollama clients
|   |-- voice_loop.py        # Package voice-loop exports
|   |-- voice_loop_optimized.py
|   |-- gui_app.py           # Flask local API plus experimental browser dashboard
|   |-- openclaw/            # Tool registry, executor, bridges, tool server
|   |-- integrations/        # Email, calendar, SMS service adapters
|   |-- computers/           # Remote computer agent/client support
|   |-- wakeword/            # Wake-word helpers
|   |-- notifications/       # Newer notification package pieces
|   `-- ...                  # Scheduler, workflows, memory, auth, etc.
|-- gui/                     # Electron + React desktop app
|-- plugins/                 # Optional legacy plugin modules, e.g. web_search
|-- config/                  # Runtime JSON config examples/defaults
|-- Memory/                  # Per-user profile and memory data
|-- data/                    # Local SQLite/state files at runtime
|-- tests/                   # Pytest suite
|-- docs/                    # Current docs plus archived planning/history
|-- rex_loop.py              # Voice loop runner
|-- rex_speak_api.py         # TTS Flask API
|-- flask_proxy.py           # Deprecated legacy compatibility proxy (see SURFACE-CLASSIFICATION.md)
`-- pyproject.toml           # Package metadata and console scripts
```

Top-level modules such as `config.py`, `llm_client.py`, and `memory_utils.py`
remain compatibility shims for older imports. New code should import from
`rex.*`.

## Entry Points

Defined in `pyproject.toml`:

| Console script | Target | Classification |
|---|---|---|
| `rex` | `rex.cli:main` | shippable |
| `rex-config` | `rex.config:cli` | developer-only |
| `rex-speak-api` | `rex_speak_api:main` | developer-only |
| `rex-agent` | `rex.computers.agent_server:main` | developer-only |
| `rex-gui` | `rex.gui_app:main` | developer-only |
| `rex-tool-server` | `rex.openclaw.tool_server:main` | developer-only |

Module/script entry points:

| Command | Purpose |
|---|---|
| `python -m rex` | Default CLI chat |
| `python -m rex doctor` | Environment diagnostics |
| `python rex_loop.py` | Full local voice loop |
| `python rex_speak_api.py` | Equivalent TTS API script form |
| `python flask_proxy.py` | **Deprecated** legacy compatibility proxy/API — use `rex-gui` instead |

`python -m rex-speak-api` is not a valid module invocation; use
`rex-speak-api` or `python rex_speak_api.py`.

## CLI Command Tree

`rex --help` currently exposes commands for:

- diagnostics: `doctor`, `version`, `tools`, `usage`
- chat and memory: `chat`, `memory`, `remember`, `history`, `quick-actions`
- knowledge: `kb`
- workflows: `plan`, `run-workflow`, `workflows`, `executor`, `approvals`
- schedule and reminders: `scheduler`, `reminders`, `cues`
- communications: `email`, `calendar`, `msg`, `notify`
- automation: `browser`, `os`, `gh`, `code`, `pc`
- integrations: `ha`, `wp`, `wc`
- identity and shopping: `whoami`, `identify`, `voice-id`, `shopping`

## Configuration Model

AskRex uses a split configuration model:

- `.env` stores secrets and service-specific environment controls.
- `config/rex_config.json` stores runtime settings such as wake word, models,
  audio devices, integrations, workflows, and UI defaults.

The canonical wake-word section is `wakeword`. The legacy `wake_word` key is
migrated at runtime with a warning.

`rex-config migrate-legacy-env` migrates older non-secret environment variables
into `config/rex_config.json` without overwriting non-default runtime values.

## Tool Architecture

Local tool execution is owned by `rex/openclaw/`:

- `tool_registry.py` builds the local registry.
- `tool_executor.py` enforces policy and executes tools.
- `tools/` contains individual tool adapters.
- `tool_server.py` exposes tools over HTTP for OpenClaw-compatible callers.

The tool server listens on `127.0.0.1:18790` by default and requires
`REX_TOOL_API_KEY` for `/rex/tools/{tool_name}` calls.

The built-in tool set includes time, weather, web search, email, SMS, calendar,
Home Assistant, Plex, WordPress, and WooCommerce paths. Optional integrations
only become usable when their dependencies and credentials are configured.

## UI Architecture

### Electron Desktop App

The Electron app lives in `gui/` and uses Electron/Vite/React. It is the current
primary user-facing GUI. Its package scripts are:

```bash
npm.cmd run dev
npm.cmd run typecheck
npm.cmd run build
npm.cmd run preview
npm.cmd run lint
```

The current Electron routes include home, devices, chat, voice, tasks,
calendar, reminders, memories, email, SMS, notifications, shopping, logs,
history, usage, integrations, settings, Home Assistant, quick actions, and
about.

For Electron-only verification harnesses, build first so
`gui/dist-electron/main/index.js` matches TypeScript sources.

### Python/Flask API and Experimental Browser Dashboard

`rex-gui` starts `rex/gui_app.py`, serves local JSON/SSE endpoints, and also
serves an incomplete browser dashboard at `/ui/`. Treat the Flask surface as
backend/API and compatibility infrastructure, not the current primary GUI.
Representative endpoints include:

- `/api/dashboard/status`
- `/api/chat/send`
- `/api/logs/stream`
- `/api/usage`
- `/api/devices`
- `/api/ha/test`
- `/api/quick-actions`
- `/api/status/stream`
- `/api/history`
- `/api/integrations`
- `/api/calendar/events`
- `/api/email/inbox`
- `/api/sms/threads`
- `/api/tools`

### Legacy UI Surfaces

`gui.py` and `run_gui.py` are **archived** Tkinter-era entry points (moved to `archived/tkinter_gui/`). `flask_proxy.py`
is a **deprecated** compatibility API/proxy surface — use `rex-gui` instead.

## TTS API

`rex-speak-api` runs `rex_speak_api.py` on `127.0.0.1:5005` by default. It
requires `REX_SPEAK_API_KEY` and accepts the key through `X-API-Key` or
`Authorization: Bearer ...`.

Main endpoints:

- `GET /health/live`
- `GET /health/ready`
- `POST /speak`

The service can optionally register Home Assistant and shopping blueprints when
their imports/configuration are available.

## OpenClaw Integration

AskRex integrates with OpenClaw over HTTP rather than importing OpenClaw as a
Python package. Gateway settings live under the `openclaw` key in
`config/rex_config.json`; the gateway secret is `OPENCLAW_GATEWAY_TOKEN` in
`.env`.

Primary modules:

- `rex/openclaw/http_client.py`
- `rex/openclaw/tool_bridge.py`
- `rex/openclaw/event_bridge.py`
- `rex/openclaw/browser_bridge.py`
- `rex/openclaw/voice_bridge.py`
- `rex/openclaw/tool_server.py`

## Testing and Quality

Pytest configuration is in `pyproject.toml`. Common checks:

```bash
pytest -q
python -m rex --help
python -m rex doctor
python scripts/security_audit.py
```

Electron checks:

```powershell
cd gui
npm.cmd run typecheck
npm.cmd run build
```

Coverage is configured in `pyproject.toml` with a `fail_under` threshold of 75.

## Design Rules

- Put new core code under `rex/`.
- Keep top-level Python files as compatibility shims or explicit entry scripts.
- Keep secrets out of `config/rex_config.json`.
- Make optional integrations fail closed or degrade gracefully when unconfigured.
- Bind network services to localhost unless a deployment explicitly opts out.
- Update docs when console scripts, ports, config keys, or UI surfaces change.
