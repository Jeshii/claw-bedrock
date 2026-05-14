# Providers as First-Class Entities

## Overview

Currently, provider configuration (base URLs, credential env var names, etc.) is duplicated across every model record. This means adding a new model for an existing provider requires manually re-entering the same `api_base`, `aws_region`, or env var references each time — and renaming a provider's endpoint means touching every model that uses it.

This document describes making providers a standalone entity type, similar to how tags are already implemented in `src/db.py`. Each provider gets its own record, its own management page, and models reference a provider by name rather than duplicating its configuration.

---

## Data Model

### Provider Record

```json
{
  "name": "bedrock-us-east",
  "display_name": "AWS Bedrock (US East)",
  "type": "bedrock",
  "aws_region": "us-east-1",
  "aws_access_key_env": "AWS_ACCESS_KEY_ID",
  "aws_secret_key_env": "AWS_SECRET_ACCESS_KEY",
  "color": "#FF9900",
  "notes": "Primary production Bedrock endpoint"
}
```

A `type` field gates which fields are relevant in the UI (e.g., `bedrock` shows AWS fields; `openai-compatible` shows `api_base`).

### Model Record Change

Add a `provider` field (string, the provider `name`) to each model:

```json
{
  "model_name": "claude-3-5-sonnet",
  "litellm_params": { ... },
  "provider": "bedrock-us-east",
  "tags": ["production"]
}
```

This is an additive change — existing models without `provider` continue to work.

---

## Architecture

```
providers_table (TinyDB)
       │
       ├── referenced by models_table.provider
       │
       └── Provider detail page
               └── lists all models using this provider
```

---

## Implementation Steps

### Step 1: DB Layer — Provider CRUD

Add to `src/db.py`, following the exact pattern of the existing tags functions:

```python
providers_table = db.table("providers")


def get_all_providers():
    """Get all provider definitions."""
    return providers_table.all()


def get_provider(name):
    """Get a single provider by name."""
    return providers_table.get(where("name") == name)


def upsert_provider(provider: dict):
    """Create or update a provider. `provider` must include a `name` key."""
    providers_table.upsert(provider, where("name") == provider["name"])


def delete_provider(name):
    """Delete a provider definition.
    
    Does NOT remove the `provider` field from models — those models will
    show an 'unknown provider' state in the UI, prompting reassignment.
    """
    providers_table.remove(where("name") == name)


def rename_provider(old_name, new_name):
    """Rename a provider and update all model references."""
    provider = providers_table.get(where("name") == old_name)
    if not provider:
        return False
    providers_table.remove(where("name") == old_name)
    provider["name"] = new_name
    providers_table.insert(provider)
    # Update all models referencing the old name
    models_table.update(
        {"provider": new_name},
        where("provider") == old_name
    )
    return True


def get_models_by_provider(provider_name):
    """Get all models assigned to a specific provider."""
    return models_table.search(where("provider") == provider_name)


def set_model_provider(model_name, provider_name):
    """Assign a provider to a model."""
    return models_table.update(
        {"provider": provider_name},
        where("model_name") == model_name
    )
```

---

### Step 2: API Routes

Add provider routes to `src/management_app.py`:

```python
# ── Providers ──────────────────────────────────────────────────────────────

@app.route("/api/providers", methods=["GET"])
def list_providers():
    return jsonify(get_all_providers())


@app.route("/api/providers", methods=["POST"])
def create_provider():
    data = request.get_json()
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    upsert_provider(data)
    return jsonify({"success": True})


@app.route("/api/providers/<name>", methods=["GET"])
def get_provider_detail(name):
    provider = get_provider(name)
    if not provider:
        return jsonify({"error": "Not found"}), 404
    models = get_models_by_provider(name)
    return jsonify({"provider": provider, "models": models})


@app.route("/api/providers/<name>", methods=["PUT"])
def update_provider(name):
    data = request.get_json()
    data["name"] = name  # ensure name key is consistent
    upsert_provider(data)
    return jsonify({"success": True})


@app.route("/api/providers/<name>", methods=["DELETE"])
def delete_provider_route(name):
    delete_provider(name)
    return jsonify({"success": True})


@app.route("/api/providers/<old_name>/rename", methods=["POST"])
def rename_provider_route(old_name):
    new_name = request.get_json().get("new_name")
    if not new_name:
        return jsonify({"error": "new_name is required"}), 400
    success = rename_provider(old_name, new_name)
    return jsonify({"success": success})
```

---

### Step 3: Provider Page (Template)

Create `templates/providers.html` (or add a tab to the existing settings page). Structure mirrors the tags page:

```
Providers
├── Provider list (sidebar or cards)
│     ├── [color dot] AWS Bedrock US East  (3 models)
│     ├── [color dot] OpenAI Compatible    (1 model)
│     └── + Add Provider
│
└── Provider detail (on click)
      ├── Name, display name, type, color
      ├── Type-specific fields (AWS region / api_base / etc.)
      ├── Notes
      ├── Models using this provider (list with links)
      └── [Edit] [Rename] [Delete]
```

Key UI considerations:
- **Type selector** drives which fields appear (use `data-type` + CSS `display:none` to show/hide field groups)
- **Color picker** — same component as used for tags
- **Delete guard** — if the provider has models assigned, show a warning: "X models use this provider. They will show as unassigned."

---

### Step 4: Add Provider Selector to Model Form

In the Add/Edit Model form, replace the bare `api_base` text input with:

```html
<label for="model-provider">Provider</label>
<select id="model-provider" name="provider">
  <option value="">— None / manual config —</option>
  <!-- Populated dynamically from /api/providers -->
</select>

<div id="provider-autofill-hint" class="hint" style="display:none">
  Provider fields will be pre-filled from the selected provider.
</div>
```

```javascript
async function loadProviderSelector() {
  const res = await fetch("/api/providers");
  const providers = await res.json();
  const sel = document.getElementById("model-provider");

  providers.forEach(p => {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.display_name || p.name;
    sel.appendChild(opt);
  });
}

document.getElementById("model-provider").addEventListener("change", function () {
  const name = this.value;
  if (!name) return;

  fetch(`/api/providers/${name}`)
    .then(r => r.json())
    .then(data => {
      const p = data.provider;
      // Auto-fill relevant litellm_params fields based on provider type
      if (p.type === "bedrock") {
        document.getElementById("aws_region_name").value = p.aws_region || "";
      } else if (p.type === "openai-compatible") {
        document.getElementById("api_base").value = p.api_base || "";
      }
      document.getElementById("provider-autofill-hint").style.display = "block";
    });
});
```

---

### Step 5: Model List — Show Provider Badge

On the models list, show a colored provider badge next to each model (similar to tags):

```python
# In the /api/models route, enrich each model with provider display info
def enrich_model_with_provider(model):
    provider_name = model.get("provider")
    if provider_name:
        provider = get_provider(provider_name)
        model["_provider"] = provider  # attach for UI use, not saved to DB
    return model
```

In the template:
```html
{% if model._provider %}
<span class="badge provider-badge"
      style="background: {{ model._provider.color }}20; 
             border: 1px solid {{ model._provider.color }}; 
             color: {{ model._provider.color }}">
  {{ model._provider.display_name or model._provider.name }}
</span>
{% endif %}
```

---

### Step 6: Migration — Infer Providers from Existing Models

Write a one-time migration helper (run manually or on first boot if `providers_table` is empty):

```python
def migrate_infer_providers():
    """
    Scan existing models and auto-create provider records based on
    patterns in litellm_params (aws_region, api_base, etc.).
    Only runs if providers_table is empty.
    """
    if providers_table.all():
        return  # Already populated

    providers_seen = {}
    for model in models_table.all():
        params = model.get("litellm_params", {})
        region = params.get("aws_region_name") or params.get("aws_region")
        if region:
            key = f"bedrock-{region}"
            if key not in providers_seen:
                providers_seen[key] = {
                    "name": key,
                    "display_name": f"AWS Bedrock ({region})",
                    "type": "bedrock",
                    "aws_region": region,
                    "color": "#FF9900",
                }
            models_table.update(
                {"provider": key},
                where("model_name") == model["model_name"]
            )

    for p in providers_seen.values():
        providers_table.insert(p)

    print(f"[DB] Inferred {len(providers_seen)} providers from existing models")
```

Call this in `_migrate_yaml_to_db()` or as a separate startup check.

---

## Key Differences vs. Tags

| Aspect | Tags | Providers |
|---|---|---|
| Cardinality | Many per model | One per model |
| Purpose | Filtering / grouping | Configuration source |
| Auto-fill behavior | None | Pre-fills litellm_params fields |
| Detail page content | Models with this tag | Models using this provider + config fields |
| Delete behavior | Removes tag from all models | Leaves models "unassigned" (softer) |

---

## Future Extensions

- **Provider health status** — ping each provider's endpoint and show a green/red indicator on the provider card
- **Per-provider rate limits** — store `rpm` / `tpm` on the provider and apply to all models that use it
- **Provider import** — a preset library of common providers (AWS Bedrock us-east-1, us-west-2, Azure OpenAI, etc.) to reduce manual entry
