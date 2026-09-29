# claw-bedrock File Structure

## Overview

All files are organized into logical subdirectories, keeping only conventional root-level files at the top level.

---

## Current Structure

```
claw-bedrock/
├── .github/
│   ├── Containerfile — the real image build; `COPY src/*.py .` so a new module cannot be omitted
│   └── workflows/
│       └── build-container.yml
├── src/
│   ├── db.py
│   ├── encryption_utils.py
│   ├── management_app.py
│   ├── password_utils.py
│   ├── settings_resolver.py
│   ├── static/
│   │   ├── management.css
│   │   ├── unofficial-b52s-Regular.ttf
│   │   └── js/
│   │       ├── auth.js
│   │       ├── backup.js
│   │       ├── groups.js
│   │       ├── init.js
│   │       ├── logs.js
│   │       ├── markdown.js
│   │       ├── models.js
│   │       ├── navigation.js
│   │       ├── playground.js
│   │       ├── playground_audio.js
│   │       ├── providers.js
│   │       ├── security.js
│   │       ├── tags.js
│   │       ├── theme.js
│   │       └── utils.js
│   └── token_refresher.py
├── config/
│   └── policy.json
├── deploy/
│   ├── .env.example
│   ├── claw-bedrock.container.example
│   ├── docker-compose.yml
│   └── start_container.sh
├── scripts/
│   ├── lint.sh
│   └── smoke.sh
├── skills/
│   ├── aws-login-remote/
│   │   └── SKILL.md
│   └── build-deps/
│       └── SKILL.md
├── templates/
│   ├── login.html
│   ├── management.html
│   └── partials/
│       ├── page_auth.html
│       ├── page_backup.html
│       ├── page_dashboard.html
│       ├── page_groups.html
│       ├── page_help.html
│       ├── page_logs.html
│       ├── page_models.html
│       ├── page_playground.html
│       ├── page_providers.html
│       ├── page_security.html
│       └── page_tags.html
├── tests/
│   ├── test_*.py — pytest suite
│   ├── playground_audio.test.mjs — node --test suite for audio mode
│   └── markdown-renderer-test.html — hand-run browser harness
├── docs/
│   ├── BUGS.md
│   ├── CHANGELOG.md
│   ├── FILE_STRUCTURE.md
│   └── ROADMAP.md
├── Dockerfile
├── AGENTS.md
├── biome.json
├── package-lock.json
├── package.json
├── README.md
├── requirements.lock
├── requirements.txt
└── .gitignore
```

---

## Directory Descriptions

### `src/`
Contains all Python source files and static assets for the application:
- `db.py` — Database models and connection logic
- `encryption_utils.py` — Encryption utilities for sensitive data
- `management_app.py` — Management UI (uvicorn on port 8282)
- `password_utils.py` — Password hashing and validation utilities
- `settings_resolver.py` — Settings precedence (flag → env → config → default) with provenance
- `static/` — Static assets for the management UI:
  - `management.css` — All CSS styles
  - `unofficial-b52s-Regular.ttf` — Font file
  - `js/` — Classic non-module scripts sharing globals, loaded with `defer` in
    document order (utils, theme, navigation, auth, security, models, tags, logs,
    providers, backup, markdown, playground_audio, playground, groups, init).
    They are deliberately not ES modules: they share globals with inline
    `onclick` handlers in the templates, which is also why biome's
    `noUnusedVariables` is disabled for this directory. `playground_audio.js`
    must load before `playground.js`, which calls into it.
- `token_refresher.py` — AWS SSO token refresh logic, imported at startup

### `config/`
Configuration and data files:
- `policy.json` — LiteLLM policy configuration

### `deploy/`
Deployment and container-related files:
- `.env.example` — Example environment variables
- `claw-bedrock.container.example` — Example container configuration
- `docker-compose.yml` — Docker Compose setup
- `start_container.sh` — Container startup script

### `scripts/`
Developer tooling:
- `lint.sh` — Single source of truth for the lint checks; run before committing, and run
  by CI in `.github/workflows/lint.yml`. Covers `ruff check`, `ruff format --check`,
  `biome ci`, djlint in lint and format modes, a duplicate-pin check on
  `requirements.lock`, and `node --test tests/*.test.mjs` for the audio-mode
  unit tests. Accumulates findings rather than failing fast, and exits 127 with
  an install hint if a tool is missing, so a version drift cannot read as a
  pass. The node version is printed in its step header: unlike every other tool
  here it is a runtime constraint rather than a config-file one, so a local
  major newer than CI's is the drift to watch for. The glob is wrapped in a
  `nullglob` no-match guard, because node exits 0 on a pattern that matches
  nothing — without it, a renamed test file is a silent pass.
- `smoke.sh` — Builds the image and boots it, asserting the app imports and LiteLLM
  reaches `status: ok` / `litellm_status: 200`. Called by CI between build and push,
  since `docker build` never runs `ENTRYPOINT` and cannot detect an unbootable image.
  Locally: `./scripts/smoke.sh`, or `IMAGE=<ref> SKIP_BUILD=1 ./scripts/smoke.sh`

### `.opencode/`
Opencode-specific configuration and dependencies:
- `node_modules/` — Node.js dependencies for opencode
- `package-lock.json` — Locked Node.js dependencies
- `package.json` — Node.js project configuration

### `.github/`
GitHub-specific workflows and configurations:
- `Containerfile` — Container definition for GitHub Actions
- `workflows/` — GitHub Actions workflows
  - `build-container.yml` — Container build workflow, gated on the `quality` job
  - `lint.yml` — Lint and test workflow (also called by `build-container.yml`)

### `.ruff_cache/`
Cache directory for the Ruff Python linter/formatter

### `skills/`
Self-contained skills with their own dependencies when possible:
- `aws-login-remote/` — AWS login remote skill
- `build-deps/` — Dependency build skill

### `templates/`
HTML templates for the management UI:
- `login.html` — Password login form
- `management.html` — Shell template with sidebar, nav, and partial includes
- `partials/` — Page fragments included by management.html (dashboard, auth, security, providers, models, playground, groups, tags, backup, logs, help)

### `tests/`
Test suites. Two runners, deliberately:
- `test_*.py` — pytest, run from the project root with `PYTHONPATH=src` and a
  `CONFIG_DIR`/`ENCRYPTION_KEY` pair. No `conftest.py`; each file that reloads
  app modules purges `sys.modules` itself.
- `playground_audio.test.mjs` — `node --test`, no dependencies. Audio APIs cannot
  run headlessly, so this covers the pure functions that carry the feature: the
  sentence chunker, the silence timer and its grace window, the restart gate,
  voice resolution and transcript accumulation. `scripts/lint.sh` runs it.
- `markdown-renderer-test.html` — hand-run browser harness, not in CI.

### `docs/`
Project documentation:
- `BUGS.md` — Numbered diagnosis notes, including the traps in AGENTS.md
- `CHANGELOG.md` — Version history and feature changelog
- `FILE_STRUCTURE.md` — This file, directory structure reference
- `ROADMAP.md` — Full development roadmap with all planned phases

### Root Files
- `Dockerfile` — Container build instructions at root (standard convention)
- `AGENTS.md` — Agent rules and instructions for AI tooling
- `biome.json` — Biome linter/formatter config for JS/HTML files
- `package-lock.json` / `package.json` — Node.js dependencies at root (standard convention)
- `README.md` — Project overview and documentation
- `requirements.txt` / `requirements.lock` — Python dependencies at root (standard pip convention)
- `.gitignore` — Git ignore file

---

## Notes

- `Dockerfile`, `requirements.txt`, `requirements.lock`, `README.md`, and `AGENTS.md` remain at root — these are standard conventions expected by Docker, pip, and AI tooling.
- Static assets were moved from `static/` to `src/static/` to better organize the source code.
- Additional utility files (`encryption_utils.py`, `password_utils.py`) were added to support security features.
- The Dockerfile and Python imports have been updated to reflect the file locations.