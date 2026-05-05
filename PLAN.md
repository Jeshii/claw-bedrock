# Implementation Plan

## Task 1: Context Display

### Changes

1. **`db.py`**:
   - Store `context_length` field when models are added via `add_model()`
   - Include `context_length` in the config returned by `get_models_for_litellm()`

2. **`management_app.py`**:
   - **OpenRouter**: Extract `context_length` from API response (`top_provider.context_length`) and include in model list
   - **Ollama**: Store context length when model is added; try to enrich from `/api/show` endpoint
   - **Bedrock**: Add `context_length` field to each entry in `bedrock_models.json`
   - **Manual**: Add "Context Length" numeric input in the add-model form
   - All model list API responses include `context_length`

3. **`templates/management.html`**:
   - In `loadModels()`: display context length next to model name (e.g., `— 128k ctx`)
   - In add-model flow: show context length field for all providers (auto-populated for OpenRouter/Ollama when possible)
   - For manual entry: numeric input field

---

## Task 2: Fix Reload Issue

### Changes

1. **`management_app.py`**:
   - Add `validate_config()` — parse generated YAML with `yaml.safe_load()` before restarting
   - Improve `reload_litellm()`:
     - After SIGTERM, use `process.wait(timeout=5)` to ensure old process is gone, fall back to SIGKILL
     - After starting new process, verify it's still running after 2s with `psutil.Process(pid).is_running()`
     - Add post-startup health check: try `GET http://localhost:4000/health` for up to 10s
     - Log full startup command and PID
   - Add `/api/health/litellm` endpoint that proxies to LiteLLM's health check

2. **`templates/management.html`**:
   - Show richer reload status: "LiteLLM restarted (PID 1234)" or "LiteLLM failed to start — check logs"
   - Add "View Startup Log" button that fetches last 20 lines of litellm.log after reload

---

## Task 3: Auto-refresh Option for Logs

### Changes

1. **`templates/management.html`**:
   - Add "Auto-refresh" toggle button next to each "Refresh Logs" button (LiteLLM + Debug Logs sections)
   - Add interval dropdown: 2s, 5s, 10s (default 5s)
   - When toggled on, use `setInterval` to call `loadLogs()` / `loadDebugLogs()` periodically
   - Persist toggle state + interval in `localStorage`
   - Clear intervals when navigating away from Logs page, restore on return
   - Visual indicator: toggle button gets green background when active

---

### Task 4: Unable to use code to successfully authenticate with AWS

After pasting the code, the auth fails with the following error:

```[TokenRefresher] DEBUG: stdout line #7: 'aws: [ERROR]: Error loading or redeeming a login authorization code: State parameter 86cb1e6c-e78d-4f80-af4b-492756bfe182 does not match expected value 9c6e1497-1c24-4183-ab45-dc9cf8cd5720.'
[TokenRefresher] DEBUG: stdout loop exhausted after 7 lines.
[TokenRefresher] DEBUG: calling proc.wait()...
[TokenRefresher] DEBUG: proc.wait() returned. returncode=255
[TokenRefresher] aws sso login exited with code 255. Login did not complete — auth still required.
```

Find the root cause of this error and implement a fix. Potential areas to investigate:
- The `aws sso login` flow and how it handles the state parameter
- Whether the code submission is correctly linked to the original login request
- Any issues with how the subprocess is being called or how input is being provided to it