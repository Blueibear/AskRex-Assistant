# Hardcoded Secret Scan Report

> **Developer reference:** This document is a security audit record for maintainers
> and contributors. It is not part of user-facing install instructions. Placeholder
> values referenced here (e.g., `changeme`, `YOUR-SECRET-HERE`) describe false
> positives found in config templates and test fixtures — they are not instructions
> to use those values in real deployments.

**Tool:** detect-secrets 1.5.0
**Date:** 2026-03-11
**Scope:** Full repository (source, tests, docs, config examples)
**Result:** Zero confirmed real secrets. All findings are false positives.

---

## Summary

`detect-secrets scan` was run against the full repository and produced 69 flagged
locations across 37 files. Every finding was manually reviewed and confirmed to be
a **false positive** — no real API keys, passwords, tokens, or private keys were
found in the repository history or working tree.

A baseline file (`.secrets.baseline`) was generated and committed. Future runs
compare against this baseline; only new, unreviewed findings cause CI failure.

---

## False Positive Categories

### 1. Test fixture credentials (most common)
Files: `tests/test_us047_dashboard_auth.py`, `tests/test_us046_dashboard_server.py`,
`tests/test_us094_input_validation.py`, `tests/test_us095_auth_session_security.py`,
`tests/test_config_loading.py`, `tests/test_doctor.py`, `tests/test_credentials.py`,
and others.

**Reason:** Tests inject dummy passwords like `"correct"`, `"test-secret"`,
`"password123"`, or `"hunter2"` via `monkeypatch.setenv`. These are obviously not
real credentials; they exist solely to drive test assertions.

### 2. Docstring and documentation examples
Files: `rex/contracts/core.py`, `docs/contracts.md`, `docs/memory.md`,
`docs/dashboard.md`, `docs/browser_os.md`, `docs/wordpress_woocommerce.md`.

**Reason:** Code examples and documentation show placeholder values such as
`"secret123"`, `"hunter2"`, and `"password"` to illustrate API usage or data shapes.
These are not real credentials.

### 3. Security audit documentation
File: `SECURITY_AUDIT_2026-01-08.md`

**Reason:** The audit document discusses credential patterns (e.g., shows what a
private key header looks like). The text `-----BEGIN PRIVATE KEY-----` is an example <!-- pragma: allowlist secret -->
pattern description, not an actual key.

### 4. Placeholder / template values
Files: `Start-RexSpeak.ps1`, `config/rex_config.example.json`,
`rex/woocommerce/config.py`.

**Reason:**
- `Start-RexSpeak.ps1` line 1: `$env:REX_SPEAK_API_KEY = 'YOUR-SECRET-HERE'` <!-- pragma: allowlist secret -->
  explicit placeholder instructing users to replace the value.
- `config/rex_config.example.json` line 171: `"password": "changeme"` <!-- pragma: allowlist secret -->
  config template.
- `rex/woocommerce/config.py` line 13: `"consumer_secret_ref": "wc:myshop:secret"` <!-- pragma: allowlist secret -->
  a config key reference string, not a credential value.

### 5. Sentinel / non-secret string
File: `rex/llm_client.py` line 389

**Reason:** `api_key = "local"` is a sentinel value used to signal local-mode <!-- pragma: allowlist secret -->
operation to the LLM client. It is not an API key with any external service.

### 6. High-entropy hex strings in tests
Files: `tests/test_twilio_signature.py`, `tests/test_us056_github_actions.py`.

**Reason:** Twilio webhook signature verification requires hex HMAC values. The
test file contains test-fixture HMAC hex strings generated for unit testing, not
real Twilio auth tokens.

### 7. Basic auth credentials in tests
Files: `tests/test_ha_tts.py`, `tests/test_wc_write_actions.py`,
`tests/test_woocommerce.py`, `tests/test_wordpress.py`.

**Reason:** WooCommerce/WordPress tests use HTTP Basic Auth format
(`user:password@host`) with obviously fake credentials (`user`, `password`, `admin`)
for mock request construction. No real credentials.

---

## Remediation Status

| Finding | File(s) | Action |
|---------|---------|--------|
| Test fixture strings | `tests/*.py` | Accepted — fake test data |
| Docstring examples | `rex/*.py`, `docs/*.md` | Accepted — documentation |
| Security audit patterns | `SECURITY_AUDIT_2026-01-08.md` | Accepted — pattern examples |
| Placeholder templates | `Start-RexSpeak.ps1`, `config/*.json` | Accepted — explicit placeholders |
| Local sentinel | `rex/llm_client.py` | Accepted — sentinel value |
| HMAC hex in tests | `tests/test_twilio_signature.py` | Accepted — test fixture |
| Basic auth in tests | `tests/test_wc_write_actions.py` etc. | Accepted — fake credentials |

**No rotation required.** No real credentials were found.

---

## Prevention

A `.secrets.baseline` file is committed to the repository. Two mechanisms block
new secret commits:

1. **Pre-commit hook** (`.pre-commit-config.yaml`): runs `detect-secrets` on staged
   files before each commit. Install locally with `pip install pre-commit &&
   pre-commit install`.

2. **CI job** (`.github/workflows/ci.yml` → `secret-scan`): runs `detect-secrets
   scan --baseline .secrets.baseline` on every push and pull request. The job fails
   if any finding is not present in the committed baseline, preventing new secrets
   from landing.

---

## How to Update the Baseline

If a new false positive is introduced (e.g., a new test with a dummy token):

```bash
python -m detect_secrets scan \
  --exclude-files "\.venv|__pycache__|\.git|\.egg-info" \
  --baseline .secrets.baseline
# Review the diff, then commit .secrets.baseline
```

Do **not** add real credentials to the baseline. Real credentials must be
revoked and rotated, not baselined.
