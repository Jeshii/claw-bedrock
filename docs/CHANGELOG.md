# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Models from custom providers now include `api_base`/`api_key` in `litellm_params`** — `addGenericModel()` in `models.js` looks up the provider from `window._allProviders` and propagates its endpoint and credentials into the model config. Also switched the model prefix from `providerName/modelId` to `openai/modelId` (the correct prefix for OpenAI-compatible endpoints).
- **Config pushed to running LiteLLM after model CRUD** — `_reload_litellm_config()` is now called after `merge_configs()` in `add_model()`, `delete_model()`, `rename_model()`, and `update_model()` in `management_app.py`. Previously the config file was written but the running process was never notified.
- **Playground dropdown refreshes when reload modal blocks navigation** — `loadPlayground()` called immediately in `navigation.js` before the `needsReload` guard returns early, so the model list is populated from TinyDB even while the reload decision is pending.

### Added

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
