# Claw-Bedrock Development Roadmap

_Model Groups • Playground • Audio Mode • Groups Dashboard • Cost Awareness • Bedrock Auth • Skills Library • MCP Integration_

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
| 5 | **Bedrock Auth Configuration** | **Paused at Step 3** — steps 1 and 2 shipped; preempted by row 10 | — |
| 6 | Skills Library V1 (Browse / Manage) | Planned | — |
| 7 | Playground V2 (Persistent Conversations) | Planned | Playground V1 |
| 8 | Skills Library V2 (MCP Integration) | Planned | Skills Library V1 |
| 9 | Polish & Everything Else | Planned | varies — see table |
| 10 | Playground Audio Mode (Hands-Free) | **Urgent — active workstream** | Playground V1 |

**Playground Audio Mode is the active workstream, and it jumps the queue.** It
is appended as row 10 rather than renumbered into position 5, because the
numbering is an ordering rather than an identity and renumbering would rewrite
five shipped rows to express a scheduling decision. It is taken out of turn
ahead of the work in progress, and it is the one item here that is *blocked*
rather than merely unstarted — the application currently denies the microphone
outright, so nothing about hands-free can be built on top of the present state.

**Bedrock Auth Configuration is paused at Step 3, not abandoned.** It remains
the next workstream once audio mode lands. It is independent of the
skills-library and playground-conversation threads, and it addresses a live
problem: the Bedrock auth surface collects configuration that does nothing and
reported status that was not accurate.

Resume at **Step 3 — Mantle Model Discovery**. Steps 1 and 2 are shipped. The
two items left open by Step 1 were run by hand and are now closed; both came
back with a different answer than expected, and "Corrections after host
verification" below records what they were. Step 2's own GUI surface still has
no host verification — see "What Step 2 still needs from the host".

---

## Database Architecture

The database tier grows alongside the features. TinyDB stays for config-scale data; SQLite is introduced when conversations demand proper indexing and search.

| Data | Workstream | Store | Rationale |
|---|---|---|---|
| Models, Providers, Tags, Settings | Existing | TinyDB | Config-scale, few hundred records, CRUD only — already working |
| Skills | Skills Library V1 | TinyDB | Same profile as models — dozens to low hundreds, CRUD only |
| Conversations, Messages | Playground V2 | SQLite | Thousands of records, needs full-text search (FTS5), concurrent reads from watchdog + UI |

Audio mode adds no store. A spoken message is an ordinary message: text in,
text out. Nothing in the audio path is persisted, and Phase B's synthesis
output is a stream consumed by the browser, not a file.

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

## Playground Audio Mode (Hands-Free)

**Status:** Urgent. Active workstream — preempts Bedrock Auth Step 3.

**Goal:** Talk to a model and hear it back without touching the keyboard. Turn-based and button-free: arming the mic starts recognition, a trailing silence sends the message, and the reply is spoken as it streams. The text chat is never removed or degraded — audio is a second way in, not a replacement.

This is two phases. Phase A is browser-native and unblocks hands-free with no backend. Phase B moves the same feature onto Bedrock speech models behind an identical interface. Phase A is designed so that Phase B is a provider swap rather than a rewrite.

### Blockers, in the order they bite

**1. The application denies the microphone today.** `security_headers_middleware` in `src/management_app.py` sends:

```python
response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
```

A browser enforcing that policy refuses `SpeechRecognition` and `getUserMedia` before any feature code runs. It becomes `microphone=(self)` — same-origin only, which is the correct scope for a single-user management UI. `camera=(), geolocation=()` stay as they are; there is no reason to widen them.

This is a deliberate security control being relaxed, so it is worth doing on purpose rather than discovering it. It is also the reason this workstream cannot begin as a pure frontend addition.

**2. Secure context — a requirement, not new work.** TLS already terminates at the reverse proxy in front of port 8282, so no TLS work belongs in this workstream. But the requirement must be written down, because the failure mode is misleading: reached over `http://<lan-ip>:8282` the mic is refused with a permissions error that reads as an application bug rather than a missing secure context. `localhost` counts as secure, so development on the host is unaffected.

**3. SSE buffering.** The chat stream already sends `X-Accel-Buffering: no`, which covers nginx-style proxies but not every proxy. A stalled stream is invisible in a text UI and audible as silence in audio mode, so this is worth verifying against the actual proxy in front of the deployment.

### Key design decisions

**One adapter, two providers.** All speech input and output goes through a single object in `playground_audio.js` — recognition lifecycle, a send trigger, and a speak function — with a `setAudioProvider()` switch between `browser` and `bedrock`. `playground.js` streams a reply the same way regardless of which provider is active, and never learns which one it is. This is what makes Phase B a swap.

**The echo loop is the constraint the design is built around.** Text-to-speech plays through the speakers, the microphone hears it, and the model transcribes its own reply and answers itself — indefinitely. Recognition is therefore stopped before the first utterance is spoken and restarted when the last one ends: half-duplex by construction. This is precisely why V1 is turn-based rather than full duplex, and why barge-in is deferred to Polish.

**There is no VAD in the Web Speech API.** `onspeechend` is non-standard and unreliable. Send-on-silence is a trailing timer, reset on every `onresult`, that fires at roughly 1.3s with no new interim or final text. Because the timer is the whole mechanism, it is a pure state machine and is unit-testable — which matters more than usual here, since none of this can be exercised headlessly.

**The transcript goes into the input box, then through the existing send path.** `sendPlaygroundMessage()` already reads the textarea, so audio mode fills it and calls the same function. The message-state contract, SSE streaming, and the Stop `AbortController` are all shared with Playground V1, and Playground V2's SQLite persistence needs no audio-specific change — a spoken message is an ordinary message row.

**Speech is spoken as it streams.** `speechSynthesis` cannot stream, so accumulated deltas flush on sentence boundaries and the first sentence starts talking while the rest is still arriving. This also sidesteps Chrome's long-utterance cutoff, which silently truncates a paragraph-sized reply partway through.

**The text path is never removed.** A misheard transcript is wrong often enough that correcting it before send has to stay possible, and the keyboard is the only path on a machine with no microphone. Interim transcript is announced via `aria-live`, and `Esc` disarms.

**No audio is persisted.** Transcripts become ordinary message text. No audio blobs, and no copy of conversation content in `localStorage` — audio mode is not a new store and does not touch the database tier.

**Voices load asynchronously.** `getVoices()` returns empty until `voiceschanged` fires, so voice selection is deferred and a fast reply does not get read in whatever default voice happened to be set.

### Support matrix

`SpeechRecognition` is a Chromium and Safari API; Firefox does not implement it. Audio mode is expected to be Chrome and Safari only, and the UI states this rather than failing silently on a button that does nothing. Phase B is not obviously constrained the same way, which is one of its arguments.

### Files created

| File | Purpose |
|---|---|
| `src/static/js/playground_audio.js` | The speech adapter — arm/disarm, recognition restart loop, silence timer, sentence chunker, speak queue, provider switch |

### Files modified

| File | Change |
|---|---|
| `src/management_app.py` | `Permissions-Policy` — `microphone=()` becomes `microphone=(self)` |
| `templates/management.html` | Add `<script defer src="/static/js/playground_audio.js">` ahead of `playground.js` |
| `templates/partials/page_playground.html` | Mic toggle, provider select, armed/listening/speaking state, interim transcript readout |
| `src/static/js/playground.js` | Fill the input from the transcript and reuse `sendPlaygroundMessage()`; feed streamed deltas to the speak queue; cancel speech on Stop and New Chat |
| `src/static/management.css` | Audio control states using the design-token layer, both light and dark |

> `playground_audio.js` is a classic non-module script like its neighbours,
> sharing globals with inline `onclick` — which is why `noUnusedVariables` is
> disabled for `src/static/js/**`. Running `biome check --write` with that rule
> enabled renames the globals and breaks the UI. See AGENTS.md.

### Phase B — Bedrock-native speech

Phase A's provider is the browser's. Phase B replaces it with Bedrock speech models — Whisper for recognition, Polly for synthesis — behind the same interface.

**Polly is not an OpenAI-compatible endpoint**, so it cannot be proxied through LiteLLM the way chat is. It is a raw `bedrock-runtime` `InvokeModel` call, using credentials the Bedrock Auth workstream already provisions. That workstream being mid-flight is a reason to sequence Phase B after it, not a reason to avoid Phase A.

**Region availability is the open risk, and it may be disqualifying.** Bedrock speech models are region-limited — Whisper to a short list of regions, Nova Sonic to `us-east-1` — while this deployment signs into `ap-northeast-1`. If no speech model is available there, Phase B is a region decision rather than a port. Verify before committing to it. This is the strongest argument for landing Phase A first: Phase A is worth having on its own merits and is not contingent on the answer.

**Polly is billed per character**, so the cost-awareness surface has to learn these prices. An unpriced model does not read as unknown to the router — it reads as one of the most expensive things available, which is the same `$5/$5` fallback the Groups page already flags for unpriced members.

### Testing

Audio APIs cannot run headlessly, so coverage splits. The two pieces of real logic — the sentence chunker and the silence timer — are pure functions and get unit tests. Everything else is a manual matrix: Chrome and Safari for support, a denied-permission path, a no-microphone machine, and the visual light/dark check, which the Playground has needed once already after shipping past the dark-mode pass.

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

**Status:** Steps 1 and 2 shipped. Step 3 not started.

> **Revised after host verification (Step 2).** Three claims in this section were
> wrong and are corrected below: the "LiteLLM is not answering" diagnosis, the
> `linux/amd64` conclusion drawn from CI, and the strength of Step 1's host
> verification. The short version: LiteLLM was never down, `podman logs` simply
> does not contain LiteLLM's output; the CI health assertion passed vacuously
> because the test container has no models; and Step 1's double-poll was
> non-diagnostic because there was no error to preserve. See "Corrections after
> host verification" at the end of this section.

**Goal:** Make the Bedrock auth surface tell the truth, then move auth configuration out of dead form fields and into settings that actually take effect. The governing principle: **the GUI is the friendly space, environment variables are expert mode.** Anything the GUI can own, it should; anything the environment owns, the GUI yields to and warns about.

Precedence follows the unix chain — **flags → environment variables → config store → GUI** — where config outranks the GUI, so an expert who sets an env var is never overridden by a form they filled in months ago. The GUI's job when it loses is to say so by name.

### Why this exists

The Bedrock provider form currently asks for three AWS fields. Investigation found that none of them work:

- `aws_access_key_env` and `aws_secret_key_env` are stored, rendered, validated and round-tripped, and read by **no runtime code**. `aws_secret_key_env` is worse: matched as sensitive by the substring `secret`, it gets encrypted (meaningless crypto on the env var *name* `AWS_SECRET_ACCESS_KEY`), nulled by `sanitize_provider_for_response` so the edit form always renders it blank, and then written back empty by `saveProviderDetail`, which pops the stored value. Opening a Bedrock provider, renaming it, and saving destroys the field silently.
- `aws_region` is read but written to `litellm_params["aws_region"]`, which LiteLLM ignores — it reads `aws_region_name`. The field looks functional and is inert.

Auth actually comes from the token refresher, entirely out of band: `AWS_PROFILE` + `AWS_REGION` → `boto3.Session(...)` → `get_token()` → `os.environ["BEDROCK_MANTLE_API_KEY"]`. The provider record plays no part.

### Step 1 — Auth Status Truth ✅ (shipped)

**Status:** Shipped and running on `mobydisk`. **Verification rests on the unit
tests, not on the host** — the host double-poll was run and came back
non-diagnostic. See "Corrections after host verification".

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

### Incident — container could not boot (fixed, shipped, deployed)

**Status:** Fixed and shipped as `a834ee8`, `30cbed5`, `fcbc415`. The service runs again on `mobydisk`. **One open issue and one unverified claim remain** — see "Where this stands".

**Symptom.** The management UI died on startup with `ModuleNotFoundError: No module named 'settings_resolver'`, from `management_app.py:30`. Because `start_container.sh` ends in `wait ${MGMT_PID}`, the app exiting took the container with it, so port 8282 stopped serving entirely and the unit sat in its `Restart=on-failure` loop rather than running degraded.

**Cause.** `.github/Containerfile` copied each Python module with its own `COPY` line, and `settings_resolver.py` was added to `src/` in `8c5ff71` without a matching line. The image built clean and every test passed. This is the second time that list lost a module — `8a9ff62` recovered `encryption_utils.py` and `password_utils.py` the same way — which is why the list was removed rather than extended.

**Confirmed on the running host**, not inferred: `/app/VERSION` read `dev-4fca2c6` (HEAD, the `dev-${GITHUB_SHA::7}` build arg), and `ls /app` held exactly the five enumerated modules with no `settings_resolver.py`. The image was current, not stale, which is what the previous note on this section assumed. The unit already reads `ghcr.io/jeshii/claw-bedrock:develop`, so no unit change is needed.

**What is done.**

| File | Change |
|---|---|
| `.github/Containerfile` | Five enumerated `COPY src/*.py` lines → `COPY src/*.py .`, so a new module cannot be omitted |
| `deploy/start_container.sh` | `init_configs()` skips the copy when source and destination are the same file. The image sets `CONFIG_DIR=/app`, so an unmounted boot ran `cp` with identical paths, `cp` exited 1 with "are the same file", and `set -euo pipefail` killed the container before either process started. Deployments never hit it because `CONFIG_DIR` points at a mounted volume |
| `scripts/smoke.sh` | **New.** Boots the image and asserts it serves. Called by `build-container.yml` between build and push, so a broken image is never published. Honours `$CONTAINER_ENGINE`; `SKIP_BUILD=1` tests an existing image |
| `.github/workflows/build-container.yml` | Build now `load: true` without pushing, smoke test runs, then a separate push step |
| `tests/test_containerfile.py` | **New.** 12 tests. Static half of the same guarantee: every `src/*.py` is copied, plus `templates`/`static`/`start_container.sh` |
| `AGENTS.md`, `docs/FILE_STRUCTURE.md`, `docs/CHANGELOG.md` | Smoke test is now part of the pre-commit loop; scratch files go in `.scratch/` |

**Verified.** 210 tests pass (198 + 12). Image built with podman and confirmed to contain `settings_resolver.py`; `scripts/smoke.sh` passed end to end against it, asserting `/api/version` and `litellm_status == 200`. Reverting the Containerfile to the old enumerated list makes exactly one test fail, `test_module_is_copied[settings_resolver.py]`, and no other — so the test is specific rather than failing broadly.

The health assertion is deliberately stricter than `AGENTS.md` used to state. `management_app.py:1646` returns `status: ok` whenever the probe does not *raise*, so a 404 or 500 from LiteLLM still reads as healthy; the smoke test also asserts `litellm_status == 200`.

### Incident — the CI build never ran at all

**Status:** Found and fixed in `fcbc415`. This is the one that actually kept the service down, and the section above could not see it.

**Symptom.** `a834ee8` fixed the Containerfile, `30cbed5` fixed the lint toolchain, both pushed, and *nothing changed on the host*. The `develop` image was still `dev-4fca2c6` — the exact broken commit.

**Cause.** The build job had been failing at setup on every run since `a834ee8`, in 1m0s, before building anything:

```
ERROR: failed to build: Cache export is not supported for the docker driver.
```

`a834ee8` added `cache-to: type=gha,mode=max` to a workflow that never called `docker/setup-buildx-action`, so the runner used its implicit default builder, which is the **`docker` driver** — it supports gha cache *import* but not *export*. A `docker-container` driver is what supports export, and nothing here created one.

**There were two more bugs behind it,** both of which would have surfaced the moment the cache error was cleared:

- `IMAGE: ${{ steps.meta.outputs.tags }}` binds a **multi-line** block (`develop` + `sha-<short>`) into one env var, so `smoke.sh` would have passed two tag names to `docker build -t`. It now gets a single local-only tag, built alongside the published ones and never pushed.
- The smoke step ran with no `SKIP_BUILD`, so it would have **rebuilt from scratch** rather than booting the artifact the push step publishes — defeating the whole build → smoke → push arrangement. It now runs `SKIP_BUILD=1` against the image `load: true` already put in the runner's store.
- Also pinned `CONTAINER_ENGINE: docker`: `smoke.sh` prefers podman when on `PATH`, ubuntu runners may have it, and podman's store is separate from Docker's, so it would not have seen the loaded image.

**Why the build cache was removed rather than fixed.** The `docker-container` driver would have worked. Measured first: the pip and AWS CLI layers are **946 MB of the 1.1 GB image** and account for ~85s of a 90s cold build; everything that changes per commit is **391 kB** and rebuilds in **1.45s**. So the cache buys ~85s by pushing ~950 MB up and back down per push, into a store that expires silently after 7 days and leaves dead config behind when it does. For a public repo on free minutes that is not a trade worth the new failure surface — and this cache had *already* cost one broken build plus a false "Verified" line in this very section. The `docker-container` fix is four lines if the build ever gets painful.

**Lesson, and it is the one worth keeping.** The verification above was real but **local-only**: podman on arm64. The "Verified" claim was true and still wrong about production, because nothing had asked whether CI could build. A local pass is not a deployment. The `linux/amd64` question that this section flagged as open is now answered by the first green CI run.

**Where this stands (as of `fcbc415`).**

| Check | Result |
|---|---|
| CI, lint + 210 tests | pass |
| CI, build + smoke + push | pass, 1m51s total; build 55s, smoke 19s |
| CI smoke assertions | `version: dev-fcbc415`, `litellm_status == 200`, booted with `SKIP_BUILD=1` so it tested the pushed artifact |
| `linux/amd64` published image boots | **partially** — the management UI boots and serves. It does **not** prove LiteLLM serves requests on amd64; the health assertion it rested on passed vacuously. See below |
| `mobydisk` | pulled `:develop`, restarted. `/app/VERSION` = `dev-fcbc415`, `settings_resolver.py` present, `NRestarts` 912 → **0**, `/api/version` 200, 9 models merged |
| `/api/auth/status` double-poll | **NOT verified** — see below |

**Open issue (now resolved, and the diagnosis was wrong): LiteLLM is not
answering on `mobydisk`.** `/api/health/litellm` returned

```json
{"status":"error","detail":"HTTPConnectionPool(host='localhost', port=4000): Read timed out. (read timeout=5)"}
```

persistently across 12 attempts spanning ~4 minutes after a healthy restart.
The note above went on to reason: *"LiteLLM's own log had produced no
`Uvicorn running on :4000` line, so it appears stuck or still initialising"*,
with a fallback that *"`read timeout=5` is the probe's own budget and may
simply be too tight on a host this loaded"*.

**Both inferences were unfounded, and the first one was based on a file that
cannot contain the evidence.** `start_container.sh:56` redirects LiteLLM's
stdout and stderr to `${CONFIG_DIR}/litellm.log`:

```bash
litellm --config "${CONFIG_PATH}" --port 4000 --host 0.0.0.0 > "${CONFIG_DIR}/litellm.log" 2>&1 &
```

`podman logs` shows the management UI and the entrypoint's own echo, and will
never contain LiteLLM's output. Reading the file that does:

```
INFO:     Uvicorn running on http://0.0.0.0:4000 (Press CTRL+C to quit)
```

LiteLLM was up the entire time. The "stuck or still initialising" reading came
from an absence the file layout guarantees — the same failure shape as the CI
lesson recorded two sections earlier, where a local pass was mistaken for a
deployment. Now `docs/BUGS.md` #11.

**The real cause is that `/api/health/litellm` is not a liveness probe.**
`/api/health/litellm` proxies LiteLLM's `/health`, which runs an *active* check
against every model in `model_list` unless `general_settings.background_health_checks`
is set — and this config does not set it
(`proxy/health_endpoints/_health_endpoints.py:1110-1140`). The host's
`/api/auth/status` reports `needs_login: true` with no token ever minted, so
each of the nine models' health checks attempts a refresh and fails, and the
set exceeds the probe's 5s budget. The endpoint was measuring Bedrock auth and
reporting it as a proxy outage.

Note that this made the endpoint *more* honest than a liveness check would
have: a red reading genuinely meant models could not be served. Step 2 adds
`/api/health/litellm/liveliness` for the "did it boot" question and leaves
`/api/health/litellm` on the active check deliberately. `docs/BUGS.md` #12.

**Not verified (and now known to be non-diagnostic): the Step 1 fix on the
running service.** Calling `/api/auth/status` twice and confirming `auth_error`
is identical is the shell-visible version of the regression test. It was run by
hand on the host, and it passed — but it proves nothing. Both responses were 438
bytes and byte-identical, and `auth_error` was `null` in both:

```json
{ "auth_error": null, "needs_login": true,
  "bedrock": { "token": { "present": false, "stale": true }, ... } }
```

There was no error to drain, so the diff would have come out identical against
the old read-and-clear code too. **Step 1's fix is verified by the 51 unit
tests in `test_auth_status.py` and by nothing else.** The guard against the
earlier false pass (two empty files) did its job — it is the *emptiness of the
error* that makes this run vacuous, which is a different failure and one the
`wc -c` check cannot detect.

**Remote access is now off-limits.** `AGENTS.md` forbids `ssh` into any host, `mobydisk` explicitly, enforced as a hard deny in `opencode.jsonc`. The two items above were captured immediately before that landed and cannot be finished from here. **To close them out, run this yourself on the host:**

```bash
curl -s http://127.0.0.1:8282/api/health/litellm | python3 -m json.tool
podman logs claw-bedrock 2>&1 | tail -40     # is LiteLLM on :4000 at all?

curl -s http://127.0.0.1:8282/api/auth/status > /tmp/a1.json
sleep 2
curl -s http://127.0.0.1:8282/api/auth/status > /tmp/a2.json
diff /tmp/a1.json /tmp/a2.json && echo "auth_error stable"
```

Guard against the false pass: check `wc -c` on both files is non-zero before trusting `diff`. If `warnings` contains `env_credentials_shadowed_by_profile`, that is live confirmation of the botocore `disable_env_vars` behavior; an empty list leaves that path untested until Step 2.

**The `lint.sh` blocker is resolved, and it was never a repo problem.** `djlint` was failing intermittently with `Path 'templates/' is not readable`, which reads like a permissions bug and is not one. The message is emitted by **click**, not djlint — `click/types.py:1193`, from `os.access(rv, os.R_OK)` returning `False` — against the `SRC` argument djlint declares at `djlint/__init__.py:145-155` as `click.Path(exists=True, readable=True, ...)`. So djlint aborted on a `stat`/`access` syscall against the directory, before opening a single `.html` file. That is why the earlier evidence all pointed at nothing: `ls` and `stat` agreeing with each other, and `os.access` returning `True` on every call from a fresh process, are all consistent with the *path being fine* and the *syscall being denied intermittently*. A path that `stat` can read but `access()` sometimes refuses is the macOS TCC layer, not the filesystem — this repo sits in `~/Documents` and carries ~31k `com.apple.*` xattrs, and the unrelated `git stash` failure on `.git/config` was the same layer.

It could not be reproduced: ~100 `djlint` runs, 5 full `lint.sh` runs, 40 concurrent `os.access` probes, 25 parallel `djlint` processes, and runs under `env -i`, `sh -c`, explicit file arguments, and absolute paths all passed. CI cannot reach it either — it installs djlint via `pip` on a clean `ubuntu-latest` runner.

**What was actually wrong locally, and is now fixed.** The venv never received `requirements-dev.txt`; it held only `pytest`. `lint.sh` was therefore silently running the **Homebrew** builds of `ruff` and `djlint` from `PATH`, not the pinned ones. Versions happened to match (`1.46.2`), so nothing had broken yet, but local linting was not the environment `AGENTS.md` describes. `lint.sh` now prepends `.venv/bin` to `PATH` and hard-fails with a per-tool install hint instead of letting a Homebrew binary answer for a pinned one — a version drift should surface as a failure, not a pass. This does **not** claim to fix the TCC flake; it makes local match CI, so a recurrence is unambiguously a machine issue to file rather than a repo issue to re-debug.

**The `AGENTS.md` rule needed a nuance, and has been given one.** "Never commit if `./scripts/lint.sh` fails" cannot distinguish a real template finding from a tool that failed to start. The rule now covers findings; a tool-level startup failure is reported rather than treated as a lint verdict.

Note that the pending change touches **zero** templates or `.html` files, so the flake was never gating this work in the first place.

**`v0.1.2` is not tagged, deliberately.** It stays untagged until the two open items above are closed by hand. `latest` is frozen at `v0.1.1` from June because no newer tag has been pushed, and tagging moves it — the workflow gates `latest` on `startsWith(github.ref, 'refs/tags/v')`, which is correct as written. Tagging would publish all 62 commits since that release, a history still not audited.

### Step 2 — GUI Bedrock Auth Configuration ✅ (shipped, host verification pending)

A settings section below Manage Providers on the Providers page, matching how
settings sit below the main content on other pages.

| File | Change |
|---|---|
| `src/db.py` | `set_secret_setting` / `get_secret_setting` / `clear_secret_setting`; `decrypt_data_strict` in `encryption_utils`; `migrate_dead_bedrock_fields()`; bedrock branch of `_merge_provider_defaults` now writes `aws_region_name` |
| `src/token_refresher.py` | `configure()` and `_build_session()`; static-key state in `auth_status_payload()` |
| `src/management_app.py` | `GET/PUT /api/settings/bedrock`; shared `_bedrock_auth_state()`; `/api/health/litellm/liveliness`; dead fields dropped from `ALLOWED_PROVIDER_FIELDS`, the encrypt loop and runtime-change detection |
| `src/static/js/providers.js` | Settings card (region, profile, collapsed advanced key pair); dead fields removed |
| `templates/partials/page_providers.html` | Settings section below Manage Providers |
| `src/static/js/auth.js` | Warning text; credential-source row |
| `scripts/smoke.sh` | Asserts liveliness rather than the auth-coupled check |
| `tests/test_bedrock_settings.py` | **New.** 26 tests |
| `tests/test_auth_status.py`, `tests/test_provider_persistence.py`, `tests/test_token_refresher.py` | Rewritten assertions for the new contract; `TestBuildSession` and `TestConfigure` added |

**Long-lived IAM keys are restored, not dropped.** `token_refresher` always
called `boto3.Session(profile_name=self._profile)`, and botocore sets
`disable_env_vars` whenever a profile is explicitly passed
(`credentials.py:95`), which removes the EnvProvider from the credential chain —
so `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` were set-but-inert on every
install. The non-SSO path was broken for everyone. `_build_session()` now passes
the credentials explicitly and **omits** `profile_name`, which is the only way to
reach them (`boto3/session.py:81-82` only sets the profile config variable when
`profile_name is not None`). `TestBuildSession` pins this, including the
half-a-key-pair case falling back to the profile rather than displacing it.

**This invalidates Step 1's warning, deliberately.** Step 1 shipped
`env_credentials_shadowed_by_profile` to explain why env keys were inert. Step 2
fixes the cause, so the warning is *removed* rather than kept — leaving it would
fire on a condition the same release repairs, which is the exact failure mode
this workstream exists to eliminate. `tests/test_auth_status.py` asserts its
absence and says why in the test name.

**Region is a global setting, not a per-provider field.** One refresher, one
`self._region`, one `BEDROCK_MANTLE_API_KEY` in `os.environ` — a second provider
in another region would present a token minted for the first. The per-provider
field is retired with the other two, and `aws_region_name` (the key LiteLLM
actually declares; `aws_region` is silently dropped) is now injected into
`litellm_params` from the resolved global region. Region resolution goes through
`settings_resolver` via a function-local import rather than a second
env-then-config walk in `db`, because a duplicated walk is exactly the drift the
resolver exists to prevent. **No seeding**: a `aws_region` value that was inert
for its entire life is not evidence of an operator's current intent, so the
global setting starts unset and env/default wins.

Validation warns rather than blocks — a region is lowercased with a message
rather than rejected. botocore can enumerate Bedrock regions offline, but that is
the **public partition only** and would falsely reject GovCloud and regions AWS
adds later.

**Secrets are reported as presence, never echoed.** The key pair follows the
existing provider `api_key` convention (`has_api_key` boolean, never the value),
not the LiteLLM master key's `key[:12] + "..." + key[-4:]` masked reveal. An AWS
access key ID is half a credential — the `AKIA` prefix identifies the account and
the key vintage — so there is nothing a mask would reveal that the boolean does
not. `get_secret_setting` returns a **state** rather than a bare value, so
"never configured" is distinguishable from "configured under a different
`ENCRYPTION_KEY`": `decrypt_data` returns its input unchanged on failure, which
would otherwise hand raw Fernet ciphertext to boto3 as a secret (BUGS.md #8).
The latter now raises a `stored_credentials_undecryptable` warning.

**A save that persists but cannot mint a token reports that.** `PUT` returns
`saved` and `token_refreshed` separately, and the UI renders
`Saved, but no token could be minted` rather than a green success. A wrong region
or bad key fails at token-mint time, not at validation.

**What Step 2 still needs from the host.** The unit tests use a fake boto3
session; no real token has been minted through `configure()`. Worth running by
hand once deployed — save a region change and confirm `region.source` flips from
`env` to `config` with `shadowed: ["env"]`, and that a token actually re-mints.

### Corrections after host verification

Three claims in this section were wrong. Recording them because the failure
mode is consistent: **each was an inference from an absence, and each absence
turned out to be guaranteed by something structural rather than informative.**

| Claim | What was actually true |
|---|---|
| "LiteLLM's log has no `Uvicorn running on :4000`, so it appears stuck or still initialising" | `podman logs` cannot contain LiteLLM's output — `start_container.sh:56` redirects it to `${CONFIG_DIR}/litellm.log`. LiteLLM was up the whole time |
| "CI smoke assertions: `litellm_status == 200` … closing the `linux/amd64` arch gap" | The CI container has no `model_list`, so `/health` returns 200 instantly. The assertion proved the management UI boots and nothing about LiteLLM serving requests |
| "the Step 1 auth-error double-poll is still unverified" | It was run, and it passed *vacuously* — `auth_error` was `null` in both responses, so it would have been identical against the old code too |

The first two are the same mistake the CI section already documents as its
lesson — *"A local pass is not a deployment"* — committed twice more in the
document that recorded it. The third is new and worth stating plainly: a green
double-diff is not evidence unless there was something to differ on. `wc -c`
guards the empty-file case; nothing guards the empty-error case.

Two mitigations now exist so this is cheaper next time. `scripts/smoke.sh`
asserts `/api/health/litellm/liveliness`, which cannot be red for provider-auth
reasons and cannot pass vacuously with no models. And `AGENTS.md` carries a
"Diagnosing the Running Container" section pointing at
`${CONFIG_DIR}/litellm.log` and naming which health endpoint answers which
question.

The host's real state, for whoever picks this up: LiteLLM is healthy, and
Bedrock auth has **never** completed on it — `needs_login: true`, no token ever
minted, no static keys set. `AWS_PROFILE=default` and
`AWS_REGION=ap-northeast-1` both resolve `source: env`, which confirms the
resolver's precedence is working live. Step 2's settings card is the intended way
out of that state, and completing the SSO login in the browser remains the only
thing that mints a real token.

### Step 3 — Mantle Model Discovery

| File | Change |
|---|---|
| `src/management_app.py` | `BEDROCK_MANTLE_API_KEY` fallback in `fetch_provider_models` so `/v1/models` works with the in-process token; return an actionable error when login is required |
| `src/db.py` | Seed the Bedrock provider's `api_base` from the global region — **provider record only, never injected into `litellm_params`**, or the all-or-nothing `api_base` override returns |
| `src/static/js/models.js` | "Fetch Models" mirroring the Ollama flow, manual entry retained; delete the caveat wall |

The Bedrock add-model form is currently the least helpful of the four: OpenRouter gets a searchable list, Ollama gets a Fetch Models button, and Bedrock gets a bare text input wrapped in two paragraphs of defensive caveats. Those caveats existed because polling Bedrock models was previously impossible. They are tech debt, not warnings anyone still needs.

**Discovery is available, with one caveat that shapes the UI.**
`https://bedrock-mantle.<region>.api.aws/v1/models` exists and takes the same
`BEDROCK_MANTLE_API_KEY` the proxy uses. Verified live: `/v1/models` returns 401
(wants a bearer), `/openai/v1/models` returns 404. An earlier entry in
`docs/BUGS.md` claimed no `/models` endpoint existed at all; that was wrong and is
now corrected.

The 404 is the real constraint: the gpt-5.x, gpt-6-\*, gemma-4-\* and grok-4.x
families are served on `/openai/v1`, so **a fetched list will not contain
them**. "Fetch Models" must therefore supplement manual entry rather than replace
it, and the UI should say so — otherwise the families it omits read as models that
do not exist. Per-model path derivation already handles the GPT-5.6+/GPT-6
family, so `BEDROCK_MANTLE_API_BASE` should stay unset.

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
| Full-duplex barge-in (interrupt playback, speak over the reply) | Medium | Audio Mode — needs real VAD, which the Web Speech API does not provide |
| Nova Sonic speech-to-speech (true duplex, one bidirectional stream) | Large | Audio Mode Phase B — `us-east-1` only, region may rule it out |
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
Audio Mode:      ... Models | Playground | Groups | Tags ...   (no nav change)
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
  ├── Playground V2 (depends on chat proxy existing)
  └── Playground Audio Mode (depends on the same chat proxy; no new backend
      in Phase A, so it can be built without waiting for V2)

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
| Bedrock Auth Configuration | done | `settings_resolver.py` |
| Skills Library V1 | planned | `page_skills.html`, `skills.js` |
| Playground V2 | planned | `conversations_db.py` |
| Skills Library V2 | planned | `mcp_skills_server.py` |
| Playground Audio Mode | planned | `playground_audio.js` |

### Files to modify (cumulative, all workstreams)

| File | Workstreams |
|---|---|
| `src/db.py` | Core Grouping, Cost Awareness, Bedrock Auth, Skills Library V1, Skills Library V2 |
| `src/management_app.py` | Core Grouping, Playground V1, Groups Dashboard, Cost Awareness, Bedrock Auth, Skills Library V1, Playground V2, Skills Library V2, Audio Mode |
| `src/token_refresher.py` | Bedrock Auth |
| `src/static/js/models.js` | Core Grouping, Cost Awareness, Bedrock Auth |
| `src/static/js/playground.js` | Playground V1, Playground V2, Audio Mode |
| `src/static/js/groups.js` | Groups Dashboard, Cost Awareness |
| `src/static/js/auth.js` | Bedrock Auth |
| `src/static/js/providers.js` | Bedrock Auth |
| `src/static/js/init.js` | Core Grouping, Playground V1, Groups Dashboard, Skills Library V1 |
| `src/static/js/navigation.js` | Groups Dashboard (and any workstream adding a nav item) |
| `src/static/js/utils.js` | Groups Dashboard, Cost Awareness (shared formatting helpers) |
| `src/static/management.css` | Core Grouping, Playground V1, Groups Dashboard, Cost Awareness, Bedrock Auth, Skills Library V1, Playground V2, Skills Library V2, Audio Mode |
| `templates/management.html` | Playground V1, Groups Dashboard, Skills Library V1, Audio Mode |
| `templates/partials/page_auth.html` | Bedrock Auth |
| `templates/partials/page_models.html` | Core Grouping |
| `templates/partials/page_playground.html` | Playground V1, Playground V2, Audio Mode |
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
| Bedrock Auth Configuration — Step 1 | — | — | 0.5 day | done |
| Bedrock Auth Configuration — Step 2 | TinyDB (encrypted settings) | — | 1-2 days | done |
| Bedrock Auth Configuration — Step 3 | — | — | 0.5-1 day | next |
| Skills Library V1 (Browse / Manage) | TinyDB (skills table) | 2 | 2-3 days | planned |
| Playground V2 (Persistent Conversations) | SQLite (conversations.db) | — | 3-4 days | planned |
| Skills Library V2 (MCP Integration) | — | — | 3-4 days | planned |
| Playground Audio Mode — Phase A (browser Web Speech) | — | 1 | 1-2 days | next |
| Playground Audio Mode — Phase B (Bedrock-native) | — | — | 2-3 days | planned — blocked on region availability |
| Polish & Everything Else | — | — | 2-3 days | planned |
