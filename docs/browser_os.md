# Browser and OS Automation

AskRex Assistant provides browser automation and OS-level automation capabilities with built-in security controls.

## Browser Automation

### Overview

Browser automation is powered by [Playwright](https://playwright.dev/), providing reliable browser control for:

- Web scraping and data extraction
- Form filling and submission
- UI testing and validation
- Screenshot capture
- Login automation with credential integration

### Installation

Browser automation requires Playwright. Install it with:

```bash
pip install playwright
playwright install chromium
```

### Basic Usage

#### Writing a Browser Script

Create a JSON script file with automation steps:

```json
{
  "session_name": "my_automation",
  "steps": [
    {
      "action": "navigate",
      "params": {
        "url": "https://example.com"
      }
    },
    {
      "action": "screenshot",
      "params": {
        "filename": "example_homepage.png"
      }
    },
    {
      "action": "click",
      "params": {
        "selector": "#search-button"
      }
    },
    {
      "action": "type",
      "params": {
        "selector": "#search-input",
        "text": "AskRex Assistant"
      }
    },
    {
      "action": "wait",
      "params": {
        "milliseconds": 2000
      }
    }
  ]
}
```

#### Running a Script

```bash
# Run in headless mode (default)
rex browser run script.json

# Run in headed mode (show browser)
rex browser run script.json --headed
```

#### Available Actions

- **navigate**: Navigate to a URL
  - `url`: Target URL
  - `wait_for`: Wait condition ("load", "domcontentloaded", "networkidle")

- **click**: Click an element
  - `selector`: CSS selector
  - `timeout`: Timeout in milliseconds (default: 5000)

- **type**: Type text into an element
  - `selector`: CSS selector
  - `text`: Text to type
  - `timeout`: Timeout in milliseconds

- **wait**: Wait for a duration
  - `milliseconds`: Duration to wait

- **screenshot**: Capture a screenshot
  - `filename`: Optional filename (auto-generated if not provided)
  - `full_page`: Capture full scrollable page (default: false)

- **login**: Login to a site using stored credentials
  - `url`: Login page URL
  - `credential_name`: Name in credential manager (e.g., "github")
  - `username_selector`: CSS selector for username field
  - `password_selector`: CSS selector for password field
  - `submit_selector`: CSS selector for submit button

- **download**: Download a file
  - `url`: File URL
  - `dest_path`: Destination path

### Credential Integration

Browser automation integrates with the credential manager for secure authentication.

#### Setting Credentials

Store credentials for a site:

```bash
# Set via environment variables
export REX_GITHUB_USERNAME="your_username"
export REX_GITHUB_PASSWORD="your_password" # pragma: allowlist secret

# Or add to config/credentials.json
{
  "github.username": "your_username",
  "github.password": "<configure-in-credential-vault>"
}
```

#### Using Credentials in Scripts

```json
{
  "action": "login",
  "params": {
    "url": "https://github.com/login",
    "credential_name": "github"
  }
}
```

### Managing Browser Sessions

```bash
# List browser sessions
rex browser sessions

# List captured screenshots
rex browser screenshots
```

Screenshots are saved to `data/browser_sessions/screenshots/`.

### Security Considerations

1. **Policy Checks**: All browser actions go through the policy engine
2. **Credential Safety**: Passwords are never logged or exposed
3. **Sandboxing**: Browser runs in a controlled environment
4. **Audit Logging**: All actions are logged for review

## OS Automation

### Overview

OS automation provides safe system-level operations with:

- Whitelisted command execution
- File operations with path restrictions
- Automatic backup before deletion
- Comprehensive audit logging

### Command Execution

#### Allowed Commands

Only whitelisted commands can be executed. The default whitelist includes:

- `ls`, `cat`, `echo`, `pwd`, `whoami`, `date`
- `head`, `tail`, `grep`, `find`, `wc`
- `sort`, `uniq`, `which`, `man`
- `file`, `stat`, `du`, `df`, `tree`
- `diff`, `cmp`, `md5sum`, `sha256sum`

Configure allowed commands in `config/os_allowed_commands.json`.

#### Running Commands

```bash
# Run a safe command
rex os run "ls -la"

# List directory contents
rex os run "ls"

# Search files
rex os run "find data/ -name '*.json'"

# Check disk usage
rex os run "du -sh data/"
```

### File Operations

All file operations are restricted to the `data/` directory for security.

#### Copy Files

```bash
rex os copy data/source.txt data/backup/source.txt
```

#### Move Files

```bash
rex os move data/old_name.txt data/new_name.txt
```

#### Delete Files

Files are moved to trash by default, not permanently deleted:

```bash
# Move to trash (safe)
rex os delete data/file.txt

# Permanent deletion (use with caution)
rex os delete data/file.txt --permanent
```

#### Trash Management

```bash
# List files in trash
rex os trash

# Restore files programmatically
# (use the Python API)
```

### Python API

#### Command Execution

```python
from rex.os_automation import get_os_service

service = get_os_service()

# Run a command
result = service.run_command(["ls", "-la"])
print(result.stdout)
print(f"Exit code: {result.returncode}")
```

#### File Operations

```python
from rex.os_automation import get_os_service

service = get_os_service()

# Copy a file
service.copy_file("data/source.txt", "data/dest.txt")

# Move a file
service.move_file("data/old.txt", "data/new.txt")

# Delete a file (with backup)
service.delete_file("data/file.txt", backup=True)

# List trash
files = service.list_trash()
for f in files:
    print(f"{f['name']}: {f['size']} bytes")
```

### Security Considerations

1. **Command Whitelist**: Only approved commands can run
2. **Path Sanitization**: All file paths must be within `data/`
3. **Backup by Default**: Deletions move to trash, not permanent
4. **Policy Checks**: Destructive operations require approval
5. **Audit Logging**: All commands and file operations are logged

### Configuration

#### Customizing Allowed Commands

Edit `config/os_allowed_commands.json`:

```json
{
  "allowed_commands": [
    "ls",
    "cat",
    "echo",
    "your_custom_command"
  ],
  "description": "Whitelist of allowed shell commands"
}
```

#### Policy Configuration

OS automation respects the policy engine. Configure policies in your policy configuration:

```python
from rex.policy_engine import ActionPolicy, RiskLevel

# High-risk commands require approval
policy = ActionPolicy(
    tool_name="execute_shell_command",
    risk=RiskLevel.HIGH,
    allow_auto=False
)
```

## Best Practices

### Browser Automation

1. **Use headless mode** for production scripts
2. **Store credentials securely** via the credential manager
3. **Add wait steps** between actions for reliability
4. **Capture screenshots** for debugging
5. **Use specific selectors** to avoid flakiness

### OS Automation

1. **Limit commands** to read-only operations when possible
2. **Always backup** before destructive operations
3. **Use relative paths** within the data directory
4. **Test commands** in a safe environment first
5. **Review audit logs** regularly for anomalies

## Troubleshooting

### Browser Automation

**Browser not launching:**
- Ensure Playwright is installed: `playwright install chromium`
- Check for permission issues
- Try headed mode for debugging: `--headed`

**Selector not found:**
- Inspect the page to verify selectors
- Add wait steps before interactions
- Use more specific selectors

**Screenshot path not found:**
- Directory created automatically on first use
- Check `data/browser_sessions/screenshots/`

### OS Automation

**Command not allowed:**
- Check `config/os_allowed_commands.json`
- Add command to whitelist if safe
- Verify command name (not full path)

**Path outside data directory:**
- All paths must be relative to or within `data/`
- Use `data/` prefix for absolute paths

**Permission denied:**
- Check policy configuration
- Some operations require approval
- Verify file permissions in data directory

## Examples

### Browser: Login and Scrape

```json
{
  "steps": [
    {
      "action": "login",
      "params": {
        "url": "https://example.com/login",
        "credential_name": "example"
      }
    },
    {
      "action": "navigate",
      "params": {
        "url": "https://example.com/dashboard"
      }
    },
    {
      "action": "screenshot",
      "params": {
        "filename": "dashboard.png"
      }
    }
  ]
}
```

### OS: Batch File Processing

```bash
# List all JSON files
rex os run "find data/ -name '*.json'"

# Check file sizes
rex os run "du -sh data/*"

# Archive old files
rex os move data/old.json data/archive/old.json
```
