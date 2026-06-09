# claw-bedrock File Structure

## Overview

All files are organized into logical subdirectories, keeping only conventional root-level files at the top level.

---

## Current Structure

```
claw-bedrock/
├── .github/
│   ├── Containerfile
│   └── workflows/
│       └── build-container.yml
├── src/
│   ├── db.py
│   ├── encryption_utils.py
│   ├── management_app.py
│   ├── password_utils.py
│   ├── static/
│   │   ├── management.css
│   │   ├── unofficial-b52s-Regular.ttf
│   │   └── js/
│   │       ├── auth.js
│   │       ├── backup.js
│   │       ├── init.js
│   │       ├── logs.js
│   │       ├── models.js
│   │       ├── navigation.js
│   │       ├── providers.js
│   │       ├── security.js
│   │       ├── tags.js
│   │       ├── theme.js
│   │       └── utils.js
│   └── token_refresher.py
├── config/
│   ├── bedrock_models.json
│   └── policy.json
├── deploy/
│   ├── .env.example
│   ├── claw-bedrock.container.example
│   ├── docker-compose.yml
│   └── start_container.sh
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
│       ├── page_help.html
│       ├── page_logs.html
│       ├── page_models.html
│       ├── page_providers.html
│       ├── page_security.html
│       ├── page_tags.html
├── docs/
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
- `static/` — Static assets for the management UI:
  - `management.css` — All CSS styles
  - `unofficial-b52s-Regular.ttf` — Font file
  - `js/` — JavaScript modules (utils, theme, navigation, auth, security, models, tags, logs, providers, backup, init)
- `token_refresher.py` — AWS SSO token refresh logic, imported at startup

### `config/`
Configuration and data files:
- `bedrock_models.json` — Bedrock model definitions
- `policy.json` — LiteLLM policy configuration

### `deploy/`
Deployment and container-related files:
- `.env.example` — Example environment variables
- `claw-bedrock.container.example` — Example container configuration
- `docker-compose.yml` — Docker Compose setup
- `start_container.sh` — Container startup script

### `.opencode/`
Opencode-specific configuration and dependencies:
- `node_modules/` — Node.js dependencies for opencode
- `package-lock.json` — Locked Node.js dependencies
- `package.json` — Node.js project configuration

### `.github/`
GitHub-specific workflows and configurations:
- `Containerfile` — Container definition for GitHub Actions
- `workflows/` — GitHub Actions workflows
  - `build-container.yml` — Container build workflow

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
- `partials/` — Page fragments included by management.html (dashboard, auth, security, models, backup, providers, tags, logs, help)

### `docs/`
Project documentation:
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