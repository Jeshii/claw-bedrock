# claw-bedrock File Structure

## Overview

All files are organized into logical subdirectories, keeping only conventional root-level files at the top level.

---

## Current Structure

```
claw-bedrock/
├── .github/
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
│   └── start_container.sh
├── templates/
├── skills/
├── docs/
│   └── FILE_STRUCTURE.md
├── Dockerfile
├── requirements.txt
├── requirements.lock
├── AGENTS.md
├── README.md
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

### `templates/`
HTML templates for the management UI.

### `skills/`
Self-contained skills with their own dependencies when possible.

### `docs/`
Project documentation including this file structure reference.

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
- `templates/` and `skills/` are subdirectories that require no changes.
- The Dockerfile and Python imports have been updated to reflect the new file locations.
