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
│   ├── management_app.py
│   └── token_refresher.py
├── config/
│   ├── bedrock_models.json
│   └── policy.json
├── deploy/
│   ├── claw-bedrock.container.example
│   ├── docker-compose.yml
│   ├── .env.example
│   └── start_container.sh
├── static/
│   ├── management.css
│   └── js/
│       ├── utils.js
│       ├── theme.js
│       ├── navigation.js
│       ├── auth.js
│       ├── security.js
│       ├── models.js
│       ├── tags.js
│       ├── logs.js
│       ├── providers.js
│       ├── backup.js
│       └── init.js
├── templates/
│   ├── login.html
│   ├── management.html
│   └── partials/
│       ├── page_dashboard.html
│       ├── page_auth.html
│       ├── page_security.html
│       ├── page_models.html
│       ├── page_backup.html
│       ├── page_providers.html
│       ├── page_tags.html
│       ├── page_logs.html
│       └── page_help.html
├── skills/
│   └── aws-login-remote/
├── docs/
│   └── FILE_STRUCTURE.md
├── Dockerfile
├── requirements.txt
├── requirements.lock
├── AGENTS.md
├── README.md
├── biome.json
└── .gitignore
```

---

## Directory Descriptions

### `src/`
Contains all Python source files for the application:
- `db.py` — Database models and connection logic
- `management_app.py` — Management UI (uvicorn on port 8282)
- `token_refresher.py` — AWS SSO token refresh logic, imported at startup

### `config/`
Configuration and data files:
- `bedrock_models.json` — Bedrock model definitions
- `policy.json` — LiteLLM policy configuration

### `deploy/`
Deployment and container-related files:
- `claw-bedrock.container.example` — Example container configuration
- `docker-compose.yml` — Docker Compose setup
- `start_container.sh` — Container startup script

### `static/`
Static assets for the management UI:
- `management.css` — All CSS styles
- `js/` — JavaScript modules (utils, theme, navigation, auth, security, models, tags, logs, providers, backup, init)

### `templates/`
HTML templates for the management UI:
- `login.html` — Password login form
- `management.html` — Shell template with sidebar, nav, and partial includes
- `partials/` — Page fragments included by management.html (dashboard, auth, security, models, backup, providers, tags, logs, help)

### `skills/`
Self-contained skills with their own dependencies when possible.

### `docs/`
Project documentation including this file structure reference.

### `biome.json`
Biome linter/formatter config for JS/HTML files.

### `Dockerfile`
Container build instructions at root (standard convention).

### `requirements.txt` / `requirements.lock`
Python dependencies at root (standard pip convention).

### `AGENTS.md`
Agent rules and instructions for AI tooling.

### `README.md`
Project overview and documentation.

---

## Notes

- `Dockerfile`, `requirements.txt`, `requirements.lock`, `README.md`, and `AGENTS.md` remain at root — these are standard conventions expected by Docker, pip, and AI tooling.
- The Dockerfile and Python imports have been updated to reflect the file locations.
