# LiteLLM API Security: Master Key & Virtual Keys

## Overview

The claw-bedrock management app currently has no authentication layer on the LiteLLM proxy endpoint. Any client that knows the proxy URL can send requests. This document outlines a complete implementation to add `master_key`-based security, with optional virtual key generation per user or application.

LiteLLM natively supports a `master_key` under `litellm_settings`. Once set, all proxy requests must include `Authorization: Bearer <key>`. The management app's DB layer (`settings_table`) and config-generation pipeline (`get_models_for_litellm()`) make this a minimal addition.

---

## Architecture

```
Web UI (generate key)
       │
       ▼
settings_table  ──► get_litellm_settings()  ──► LiteLLM config
   master_key                                     litellm_settings.master_key
                                                       │
                                                       ▼
                                              Proxy requires
                                        Authorization: Bearer <key>
```

---

## Implementation Steps

### Step 1: DB Layer — Store the Master Key

In `src/db.py`, add helper functions alongside existing `get_setting` / `set_setting`:

```python
import secrets

def get_master_key():
    """Get the LiteLLM master key, or None if not set."""
    return get_setting("litellm_master_key", None)

def generate_master_key():
    """Generate a new cryptographically secure master key and persist it."""
    key = "sk-claw-" + secrets.token_urlsafe(32)
    set_setting("litellm_master_key", key)
    return key

def clear_master_key():
    """Remove the master key (disables auth on next reload)."""
    settings_table.remove(where("key") == "litellm_master_key")
```

**Why `secrets.token_urlsafe`?** It uses OS-level entropy (CSPRNG), unlike `random`, making brute-force infeasible.

---

### Step 2: Inject into LiteLLM Config

Modify `get_litellm_settings()` in `src/db.py`:

```python
def get_litellm_settings():
    """Get litellm_settings with token_refresher and optional master_key."""
    settings = {
        "callbacks": ["token_refresher.BedrockTokenRefresher"],
    }
    key = get_master_key()
    if key:
        settings["master_key"] = key
    return settings
```

This is backward-compatible: if no key is stored, auth is not enabled and existing behavior is preserved.

---

### Step 3: Web UI — Security Settings Card

Add a new "Security" section to the settings page in `src/management_app.py`.

#### Route: GET current key status

```python
@app.route("/api/security/key", methods=["GET"])
def get_key_status():
    key = get_master_key()
    if key:
        # Return masked version — show only prefix + last 4 chars
        masked = key[:12] + "..." + key[-4:]
        return jsonify({"enabled": True, "masked_key": masked})
    return jsonify({"enabled": False})
```

#### Route: POST generate new key

```python
@app.route("/api/security/key/generate", methods=["POST"])
def generate_key():
    key = generate_master_key()
    # Trigger LiteLLM config reload so the key takes effect immediately
    _reload_litellm_config()
    return jsonify({"success": True, "key": key})
```

> **Important:** Return the full key only once on generation. After this the UI shows only the masked version. Instruct the user to copy it immediately.

#### Route: DELETE revoke key

```python
@app.route("/api/security/key", methods=["DELETE"])
def revoke_key():
    clear_master_key()
    _reload_litellm_config()
    return jsonify({"success": True})
```

---

### Step 4: UI Component (Jinja2 Template)

In the relevant template file, add a Security card:

```html
<section class="settings-card" id="security">
  <h2>API Security</h2>
  <p class="muted">
    When a master key is set, all LiteLLM proxy requests must include
    <code>Authorization: Bearer &lt;key&gt;</code>.
  </p>

  <div id="key-status">
    <!-- Populated by JS on load -->
  </div>

  <div class="button-row">
    <button id="btn-generate-key" class="btn btn-primary">
      Generate New Key
    </button>
    <button id="btn-revoke-key" class="btn btn-danger" style="display:none">
      Revoke Key
    </button>
  </div>

  <!-- One-time reveal modal -->
  <dialog id="key-reveal-modal">
    <h3>Your New API Key</h3>
    <p class="warning">Copy this now — it will not be shown again.</p>
    <code id="revealed-key"></code>
    <button onclick="navigator.clipboard.writeText(document.getElementById('revealed-key').textContent)">
      Copy
    </button>
    <button onclick="document.getElementById('key-reveal-modal').close()">
      Done
    </button>
  </dialog>
</section>
```

```javascript
async function loadKeyStatus() {
  const res = await fetch("/api/security/key");
  const data = await res.json();
  const statusEl = document.getElementById("key-status");
  const revokeBtn = document.getElementById("btn-revoke-key");

  if (data.enabled) {
    statusEl.innerHTML = `<span class="badge badge-success">Active</span>
      <code class="key-masked">${data.masked_key}</code>`;
    revokeBtn.style.display = "inline-block";
  } else {
    statusEl.innerHTML = `<span class="badge badge-neutral">Not configured</span>`;
    revokeBtn.style.display = "none";
  }
}

document.getElementById("btn-generate-key").addEventListener("click", async () => {
  if (!confirm("Generate a new key? Any existing key will be invalidated.")) return;
  const res = await fetch("/api/security/key/generate", { method: "POST" });
  const data = await res.json();
  document.getElementById("revealed-key").textContent = data.key;
  document.getElementById("key-reveal-modal").showModal();
  loadKeyStatus();
});

document.getElementById("btn-revoke-key").addEventListener("click", async () => {
  if (!confirm("Revoke the current key? The proxy will become unauthenticated.")) return;
  await fetch("/api/security/key", { method: "DELETE" });
  loadKeyStatus();
});

loadKeyStatus();
```

---

### Step 5: LiteLLM Config Reload

LiteLLM supports hot config reload via its admin API. Add a helper in `management_app.py`:

```python
import requests as req

LITELLM_BASE_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")

def _reload_litellm_config():
    """Push updated config to LiteLLM without restart."""
    config = get_models_for_litellm()
    try:
        # LiteLLM's /config/update endpoint (requires master_key if already set)
        key = get_master_key()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        req.post(
            f"{LITELLM_BASE_URL}/config/update",
            json=config,
            headers=headers,
            timeout=5,
        )
    except Exception as e:
        print(f"[reload] Config reload failed: {e}")
```

---

## Security Considerations

| Concern | Mitigation |
|---|---|
| Key stored in plaintext in TinyDB JSON | Acceptable for local/container deployment; add at-rest encryption if deployed publicly |
| Key visible in environment or logs | Never log the master key; ensure config file is in `.gitignore` |
| Key rotation | Use the "Generate New Key" button; immediately update all clients |
| Proxy accessible without the management app | Consider firewall rules to restrict LiteLLM port to localhost or trusted IPs |

---

## Usage After Implementation

Once a master key is set, all LiteLLM proxy calls require a header:

```
Authorization: Bearer sk-claw-<your-key>
```

For example, with `curl`:

```bash
curl http://localhost:4000/chat/completions \
  -H "Authorization: Bearer sk-claw-xxxx" \
  -H "Content-Type: application/json" \
  -d '{"model": "my-model", "messages": [{"role": "user", "content": "Hello"}]}'
```

Or in Python with the OpenAI client:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4000",
    api_key="sk-claw-xxxx",
)
```
