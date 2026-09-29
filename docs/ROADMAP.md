# Claw-Bedrock Development Roadmap

_Model Groups • Playground • Groups Dashboard • Cost Awareness • Bedrock Auth • Skills Library • MCP Integration_

---

## Implementation Order

Phases are named, not numbered. The numbering that used to live in the headings
was a poor proxy for sequence — it made unrelated workstreams look ordered by
intent, and inserting a new workstream between two completed ones would have
been dishonest. This list is the ordering.

| # | Workstream | Status | Depends on |
|---|---|---|---|
| 1 | Core Model Grouping & Auto-Failover | Complete | — |
| 2 | Playground V1 (Session-Scoped Chat) | Complete | — |
| 3 | Groups Dashboard | Complete | Core Model Grouping |
| 4 | Cost Awareness | Complete | Core Model Grouping |
| 5 | **Bedrock Auth Configuration** | **In progress** — step 1 of 3 shipped | — |
| 6 | Skills Library V1 (Browse / Manage) | Planned | — |
| 7 | Playground V2 (Persistent Conversations) | Planned | Playground V1 |
| 8 | Skills Library V2 (MCP Integration) | Planned | Skills Library V1 |
| 9 | Polish & Everything Else | Planned | varies — see table |

**Bedrock Auth Configuration is the active workstream.** It is independent of
the skills-library and playground-conversation threads, and it addresses a live
problem: the Bedrock auth surface collects configuration that does nothing and
reported status that was not accurate. Everything below it is unaffected by
finishing it first.

Resume here. The immediate task is not code — it is rebuilding the container so
Step 1's changes are actually running, then confirming the auth-error fix from
the shell. See the Step 1 section for the commands.

---

## Database Architecture

The database tier grows alongside the features. TinyDB stays for config-scale data; SQLite is introduced when conversations demand proper indexing and search.

| Data | Workstream | Store | Rationale |
|---|---|---|---|
| Models, Providers, Tags, Settings | Existing | TinyDB | Config-scale, few hundred records, CRUD only — already working |
| Skills | Skills Library V1 | TinyDB | Same profile as models — dozens to low hundreds, CRUD only |
| Conversations, Messages | Playground V2 | SQLite | Thousands of records, needs full-text search (FTS5), concurrent reads from watchdog + UI |

SQLite is ideal here: stdlib (no new deps), single file (`conversations.db` alongside `clawbedrock.db.json`), supports FTS5, WAL mode for concurrent reads, and is a clear stepping-stone to PostgreSQL if multi-user is ever needed.

---

## Core Model Grouping & Auto-Failover ✅

**Status:** Complete. All files in the table below shipped.

**Goal:** Add the `model_group` field to models. Transform config generation so grouped models share a `model_name` in LiteLLM (enabling native failover). Expose `router_settings` in the UI.

### Files modified

| File | Change |
|---|---|
| `src/db.py` | `get_models_for_litellm()` — emit `model_name: model_group` when set; add `get_router_settings()` exists, no change needed |
| `src/management_app.py` | PATCH allowed_fields add `"model_group"`; new `GET/POST /api/settings/router` endpoints |
| `src/static/js/models.js` | Group badge in model rows; inline group-name input; `updateModelGroup()`; `saveRouterSetting()` |
| `src/static/js/init.js` | `loadRouterSettings()` on page init |
| `templates/partials/page_models.html` | New "Router Settings" section (strategy, allowed_fails, retries) |
| `src/static/management.css` | `.group-badge` style |

### Key design decisions

- Backward compatibility is automatic — models without `model_group` use their own `model_name` as today.
- Models with `model_group` set get their LiteLLM `model_name` replaced by the group name.
- Groups with one member behave identically to ungrouped models.
- Router settings defaults if unset: LiteLLM uses its own defaults (simple-shuffle, no failover). The UI lets users opt in.

---

## Playground V1 (Session-Scoped Chat) ✅

**Status:** Complete. Markdown rendering and reasoning display were added beyond the original spec.

**Goal:** An inline chatbox in the management UI. Select a model, type a message, and stream a response. Conversation context is retained in browser memory for the current Playground session only; no database persistence, conversation list, or reload recovery. This establishes the message-state contract reused by Playground V2.

### Files created

| File | Purpose |
|---|---|
| `templates/partials/page_playground.html` | Chat UI layout — model selector, message input, conversation display |
| `src/static/js/playground.js` | In-memory `messages` array, sends full history, streams SSE responses, renders bubbles, supports New Chat/reset |

### Files modified

| File | Change |
|---|---|
| `src/management_app.py` | `POST /api/chat/completions` — proxies to LiteLLM's `/v1/chat/completions` with SSE streaming; `GET /api/chat/models` — returns LiteLLM's model list |
| `templates/management.html` | Add "Playground" nav item (between "Models" and "Tags") |
| `src/static/js/init.js` | Add `loadPlayground()` to page list |
| `src/static/management.css` | Playground styles — message bubbles, input area, streaming indicator |

### Streaming architecture

```python
@app.post("/api/chat/completions")
async def chat_completion(body: dict):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    resp = requests.post(
        f"{LITELLM_BASE_URL}/v1/chat/completions",
        json=body,
        stream=True,
    )
    return StreamingResponse(
        resp.iter_lines(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )
```

---

## Groups Dashboard ✅

**Status:** Complete. Implemented as specified, with two additions to the response shape — `active_member_count` per group and a config-derived `status` object per member. Follow-up pass added group management, group-name validation, the Router Settings relocation, and a full light/dark rebuild.

**Goal:** A dedicated page showing all model groups, their members, and per-member status.

### Files created

| File | Purpose |
|---|---|
| `templates/partials/page_groups.html` | Card view of groups — name, member count, member list with provider badges, rename/unassign/add-member controls, Router Settings |
| `src/static/js/groups.js` | Load groups from API, render, expand members, group management actions, Router Settings |

### Files modified

| File | Change |
|---|---|
| `src/management_app.py` | `GET /api/model-groups` — aggregates models by `model_group`, returns grouped structure with member details. `POST /api/model-groups/rename` and `POST /api/model-groups/unassign` — bulk group operations with name validation |
| `templates/management.html` | Add "Groups" nav item (between "Playground" and "Tags") |
| `src/static/js/init.js` | Add `loadGroups()` |
| `src/static/js/models.js` | Group input becomes a datalist of existing groups; `setModelGroup()` shared with the Groups page |
| `src/static/management.css` | Groups page styles; design-token layer and `light-dark()` rebuild |

### API response shape

```json
GET /api/model-groups
{
  "groups": [
    {
      "name": "sonnet",
      "member_count": 2,
      "active_member_count": 1,
      "members": [
        { "model_name": "claude-3-5-sonnet-v2", "provider": "bedrock",
          "litellm_params": { ... }, "_provider": { ... },
          "status": { "level": "ok", "detail": "" } },
        { "model_name": "claude-3-5-sonnet-v1", "provider": "bedrock",
          "status": { "level": "error",
            "detail": "Provider 'bedrock-v1' no longer exists" } }
      ]
    }
  ],
  "ungrouped_count": 5,
  "ungrouped_models": ["claude-haiku-3-5", "gemini-2-0-flash"]
}

POST /api/model-groups/rename    { "from": "sonnet", "to": "sonnet-v2" }
  -> 200 { "renamed": 2, "from": "sonnet", "to": "sonnet-v2" }
  -> 400 invalid name (allowlist: letters, numbers, dot, dash, underscore)
  -> 404 no such group
  -> 409 target group already exists (rename never merges)

POST /api/model-groups/unassign  { "name": "sonnet-v2" }
  -> 200 { "cleared": 2, "name": "sonnet-v2" }
```

### Design notes

- **Status is config-derived, not live.** There is no per-model health source in the codebase (only a LiteLLM-wide `/health` poller), so probing was deliberately deferred. Status answers "will this model be in the generated config?":
  - `error` — no provider assigned, or the referenced provider no longer exists
  - `warn` — `litellm_params` absent or missing the required `model` key
  - `ok` — will be emitted to the config as-is
- **`active_member_count` counts members that survive config generation.** `db._merge_provider_defaults` silently skips models whose provider is dangling, so a group can hold N models in TinyDB while shipping fewer to LiteLLM. The header chip surfaces this drift (`"1 of 2 in config"`). This is the highest-value signal on the page and cost nothing to add.
- **A group name is a public API surface.** `db.get_models_for_litellm` makes it the `model_name` clients call, prefixed `claw-bedrock/` when `use_prefix` is on. Two consequences drove the design: names are validated against an allowlist (nothing checked them before, so whitespace produced an uncallable model name), and **rename is a breaking change** — the UI confirms with the old and new model ids spelled out. Rename also refuses to merge onto an existing group, because that would silently repoint every client of the losing name.
- **The typo problem is fixed at the source, not just in cleanup.** The Groups page was originally read-only, so a typo could only be cleared by editing every member by hand. The Models page Group input is now a `datalist` of existing names, so near-duplicates cannot be created in the first place.
- **Router Settings moved here.** `routing_strategy`, `allowed_fails` and `num_retries` are per-group failover behavior but lived on the Models page, the one place you cannot see a group.
- Live health probing and "all members down" notifications remain Polish work alongside the watchdog.

---

## Cost Awareness ✅

**Status:** Complete. The original spec emitted costs only to `model_info`; that was wrong and is corrected below.

**Goal:** Let users set `input_cost` / `output_cost` on models. Enable `routing_strategy: "cost-based-routing"` to automatically prefer the cheapest healthy member in a group.

### Files modified

| File | Change |
|---|---|
| `src/db.py` | `_apply_model_costs()` converts stored $/1M to per-token and writes `litellm_params` + `model_info`; `_strip_ui_only_fields()` drops UI bookkeeping from the config |
| `src/management_app.py` | PATCH `allowed_fields` add `"input_cost"`, `"output_cost"` with validation and clearing; `GET /api/model-groups` reports the active `routing_strategy` |
| `src/static/js/models.js` | Cost inputs in the model detail section; `updateModelCost()`; OpenRouter catalog pricing auto-filled on add |
| `src/static/js/groups.js` | Per-member price chip, cheapest-member chip, "no cost" warning under cost-based routing |
| `src/static/js/utils.js` | `formatCost()` / `hasCost()` |
| `src/static/management.css` | `.cost-input`, `.cost-chip`, `.model-detail-costs` |
| `templates/partials/page_groups.html` | Routing strategy literals corrected (see below) |

### Per-model cost fields

```json
{
  "model_name": "claude-3-5-sonnet-v2",
  "model_group": "sonnet",
  "input_cost": 3.0,      # dollars per 1M input tokens
  "output_cost": 15.0,    # dollars per 1M output tokens
  "litellm_params": { ... }
}
```

### What the generated config actually needs

The spec originally said to emit `model_info: { input_cost, output_cost }`. **LiteLLM's router never reads that.** `router_strategy/lowest_cost.py` prices a deployment from, in order:

1. `litellm_params.input_cost_per_token` / `output_cost_per_token`
2. `litellm.model_cost[litellm_params.model]`

`model_info` feeds spend reporting, not routing. So both values are written to `litellm_params` (per token) and mirrored into `model_info`.

The fallback in (2) is the trap: it is a direct dict lookup, so a provider-prefixed name like `bedrock_mantle/…` or `openai/…` misses and defaults to **$5/$5** ([BerriAI/litellm#35787](https://github.com/BerriAI/litellm/issues/35787)). An unpriced model does not read as "unknown" to the router — it reads as one of the most expensive things in the group and is never picked. That is why the Groups page flags a member with no cost while cost-based routing is active, and why OpenRouter prices are auto-filled rather than left to manual entry.

### Units

Stored and entered as **dollars per 1M tokens**, which is how Bedrock and OpenRouter quote. The `/1000/1000` conversion happens in exactly one place (`_apply_model_costs`). A cost of `0` is a real price (free) and is kept, distinct from unset.

### Config cleanliness

`get_models_for_litellm()` used to copy every stored field into each `model_list` entry, so `provider`, `tags` and `reasoning_effort` all shipped to LiteLLM. They are now dropped via `UI_ONLY_MODEL_FIELDS`. This is a **denylist**, not an allowlist, on purpose: records migrated from an older `config.local.yaml` can carry genuine top-level LiteLLM keys (`rpm`, `tpm`) that an allowlist would silently discard.

### Routing strategy literals

Groups Dashboard shipped a dropdown writing `latency-based`, `usage-based` and `cost-based`. LiteLLM takes `latency-based-routing`, `usage-based-routing` and `cost-based-routing`; the short forms are ignored at proxy startup, so the setting looked applied while routing stayed on the default. Corrected in Cost Awareness, since selecting "Cost-based" is the entire point of that workstream. `db.normalize_routing_strategy()` maps the old values forward so existing installs heal without a migration, and `POST /api/settings/router` rejects anything unrecognized instead of storing a value that does nothing.

### Auto-populated pricing

OpenRouter's model catalog already returned a `pricing` block (`prompt` / `completion`, per token as strings), so choosing a model in the add form fills in both cost fields. This was originally slated for Polish; it was cheap enough to do here because the data was already on the wire.

---

## Bedrock Auth Configuration

**Status:** Step 1 shipped but unverified in the running container. Steps 2 and 3 not started.

**Goal:** Make the Bedrock auth surface tell the truth, then move auth configuration out of dead form fields and into settings that actually take effect. The governing principle: **the GUI is the friendly space, environment variables are expert mode.** Anything the GUI can own, it should; anything the environment owns, the GUI yields to and warns about.

Precedence follows the unix chain — **flags → environment variables → config store → GUI** — where config outranks the GUI, so an expert who sets an env var is never overridden by a form they filled in months ago. The GUI's job when it loses is to say so by name.

### Why this exists

The Bedrock provider form currently asks for three AWS fields. Investigation found that none of them work:

- `aws_access_key_env` and `aws_secret_key_env` are stored, rendered, validated and round-tripped, and read by **no runtime code**. `aws_secret_key_env` is worse: matched as sensitive by the substring `secret`, it gets encrypted (meaningless crypto on the env var *name* `AWS_SECRET_ACCESS_KEY`), nulled by `sanitize_provider_for_response` so the edit form always renders it blank, and then written back empty by `saveProviderDetail`, which pops the stored value. Opening a Bedrock provider, renaming it, and saving destroys the field silently.
- `aws_region` is read but written to `litellm_params["aws_region"]`, which LiteLLM ignores — it reads `aws_region_name`. The field looks functional and is inert.

Auth actually comes from the token refresher, entirely out of band: `AWS_PROFILE` + `AWS_REGION` → `boto3.Session(...)` → `get_token()` → `os.environ["BEDROCK_MANTLE_API_KEY"]`. The provider record plays no part.

### Step 1 — Auth Status Truth ✅

**Status:** Code complete and pushed (`8c5ff71`, `8a75317`, `7b8858e`, `a10c0df`). 198 tests pass, lint clean. **Not yet verified against the running container** — see below.

Read-only with respect to configuration: nothing about how auth works changed, only what the app reports about it.

| File | Change |
|---|---|
| `src/settings_resolver.py` | **New.** `resolve()` walks flag → env → config → default, returns `(value, source, shadowed)`. One resolver so precedence cannot drift between the engine and the warning badges |
| `src/management_app.py` | Extended `GET /api/auth/status` with a `bedrock` block. All five keys the auth UI depends on preserved |
| `src/token_refresher.py` | `peek_auth_error()` replaces the read-and-clear accessor; `_auth_error` now cleared on successful refresh and at the start of a retry; shared `auth_status_payload()` for both status endpoints |
| `src/static/js/auth.js` | Renders the `bedrock` block with source badges; warning codes mapped to prose in JS |
| `src/static/management.css` | `.resolved-*` and `.source-badge` styles |
| `tests/test_settings_resolver.py` | **New.** 21 tests — precedence, blank-value handling, namespacing, statelessness |
| `tests/test_auth_status.py` | **New.** 30 tests — response shape, provenance, token arithmetic, error lifecycle, no credential leakage |

**The bug this fixed.** `get_auth_error()` was read-and-clear (`token_refresher.py:460-464`, the docstring said so) and `management_app.py` called it from inside the status endpoint, while `auth.js` re-polls every **1 second** during the login flow. An error could be consumed by a poll before it was ever displayed. The proxy's own `/auth/status` read `self._auth_error` directly and did not drain it — two status endpoints with divergent semantics, one destructive.

Read-and-clear was also doing double duty as the de facto reset, since nothing else cleared `_auth_error`. Peeking alone would have left a stale error on the page forever, so the clear moved to the two points where the error genuinely stops being true.

**Two implementation decisions worth knowing.** `default` is excluded from `shadowed` — it is a fallback, not a choice anyone made, so "overrides config, default" is not actionable. And the endpoint reports the *refresher's* region and profile rather than the resolver's, because those are what the token was actually minted against; the resolver only classifies provenance, which makes a disagreement visible instead of silently wrong.

### Incident — container could not boot (fixed, uncommitted)

**Status:** Cause found, fixed, and verified locally. **Not committed and not pushed.** The live service is still down.

**Symptom.** The management UI died on startup with `ModuleNotFoundError: No module named 'settings_resolver'`, from `management_app.py:30`. Because `start_container.sh` ends in `wait ${MGMT_PID}`, the app exiting took the container with it, so port 8282 stopped serving entirely and the unit sat in its `Restart=on-failure` loop rather than running degraded.

**Cause.** `.github/Containerfile` copied each Python module with its own `COPY` line, and `settings_resolver.py` was added to `src/` in `8c5ff71` without a matching line. The image built clean and every test passed. This is the second time that list lost a module — `8a9ff62` recovered `encryption_utils.py` and `password_utils.py` the same way — which is why the list was removed rather than extended.

**Confirmed on the running host**, not inferred: `/app/VERSION` read `dev-4fca2c6` (HEAD, the `dev-${GITHUB_SHA::7}` build arg), and `ls /app` held exactly the five enumerated modules with no `settings_resolver.py`. The image was current, not stale, which is what the previous note on this section assumed. The unit already reads `ghcr.io/jeshii/claw-bedrock:develop`, so no unit change is needed.

**What is done.**

| File | Change |
|---|---|
| `.github/Containerfile` | Five enumerated `COPY src/*.py` lines → `COPY src/*.py .`, so a new module cannot be omitted |
| `deploy/start_container.sh` | `init_configs()` skips the copy when source and destination are the same file. The image sets `CONFIG_DIR=/app`, so an unmounted boot ran `cp` with identical paths, `cp` exited 1 with "are the same file", and `set -euo pipefail` killed the container before either process started. Deployments never hit it because `CONFIG_DIR` points at a mounted volume |
| `scripts/smoke.sh` | **New.** Boots the image and asserts it serves. Called by `build-container.yml` between build and push, so a broken image is never published. Honours `$CONTAINER_ENGINE`; `SKIP_BUILD=1` tests an existing image |
| `.github/workflows/build-container.yml` | Build now `load: true` without pushing, smoke test runs, then a separate push step. Added `cache-from`/`cache-to: type=gha` — the AWS CLI and pip layers are most of the build and change rarely |
| `tests/test_containerfile.py` | **New.** 12 tests. Static half of the same guarantee: every `src/*.py` is copied, plus `templates`/`static`/`start_container.sh` |
| `AGENTS.md`, `docs/FILE_STRUCTURE.md`, `docs/CHANGELOG.md` | Smoke test is now part of the pre-commit loop; scratch files go in `.scratch/` |

**Verified.** 210 tests pass (198 + 12). Image built with podman and confirmed to contain `settings_resolver.py`; `scripts/smoke.sh` passed end to end against it, asserting `/api/version` and `litellm_status == 200`. Reverting the Containerfile to the old enumerated list makes exactly one test fail, `test_module_is_copied[settings_resolver.py]`, and no other — so the test is specific rather than failing broadly.

The health assertion is deliberately stricter than `AGENTS.md` used to state. `management_app.py:1646` returns `status: ok` whenever the probe does not *raise*, so a 404 or 500 from LiteLLM still reads as healthy; the smoke test also asserts `litellm_status == 200`.

**Two things to know before resuming.**

The smoke test builds for the host arch, so a local arm64 run proves the app boots but not that the published `linux/amd64` image does. The first CI run is the real check.

`lint.sh` is currently red on this machine, for a reason unrelated to the above and not yet explained. `djlint` intermittently fails with `Path 'templates/' is not readable`. Ruled out so far: the permissions are correct (`os.access` returns `True` in 2000/2000 calls from fresh processes, and `ls`/`stat` agree), it fails identically at clean `HEAD` with the working tree stashed, and `ruff`, `biome`, and the lock check all pass. The pattern looks cold-start or macOS-`TCC`-related rather than repo-related — this repo sits in `~/Documents`, and an unrelated `git stash` also hit `Operation not permitted` on `.git/config` from a `com.apple.provenance` xattr. `brew doctor` is the obvious next thing to rule out, but the evidence so far does not point at Homebrew. **Nothing may be committed until this is resolved**, per the `AGENTS.md` rule.

**To finish, once lint is green:** push `develop` (CI builds and smoke-tests `:develop`), then tag and push `v0.1.2`. The `latest` tag is gated on `startsWith(github.ref, 'refs/tags/v')`, which is correct as written, so tagging a `v*` release does move `latest` — currently frozen at `v0.1.1` from June simply because no newer tag has been pushed. Tagging publishes everything since that release, a history not yet audited. Then on the host:

```bash
podman pull ghcr.io/jeshii/claw-bedrock:develop
systemctl --user restart claw-bedrock
sleep 15
podman exec claw-bedrock ls /app | grep settings_resolver
curl -s http://127.0.0.1:8282/api/health/litellm | python3 -m json.tool
```

Then confirm the Step 1 fix by calling `/api/auth/status` **twice** — `auth_error` must be identical both times, which is the shell-visible version of the regression test. If `warnings` contains `env_credentials_shadowed_by_profile`, that is live confirmation of the botocore `disable_env_vars` behavior; an empty list leaves that path untested against reality until Step 2.

### Step 2 — GUI Bedrock Auth Configuration

A settings section below Manage Providers on the Providers page, matching how settings sit below the main content on other pages.

| File | Change |
|---|---|
| `src/db.py` | Encrypted settings path (`set_setting` is plaintext today); read-time migration off the dead `aws_*_env` fields |
| `src/token_refresher.py` | `configure()` — re-read region/profile/creds, rebuild the session, force a refresh, no container restart. Store the profile so the login flow targets it |
| `src/management_app.py` | `GET/PUT` settings endpoints; drop `aws_access_key_env` / `aws_secret_key_env` from allowed fields, the encrypt loop, and runtime-change detection |
| `src/static/js/providers.js` | Remove the two dead fields; add the settings card with region, profile, and a collapsed advanced section |
| `templates/partials/page_providers.html` | New settings section below Manage Providers |

**Long-lived IAM keys are restored, not dropped.** `token_refresher` always calls `boto3.Session(profile_name=self._profile)`, and botocore sets `disable_env_vars` whenever a profile is explicitly passed (`credentials.py:95`), skipping the env-var provider unless *all three* of `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` are set. The non-SSO path is broken today. The fix: when a key pair is configured, pass the credentials explicitly and **omit `profile_name`**.

The key pair is opt-in behind a collapsed advanced section and encrypted at rest. The section notes that the equivalent env vars also work, and which one wins.

**Region is a global setting, not a per-provider field.** One refresher, one `self._region`, one `BEDROCK_MANTLE_API_KEY` in `os.environ` — a second provider in another region would present a token minted for the first. A per-provider region field is a lie regardless of the key-name fix, so it is replaced by a global free-text field, soft-validated on set. Validation warns rather than blocks: botocore can list Bedrock regions offline (`get_available_regions('bedrock')`, 33 regions, no network), but that is the **public partition only** and would falsely reject GovCloud and regions AWS adds later. A wrong region fails loudly at token-mint time; a false rejection would be worse.

### Step 3 — Mantle Model Discovery

| File | Change |
|---|---|
| `src/management_app.py` | `BEDROCK_MANTLE_API_KEY` fallback in `fetch_provider_models` so `/v1/models` works with the in-process token; return an actionable error when login is required |
| `src/db.py` | Seed the Bedrock provider's `api_base` from the global region — **provider record only, never injected into `litellm_params`**, or the all-or-nothing `api_base` override returns |
| `src/static/js/models.js` | "Fetch Models" mirroring the Ollama flow, manual entry retained; delete the caveat wall |

The Bedrock add-model form is currently the least helpful of the four: OpenRouter gets a searchable list, Ollama gets a Fetch Models button, and Bedrock gets a bare text input wrapped in two paragraphs of defensive caveats. Those caveats existed because polling Bedrock models was previously impossible. They are tech debt, not warnings anyone still needs.

**Discovery is available.** `https://bedrock-mantle.<region>.api.aws/v1/models` exists — the OpenAI-compatible client `models.list()` path. An earlier entry in `docs/BUGS.md` claiming otherwise is wrong and needs correcting. Per-model path derivation already handles the GPT-5.6+/GPT-6 family, so `BEDROCK_MANTLE_API_BASE` should stay unset.

---

## Skills Library V1 (Browse / Manage)

**Goal:** A managed repository of skills in TinyDB. Create, edit, delete, browse, search skills.

### Files created

| File | Purpose |
|---|---|
| `templates/partials/page_skills.html` | List view with search/filter + detail/edit panel with code editor |
| `src/static/js/skills.js` | CRUD operations, code editing |

### Files modified

| File | Change |
|---|---|
| `src/db.py` | New `skills` table; CRUD functions |
| `src/management_app.py` | CRUD endpoints: `GET/POST /api/skills`, `GET/PUT/DELETE /api/skills/{name}` |
| `templates/management.html` | Add "Skills" nav item |
| `src/static/js/init.js` | Add `loadSkills()` |
| `src/static/management.css` | Skills page styles |

### Skill schema

```json
{
    "name": "deploy-to-ecs",              # unique
    "description": "Deploy a container to ECS with Fargate",
    "content": "#!/usr/bin/env python3\n...",  # code or prompt text
    "type": "python",                      # python | shell | prompt | template
    "tags": ["aws", "deployment"],
    "created_at": "2026-06-09T12:00:00Z",
    "updated_at": "2026-06-09T12:00:00Z",
}
```

---

## Playground V2 (Persistent Conversations)

**Goal:** Conversations persist to SQLite. Users can create, rename, search, and delete conversations. Messages auto-save during chat. Full-text search across all messages.

### Files created

| File | Purpose |
|---|---|
| `src/conversations_db.py` | SQLite connection management, table creation, CRUD for conversations + messages |

### Files modified

| File | Change |
|---|---|
| `src/management_app.py` | REST endpoints for conversations + messages; `startup_event()` calls `conversations_db.init_db()` |
| `src/static/js/playground.js` | Add conversation sidebar — list, create, rename, delete, search; replace in-memory state with SQLite persistence |
| `templates/partials/page_playground.html` | Restructure for persistent mode — sidebar + main area |
| `src/static/management.css` | Conversation list, search box, sidebar layout |

### SQLite schema

```python
def init_db():
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_calls TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conv
            ON messages(conversation_id, created_at);
    """)
```

### API endpoints

```
GET    /api/conversations?search=&page=&per_page=
POST   /api/conversations               — body: { title, model, first_message }
GET    /api/conversations/{id}
PUT    /api/conversations/{id}           — rename or change model
DELETE /api/conversations/{id}
POST   /api/conversations/{id}/messages  — saves message, streams response, saves reply
```

---

## Skills Library V2 (MCP Integration)

**Goal:** Skills become callable by models during chat. An MCP server reads from the skills TinyDB and exposes each skill as an MCP tool. LiteLLM config includes `mcp_settings` pointing to this server.

### Files created

| File | Purpose |
|---|---|
| `src/mcp_skills_server.py` | MCP server (stdio transport) — reads skills from TinyDB, exposes as MCP tools |

### Files modified

| File | Change |
|---|---|
| `src/management_app.py` | Config generation adds `mcp_settings` to the output YAML |
| `src/static/js/playground.js` | Show tool calls/responses inline in the conversation display |
| `src/static/js/skills.js` | "Test" button — invoke a skill and show output |
| `src/static/management.css` | Tool call styling in chat |

### Config generation addition

```yaml
mcp_settings:
  servers:
    claw-skills:
      command: "python"
      args: ["/app/mcp_skills_server.py"]
      type: "stdio"
```

### Skill execution modes

- `type: "python"` — runs the code in a subprocess, returns stdout
- `type: "shell"` — runs as a shell command
- `type: "prompt"` — returns the content as text for the model to consume (no execution)
- `type: "template"` — same as prompt, with argument interpolation

---

## Polish & Everything Else

| Feature | Effort | Depends on |
|---|---|---|
| Group filter in model list (composable with tag filter) | Small | Core Model Grouping |
| Drag-to-reorder within groups (`group_position` field) | Medium | Core Model Grouping |
| Notifications when all group members down | Medium | Core Model Grouping + watchdog |
| Auto-populate costs from Bedrock APIs (OpenRouter already done) | Small | Cost Awareness |
| In-browser code editor (Monaco/CodeMirror) for skills | Medium | Skills Library V1 |
| Conversation auto-title from LLM (first message → title) | Small | Playground V2 |
| Conversation export (JSON / Markdown) | Small | Playground V2 |
| FTS5 full-text search for conversations | Small | Playground V2 |
| Skills sandboxing (subprocess resource limits, read-only FS) | Medium | Skills Library V2 |
| Conversation light/dark theme toggle for chat bubbles | Small | Playground V1 |
| Migration scripts (if TinyDB skills → SQLite) | Medium | Skills Library V1+ |
| Fix `_reload_litellm_config` treating any status < 500 as success | Small | — |
| Investigate the unexplained 5xx on `POST /config/update` | Medium | — |

---

## Navigation Structure Evolution

```
Initial:         Dashboard | Auth | Security | Providers | Models | Tags | Backup | Logs | Help
Playground V1:   ... Models | Playground | Tags ...
Groups Dashboard:... Models | Playground | Groups | Tags ...
Cost Awareness:  ... Models | Playground | Groups | Tags ...   (no nav change)
Bedrock Auth:    ... Models | Playground | Groups | Tags ...   (no nav change)
Skills Library V1:... Models | Playground | Groups | Skills | Tags ...
```

Final nav order: `Dashboard | Auth | Security | Providers | Models | Playground | Groups | Skills | Tags | Backup | Logs | Help`

---

## Dependency Graph

```
Core Model Grouping
  ├── Groups Dashboard (depends on model_group existing)
  ├── Cost Awareness (depends on model_group existing)
  └── Polish: group filter, drag-to-reorder (depends on model_group existing)

Playground V1
  └── Playground V2 (depends on chat proxy existing)

Skills Library V1
  └── Skills Library V2 (MCP) (depends on skills DB existing)

Bedrock Auth Configuration — standalone; depends on nothing and nothing on it.
```

---

## Total File Inventory

### Files to create (by workstream)

| Workstream | Status | Files |
|---|---|---|
| Playground V1 | done | `page_playground.html`, `playground.js` |
| Groups Dashboard | done | `page_groups.html`, `groups.js` |
| Bedrock Auth Configuration | next | `settings_resolver.py` |
| Skills Library V1 | planned | `page_skills.html`, `skills.js` |
| Playground V2 | planned | `conversations_db.py` |
| Skills Library V2 | planned | `mcp_skills_server.py` |

### Files to modify (cumulative, all workstreams)

| File | Workstreams |
|---|---|
| `src/db.py` | Core Grouping, Cost Awareness, Bedrock Auth, Skills Library V1, Skills Library V2 |
| `src/management_app.py` | Core Grouping, Playground V1, Groups Dashboard, Cost Awareness, Bedrock Auth, Skills Library V1, Playground V2, Skills Library V2 |
| `src/token_refresher.py` | Bedrock Auth |
| `src/static/js/models.js` | Core Grouping, Cost Awareness, Bedrock Auth |
| `src/static/js/playground.js` | Playground V1, Playground V2 |
| `src/static/js/groups.js` | Groups Dashboard, Cost Awareness |
| `src/static/js/auth.js` | Bedrock Auth |
| `src/static/js/providers.js` | Bedrock Auth |
| `src/static/js/init.js` | Core Grouping, Playground V1, Groups Dashboard, Skills Library V1 |
| `src/static/js/navigation.js` | Groups Dashboard (and any workstream adding a nav item) |
| `src/static/js/utils.js` | Groups Dashboard, Cost Awareness (shared formatting helpers) |
| `src/static/management.css` | Core Grouping, Playground V1, Groups Dashboard, Cost Awareness, Bedrock Auth, Skills Library V1, Playground V2, Skills Library V2 |
| `templates/management.html` | Playground V1, Groups Dashboard, Skills Library V1 |
| `templates/partials/page_auth.html` | Bedrock Auth |
| `templates/partials/page_models.html` | Core Grouping |
| `templates/partials/page_playground.html` | Playground V1, Playground V2 |
| `templates/partials/page_providers.html` | Bedrock Auth |
| `templates/partials/page_groups.html` | Cost Awareness (router strategy literals) |
| `templates/partials/page_skills.html` | Skills Library V2 (tool call rendering) |

> **Note for future workstreams:** any one that adds a nav item must touch three
> places — the `<ul class="nav">` list and the `{% include %}` block in
> `templates/management.html`, plus a single dispatch branch in `activatePage()`
> in `navigation.js`. Groups Dashboard consolidated the previously-duplicated
> dispatch so there is now only one place to edit.

---

## Estimated Implementation Time

| Workstream | DB | New Frontend Files | Estimate | Status |
|---|---|---|---|---|
| Core Model Grouping & Auto-Failover | TinyDB (model_group field) | — | 2-3 days | done |
| Playground V1 (Session-Scoped Chat) | — | 2 | 2-3 days | done |
| Groups Dashboard | — | 2 | 1-2 days | done |
| Cost Awareness | TinyDB (cost fields) | — | 1 day | done |
| Bedrock Auth Configuration — Step 1 | — | — | 0.5 day | next |
| Bedrock Auth Configuration — Step 2 | TinyDB (encrypted settings) | — | 1-2 days | planned |
| Bedrock Auth Configuration — Step 3 | — | — | 0.5-1 day | planned |
| Skills Library V1 (Browse / Manage) | TinyDB (skills table) | 2 | 2-3 days | planned |
| Playground V2 (Persistent Conversations) | SQLite (conversations.db) | — | 3-4 days | planned |
| Skills Library V2 (MCP Integration) | — | — | 3-4 days | planned |
| Polish & Everything Else | — | — | 2-3 days | planned |
