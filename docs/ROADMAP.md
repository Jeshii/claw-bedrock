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

## Phase 1 — Core Model Grouping + Auto-Failover

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

## Phase 2 — Playground V1 (Session-Scoped Multi-Turn Chat)

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

## Phase 3 — Groups Dashboard

**Goal:** A dedicated page showing all model groups, their members, and per-member status.

### Files created

| File | Purpose |
|---|---|
| `templates/partials/page_groups.html` | Table/card view of groups — name, member count, member list with provider badges |
| `src/static/js/groups.js` | Load groups from API, render, expand members |

### Files modified

| File | Change |
|---|---|
| `src/management_app.py` | `GET /api/model-groups` — aggregates models by `model_group`, returns grouped structure with member details |
| `templates/management.html` | Add "Groups" nav item (between "Playground" and "Tags") |
| `src/static/js/init.js` | Add `loadGroups()` |
| `src/static/management.css` | Groups page styles |

### API response shape

```json
GET /api/model-groups
{
  "groups": [
    {
      "name": "sonnet",
      "member_count": 2,
      "members": [
        { "model_name": "claude-3-5-sonnet-v2", "provider": "bedrock",
          "litellm_params": { ... }, "context_length": 200000 },
        { "model_name": "claude-3-5-sonnet-v1", "provider": "bedrock", ... }
      ]
    }
  ],
  "ungrouped_count": 5
}
```

---

## Phase 4 — Cost Awareness

**Goal:** Let users set `input_cost` / `output_cost` on models. Enable `routing_strategy: "cost-based"` to automatically prefer the cheapest healthy member in a group.

### Files modified

| File | Change |
|---|---|
| `src/db.py` | `get_models_for_litellm()` — emit `model_info: { input_cost, output_cost }` when present |
| `src/management_app.py` | PATCH allowed_fields add `"input_cost"`, `"output_cost"`; `GET /api/models` includes cost fields |
| `src/static/js/models.js` | Cost fields in model detail section (editable inputs); cost column in groups dashboard |
| `src/static/management.css` | `.cost-input` style |

### Per-model cost fields

```python
{
  "model_name": "claude-3-5-sonnet-v2",
  "model_group": "sonnet",
  "input_cost": 0.003,     # dollars per 1K input tokens
  "output_cost": 0.015,    # dollars per 1K output tokens
  "litellm_params": { ... }
}
```

When set, the LiteLLM config entry includes `model_info: { input_cost, output_cost }`.

Costs are manually entered in Phase 4. Auto-population from provider APIs is deferred to Phase 8.

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
| Auto-populate costs from OpenRouter/Bedrock APIs | Small | Phase 4 |
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
Phase 0 (current):     Dashboard | Auth | Security | Providers | Models | Tags | Backup | Logs | Help
Phase 2 (after):       ... Models | Playground | Tags ...
Phase 3 (after):       ... Models | Playground | Groups | Tags ...
Phase 5 (after):       ... Models | Playground | Groups | Skills | Tags ...
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

| Phase | Files |
|---|---|
| 2 | `page_playground.html`, `playground.js` |
| 3 | `page_groups.html`, `groups.js` |
| 5 | `page_skills.html`, `skills.js` |
| 6 | `conversations_db.py` |
| 7 | `mcp_skills_server.py` |

### Files to modify (cumulative, all phases)

| File | Phases |
|---|---|
| `src/db.py` | 1, 4, 5, 7 |
| `src/management_app.py` | 1, 2, 3, 4, 5, 6 |
| `src/static/js/models.js` | 1, 4 |
| `src/static/js/playground.js` | 2, 6 |
| `src/static/js/init.js` | 1, 2, 3, 5 |
| `src/static/management.css` | 1, 2, 3, 4, 5, 6, 7 |
| `templates/management.html` | 2, 3, 5 |
| `templates/partials/page_models.html` | 1 |
| `templates/partials/page_playground.html` | 2, 6 |
| `templates/partials/page_skills.html` | 7 (tool call rendering) |

---

## Estimated Implementation Time

| Phase | Name | DB | New Frontend Files | Estimate |
|---|---|---|---|---|
| 1 | Core Grouping + Failover | TinyDB (model_group field) | — | 2-3 days |
| 2 | Playground V1 (stateless) | — | 2 | 2-3 days |
| 3 | Groups Dashboard | — | 2 | 1-2 days |
| 4 | Cost Awareness | TinyDB (cost fields) | — | 1 day |
| 5 | Skills Library V1 | TinyDB (skills table) | 2 | 2-3 days |
| 6 | Playground V2 (persistent) | SQLite (conversations.db) | — | 3-4 days |
| 7 | Skills Library V2 (MCP) | — | — | 3-4 days |
| 8 | Polish | — | — | 2-3 days |
