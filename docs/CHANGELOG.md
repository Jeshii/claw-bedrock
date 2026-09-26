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

### Added

- **Group management on the Groups page** — the dashboard was read-only, so an unwanted or typo'd group could only be cleared by expanding every member on the Models page and emptying the input one at a time.
  - `POST /api/model-groups/rename` `{from, to}` and `POST /api/model-groups/unassign` `{name}`. Both apply every member in one batch and issue a single config push, so a mid-way failure cannot leave a group split across two names
  - Per-group **Rename** and **Unassign** buttons, plus an **add member** dropdown fed by the models currently in no group
  - Rename is gated behind a confirm that spells out the consequence: a group name *is* the model name clients call, so the old name stops working
  - Rename deliberately refuses to merge onto an existing group (409) rather than silently repointing the losing name's clients
- **`ungrouped_models` on `GET /api/model-groups`** — the ungrouped model names, not just the count, so the add-member dropdown can be built without a second request
- **Group-name suggestions on the Models page** — the Group input is now a `list=` datalist fed from the same response, so a typo cannot silently invent a near-duplicate group

### Fixed

- **Group names are validated.** A name becomes the public `model_name` (`db.get_models_for_litellm`, optionally prefixed `claw-bedrock/`) but nothing checked it, so a name containing whitespace produced a model name no client could request. An allowlist of letters, numbers, dots, dashes and underscores; empty, over-64-char and non-matching names are rejected with 400, and surrounding whitespace is trimmed so a trailing space cannot create a near-duplicate.
- **`.rename-btn` was 2.33:1 in dark mode.** It paired `var(--neutral)` with `var(--text-inverse)`, but unlike `--danger`/`--success` (which lighten for dark mode) `--neutral` gets *darker*, so dark mode painted dark text on a dark fill. Added `--text-on-neutral`.
- **`.rename-btn.confirming` was 3.13:1 in light mode** — white on mid-green. Added `--text-on-success`. Both buttons are used on the Models page too, so this fixes them there as well.

### Changed

- **Context length formatting** — `formatContextLength` divided by 1024/1048576 while model catalogs report decimal units, and never rounded, so a 1,000,000-token model rendered as `976.5625k ctx` instead of `1M ctx`. Now divides by 1000/1e6 and suppresses trailing zeros (128k stays 128k, not 125k).
- **Model render path no longer assumes `litellm_params` exists.** `POST /api/models` does no validation, so a model without it is creatable, and the unguarded dereference threw mid-map and blanked the whole Models page.
- **Router Settings moved to the Groups page.** `routing_strategy`, `allowed_fails` and `num_retries` are failover behavior for model groups, but the controls lived on the Models page — the one place you cannot see a group. Pure relocation; the API is unchanged.
- **Rebuilt light/dark mode on a design-token layer** — `management.css` had **zero** CSS custom properties; all 66 colours were hardcoded and 106 one-off `.dark X {}` rules patched individual selectors. 26 of those 66 colours existed *only* inside `.dark` rules, i.e. dark mode had a deliberately designed palette while light mode inherited browser defaults.
  - 60 semantic tokens (surfaces, interactive states, borders, text, accent, status, alpha overlays, shadows) declared once in `:root` with `light-dark()` for their dark value, matching the pattern `login.html` already used
  - `color-scheme: light` on `:root` / `color-scheme: dark` on `.dark` makes `light-dark()` resolve and themes native scrollbars and form controls, which the app never themed
  - 105 `.dark` override rules deleted, folding into the token block. One survives — `.dark #reload-litellm-btn.needs-reload { animation }` — because an animation is not a token
  - **Fixed: invisible hover states in light mode.** Nine hover rules used `#f8f9fa` on a white page — a contrast ratio of **1.05:1**, effectively invisible. Hover tokens are now 1.24:1 (light) and 1.38:1 (dark), matching dark's perceptual step
  - **Fixed: `.section` had no light background** (transparent, relying on a white body) while dark got `#2a2a2a`; both now use `--bg-surface`
  - **Fixed: `input`/`select` and bare `pre` were styled only in dark mode.** `.dark select` and `.dark pre` were the sole source of their background, so deleting those rules would have left white-on-white inputs and log viewers. Both now carry explicit token-backed base styles
  - **Fixed: Playground surfaces had no dark styling at all.** `.playground-toolbar`, `.playground-input-area` and `.playground-messages` kept `#f8f9fa`/`#fefefe` with `#ddd` borders in dark mode — the Playground shipped after the dark-mode pass and was never covered. The token layer fixes this automatically
  - Dark values are carried over verbatim from the previous stylesheet, so dark mode's appearance is unchanged
  - Requires `light-dark()` support (Chrome 123+, Safari 17.5+, Firefox 120+) — the repo already depended on it via `login.html`
- **Converted the remaining inline colours to tokens** — the token migration only caught colours a single-line search matched, missing multi-line `style` attributes: 12 dialog borders in the Logs page, 4 more `#ccc`/`#ddd` borders, the key-reveal code background, Backup's empty-state text, the Tags Cancel button, three status colours set from JS, the auto-refresh toggle, Backup's drag-over border, and the provider swatch border. `--swatch-border` was also defined as `light-dark(rgba(0,0,0,.15), rgba(0,0,0,.15))` — identical branches, i.e. a black border invisible on a dark swatch. Deliberately left alone: the ANSI terminal palette, the user-chosen tag colours, and provider swatch colours, which are data rather than theme.
- **`navigation.js` page dispatch consolidated** — the page-activation and loader-dispatch block was duplicated between `showPage()` and `dismissReloadWarning()`; it is now a single `activatePage(pageId)` helper. The two copies had already drifted: the `dismissReloadWarning()` copy was missing the `loadModels()` branch. That turned out to be unreachable rather than a live bug (the `needsReload` gate excludes `"models"` from the modal path), but the duplication is exactly what let it drift.
- **Nav highlighting no longer relies on the implicit global `event`** — `showPage()` used `event.target.classList.add("active")`, which is undefined outside Chrome for this access pattern and highlighted the wrong element when a click landed on the wrapping `<li>` or a child node. Now matches on each link's `onclick` attribute, the approach `dismissReloadWarning()` already used.
