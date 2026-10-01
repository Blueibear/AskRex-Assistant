# WordPress + WooCommerce Integration

**Implementation Status: Beta (read + write)**

Cycle 6.1 added read-only monitoring for WordPress sites and WooCommerce stores.
Cycle 6.3 adds WooCommerce write actions (order status updates, coupon management)
gated by the Rex policy-approval system.

---

## Overview

| Command | Description |
|---|---|
| `rex wp health --site <id>` | Check WordPress site reachability and WP detection |
| `rex wc orders list --site <id>` | List WooCommerce orders (optional status filter) |
| `rex wc orders set-status --site <id> --order-id <n> --status <s>` | Update order status (approval-gated write) |
| `rex wc products list --site <id>` | List WooCommerce products (optional low-stock filter) |
| `rex wc coupons create --site <id> --code <code> --amount <n> --type <t>` | Create a coupon (approval-gated write) |
| `rex wc coupons disable --site <id> --coupon-id <n>` | Disable a coupon (approval-gated write) |

---

## Configuration

### WordPress

Add a `wordpress.sites[]` section to `config/rex_config.json`:

```json
{
  "wordpress": {
    "sites": [
      {
        "id": "myblog",
        "base_url": "https://example.com",
        "enabled": true,
        "auth_method": "application_password",
        "credential_ref": "wp:myblog",
        "timeout_seconds": 15
      }
    ]
  }
}
```

#### Config keys

| Key | Type | Default | Description |
|---|---|---|---|
| `id` | string | (required) | Unique site identifier used in CLI commands |
| `base_url` | string | (required) | Base URL of the WordPress site (http or https) |
| `enabled` | bool | `true` | Disabled sites are ignored by CLI commands |
| `auth_method` | string | `"none"` | `none`, `application_password`, or `basic` |
| `credential_ref` | string | `""` | CredentialManager key; ignored when `auth_method=none` |
| `timeout_seconds` | int | `15` | HTTP request timeout (1–120 seconds) |

#### CredentialManager refs (WordPress)

When `auth_method` is `application_password` or `basic`, the credential value
must be in `"username:password"` format and stored in `.env` or
`config/credentials.json` — **never** in `rex_config.json`.

Example in `config/credentials.json`:
```json
{
  "wp:myblog": "admin:xxxx xxxx xxxx xxxx xxxx xxxx"
}
```

The application password shown above is a WordPress-generated token created at
**Users → Your Profile → Application Passwords** in the WP admin panel.

### WooCommerce

Add a `woocommerce.sites[]` section to `config/rex_config.json`:

```json
{
  "woocommerce": {
    "sites": [
      {
        "id": "myshop",
        "base_url": "https://example.com",
        "enabled": true,
        "consumer_key_ref": "wc:myshop:key",
        "consumer_secret_ref": "wc:myshop:secret", # pragma: allowlist secret
        "timeout_seconds": 30
      }
    ]
  }
}
```

#### Config keys

| Key | Type | Default | Description |
|---|---|---|---|
| `id` | string | (required) | Unique site identifier used in CLI commands |
| `base_url` | string | (required) | Base URL of the WordPress/WooCommerce site |
| `enabled` | bool | `true` | Disabled sites are ignored by CLI commands |
| `consumer_key_ref` | string | (required) | CredentialManager key for the WC consumer key |
| `consumer_secret_ref` | string | (required) | CredentialManager key for the WC consumer secret |
| `timeout_seconds` | int | `30` | HTTP request timeout (1–120 seconds) |

#### CredentialManager refs (WooCommerce)

Consumer key and consumer secret are generated in the WooCommerce admin:
**WooCommerce → Settings → Advanced → REST API → Add Key**.

For write actions (Cycle 6.3), the API key must have **Read/Write** permissions.

Store them in `.env` or `config/credentials.json`:

```json
{
  "wc:myshop:key": "ck_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
   "wc:myshop:secret": "cs_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" # pragma: allowlist secret
}
```

Never store raw credentials in `rex_config.json`.

---

## CLI Commands

### WordPress

#### `rex wp health --site <id>`

Check that a WordPress site is reachable and responds like a WordPress instance.

- Calls `GET /wp-json` (public, no auth required).
- If `auth_method` is configured, also calls `GET /wp-json/wp/v2/users/me` to
  verify credentials.
- A 401 on the auth check is reported as `Auth check: FAILED` but does **not**
  fail the overall health result (the site is still reachable).

```
$ rex wp health --site myblog

WordPress Health: myblog
============================================================
  Reachable    : Yes
  WP detected  : Yes
  Site name    : My Blog
  Site URL     : https://example.com
  Auth check   : OK

Overall status: OK
```

### WooCommerce — read commands

#### `rex wc orders list --site <id> [--status <status>] [--limit N]`

Fetch orders from a WooCommerce site via REST API v3.

```
$ rex wc orders list --site myshop --status pending --limit 5

WooCommerce Orders: myshop [pending]
============================================================
  #101  [pending]  USD 49.99  2026-02-01  | Alice Smith
  #103  [pending]  USD 12.00  2026-02-03  | Carol Davis

Total: 2 order(s)
```

Available `--status` values: `pending`, `processing`, `on-hold`, `completed`,
`cancelled`, `refunded`, `failed`, `trash` (WooCommerce defaults).

Default `--limit` is 10, maximum is 100.

#### `rex wc products list --site <id> [--low-stock] [--limit N]`

Fetch products from a WooCommerce site via REST API v3.

```
$ rex wc products list --site myshop --low-stock

WooCommerce Products: myshop [low-stock]
============================================================
  #2  Widget B  $19.99  [outofstock]
  #3  Widget C  $4.99  stock=3

Total: 2 product(s)
```

The `--low-stock` filter is applied **client-side** after fetching products.
A product is considered low-stock when:
- `stock_status` is `"outofstock"`, **or**
- `manage_stock=true` and `stock_quantity` ≤ `low_stock_amount`, **or**
- `manage_stock=true` and `stock_quantity` is 0.

Products where `manage_stock=false` or stock fields are missing are excluded
from low-stock results (stock level cannot be determined).

---

### WooCommerce — write commands (approval-gated, Cycle 6.3)

All write commands follow a **two-step approval flow**:

```
Step 1  Run the command  → creates a pending approval record; exits with instructions.
Step 2  Approve it       → rex approvals --approve <id>
Step 3  Re-run + --yes  → performs the write after approval is confirmed.
```

The `--yes` flag is a second-layer confirmation that is **always** required even
after approval. Skipping `--yes` will refuse the action with a clear message.

#### Why are WooCommerce writes HIGH risk?

WooCommerce write actions mutate live store data:
- Changing an order status triggers customer notifications, fulfilment workflows,
  and accounting records.
- Creating a coupon makes a discount code immediately redeemable.
- Disabling a coupon breaks any active promotions using that code.

These actions are irreversible or hard-to-undo in production, justifying the
same HIGH-risk classification used for remote computer execution (`pc_run`).

---

#### `rex wc orders set-status --site <id> --order-id <n> --status <s> [--note "<text>"] [--yes] [--user <uid>]`

Update the status of a WooCommerce order.

**API call:** `PUT /wp-json/wc/v3/orders/<order_id>` with `{"status": "<status>"}`

If `--note` is provided, a second call adds an internal note to the order:
`POST /wp-json/wc/v3/orders/<order_id>/notes` with `{"note": "...", "customer_note": false}`.

```
$ rex wc orders set-status --site myshop --order-id 101 --status completed

Approval required before this WooCommerce write action can proceed.

  Approval ID : apr_abc123
  Site        : myshop
  Action      : set order #101 status -> 'completed'
  Requested by: alice

  To approve : rex approvals --approve apr_abc123
  To deny    : rex approvals --deny apr_abc123

After approving, re-run this command with --yes to execute.

$ rex approvals --approve apr_abc123
$ rex wc orders set-status --site myshop --order-id 101 --status completed --yes

Order #101 status updated to 'completed'.
```

---

#### `rex wc coupons create --site <id> --code <code> --amount <n> --type <t> [--expires YYYY-MM-DD] [--usage-limit N] [--yes] [--user <uid>]`

Create a new WooCommerce coupon.

**API call:** `POST /wp-json/wc/v3/coupons`

| Argument | Required | Description |
|---|---|---|
| `--code` | Yes | Coupon code (non-empty string) |
| `--amount` | Yes | Discount amount (positive number) |
| `--type` | Yes | `percent`, `fixed_cart`, or `fixed_product` |
| `--expires` | No | Expiry date in `YYYY-MM-DD` format |
| `--usage-limit` | No | Maximum number of times the coupon can be used |

Local validation runs **before** any policy check or network call:
- `--code` must be non-empty.
- `--amount` must be a positive number.
- `--type` must be one of the three allowed values.
- `--expires` (if given) must match `YYYY-MM-DD`.

```
$ rex wc coupons create --site myshop --code SAVE10 --amount 10 --type percent

Approval required before this WooCommerce write action can proceed.
  ...

$ rex approvals --approve apr_xyz456
$ rex wc coupons create --site myshop --code SAVE10 --amount 10 --type percent --yes

Coupon created: #12 code='SAVE10'
```

---

#### `rex wc coupons disable --site <id> --coupon-id <n> [--yes] [--user <uid>]`

Disable a WooCommerce coupon by setting its status to `"draft"`.

**API call:** `PUT /wp-json/wc/v3/coupons/<coupon_id>` with `{"status": "draft"}`

`--coupon-id` must be a positive integer (validated locally before any policy check).

```
$ rex wc coupons disable --site myshop --coupon-id 55

Approval required before this WooCommerce write action can proceed.
  ...

$ rex approvals --approve apr_def789
$ rex wc coupons disable --site myshop --coupon-id 55 --yes

Coupon #55 disabled (status='draft').
```

---

## Approval flow details

Approvals are stored in `data/approvals/` as JSON files (the same store used
by `rex pc run`).  Each approval record contains:

| Field | Description |
|---|---|
| `approval_id` | Unique ID shown in CLI output |
| `workflow_id` | Always `"wc_write"` for WooCommerce write actions |
| `step_id` | Deterministic hash of action + site_id + key identifiers |
| `status` | `pending` -> `approved` (or `denied`) |
| `tool_call_summary` | Action parameters (no credentials stored) |

**Deterministic `step_id`**: Running the same command twice produces the same
approval record. The second call finds the existing pending approval instead of
creating a duplicate.

**Approval commands:**

```
rex approvals                        # list pending approvals
rex approvals --approve <id>        # approve a pending record
rex approvals --deny <id>           # deny a pending record
rex approvals --show <id>           # show full record details
```

---

## Security notes

- **No secrets in config**: `credential_ref`, `consumer_key_ref`, and
  `consumer_secret_ref` are CredentialManager lookup keys. The actual values
  must be stored in `.env` or `config/credentials.json`.
- **Credentials are never logged**: URL paths, query parameters, and response
  bodies do not appear in log output. Only the site `id` label is logged.
- **Approval payload is sanitized**: Consumer keys/secrets are never stored in
  approval records on disk. Only site ID, action name, and action parameters
  (order_id, status, coupon code, etc.) are persisted.
- **Error messages are sanitized**: Request failures are normalized to avoid
  leaking credential material from exception strings.
- **SSRF hardening**: `base_url` must not contain embedded credentials and must
  resolve to non-local, non-reserved addresses (localhost/private/link-local/
  multicast/unspecified are rejected). This applies to both read and write paths.
- **Timeouts**: All requests have configurable timeouts (default 15 s for WP,
  30 s for WC) to prevent hanging on slow or unreachable sites.
- **Two-step execution**: Write actions require both an approved approval record
  **and** the `--yes` flag. Neither alone is sufficient.

---

## Module layout

```
rex/wordpress/
    __init__.py
    config.py         -- Pydantic v2 config models (WordPressSiteConfig, WordPressConfig)
    client.py         -- HTTP client (WordPressClient, WPHealthResult)
    service.py        -- Service facade (WordPressService, get_wordpress_service)

rex/woocommerce/
    __init__.py
    config.py         -- Pydantic v2 config models (WooCommerceSiteConfig, WooCommerceConfig)
    client.py         -- HTTP client (WooCommerceClient, OrdersResult, ProductsResult, WriteResult)
    service.py        -- Service facade (WooCommerceService, get_woocommerce_service)
    write_policy.py   -- Policy + approval gating for write actions (Cycle 6.3)
```

---

## Deferred (post Cycle 6.3)

- Webhook support for real-time order notifications.
- Product write actions (create/update/delete products).
- CalDAV/Google Calendar OAuth write support.
