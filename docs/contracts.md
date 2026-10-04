# Rex Contracts

Rex Contracts are versioned Pydantic schemas that define the internal data structures used across all Rex components: voice node, Electron GUI/bridge paths, Flask API/browser-dashboard surface, gateway API, tool adapters, audit log, and scheduler.

## Installation

### Installing Rex

Rex uses modern Python packaging with `pyproject.toml`. To install:

```bash
# Clone the repository
git clone https://github.com/Blueibear/AskRex-Assistant.git
cd AskRex-Assistant

# Install in development/editable mode
pip install -e .

# Or install with development dependencies
pip install -e ".[dev]"
```

### Verifying Installation

After installation, run the doctor command to verify your environment:

```bash
rex doctor
```

Or via the Python module:

```bash
python -m rex doctor
```

This checks for:
- Python version compatibility
- Configuration files
- Required environment variables
- External dependencies (ffmpeg, etc.)

See [docs/doctor.md](doctor.md) for detailed documentation.

### CLI Commands

After installation, the following commands are available:

| Command | Description |
|---------|-------------|
| `rex` or `rex chat` | Start interactive chat |
| `rex doctor` | Run environment diagnostics |
| `rex version` | Show version information |
| `rex-config` | Configuration management |
| `rex-speak-api` | Start the TTS API server |

---

## Purpose

Contracts provide:

- **Type Safety**: Strongly-typed models with validation
- **Consistency**: All components use the same data structures
- **Discoverability**: JSON Schema export for external tools
- **Versioning**: Explicit version tracking for compatibility

## Versioning Policy

Contract versions follow semantic versioning:

- **Major** (X.0.0): Breaking changes (removed/renamed fields, changed types)
- **Minor** (0.X.0): Backward-compatible additions (new optional fields)
- **Patch** (0.0.X): Bug fixes, documentation updates

The current version can be retrieved programmatically:

```python
from rex.contracts import CONTRACT_VERSION, get_version_info

print(CONTRACT_VERSION)  # e.g., "0.1.0"
info = get_version_info()
print(info["status"])    # e.g., "alpha"
```

## Available Models

| Model | Purpose |
|-------|---------|
| `EvidenceRef` | Reference to evidence (screenshots, logs, files) |
| `ToolCall` | Request to invoke a tool |
| `ToolResult` | Result of a tool invocation |
| `Approval` | Approval request and resolution |
| `Action` | A discrete action within a task |
| `Task` | A unit of work containing actions |
| `Notification` | Notification to be delivered to users |

## Usage

### Importing Models

```python
from rex.contracts import (
    Task, Action, ToolCall, ToolResult,
    Approval, Notification, EvidenceRef,
)

# Create a task
task = Task(
    task_id="task_001",
    title="Check current time",
    status="running",
    requested_by="user:james",
)

# Create a tool call
tool_call = ToolCall(
    tool="time_now",
    args={"location": "Dallas, TX"},
    requested_by="assistant",
)

# Create an action with the tool call
action = Action(
    action_id="act_001",
    task_id=task.task_id,
    kind="tool_call",
    risk="low",
    tool_call=tool_call,
)
```

### JSON Serialization

All models serialize to JSON cleanly:

```python
# Serialize to JSON string
json_str = task.model_dump_json()

# Serialize to dict
data = task.model_dump()

# Deserialize from JSON
restored = Task.model_validate_json(json_str)
```

### Redacting Sensitive Data

Use the `redact_sensitive_keys` helper for logging:

```python
from rex.contracts import redact_sensitive_keys

data = {
    "api_key": "sk-secret123",  # pragma: allowlist secret -- redaction example
    "user": "james",
    "nested": {"password": "hunter2"},  # pragma: allowlist secret -- redaction example
}

safe_data = redact_sensitive_keys(data)
# {'api_key': '[REDACTED]', 'user': 'james', 'nested': {'password': '[REDACTED]'}}
```

The following key patterns are redacted:
- `token`, `access_token`, `refresh_token`
- `authorization`
- `password`
- `secret`
- `api_key`

## Exporting Schemas

Generate JSON Schema files for all models:

```bash
python scripts/export_contract_schemas.py
```

This creates:
- `docs/contracts/index.json` - Index with version and file list
- `docs/contracts/<ModelName>.json` - Schema for each model

Override the output directory with `REX_CONTRACTS_OUTPUT_DIR`:

```bash
REX_CONTRACTS_OUTPUT_DIR=/tmp/schemas python scripts/export_contract_schemas.py
```

## API Discovery

The `/contracts` endpoint returns schema metadata:

```bash
curl http://localhost:5000/contracts
```

Response:
```json
{
    "contract_version": "0.1.0",
    "schema_docs_path": "docs/contracts/",
    "models": ["EvidenceRef", "ToolCall", "ToolResult", "Approval", "Action", "Task", "Notification"]
}
```

## Consuming Contracts

### From Python

```python
from rex.contracts import Task, Action

# Validate incoming data
task_data = {"task_id": "t1", "title": "Test"}
task = Task.model_validate(task_data)
```

### From Other Languages

1. Export schemas: `python scripts/export_contract_schemas.py`
2. Use the JSON Schema files in `docs/contracts/` with your language's JSON Schema validator
3. Check `docs/contracts/index.json` for the contract version

### Version Compatibility

Components should:

1. Check `CONTRACT_VERSION` or `/contracts` endpoint on startup
2. Log a warning if versions differ between components
3. For major version mismatches, fail safely rather than process incompatible data

## Future Enhancements

As Rex grows, contracts will expand to cover:

- Scheduling and cron-like task definitions
- Audit log entries
- User permissions and roles
- Remote access session management
