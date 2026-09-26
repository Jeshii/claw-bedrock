# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Models from custom providers now include `api_base`/`api_key` in `litellm_params`** — `addGenericModel()` in `models.js` looks up the provider from `window._allProviders` and propagates its endpoint and credentials into the model config. Also switched the model prefix from `providerName/modelId` to `openai/modelId` (the correct prefix for OpenAI-compatible endpoints).
- **Config pushed to running LiteLLM after model CRUD** — `_reload_litellm_config()` is now called after `merge_configs()` in `add_model()`, `delete_model()`, `rename_model()`, and `update_model()` in `management_app.py`. Previously the config file was written but the running process was never notified.
- **Playground dropdown refreshes when reload modal blocks navigation** — `loadPlayground()` called immediately in `navigation.js` before the `needsReload` guard returns early, so the model list is populated from TinyDB even while the reload decision is pending.
- **Playground model dropdown retries and manual refresh** — added retry buttons to all error/empty states in `loadPlayground()` (`playground.js`), and a refresh `↻` button next to the model select (`page_playground.html`). Navigation now calls `loadPlayground()` on every Playground tab activation (`navigation.js`).

### Added

- **Groups dashboard (Phase 3)**
  - New `GET /api/model-groups` endpoint aggregating models by `model_group`, with provider display info attached to each member
  - New Groups page (`templates/partials/page_groups.html`, `src/static/js/groups.js`) listing every group, its member count, and expandable members with provider badges and context length
  - Per-member status chips derived from config only: `Ready`, `Check config`, `Not in config`. No live probing — status reflects what is actually written to `config.yaml`
  - Group header reports `active_member_count` vs `member_count`, surfacing members silently dropped from the generated config by `db._merge_provider_defaults` (e.g. a dangling provider reference)
  - Clicking a member jumps to the Models page with that model expanded
  - Ungrouped-model summary with a shortcut to the Models page
  - `tests/test_model_groups.py` — 11 tests covering aggregation, sorting, status levels, `active_member_count`, and secret redaction
- **Model groups with auto-failover (Phase 1)**
  - New optional `model_group` field on models — when set, multiple models sharing a group form a LiteLLM failover group
  - `get_models_for_litellm()` in `db.py` now transforms `model_group` into LiteLLM's `model_name`, with prefix applied if configured
  - `PATCH /api/models/{name}` accepts `model_group` for per-model assignment
  - `GET/POST /api/settings/router` — expose `routing_strategy`, `allowed_fails`, and `num_retries`
  - Group badge displayed in model rows in the UI
  - Inline group-name input in the model detail section
  - New Router Settings section in the Models page (strategy, fails, retries)
  - Backward compatible — models without `model_group` behave identically to before
- **docs/ROADMAP.md** — full 8-phase development roadmap
- **docs/CHANGELOG.md** — this file

### Changed

- **`navigation.js` page dispatch consolidated** — the page-activation and loader-dispatch block was duplicated between `showPage()` and `dismissReloadWarning()`; it is now a single `activatePage(pageId)` helper. The two copies had already drifted: the `dismissReloadWarning()` copy was missing the `loadModels()` branch. That turned out to be unreachable rather than a live bug (the `needsReload` gate excludes `"models"` from the modal path), but the duplication is exactly what let it drift.
- **Nav highlighting no longer relies on the implicit global `event`** — `showPage()` used `event.target.classList.add("active")`, which is undefined outside Chrome for this access pattern and highlighted the wrong element when a click landed on the wrapping `<li>` or a child node. Now matches on each link's `onclick` attribute, the approach `dismissReloadWarning()` already used.
