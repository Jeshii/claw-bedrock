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

> **Correction (Step 2).** An earlier revision of this entry also claimed
> "`bedrock_mantle` exposes no OpenAI-compatible `/models` endpoint". That was
> wrong, and wrong in a way that mattered: it made model discovery look
> impossible and pushed a UI workaround into the codebase. Verified against the
> live endpoint:
>
> ```
> GET https://bedrock-mantle.us-east-1.api.aws/v1/models         -> 401 (exists; wants a bearer token)
> GET https://bedrock-mantle.us-east-1.api.aws/openai/v1/models  -> 404
> ```
>
> Discovery exists at `/v1/models` and takes the same `BEDROCK_MANTLE_API_KEY`
> the proxy uses. The 404 on `/openai/v1/models` is the real constraint, and it
> is a different one: the gpt-5.x, gpt-6-\*, gemma-4-\* and grok-4.x families are
> served on `/openai/v1`, so **a fetched list will not contain them**. Discovery
> can therefore supplement manual entry but must never replace it.

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

### The override is all-or-nothing on the OpenAI surface

The same variable means two different things depending on which surface reads it:

- **Anthropic surface** (`bedrock/*` → `/anthropic/v1/messages`): treated as a host. A
  trailing `/v1` or `/openai/v1` is deliberately stripped before the messages path is
  appended (`llms/bedrock/common_utils.py:804-805, 824-829`), so `.../v1` is handled
  correctly there.
- **OpenAI surface** (`bedrock_mantle/*`): used verbatim, suppressing per-model derivation
  (`llms/bedrock_mantle/chat/transformation.py:75-78`), and
  `llms/openai/chat/gpt_transformation.py:722-731` then appends `/chat/completions` to
  whatever it is given.

Upstream notes the asymmetry at `common_utils.py:820-822`.

There is no middle setting. A bare host (`https://bedrock-mantle.<region>.api.aws`) yields
`.../chat/completions` with no version segment, which is also wrong. So on the OpenAI
surface you can override the host *or* keep per-model path selection, never both. Since
`src/db.py` only injects `api_base` for `type: "openai-compatible"` providers and the
`bedrock` branch reads only `aws_region` (`src/db.py:433-435`), the environment variable is
the sole lever — there is no per-model escape hatch.

The variable exists for non-public endpoints: the docstring at
`llms/bedrock/common_utils.py:815-818` cites "private VPC / VPCE / GovCloud Mantle
endpoints", and `MANTLE_HOST_RE` (`common_utils.py:32`) matches any region including
`us-gov-*`. Region falls back to `AWS_REGION` when the variable is unset
(`common_utils.py:43-52`).

## 11. LiteLLM's log is not in `podman logs`, which sent a diagnosis down a dead end

`start_container.sh:56` redirects LiteLLM's stdout and stderr:

```bash
litellm --config "${CONFIG_PATH}" --port 4000 --host 0.0.0.0 > "${CONFIG_DIR}/litellm.log" 2>&1 &
```

So **LiteLLM's output only ever appears in `${CONFIG_DIR}/litellm.log`** (`/config/litellm.log`
on a normal deployment). `podman logs` shows the management UI and the entrypoint's own echo,
and will *never* contain `Uvicorn running on ...:4000` or a LiteLLM traceback. The management
UI's `exec > >(tee -a container.log)` on line 42 does not capture it either — the redirect is
per-command.

This cost a real diagnosis. An earlier note recorded that "LiteLLM's own log had produced no
`Uvicorn running on :4000` line, so it appears stuck or still initialising" — an inference
drawn from an absence the file layout guarantees. LiteLLM had been up the whole time.

```bash
podman exec claw-bedrock cat /config/litellm.log | tail -60
```

## 12. `/api/health/litellm` is an active model check, not a liveness probe

`GET /api/health/litellm` proxies LiteLLM's `/health`, which is **not** a liveness endpoint.
It runs `_perform_health_check_and_save` against every model in `model_list` unless
`general_settings.background_health_checks: true` is set — and this config does not set it
(`llms/../proxy/health_endpoints/_health_endpoints.py:1110-1140`).

Two consequences, both of which produced wrong conclusions:

1. **It is coupled to provider auth.** With Bedrock unauthenticated, nine models' worth of
   outbound calls each attempt a token refresh and fail, so the whole set exceeds the probe's
   5s budget and the endpoint returns `Read timed out`. This looks exactly like a dead proxy
   and is not one — `/config/litellm.log` shows `Uvicorn running on http://0.0.0.0:4000`.
2. **With zero models it passes vacuously.** In CI the container has no `model_list`, so the
   check returns 200 instantly. `scripts/smoke.sh` asserted on this endpoint, which is why CI
   reported `litellm_status == 200` while proving nothing about serving a request. The
   `linux/amd64` conclusion drawn from it is narrower than stated: the image boots and the
   management UI serves; whether LiteLLM serves *requests* on amd64 remains unproven.

`/health/liveliness` (`_health_endpoints.py:1938-1958`) is the actual liveness endpoint — it
only checks whether a graceful shutdown has begun, with no model list, no provider calls and no
auth. `smoke.sh` now asserts that. `/api/health/litellm` is deliberately **left** on the active
check: a red reading there means models genuinely cannot be served, and swapping it for
liveliness would make the dashboard green while Bedrock stayed dark.
