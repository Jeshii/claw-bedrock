# Implementation Plan

## Task 1: Context Display

Show context window size (max tokens) for each model in the management UI.

### Bedrock Model Data

Fetched from AWS Bedrock model cards (https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html) on 2026-05-05. Full table:

| Model | Context window | Max output |
|---|---|---|
| deepseek.v3.2 | 164K (167936) | 8K |
| deepseek.v3.1 | 128K | 8K |
| moonshotai.kimi-k2.5 | 256K | 16K |
| moonshotai.kimi-k2-thinking | 256K | 16K |
| mistral.ministral-3-3b-instruct | 128K | 8K |
| mistral.ministral-3-8b-instruct | 128K | 8K |
| mistral.ministral-3-14b-instruct | 128K | 8K |
| mistral.magistral-small-2509 | 128K | 40K |
| mistral.mistral-large-3-675b-instruct | 256K | 32K |
| mistral.devstral-2-123b | 256K | 32K |
| nvidia.nemotron-nano-3-30b | 256K | 8K |
| nvidia.nemotron-nano-9b-v2 | 128K | 8K |
| nvidia.nemotron-nano-12b-v2 | 128K | 8K |
| qwen.qwen3-235b-a22b-2507 | 128K | 8K |
| qwen.qwen3-next-80b-a3b-instruct | 256K | 8K |
| qwen.qwen3-coder-480b-a35b-instruct | 128K | 16K |
| qwen.qwen3-coder-30b-a3b-instruct | 128K | 16K |
| qwen.qwen3-coder-next | 256K | 16K |
| qwen.qwen3-32b | 128K | 8K |
| openai.gpt-oss-20b | 128K | 16K |
| openai.gpt-oss-120b | 128K | 16K |
| google.gemma-3-4b-it | 128K | 8K |
| google.gemma-3-12b-it | 128K | 8K |
| google.gemma-3-27b-it | 128K | 8K |
| zai.glm-4.7 | 203K (207872) | 4K |
| zai.glm-4.7-flash | 203K (207872) | 4K |
| minimax.minimax-m2 | 1M (1048576) | 8K |
| minimax.minimax-m2.1 | 196K (200704) | 8K |

Note: Previous `max_tokens` values in bedrock_models.json were incorrect (all set to 32768). Updated to match actual AWS documentation.

### Changes

1. **`management_app.py`**:
   - **OpenRouter**: Extract `context_length` from API response. The OpenRouter `/v1/models` response includes `architecture.context_length` or `top_provider.context_length` per model. Include it in the filtered response.
   - **Ollama**: The `/api/tags` response doesn't include context length. Add a new endpoint `/api/providers/ollama/model-details?name=<name>` that calls `/api/show` and returns `details.context_length`. Also add `context_length` input when manually adding Ollama models.
   - **Manual**: Add "Context Length" numeric input in add-model form.

2. **`templates/management.html`**:
   - In `loadModels()`: display context length next to model name (e.g., `— 128k ctx`)
   - In add-model flow: show context length field for all providers (auto-populated for OpenRouter when model is selected)
   - For manual entry: numeric input field
   - Add `context_length` to the model item display

---

## Task 2: Fix Reload Issue

When LiteLLM is reloaded (via add/delete/rename model or manual reload), the API becomes unreachable.

### Changes

1. **`management_app.py`**:
   - Add `validate_config()` — parse generated YAML with `yaml.safe_load()` before restarting
   - Improve `reload_litellm()`:
     - After SIGTERM, use `process.wait(timeout=5)` to ensure old process is gone, fall back to SIGKILL
     - After starting new process, verify it's still running after 2s with `psutil.Process(pid).is_running()`
     - Add post-startup health check: try `GET http://localhost:4000/health` for up to 10s
     - Log the full startup command and PID
   - Add `/api/health/litellm` endpoint that proxies to LiteLLM's health check
   - Update all callers (add/delete/rename model) to return more detailed reload status

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

## Task 4: Add timestamps to logs

Add timestamps to each log line in both LiteLLM and debug logs for better troubleshooting.

## Task 5: Clean up messy looking python errors in the management UI and replace with user-friendly messages

```
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/litellm/proxy/proxy_server.py", line 5785, in async_data_generator
    async for chunk in proxy_logging_obj.async_post_call_streaming_iterator_hook(
  File "/usr/local/lib/python3.12/site-packages/litellm/proxy/utils.py", line 2235, in async_post_call_streaming_iterator_hook
    async for chunk in current_response:
  File "/usr/local/lib/python3.12/site-packages/litellm/integrations/custom_logger.py", line 470, in async_post_call_streaming_iterator_hook
    async for item in response:
  File "/usr/local/lib/python3.12/site-packages/litellm/integrations/custom_logger.py", line 470, in async_post_call_streaming_iterator_hook
    async for item in response:
  File "/usr/local/lib/python3.12/site-packages/litellm/integrations/custom_logger.py", line 470, in async_post_call_streaming_iterator_hook
    async for item in response:
  File "/usr/local/lib/python3.12/site-packages/litellm/proxy/hooks/responses_id_security.py", line 286, in async_post_call_streaming_iterator_hook
    async for chunk in response:
  File "/usr/local/lib/python3.12/site-packages/litellm/integrations/custom_logger.py", line 470, in async_post_call_streaming_iterator_hook
    async for item in response:
  File "/usr/local/lib/python3.12/site-packages/litellm/integrations/custom_logger.py", line 470, in async_post_call_streaming_iterator_hook
    async for item in response:
  File "/usr/local/lib/python3.12/site-packages/litellm/integrations/custom_logger.py", line 470, in async_post_call_streaming_iterator_hook
    async for item in response:
  [Previous line repeated 2 more times]
  File "/usr/local/lib/python3.12/site-packages/litellm/router.py", line 1793, in __anext__
    return await self._async_generator.__anext__()
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/litellm/router.py", line 1878, in stream_with_fallbacks
    raise fallback_error
  File "/usr/local/lib/python3.12/site-packages/litellm/router.py", line 1845, in stream_with_fallbacks
    await self.async_function_with_fallbacks_common_utils(
  File "/usr/local/lib/python3.12/site-packages/litellm/router.py", line 5520, in async_function_with_fallbacks_common_utils
    raise original_exception
  File "/usr/local/lib/python3.12/site-packages/litellm/router.py", line 1798, in stream_with_fallbacks
    async for item in model_response:
  File "/usr/local/lib/python3.12/site-packages/litellm/litellm_core_utils/streaming_handler.py", line 2240, in __anext__
    self._handle_stream_fallback_error(e)
  File "/usr/local/lib/python3.12/site-packages/litellm/litellm_core_utils/streaming_handler.py", line 2308, in _handle_stream_fallback_error
    raise MidStreamFallbackError(
litellm.exceptions.MidStreamFallbackError: litellm.MidStreamFallbackError: litellm.RateLimitError: RateLimitError: OpenrouterException - Message: Provider returned error, Metadata: {'error_type': 'rate_limit_exceeded'}, User ID: . Received Model Group=openrouter/owl-alpha
Available Model Group Fallbacks=None Original exception: RateLimitError: litellm.RateLimitError: RateLimitError: OpenrouterException - Message: Provider returned error, Metadata: {'error_type': 'rate_limit_exceeded'}, User ID: 
```

Should be replaced with something like:

"OpenRouter rate limit exceeded for model owl-alpha. Please try again later or switch to a different model."

## Task 6: Collapse Log windows by default, with option to expand

In the management UI, the LiteLLM Logs and Debug Logs sections can be quite long and overwhelming. By default, these sections should be collapsed with only the title and "Show Logs" button visible. When the user clicks "Show Logs", the section expands to show the log content and the button changes to "Hide Logs". This allows users to focus on the main management features without being distracted by long logs, while still providing easy access to logs when needed.

## Task 7: Reorganize project structure

See `docs/FILE_STRUCTURE.md` for proposed new structure and file moves. Update all imports, Dockerfile paths, and documentation references accordingly.