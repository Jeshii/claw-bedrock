# Claw-Bedrock Development Roadmap

_Model Groups • Playground • Groups Dashboard • Cost Awareness • Skills Library • MCP Integration_

---

## Database Architecture

The database tier grows alongside the features. TinyDB stays for config-scale data; SQLite is introduced when conversations demand proper indexing and search.

| Data | Phase | Store | Rationale |
|---|---|---|---|
| Models, Providers, Tags, Settings | Existing | TinyDB | Config-scale, few hundred records, CRUD only — already working |
| Skills | Phase 5 | TinyDB | Same profile as models — dozens to low hundreds, CRUD only |
| Conversations, Messages | Phase 6 | SQLite | Thousands of records, needs full-text search (FTS5), concurrent reads from watchdog + UI |

SQLite is ideal here: stdlib (no new deps), single file (`conversations.db` alongside `clawbedrock.db.json`), supports FTS5, WAL mode for concurrent reads, and is a clear stepping-stone to PostgreSQL if multi-user is ever needed.

---

## Phase 1 — Core Model Grouping + Auto-Failover ✅

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

## Phase 2 — Playground V1 (Session-Scoped Multi-Turn Chat) ✅

**Status:** Complete. Markdown rendering and reasoning display were added beyond the original spec.

**Goal:** An inline chatbox in the management UI. Select a model, type a message, and stream a response. Conversation context is retained in browser memory for the current Playground session only; no database persistence, conversation list, or reload recovery. This establishes the message-state contract reused by Phase 6.

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

## Phase 3 — Groups Dashboard ✅

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
- Live health probing and "all members down" notifications remain Phase 8 work alongside the watchdog.

---

## Phase 4 — Cost Awareness ✅

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

```python
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

Phase 3 shipped a dropdown writing `latency-based`, `usage-based` and `cost-based`. LiteLLM takes `latency-based-routing`, `usage-based-routing` and `cost-based-routing`; the short forms are ignored at proxy startup, so the setting looked applied while routing stayed on the default. Corrected in the same phase, since selecting "Cost-based" is the entire point of Phase 4. `db.normalize_routing_strategy()` maps the old values forward so existing installs heal without a migration, and `POST /api/settings/router` rejects anything unrecognized instead of storing a value that does nothing.

### Auto-populated pricing

OpenRouter's model catalog already returned a `pricing` block (`prompt` / `completion`, per token as strings), so choosing a model in the add form fills in both cost fields. This was Phase 8 work; it was cheap enough to do here because the data was already on the wire.

---

## Phase 5 — Skills Library V1 (Browse / Manage)

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

```python
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

## Phase 6 — Playground V2 (Persistent Conversations)

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

## Phase 7 — Skills Library V2 (MCP Integration)

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

## Phase 8 — Polish & Everything Else

| Feature | Effort | Dependencies |
|---|---|---|
| Group filter in model list (composable with tag filter) | Small | Phase 1 |
| Drag-to-reorder within groups (`group_position` field) | Medium | Phase 1 |
| Notifications when all group members down | Medium | Phase 1 + watchdog |
| Auto-populate costs from Bedrock APIs (OpenRouter done in Phase 4) | Small | Phase 4 |
| In-browser code editor (Monaco/CodeMirror) for skills | Medium | Phase 5 |
| Conversation auto-title from LLM (first message → title) | Small | Phase 6 |
| Conversation export (JSON / Markdown) | Small | Phase 6 |
| FTS5 full-text search for conversations | Small | Phase 6 |
| Skills sandboxing (subprocess resource limits, read-only FS) | Medium | Phase 7 |
| Conversation light/dark theme toggle for chat bubbles | Small | Phase 2 |
| Migration scripts (if TinyDB skills → SQLite) | Medium | Phase 5+ |

---

## Navigation Structure Evolution

```
Phase 0 (done):        Dashboard | Auth | Security | Providers | Models | Tags | Backup | Logs | Help
Phase 2 (done):        ... Models | Playground | Tags ...
Phase 3 (done):        ... Models | Playground | Groups | Tags ...
Phase 4 (done, current): ... Models | Playground | Groups | Tags ...   (no nav change)
Phase 5 (planned):     ... Models | Playground | Groups | Skills | Tags ...
```

Final nav order: `Dashboard | Auth | Security | Providers | Models | Playground | Groups | Skills | Tags | Backup | Logs | Help`

---

## Dependency Graph

```
Phase 1: Core Grouping
  ├── Phase 3: Groups Dashboard (depends on model_group existing)
  ├── Phase 4: Cost Awareness (depends on model_group existing)
  └── Phase 8: Group filter, drag-to-reorder (depends on model_group existing)

Phase 2: Playground V1
  └── Phase 6: Playground V2 (depends on chat proxy existing)

Phase 5: Skills Library V1
  └── Phase 7: Skills MCP (depends on skills DB existing)

Phases 2, 5 are independent of each other and of Phases 3, 4, 8.
Phases 1, 2, 5 are the three foundation layers (routing, testing, extending).
```

---

## Total File Inventory

### Files to create (by phase)

| Phase | Status | Files |
|---|---|---|
| 2 | done | `page_playground.html`, `playground.js` |
| 3 | done | `page_groups.html`, `groups.js` |
| 5 | planned | `page_skills.html`, `skills.js` |
| 6 | planned | `conversations_db.py` |
| 7 | planned | `mcp_skills_server.py` |

### Files to modify (cumulative, all phases)

| File | Phases |
|---|---|
| `src/db.py` | 1, 4, 5, 7 |
| `src/management_app.py` | 1, 2, 3, 4, 5, 6 |
| `src/static/js/models.js` | 1, 4 |
| `src/static/js/playground.js` | 2, 6 |
| `src/static/js/groups.js` | 3, 4 |
| `src/static/js/init.js` | 1, 2, 3, 5 |
| `src/static/js/navigation.js` | 3 (and any phase adding a nav item) |
| `src/static/js/utils.js` | 3, 4 (shared formatting helpers) |
| `src/static/management.css` | 1, 2, 3, 4, 5, 6, 7 |
| `templates/management.html` | 2, 3, 5 |
| `templates/partials/page_models.html` | 1 |
| `templates/partials/page_playground.html` | 2, 6 |
| `templates/partials/page_groups.html` | 4 (router strategy literals) |
| `templates/partials/page_skills.html` | 7 (tool call rendering) |

> **Note for future phases:** any phase that adds a nav item must touch three places —
> the `<ul class="nav">` list and the `{% include %}` block in `templates/management.html`,
> plus a single dispatch branch in `activatePage()` in `navigation.js`. Phase 3
> consolidated the previously-duplicated dispatch so there is now only one place to edit.

---

## Estimated Implementation Time

| Phase | Name | DB | New Frontend Files | Estimate | Status |
|---|---|---|---|---|---|
| 1 | Core Grouping + Failover | TinyDB (model_group field) | — | 2-3 days | done |
| 2 | Playground V1 (stateless) | — | 2 | 2-3 days | done |
| 3 | Groups Dashboard | — | 2 | 1-2 days | done |
| 4 | Cost Awareness | TinyDB (cost fields) | — | 1 day | done |
| 5 | Skills Library V1 | TinyDB (skills table) | 2 | 2-3 days | next |
| 6 | Playground V2 (persistent) | SQLite (conversations.db) | — | 3-4 days | planned |
| 7 | Skills Library V2 (MCP) | — | — | 3-4 days | planned |
| 8 | Polish | — | — | 2-3 days | planned |
