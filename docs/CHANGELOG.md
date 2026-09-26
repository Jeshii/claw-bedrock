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

- **Light/dark mode rebuilt on a design-token layer** — `management.css` had **zero** CSS custom properties; all 66 colours were hardcoded and 106 one-off `.dark X {}` rules patched individual selectors. 26 of those 66 colours existed *only* inside `.dark` rules, i.e. dark mode had a deliberately designed palette while light mode inherited browser defaults.
  - 60 semantic tokens (surfaces, interactive states, borders, text, accent, status, alpha overlays, shadows) declared once in `:root` with `light-dark()` for their dark value, matching the pattern `login.html` already used
  - `color-scheme: light` on `:root` / `color-scheme: dark` on `.dark` makes `light-dark()` resolve and themes native scrollbars and form controls, which the app never themed
  - 105 `.dark` override rules deleted, folding into the token block. One survives — `.dark #reload-litellm-btn.needs-reload { animation }` — because an animation is not a token
  - **Fixed: invisible hover states in light mode.** Nine hover rules used `#f8f9fa` on a white page — a contrast ratio of **1.05:1**, effectively invisible. Hover tokens are now 1.24:1 (light) and 1.38:1 (dark), matching dark's perceptual step
  - **Fixed: `.section` had no light background** (transparent, relying on a white body) while dark got `#2a2a2a`; both now use `--bg-surface`
  - **Fixed: `input`/`select` and bare `pre` were styled only in dark mode.** `.dark select` and `.dark pre` were the sole source of their background, so deleting those rules would have left white-on-white inputs and log viewers. Both now carry explicit token-backed base styles
  - **Fixed: Playground surfaces had no dark styling at all.** `.playground-toolbar`, `.playground-input-area` and `.playground-messages` kept `#f8f9fa`/`#fefefe` with `#ddd` borders in dark mode — the Playground shipped after the dark-mode pass and was never covered. The token layer fixes this automatically
  - Dark values are carried over verbatim from the previous stylesheet, so dark mode's appearance is unchanged
  - 11 hardcoded inline colours across 6 JS/template files converted to `var(--token)`
  - Verified with a headless-Chrome render harness: 27 assertions across both themes, including that the theme switch is reversible
  - Requires `light-dark()` support (Chrome 123+, Safari 17.5+, Firefox 120+) — the repo already depended on it via `login.html`
- **`navigation.js` page dispatch consolidated** — the page-activation and loader-dispatch block was duplicated between `showPage()` and `dismissReloadWarning()`; it is now a single `activatePage(pageId)` helper. The two copies had already drifted: the `dismissReloadWarning()` copy was missing the `loadModels()` branch. That turned out to be unreachable rather than a live bug (the `needsReload` gate excludes `"models"` from the modal path), but the duplication is exactly what let it drift.
- **Nav highlighting no longer relies on the implicit global `event`** — `showPage()` used `event.target.classList.add("active")`, which is undefined outside Chrome for this access pattern and highlighted the wrong element when a click landed on the wrapping `<li>` or a child node. Now matches on each link's `onclick` attribute, the approach `dismissReloadWarning()` already used.
