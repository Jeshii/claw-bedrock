# Plan: Fix Ollama, LiteLLM Reload, Emojis, and Syntax Warnings

## 1. Ollama Integration Fixes

### Auto-populate API Base
- Store `/api/auth/status` response in `window.authData` JS global variable
- Update `loadProviderUI()` to set `#ollama-api-base` value to `authData.ollama.host` when Ollama provider is selected
- Ensure the field is pre-filled if `OLLAMA_API_BASE` environment variable is set

### Fix 500 Model Fetch Error
- Modify `/api/providers/ollama/models` endpoint to default to `OLLAMA_API_BASE` env var if `api_base` query param is missing
- Return 400 (not 500) for connection errors with descriptive messages
- Add proper exception handling for DNS resolution failures, timeouts, and connection refused errors

## 2. LiteLLM Reload Failure Fix

### Improve PID Tracking
- Update `reload_litellm()` to verify PID from `/tmp/litellm.pid` is running using `os.kill(pid, 0)` before sending SIGHUP
- If PID file is stale (process not running), auto-detect running LiteLLM process and update PID file
- Consider using `psutil` or `/proc` to find the actual LiteLLM process if PID file is stale

### Graceful Failure Handling
- Catch "No such process" errors (Errno 3)
- Log a warning instead of crashing
- Notify the user via frontend toast that the model was added but reload failed
- Do not block model addition on reload failure

## 3. Remove All Emojis

Replace all emojis with text equivalents to fix garbling issues:

| Emoji | Unicode | Replacement |
|-------|---------|-------------|
| ✅ | `\u2705` | `[OK]` |
| ❌ | `\u274c` | `[ERROR]` |
| ↻ | `\u21bb` | `[Reload]` |
| ▶ | `\u25b6` | `>` |
| ▼ | `\u25bc` | `v` |

- Search entire codebase for emojis and unicode icon escapes
- Replace all occurrences in HTML, JavaScript, and Python code
- Pay special attention to free model icons that may be garbled
- Remove any emoji characters from model names or display text

## 4. Fix Escape Sequence Warning

- Line 1031 in `management_app.py`: Change `\/` to `\\/` in the JS regex string
- The Python `SyntaxWarning: invalid escape sequence '\/'` occurs because `\/` is not a valid Python escape sequence
- Fix by using raw string or double-escaping the backslash

## Verification Steps

1. Test Ollama auto-populate with `OLLAMA_API_BASE` set - verify field is pre-filled
2. Confirm no 500 errors when fetching Ollama models - should get proper error messages
3. Verify LiteLLM reload handles stale PIDs without crashing - check warning logs
4. Search codebase to confirm no remaining emojis/unicode icon escapes
5. Run management app to confirm no Python syntax warnings
6. Test model addition still works even when LiteLLM reload fails
7. Verify all UI elements display correctly without garbled characters
