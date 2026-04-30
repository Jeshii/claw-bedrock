from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
import yaml
import os
import requests
from typing import Optional, List, Dict
import re

app = FastAPI(title="Claw Bedrock Management")

CONFIG_PATH = "/app/config.yaml"
LOCAL_CONFIG_PATH = "/app/config.local.yaml"
BEDROCK_CONFIG_PATH = "/app/config.bedrock.yaml"


def load_config() -> Dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {"model_list": []}
    return {"model_list": []}


def save_local_config(config: Dict):
    with open(LOCAL_CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


@app.get("/api/auth/status")
async def auth_status():
    """Check if AWS auth is needed and get auth URL."""
    auth_needed = os.path.exists("/tmp/auth_needed")
    auth_url = None
    if os.path.exists("/tmp/auth_url"):
        with open("/tmp/auth_url", "r") as f:
            auth_url = f.read().strip()
    return {"auth_needed": auth_needed, "auth_url": auth_url}


@app.get("/api/models")
async def list_models():
    """List all configured models."""
    config = load_config()
    return {"models": config.get("model_list", [])}


@app.get("/api/providers/openrouter/models")
async def fetch_openrouter_models(include_free: bool = True, api_key: Optional[str] = None):
    """Fetch available models from OpenRouter."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif os.environ.get("OPENROUTER_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['OPENROUTER_API_KEY']}"
    
    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=30)
        resp.raise_for_status()
        models = resp.json().get("data", [])
        
        if not include_free:
            def _is_free(m):
                try:
                    return float(m.get("pricing", {}).get("prompt", "1")) == 0
                except:
                    return False
            models = [m for m in models if not _is_free(m)]
        
        # Sort: free first, then by name
        def sort_key(m):
            try:
                cost = float(m.get("pricing", {}).get("prompt", "inf"))
            except:
                cost = float("inf")
            return (cost, m.get("name", "").lower())
        models.sort(key=sort_key)
        
        return {"models": models}
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch OpenRouter models: {str(e)}")


@app.get("/api/providers/ollama/models")
async def fetch_ollama_models(api_base: str = Query(...)):
    """Fetch models from a remote Ollama instance."""
    try:
        resp = requests.get(f"{api_base.rstrip('/')}/api/tags", timeout=10)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return {"models": sorted(models, key=lambda m: m.get("name", "").lower())}
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch Ollama models: {str(e)}")


@app.post("/api/models")
async def add_model(model: Dict):
    """Add a new model to config.local.yaml."""
    config = {"model_list": []}
    if os.path.exists(LOCAL_CONFIG_PATH):
        with open(LOCAL_CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f) or {"model_list": []}
    
    model_list = config.setdefault("model_list", [])
    model_list.append(model)
    
    save_local_config(config)
    
    # Trigger config merge by recreating config.yaml
    merge_configs()
    
    return {"status": "success", "model": model}


def merge_configs():
    """Merge bedrock and local configs into config.yaml."""
    with open(CONFIG_PATH, "w") as f:
        f.write("")
    
    if os.path.exists(BEDROCK_CONFIG_PATH):
        with open(BEDROCK_CONFIG_PATH, "r") as f:
            f.write(f.read())
        with open(CONFIG_PATH, "a") as f:
            f.write("\n")
    
    if os.path.exists(LOCAL_CONFIG_PATH):
        with open(LOCAL_CONFIG_PATH, "r") as f:
            lines = f.readlines()
            if lines:
                with open(CONFIG_PATH, "a") as out:
                    out.writelines(lines[1:])  # Skip first line (duplicate model_list header)


@app.get("/")
async def dashboard():
    """Serve the management dashboard."""
    html = """
<!DOCTYPE html>
<html>
<head>
    <title>Claw Bedrock Management</title>
    <style>
        body { font-family: sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }
        .section { margin-bottom: 30px; border: 1px solid #ddd; padding: 20px; border-radius: 8px; }
        .auth-needed { background: #fff3cd; border-color: #ffc107; }
        button { padding: 8px 16px; margin: 5px; cursor: pointer; }
        input, select { padding: 8px; margin: 5px; width: 300px; }
        .model-item { padding: 10px; border-bottom: 1px solid #eee; }
    </style>
</head>
<body>
    <h1>Claw Bedrock Management</h1>
    
    <!-- Auth Section -->
    <div class="section" id="auth-section">
        <h2>AWS Authentication</h2>
        <div id="auth-status"></div>
    </div>
    
    <!-- Models Section -->
    <div class="section">
        <h2>Configured Models</h2>
        <div id="models-list"></div>
        <button onclick="showAddModel()">Add New Model</button>
    </div>
    
    <!-- Add Model Modal -->
    <div class="section" id="add-model-section" style="display:none;">
        <h2>Add New Model</h2>
        <select id="provider-select" onchange="loadProviderUI()">
            <option value="">Select Provider</option>
            <option value="openrouter">OpenRouter</option>
            <option value="ollama">Ollama (Remote)</option>
            <option value="huggingface">HuggingFace</option>
            <option value="manual">Manual</option>
        </select>
        <div id="provider-ui"></div>
    </div>

    <script>
        // Load auth status
        async function loadAuth() {
            const res = await fetch('/api/auth/status');
            const data = await res.json();
            const authDiv = document.getElementById('auth-status');
            const authSection = document.getElementById('auth-section');
            
            if (data.auth_needed) {
                authSection.classList.add('auth-needed');
                authDiv.innerHTML = `
                    <p>AWS Authentication Required</p>
                    <p>Visit this URL to authenticate: <a href="${data.auth_url}" target="_blank">${data.auth_url}</a></p>
                    <p>After authenticating, the token will refresh automatically.</p>
                `;
            } else {
                authSection.classList.remove('auth-needed');
                authDiv.innerHTML = '<p>AWS Authentication: OK</p>';
            }
        }
        
        // Load models
        async function loadModels() {
            const res = await fetch('/api/models');
            const data = await res.json();
            const modelsDiv = document.getElementById('models-list');
            modelsDiv.innerHTML = data.models.map(m => 
                `<div class="model-item">
                    <strong>${m.model_name}</strong>: ${m.litellm_params.model}
                </div>`
            ).join('');
        }
        
        // Show add model UI
        function showAddModel() {
            document.getElementById('add-model-section').style.display = 'block';
        }
        
        // Load provider-specific UI
        async function loadProviderUI() {
            const provider = document.getElementById('provider-select').value;
            const uiDiv = document.getElementById('provider-ui');
            
            if (provider === 'openrouter') {
                uiDiv.innerHTML = `
                    <h3>OpenRouter Models</h3>
                    <button onclick="loadOpenRouterModels()">Fetch Available Models</button>
                    <div id="openrouter-models"></div>
                `;
            } else if (provider === 'ollama') {
                uiDiv.innerHTML = `
                    <h3>Ollama (Remote Instance)</h3>
                    <input id="ollama-api-base" placeholder="Ollama API Base (e.g., http://192.168.10.1:11434)" />
                    <button onclick="loadOllamaModels()">Fetch Models</button>
                    <div id="ollama-models"></div>
                `;
            } else if (provider === 'manual') {
                uiDiv.innerHTML = `
                    <h3>Manual Model Entry</h3>
                    <input id="manual-name" placeholder="Model Name" />
                    <input id="manual-model-path" placeholder="Model Path (e.g., openrouter/anthropic/claude-3)" />
                    <input id="manual-api-base" placeholder="API Base (optional)" />
                    <button onclick="addManualModel()">Add Model</button>
                `;
            }
        }
        
        // Load OpenRouter models
        async function loadOpenRouterModels() {
            const res = await fetch('/api/providers/openrouter/models');
            const data = await res.json();
            const modelsDiv = document.getElementById('openrouter-models');
            modelsDiv.innerHTML = data.models.slice(0, 20).map(m => 
                `<div>
                    <input type="checkbox" id="or-${m.id}" />
                    <label for="or-${m.id}">${m.name} (${m.id})</label>
                </div>`
            ).join('');
        }
        
        // Load Ollama models
        async function loadOllamaModels() {
            const apiBase = document.getElementById('ollama-api-base').value;
            if (!apiBase) return alert('Enter Ollama API Base');
            const res = await fetch(`/api/providers/ollama/models?api_base=${encodeURIComponent(apiBase)}`);
            const data = await res.json();
            const modelsDiv = document.getElementById('ollama-models');
            modelsDiv.innerHTML = data.models.map(m => 
                `<div>
                    <input type="checkbox" id="ol-${m.name}" />
                    <label for="ol-${m.name}">${m.name}</label>
                </div>`
            ).join('');
        }
        
        // Initial load
        loadAuth();
        loadModels();
        setInterval(loadAuth, 30000); // Refresh auth status every 30s
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)
