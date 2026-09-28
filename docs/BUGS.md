# Bugs found

## 1. Reload flashing is too dim + no nav-away confirmation modal

Reload LiteLLM button flashes when a reload is needed. It should be flashier. Also, when trying to navigate away from the Models screen, it should popup with a reload or dismiss screen.

## 2. API Key popup is top-left aligned and ignores dark/light mode

Should be centered and respect the dark mode or light mode

## 3. Provider dropdown should not have a `None`/manual config option — Bedrock should be the default

Providers are necessary. Default is bedrock since it should always exist.

## 4. Poll Models button on Bedrock Add New Model gives a pagination error; region should be configurable in Providers

"Error: Failed to connect to Bedrock in region us-east-1: Operation cannot be paginated: list_foundation_models. Check network and region name." Additionally, the region dropdowns should be configurable in Providers

## 5. Bedrock Mantle models don't get a provider tag

## 6. Provider page "Save Changes" does not persist after LiteLLM reload

On the Providers page, clicking "Save Changes" shows a "Provider updated" success popup, but after reloading LiteLLM the provider configuration reverts to its previous state. Changes are not actually being saved/written through to the underlying config.

## 7. Auto-backup writes plaintext provider API keys into CONFIG_DIR

`_auto_backup_before_replace` (`src/db.py:781-791`) calls `export_backup()`, which decrypts
provider fields (`src/db.py:688`), then writes the result to `CONFIG_DIR/auto-backup-{ts}.json`
at mode 0600. Every `mode=replace` backup import therefore leaves a file of plaintext API keys
in the config volume, partially defeating the at-rest encryption the rest of the DB relies on.

Covered by `.gitignore` (`auto-backup-*.json`), so it will not be committed — this is an
at-rest issue, not a VCS one. Note that `GET /api/backup/export` also writes plaintext by
design, so a downloaded backup is sensitive too.

Options: encrypt the sensitive fields in the snapshot, write it outside `CONFIG_DIR`, or
skip the snapshot entirely since the user already holds a backup.

## 8. Restoring a backup under a different ENCRYPTION_KEY yields a silently broken master key

`get_settings` (`src/db.py:160-163`) returns raw values, so `litellm_master_key` is stored as
ciphertext and round-trips through export/import *as ciphertext*. Import a backup that was
taken under a different `ENCRYPTION_KEY` and the stored value goes stale.

It then fails silently: `decrypt_data` (`src/encryption_utils.py:91-94`) catches every
exception and returns the token unchanged, so `get_master_key` (`src/db.py:223-228`) hands the
raw Fernet ciphertext to LiteLLM as `master_key`. The result is a misleading 401 rather than a
decryption error, and the UI's masked key display will show ciphertext.

Mitigation today: after any restore, regenerate via `POST /api/security/key/generate`.

Underlying issue: `decrypt_data` swallowing `InvalidToken` and returning the ciphertext is
load-bearing for backward compatibility, but it converts every decryption failure into a
silent wrong answer. A `decrypt_data(strict=True)` that re-raises would let callers
distinguish "not encrypted" from "encrypted under a different key".

## 9. Token refresher stampedes on concurrent auth failures

A burst of expiring credentials produced six forced refreshes in 15 seconds
(`Token refreshed at 23:49:04, :07, :11, :13, :16, :19`). Three compounding defects:

- `_refresh` (`src/token_refresher.py`) had no `threading.Lock`, so N concurrent failures
  caused N concurrent refreshes.
- `async_log_failure_event` calls `_refresh()` synchronously from inside an async hook,
  blocking the event loop so failures serialize and each one re-arms the next.
- `_is_expired_error` matched bare substrings (`"expired" in error_str`), so any error
  containing that word triggered a token refresh regardless of cause.
- `_force_refresh` was set by the failure hook but only cleared in `pre_call_hook`, so a
  failing stream kept it latched.

It self-resolves once a valid token exists, but it burns API calls and will misfire on any
unrelated error that happens to contain the word "expired".

## 10. BEDROCK_MANTLE_API_BASE overrides per-model path selection, misrouting half the catalog

`BEDROCK_MANTLE_API_BASE` looks like a harmless region hint, but it takes precedence over
per-model path derivation. In litellm's
`llms/bedrock_mantle/chat/transformation.py`:

```python
api_base = (
    api_base
    or get_secret_str("BEDROCK_MANTLE_API_BASE")
    or f".../{mantle_base_segment(model, ...)}"
)
```

`mantle_base_segment` picks `/openai/v1` vs `/v1` from each model's
`use_openai_responses_path` flag. Setting the env var short-circuits that. Since
`deploy/claw-bedrock.container.example` ships it set to the standard `/v1` base, every
gpt-5.x, gpt-6-*, gemma-4-* and grok-4.x model is forced onto the wrong path and fails as:

```
litellm.NotFoundError: BedrockException - {"code":"not_found_error",
"message":"The model 'openai.gpt-6-luna' does not exist"}
```

which reads as catalog churn rather than a routing misconfiguration. The responses path
(`responses/transformation.py`) strips and re-pins the host, so it cannot self-correct.

Fix: leave `BEDROCK_MANTLE_API_BASE` unset so litellm derives the segment per model, and
clear any explicit `api_base` on the bedrock provider (an explicit value wins by the same
precedence). The 42 `bedrock_mantle/*` models split cleanly: gpt-5.x, gpt-6-*, gemma-4-*,
grok-4.x use `/openai/v1`; claude-haiku-4-5, deepseek, kimi, qwen and gpt-oss use `/v1`. A
single hardcoded base cannot express that mix.

Also note `bedrock_mantle` exposes no OpenAI-compatible `/models` endpoint, so the UI's
generic "Poll Models" (`src/static/js/models.js:522`) 404s for this provider regardless of
configuration. Model discovery has to come from the price map or hand entry.
