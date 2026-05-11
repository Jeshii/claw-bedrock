# claw-bedrock Agent Rules

## Code Quality
- Never commit before running the following
 - `ruff check --fix` to auto-fix linting issues
 - `ruff format` to enforce formatting
 - `python -m py_compile <file>` to syntax-check any modified Python file
- Never commit if linting or syntax checks fail
- Ask before pushing since develop branch will build on push

## Python Style
- Python 3.12+ — use `match`, `type X = ...`, and modern union syntax (`X | Y`)
- Prefer `except FileNotFoundError` over bare `except Exception` where specific errors are expected

## Git Workflow
- Never push directly to `main`, just push to `develop` first, PRs unnecessary for now
- Commit messages: imperative mood, e.g. "Fix token_refresher syntax error"
- Feel free to push multiple commits for a single task if it helps with clarity, but consider squashing before pushing to `develop`

## General Practices
- Compact when you hit 80% of your context window
- Skills are located in `skills/` and should be self-contained with their own dependencies if possible

## Project Map
- `management_app.py` — Management UI (uvicorn on port 8282)
- `token_refresher.py` — AWS SSO token refresh logic, imported at startup
- Container starts LiteLLM on port 4000
- Container start Management UI on port 8282
- See docs/FILE_STRUCTURE.md for locations of files