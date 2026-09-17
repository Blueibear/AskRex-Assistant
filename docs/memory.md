# Memory System

> **Current implementation vs. continuity target:** This document describes the currently implemented `rex.memory` working/long-term stores and their user-isolation behavior. The long-term target architecture is defined by the [Rex Continuity Architecture](architecture/rex-continuity-architecture.md) and [Persistent Memory, Experiential Learning, and Resource Self-Maintenance](architecture/persistent-memory-experiential-learning.md). Do not interpret today's two-store implementation as the final memory taxonomy or create a competing memory subsystem.

Rex includes a comprehensive memory system with two types of memory:

1. **Working Memory** - Short-term buffer for recent interactions and context
2. **Long-Term Memory** - Structured storage with categories, expiration, and search

## Overview

The memory system enables Rex to:
- Maintain context across conversations
- Store user preferences and facts
- Remember important information with automatic cleanup
- Protect sensitive data from being exposed

## Ownership and Isolation (US-303)

Both stores are strictly per-user:

- Every operation requires an explicit `user_id`, validated by
  `rex.identity.validate_user_id`. Missing, blank, malformed, or
  traversal-style identity fails closed (`TypeError`/`ValueError`).
- There is **no silent fallback** to `default`, the active user, or any
  other profile. Single-user setups select the `default` profile
  explicitly (`--user default` or `rex identify --user default`).
- Stores are partitioned on disk per user (see [Storage](#storage)), so
  entry IDs may repeat across users without collision, and one user can
  never read, search, delete, clear, or overwrite another user's entries.
- Process-level instances are cached per validated `user_id`; a user never
  receives an object backed by another user's file or in-memory entries.
- Logs identify the owning user, never memory content.

## Working Memory

Working memory stores recent interactions and task summaries, providing immediate context for conversations.

### Features

- Ordered list of recent entries (most recent last)
- Automatic persistence to disk
- Configurable maximum entries (default: 100)
- Loads automatically on startup

### Usage

```python
from rex.memory import get_working_memory, remember_context, get_recent_context

# Get the working memory instance for one user
wm = get_working_memory(user_id="james")

# Add an entry
wm.add_entry("User asked about weather in Dallas")

# Get recent entries (content only)
recent = wm.get_recent(5)  # Returns list of strings

# Get entries with timestamps
entries = wm.get_recent_with_timestamps(5)
# Returns: [{"content": "...", "timestamp": "..."}, ...]

# Clear all entries (this user only)
wm.clear()

# Convenience functions
remember_context("User prefers dark mode", user_id="james")
context = get_recent_context(3, user_id="james")
```

### CLI Commands

Every `rex memory` command resolves the requesting user from `--user`,
then the `rex identify` session, then `runtime.active_user` /
`runtime.user_id` in config. With no resolvable user the command fails
closed with an actionable error.

```bash
# Show recent working memory entries
rex memory recent 10 --user james

# Clear working memory (this user only)
rex memory clear --user james

# Show memory statistics
rex memory stats --user james
```

## Long-Term Memory

Long-term memory stores structured entries organized by category with support for expiration, search, and sensitive data protection.

### Features

- Categorized entries (preferences, facts, etc.)
- Optional expiration (TTL)
- Sensitive data flagging
- Keyword search across content
- Automatic cleanup of expired entries

### Memory Entry Structure

Each entry contains:
- `entry_id`: Unique identifier (unique within one user's store)
- `category`: Category name (e.g., "preferences", "facts")
- `content`: Dictionary with the stored data
- `created_at`: Creation timestamp
- `expires_at`: Optional expiration timestamp
- `sensitive`: Boolean flag for sensitive data

### Usage

```python
from datetime import timedelta
from rex.memory import (
    get_long_term_memory,
    add_user_preference,
    get_user_preferences,
)

# Get the long-term memory instance for one user
ltm = get_long_term_memory(user_id="james")

# Add an entry
entry = ltm.add_entry(
    category="preferences",
    content={"theme": "dark", "language": "en"},
)

# Add with expiration
entry = ltm.add_entry(
    category="temp_data",
    content={"session_id": "abc123"},
    expires_in=timedelta(hours=24),
)

# Add sensitive data
entry = ltm.add_entry(
    category="secrets",
    content={"api_key": "secret123"},
    sensitive=True,
)

# Search entries (this user's entries only)
results = ltm.search(category="preferences")
results = ltm.search(keyword="theme")
results = ltm.search(category="preferences", keyword="dark")

# Get a specific entry
entry = ltm.get_entry("mem_abc123")

# Delete an entry
ltm.forget("mem_abc123")

# Run retention policy (delete expired)
deleted_count = ltm.run_retention_policy()

# List categories
categories = ltm.list_categories()

# Count by category
counts = ltm.count_by_category()

# Convenience functions
add_user_preference("notification_sound", "chime", user_id="james")
prefs = get_user_preferences("notification", user_id="james")
```

### CLI Commands

```bash
# Add a long-term memory entry
rex memory add "preferences" '{"theme": "dark"}' --user james

# Add with expiration (7 days)
rex memory add "temp" '{"key": "value"}' --ttl=7d --user james

# Add sensitive entry
rex memory add "secrets" '{"api_key": "xxx"}' --sensitive --user james

# Search entries (this user's entries only)
rex memory search "theme" --user james
rex memory search --category preferences --user james

# Search and show sensitive content
rex memory search "api_key" --show-sensitive --user james

# Delete an entry (must own it)
rex memory forget mem_abc123 --user james

# Run retention policy (this user's store only)
rex memory retention --user james

# Show statistics
rex memory stats --user james
```

### TTL Formats

When specifying expiration times:
- `7d` - 7 days
- `24h` - 24 hours
- `30m` - 30 minutes
- `2w` - 2 weeks
- `10s` - 10 seconds

## Sensitive Data Handling

Entries marked as sensitive are protected:

1. Content is hidden by default in CLI output
2. Use `--show-sensitive` flag to view content
3. The `to_safe_dict()` method returns redacted content
4. Sensitive entries are included in searches but content is hidden
5. Sensitive entries are isolated per user like everything else — another
   user cannot see them at all, with or without `--show-sensitive`

```python
# Check if entry is sensitive
if entry.sensitive:
    # Use safe dict for logging
    safe_data = entry.to_safe_dict()
    # safe_data["content"] = {"[SENSITIVE]": "Content hidden"}
```

## Retention Policies

Expired entries are automatically managed, always per user:

1. **On Startup**: Expired entries are deleted when a user's LongTermMemory loads
2. **Manual**: Call `run_retention_policy()` on a user's store
3. **CLI**: Run `rex memory retention --user <id>`
4. **Scheduled**: `schedule_memory_cleanup(scheduler)` registers a job that
   runs `run_memory_cleanup()`, which compacts **each user's store
   independently**. A failure in one user's store never touches another
   user's file; directory names that are not valid user IDs are ignored
   and never treated as users.

Entries without `expires_at` never expire.

## Storage

Memory is persisted to JSON files, partitioned per user:

- Working Memory: `data/memory/<user_id>/working_memory.json`
- Long-Term Memory: `data/memory/<user_id>/long_term_memory.json`

### Legacy unscoped files and migration

Installations that predate per-user isolation stored one shared
`data/memory/working_memory.json` and `data/memory/long_term_memory.json`
at the data-dir root. Policy:

- Those files belong **only** to the distinct `default` profile. They are
  not shared, and they are never reassigned to a named user. Named users
  never read them; a named user accessing memory first neither sees nor
  consumes them.
- Migration runs only when a caller explicitly requests
  `user_id="default"` (e.g. `rex memory stats --user default`).
- Migration is idempotent and crash-safe: the legacy file is copied into
  `data/memory/default/` via a temp file and atomic replace. Only after
  the default-profile copy exists is the original renamed to
  `<name>.json.pre-user-isolation.bak` at the data-dir root — it is
  preserved, never deleted.
- A failed or partial migration leaves the original untouched and fails
  the requesting call; the next explicit `default` access retries.

**Recovery**: to undo a migration, delete
`data/memory/default/<name>.json` and rename
`data/memory/<name>.json.pre-user-isolation.bak` back to
`data/memory/<name>.json`. To reassign legacy data to a named user,
manually copy the file into `data/memory/<user_id>/` — there is
deliberately no automatic reassignment tool.

### Storage Format

**Working Memory:**
```json
{
  "entries": [
    {
      "content": "User asked about weather",
      "timestamp": "2024-01-15T10:30:00Z"
    }
  ]
}
```

**Long-Term Memory:**
```json
{
  "entries": [
    {
      "entry_id": "mem_abc123",
      "category": "preferences",
      "content": {"theme": "dark"},
      "created_at": "2024-01-15T10:30:00Z",
      "expires_at": null,
      "sensitive": false
    }
  ]
}
```

## Categories

Common categories for organizing entries:

| Category | Description |
|----------|-------------|
| `user_preferences` | User settings and preferences |
| `facts` | Learned facts about the user or environment |
| `context` | Conversational context |
| `secrets` | Sensitive credentials (mark as sensitive!) |
| `temp_data` | Temporary data with expiration |

## Best Practices

1. **Use categories consistently** - Group related entries
2. **Set expiration for temporary data** - Prevent unbounded growth
3. **Mark sensitive data** - Protect credentials and personal info
4. **Clean up regularly** - Run retention policy periodically
5. **Use convenience functions** - `add_user_preference()` for common tasks
6. **Always pass the real requester's `user_id`** - never hardcode a
   profile on behalf of someone else

## Integration

The memory system integrates with other Rex components:

```python
from rex.memory import remember_context, get_recent_context

# In assistant code - track conversation context for the identified user
async def generate_reply(self, user_input: str, user_id: str) -> str:
    # Add to this user's working memory
    remember_context(f"User: {user_input}", user_id=user_id)

    # Get this user's recent context for the LLM
    context = get_recent_context(5, user_id=user_id)

    # ... generate reply ...

    remember_context(f"Rex: {reply}", user_id=user_id)
    return reply
```

## API Reference

### WorkingMemory

| Method | Description |
|--------|-------------|
| `WorkingMemory(user_id=...)` | Construct a store owned by one user |
| `add_entry(content)` | Add a new entry |
| `get_recent(n)` | Get last n entries (content only) |
| `get_recent_with_timestamps(n)` | Get last n entries with timestamps |
| `clear()` | Remove all entries (this owner only) |

### LongTermMemory

| Method | Description |
|--------|-------------|
| `LongTermMemory(user_id=...)` | Construct a store owned by one user |
| `add_entry(category, content, expires_in, sensitive)` | Add entry |
| `get_entry(entry_id)` | Get entry by ID |
| `search(category, keyword, include_sensitive, include_expired)` | Search entries |
| `forget(entry_id)` | Delete entry |
| `run_retention_policy()` | Delete expired entries |
| `list_categories()` | List all categories |
| `count_by_category()` | Count entries per category |

Both classes also accept an explicit `storage_path` for unit tests; the
production getters below always use the validated per-user path.

### Module Functions

| Function | Description |
|----------|-------------|
| `get_working_memory(user_id=...)` | Per-user working memory instance |
| `get_long_term_memory(user_id=...)` | Per-user long-term memory instance |
| `set_working_memory(wm, user_id=...)` | Inject an instance for one user (testing) |
| `set_long_term_memory(ltm, user_id=...)` | Inject an instance for one user (testing) |
| `add_user_preference(key, value, ..., user_id=...)` | Add user preference |
| `get_user_preferences(key, user_id=...)` | Get user preferences |
| `add_fact(topic, content, ..., user_id=...)` | Add a fact |
| `remember_context(summary, user_id=...)` | Add to working memory |
| `get_recent_context(n, user_id=...)` | Get recent working memory |
| `list_memory_user_ids()` | Validated user IDs with a store on disk |
| `run_memory_cleanup()` | Compact every user's store independently |
| `schedule_memory_cleanup(scheduler, ...)` | Register the per-user cleanup job |
| `memory_store_metrics()` | Aggregate entry counts (no content) |

---

## User Profiles & Personalization

Each user has a dedicated profile in `Memory/<username>/`:

```
Memory/james/
├── core.json         # User preferences and voice settings
├── history.log       # Conversation history
└── notes.md          # Freeform notes about the user
```

**Example `core.json`:**
```json
{
  "name": "James",
  "email": "james@example.com",
  "preferences": {
    "preferred_name": "Jim",
    "timezone": "America/New_York"
  },
  "voice": {
    "sample_path": "Memory/james/voice_sample.wav",
    "gender": "male",
    "style": "friendly and warm"
  }
}
```

Rex uses voice cloning with XTTS when a valid `voice.sample_path` is provided.
