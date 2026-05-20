# Dynamic Provider-Based "Add New Model" Refactor

## Problem

The "Add New Model" section on the Models page was broken due to a variable name bug (`provider` instead of `type` in `loadProviderUI()`). Additionally, the UI had hardcoded provider types (OpenRouter, Ollama, Bedrock, Manual) rather than supporting a fluid "define your own provider" paradigm.

## Solution

Refactor the "Add New Model" flow to be fully dynamic based on provider definitions stored in the database.

## New User Flow

1. **Select Provider** → Dropdown shows all configured providers (Bedrock seeded by default, plus any user-added providers)
2. **Model Selection**:
   - **Bedrock**: Keep existing Mantle API polling + catalog loading (special UI)
   - **Other providers**: Generic "Poll Models" button that fetches `GET /v1/models` from the provider's `api_base`
3. **Fuzzy Search** → Filter the model list as you type
4. **Add Model** → Select from list, auto-fills context length if available, user can override
5. **Reload Indicator** → Reload LiteLLM button flashes to indicate a reload is needed

## API Changes

### New Endpoint: `GET /api/providers/{name}/models`

**File:** `src/management_app.py`

- Look up provider by name from TinyDB
- Get `api_base` from provider definition
- If no `api_base`, return 400 with message "Provider has no api_base configured"
- Proxy to `{api_base}/v1/models` (OpenAI-compatible standard)
- Normalize response to `{id, name, context_length}` format
- Handle errors (connection, timeout, auth) with helpful messages

## Frontend Changes

### 1. Fix Immediate Bug
**File:** `src/static/js/models.js` lines 736, 751, 774

```javascript
// Before (broken):
} else if (provider === "ollama") {
} else if (provider === "bedrock") {
} else if (provider === "manual") {

// After (fixed):
} else if (type === "ollama") {
} else if (type === "bedrock") {
} else if (type === "manual") {
```

### 2. Update `onModelProviderSelect()`
**File:** `src/static/js/providers.js`

Pass full provider object instead of just name+type:

```javascript
// Before:
if (p.type === "bedrock") {
    loadProviderUIForProvider(name, "bedrock");
} else {
    loadProviderUIForProvider(name, "openai-compatible");
}

// After:
loadProviderUIForProvider(data.provider);
```

### 3. Refactor `loadProviderUIForProvider()`
**File:** `src/static/js/models.js`

Accept full provider object, branch on type:

```javascript
async function loadProviderUIForProvider(provider) {
    if (provider.type === "bedrock") {
        loadProviderUI("bedrock");
        // Pre-fill region from provider.aws_region
    } else {
        loadGenericProviderUI(provider);
    }
}
```

### 4. New `loadGenericProviderUI(provider)`
**File:** `src/static/js/models.js`

Renders a dynamic form for any non-Bedrock provider:

- Provider badge with name, type, color
- Show `api_base` as read-only info
- If no `api_base`: hide poll button, show message "Configure api_base in Providers first"
- "Poll Models" button → calls `GET /api/providers/{name}/models`
- Search input for fuzzy filtering (filter as you type)
- Model list (`<select size="10">`) showing `name (context_length)`
- On select: auto-fill context length if available
- Context length input (editable, auto-filled from selection)
- "Add Model" button → calls `addGenericModel()`

### 5. New `addGenericModel()`
**File:** `src/static/js/models.js`

Builds model config from provider + selected model:

```json
{
  "model_name": "claw-bedrock/{model_id}",
  "litellm_params": {
    "model": "{provider.name}/{model_id}",
    "api_base": "{provider.api_base}",
    "context_length": 131072
  }
}
```

### 6. Update `addModelCommon()` to Flash Reload Button
**File:** `src/static/js/models.js`

On success, add `needs-reload` class to `#reload-litellm-btn` (matches existing delete flow).

## Model Config Examples

### Bedrock
```json
{
  "model_name": "claw-bedrock/anthropic.claude-3-5-sonnet",
  "litellm_params": {
    "model": "bedrock_mantle/anthropic.claude-3-5-sonnet",
    "api_base": "os.environ/BEDROCK_MANTLE_API_BASE",
    "max_tokens": 8192,
    "context_length": 200000
  }
}
```

### Generic OpenAI-compatible
```json
{
  "model_name": "claw-bedrock/gpt-4o",
  "litellm_params": {
    "model": "custom-provider/gpt-4o",
    "api_base": "https://api.example.com/v1",
    "context_length": 131072
  }
}
```

## Files Modified

1. `src/management_app.py` - New `GET /api/providers/{name}/models` endpoint
2. `src/static/js/models.js` - Bug fix + refactor + new functions
3. `src/static/js/providers.js` - Pass full provider object

## Files NOT Modified

- `src/static/css/management.css` - Existing styles work
- `templates/partials/page_models.html` - Structure is fine
- `src/db.py` - Provider CRUD already supports `api_base`

## Design Decisions

1. **Model naming**: Use `{provider_name}/{model_id}` format for `litellm_params.model`, user can rename later
2. **No api_base**: Hide poll button, show message to configure in Providers page
3. **Context length**: Auto-detect from `/v1/models` response when available, user can override manually
4. **Reload indicator**: Flash Reload LiteLLM button after successful add (matches delete behavior)
