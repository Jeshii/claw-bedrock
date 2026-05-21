# Security Hardening Plan

## Goal
Mitigate risks from storing sensitive API keys in TinyDB by adding encryption at rest, warning banners, and stricter file permissions.

---

## 1. Encryption Warning Banner

### Problem
If neither `ENCRYPTION_KEY` nor `ENCRYPTION_PASSWORD` is set, the app falls back to a hardcoded dev encryption key. Data is still encrypted, but the key is predictable.

### Solution
- Use `GET /api/security/encryption-status`
  - Returns `{ configured: bool, using: "key" | "password" | "dev" }`
- **Dashboard banner**: Add `#encryption-warning-banner` div (similar to existing `#dashboard-auth-banner`)
  - Shown by default, hidden when encryption is properly configured
  - Dismissible via ✕ button — no persistence, reappears on every page load
  - Env var `DISABLE_ENCRYPTION_WARNING=1` suppresses it entirely (for dev convenience)
- **CSS**: Add styles reusing the auth banner pattern with red/orange warning colors

---

## 2. Encrypt Sensitive Provider Fields at Rest

### Problem
Provider API keys (e.g., `api_key` for OpenAI-compatible providers) are stored in plain text in `clawbedrock.db.json`. If the DB file leaks, all credentials are exposed.

### Solution
Add encryption/decryption in `db.py` for any provider field matching `*key*`, `*secret*`, `*password*`, `*token*` (case-insensitive):

- **`_encrypt_sensitive_fields(provider)`** — encrypts matching fields before DB write
- **`_decrypt_sensitive_fields(provider)`** — decrypts matching fields after DB read
- **`upsert_provider()`** — calls `_encrypt_sensitive_fields` before writing
- **`get_provider()` / `get_all_providers()`** — calls `_decrypt_sensitive_fields` after reading
- **`export_backup()`** — decrypts before exporting (backups contain plaintext for portability)
- **`import_backup()`** — encrypts after importing

Uses existing `encryption_utils.py` (Fernet symmetric encryption).

No changes needed to `management_app.py` provider endpoints — encryption is transparent in the DB layer.

---

## 3. DB File Permissions

### Problem
`clawbedrock.db.json` and auto-backup files are created with default umask (typically `0o644`), meaning other users on the host could read them.

### Solution
- After `TinyDB(DB_PATH, ...)` opens the file: `os.chmod(DB_PATH, 0o600)`
- After writing auto-backup files in `_auto_backup_before_replace()`: `os.chmod(path, 0o600)`
- The `.encryption_salt` file is already `0o600` — no change needed.

---

## Files to Modify

| File | Change |
|------|--------|
| `src/db.py` | Encrypt/decrypt sensitive provider fields, chmod DB + backup files |
| `src/management_app.py` | Add `/api/security/encryption-status` endpoint |
| `src/encryption_utils.py` | Expose helper to check which encryption mode is active |
| `templates/partials/page_dashboard.html` | Add encryption warning banner div |
| `src/static/management.css` | Add banner styles |
| `src/static/js/init.js` | Call encryption status check on page load |
| `src/static/js/security.js` | Add `loadEncryptionStatus()` + banner dismiss logic |

## Files NOT Changed

- `static/js/providers.js` — provider CRUD already passes through `db.upsert_provider()`, encryption is transparent
- `templates/partials/page_security.html` — optional future enhancement to show encryption status

## Documentation Updates (Required)

After implementing the above changes, update these files to reflect the new security features:

### `README.md`
- Document the new `ENCRYPTION_KEY` and `ENCRYPTION_PASSWORD` environment variables
- Document the `DISABLE_ENCRYPTION_WARNING` env var
- Note that provider API keys are now encrypted at rest
- Mention the `0o600` file permissions on the DB file

### `templates/partials/page_help.html`
- Add a new "Security" section (or extend the existing Login section) covering:
  - How to set `ENCRYPTION_KEY` (preferred) or `ENCRYPTION_PASSWORD`
  - What the encryption warning banner means
  - That provider API keys are encrypted at rest
  - That `DISABLE_ENCRYPTION_WARNING=1` suppresses the banner for dev setups
