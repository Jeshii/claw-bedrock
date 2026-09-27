# claw-bedrock Agent Rules

## Code Quality
- Run `./scripts/lint.sh` before committing. It is the single source of truth for the
  checks, and CI runs the same file, so the two cannot drift.
- First-time setup: `pip install -r requirements-dev.txt` (ruff, djlint, pytest) and
  `npm install -g @biomejs/biome@2.5.14` (biome is not pip-installable)
- The script only reports. To fix what it finds:
  - python — `ruff check --fix` then `ruff format`
  - static HTML/JS/CSS — `biome check --write .`
  - Jinja templates — `djlint templates/ --reformat`. Biome cannot parse `{{ }}`, so
    `templates/` is djlint's job
- **No git hooks by design.** Nothing is installed into `.git/hooks`. For as-you-type
  feedback use your editor's language servers instead — both ruff and biome ship one and
  need no repo config.
- Never commit if `./scripts/lint.sh` fails
- Ask before pushing since develop branch will build on push

## Lint Configuration
All three linters are configured in-repo so results are reproducible. Do not rely on
machine-level defaults.
- `scripts/lint.sh` — the checks themselves: `ruff check`, `ruff format --check`, `biome ci`,
  and djlint in both lint and format modes, plus a duplicate-pin check on `requirements.lock`
- `ruff.toml` — pins `target-version = "py314"` and the explicit rule set. Regenerate the
  rule list with `ruff check --show-settings | sed -n '/linter.rules.enabled/,/^]/p'`
- `biome.json` — excludes `templates/` and `tests/**/*.html` (Jinja `{{ }}` is unparseable by
  biome), and disables `noUnusedVariables` for `src/static/js/**`
- `.djlintrc` — ignores `H021` (inline styles are needed for `display:none` toggles) and
  `H030`
- `noUnusedVariables` is off for `src/static/js/` on purpose: those files are classic
  non-module scripts (no `import`/`export`) that share globals across files and are called from
  inline `onclick` in Jinja templates. **Never run `biome check --write` with that rule enabled
  here — the autofix renames globals to `_foo` and breaks the UI.**
- Accepted remaining warnings: 5× `noDescendingSpecificity` in `src/static/management.css`
  (reordering a 1600-line stylesheet risks cascade regressions for no functional gain), and
  a11y findings in `templates/management.html`

## Python Style
- Python 3.14+ — use `match`, `type X = ...`, and modern union syntax (`X | Y`)
- Prefer `except FileNotFoundError` over bare `except Exception` where specific errors are expected
- Broad `except Exception` is allowed at module boundaries (startup, watchdog, config merge,
  token refresher I/O) but must carry `# noqa: BLE001 - <reason>` naming what degrades and why
- Handlers that do blocking I/O (subprocess, `open`, `requests`) must be sync `def`, not
  `async def` — FastAPI runs sync handlers in a threadpool. Reserve `async` for genuine `await`
- Prefer `datetime.now(UTC)` over the deprecated `datetime.utcnow()`; when emitting an ISO
  timestamp string use `.isoformat().replace("+00:00", "Z")` to keep the existing format

## Testing
- Run from project root: `PYTHONPATH=src CONFIG_DIR=/tmp ENCRYPTION_KEY=<fernet-key> python3 -m pytest tests/ -q`
- Use the 3.14 venv: `.venv/bin/python -m pytest tests/ -q` with the same env vars
- `PYTHONPATH=src` is required (no `conftest.py`); 126 tests should pass
- Do not test against a system Python's ambient packages. `httpx >= 0.28` encodes
  `json=` with `allow_nan=False`, so a `float("nan")` body raises client-side;
  `patch_model_literal` in `tests/test_model_costs.py` sends it as a raw body
  instead. Local package sets can differ from `requirements.lock` in ways that
  hide or invent failures
- `@app.on_event` in `src/management_app.py` emits FastAPI deprecation warnings — pre-existing,
  not yet migrated to lifespan handlers
- **CI does not run the container.** `.github/workflows/lint.yml` lints and tests on
  Python 3.14, but `docker build` never executes `ENTRYPOINT`, so "does the image
  actually boot and bring up LiteLLM" is unverified. That is how the litellm callbacks
  regression reached production. The assertion that would catch it is
  `GET /api/health/litellm` returning `status: ok`; adding a smoke test is the fix.

## Git Workflow
- Never push directly to `main`, just push to `develop` first, PRs unnecessary for now
- Commit messages: imperative mood, e.g. "Fix token_refresher syntax error"
- Feel free to push multiple commits for a single task if it helps with clarity, but consider squashing before pushing to `develop`

## General Practices
- Compact when you hit 80% of your context window
- Mention your context window when it hits 25%, 50%, and 75%
- Skills are located in `skills/` and should be self-contained with their own dependencies if possible

## Project Map
- `src/management_app.py` — Management UI (uvicorn on port 8282)
- `src/token_refresher.py` — AWS SSO token refresh logic, imported at startup
- `templates/management.html` - HTML template for the management UI
- `src/static/js/*.js` — classic non-module scripts sharing globals (see Lint Configuration)
- Container starts LiteLLM on port 4000
- Container start Management UI on port 8282
- See docs/FILE_STRUCTURE.md for locations of other files

## Dependency Management
- `requirements.txt` — loose dependency pins (source of truth)
- `requirements.lock` — auto-generated by `pip-compile`, never edit manually
- `requirements-dev.txt` — lint and test tooling, pinned to what CI installs
- See `skills/build-deps/SKILL.md` for regeneration instructions