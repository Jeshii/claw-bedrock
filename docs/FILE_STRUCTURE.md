# claw-bedrock Reorganization Plan

## Overview

This plan moves all files from the flat root structure into logical subdirectories, keeping only conventional root-level files at the top level.

---

## Target Structure

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
│   └── PLAN.md
├── Dockerfile
├── requirements.txt
├── requirements.lock
├── AGENTS.md
├── README.md
└── .gitignore
```

---

## File Moves

| File | From | To | Notes |
|------|------|----|-------|
| `db.py` | `/` | `src/` | |
| `management_app.py` | `/` | `src/` | |
| `token_refresher.py` | `/` | `src/` | |
| `bedrock_models.json` | `/` | `config/` | |
| `policy.json` | `/` | `config/` | |
| `docker-compose.yml` | `/` | `deploy/` | |
| `start_container.sh` | `/` | `deploy/` | |
| `claw-bedrock.container.example` | `/` | `deploy/` | |
| `PLAN.md` | `/` | `docs/` | |

---

## Required Code Updates

After moving files, update the following references:

### Dockerfile
- Update `COPY` paths for any Python files copied from root
- Update `CMD` or `ENTRYPOINT` if they reference root-level `.py` files directly
- Example: `COPY db.py .` → `COPY src/db.py .`

### Python imports
- If any file imports from another using relative paths or `sys.path` assumptions, update accordingly
- If using a package structure, add `src/__init__.py`

### docker-compose.yml
- If `docker-compose.yml` references `start_container.sh` or any root paths, update to `deploy/` prefix

### AGENTS.md / README.md
- Update any file path references in documentation

---

## Git Commands

```bash
# Create directories
mkdir -p src config deploy docs

# Move Python source files
git mv db.py src/
git mv management_app.py src/
git mv token_refresher.py src/

# Move config/data files
git mv bedrock_models.json config/
git mv policy.json config/

# Move deployment files
git mv docker-compose.yml deploy/
git mv start_container.sh deploy/
git mv claw-bedrock.container.example deploy/

# Move internal docs
git mv PLAN.md docs/

# Commit
git commit -m "refactor: reorganize files into src/, config/, deploy/, docs/"
```

---

## Notes

- `Dockerfile`, `requirements.txt`, `requirements.lock`, `README.md`, and `AGENTS.md` stay at root — these are standard conventions expected by Docker, pip, and AI tooling.
- `templates/` and `skills/` are already subdirectories and need no change.
- Do the Dockerfile/import updates **before** committing, or in the same commit, to avoid breaking the build.
