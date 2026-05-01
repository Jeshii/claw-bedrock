from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
import yaml
import os
import sys
import requests
import subprocess
from typing import Optional, List, Dict
import re

app = FastAPI(title="Claw Bedrock Management")

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
LOCAL_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.local.yaml")
BEDROCK_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.bedrock.yaml")
LOG_PATH = os.path.join(CONFIG_DIR, "litellm.log")


@app.on_event("startup")
async def startup_event():
    merge_configs()
    print(f"[Startup] Merged configs on startup (CONFIG_DIR={CONFIG_DIR})")


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


@app.get("/api/logs")
async def get_logs(lines: int = 50):
    """Return the last N lines of the LiteLLM log."""
    if not os.path.exists(LOG_PATH):
        return {"logs": "No logs available yet."}
    try:
        result = subprocess.run(
            ["tail", f"-{lines}", LOG_PATH],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {"logs": result.stdout or "Log is empty."}
    except Exception as e:
        return {"logs": f"Error reading logs: {str(e)}"}


@app.get("/api/models")
async def list_models():
    """List all configured models."""
    config = load_config()
    return {"models": config.get("model_list", [])}


@app.get("/api/providers/openrouter/models")
async def fetch_openrouter_models(include_free: bool = True, search: Optional[str] = None, api_key: Optional[str] = None):
    """Fetch available models from OpenRouter with optional filtering."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif os.environ.get("OPENROUTER_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['OPENROUTER_API_KEY']}"
    
    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=30)
        resp.raise_for_status()
        models = resp.json().get("data", [])
        
        # Filter by free/paid
        if include_free:
            def _is_free(m):
                try:
                    return float(m.get("pricing", {}).get("prompt", "1")) == 0
                except:
                    return False
            models = [m for m in models if _is_free(m)]
        else:
            # Exclude free models
            def _is_not_free(m):
                try:
                    return float(m.get("pricing", {}).get("prompt", "1")) != 0
                except:
                    return True
            models = [m for m in models if _is_not_free(m)]
        
        # Filter by search term
        if search:
            search_lower = search.lower()
            models = [m for m in models if search_lower in m.get("id", "").lower() or search_lower in m.get("name", "").lower()]
        
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


@app.get("/api/providers/bedrock/models")
async def fetch_bedrock_models():
    """Fetch available Bedrock Mantle models from template, excluding already added ones."""
    # Load bedrock template
    bedrock_config = {"model_list": []}
    if os.path.exists(BEDROCK_CONFIG_PATH):
        try:
            with open(BEDROCK_CONFIG_PATH, "r") as f:
                bedrock_config = yaml.safe_load(f) or {"model_list": []}
        except Exception as e:
            print(f"[Bedrock] Error loading bedrock template: {e}", file=sys.stderr)

    # Load local config to exclude already added models
    local_config = {"model_list": []}
    if os.path.exists(LOCAL_CONFIG_PATH):
        try:
            with open(LOCAL_CONFIG_PATH, "r") as f:
                local_config = yaml.safe_load(f) or {"model_list": []}
        except Exception as e:
            print(f"[Bedrock] Error loading local config: {e}", file=sys.stderr)

    # Get set of already added model names
    added_model_names = {m.get("model_name") for m in local_config.get("model_list", [])}

    # Filter out already added models
    available_models = [
        m for m in bedrock_config.get("model_list", [])
        if m.get("model_name") not in added_model_names
    ]

    return {"models": available_models}


@app.delete("/api/models/{model_name}")
async def delete_model(model_name: str):
    """Delete a model from config.local.yaml."""
    if not os.path.exists(LOCAL_CONFIG_PATH):
        raise HTTPException(404, "No local config found")

    with open(LOCAL_CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f) or {"model_list": []}

    model_list = config.get("model_list", [])
    initial_count = len(model_list)
    config["model_list"] = [m for m in model_list if m.get("model_name") != model_name]

    if len(config["model_list"]) == initial_count:
        raise HTTPException(404, f"Model {model_name} not found in local config")

    save_local_config(config)
    merge_configs()
    reload_litellm()

    return {"status": "success", "deleted": model_name}


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

    # Trigger config merge and reload LiteLLM
    merge_configs()
    reload_litellm()

    return {"status": "success", "model": model}


@app.put("/api/models/{old_model_name}")
async def rename_model(old_model_name: str, update: Dict):
    """Rename a model in config.local.yaml."""
    new_model_name = update.get("model_name")
    if not new_model_name:
        raise HTTPException(400, "model_name is required")

    if not os.path.exists(LOCAL_CONFIG_PATH):
        raise HTTPException(404, "No local config found")

    with open(LOCAL_CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f) or {"model_list": []}

    model_list = config.get("model_list", [])
    found = False
    for model in model_list:
        if model.get("model_name") == old_model_name:
            model["model_name"] = new_model_name
            found = True
            break

    if not found:
        raise HTTPException(404, f"Model {old_model_name} not found in local config")

    save_local_config(config)
    merge_configs()
    reload_litellm()

    return {"status": "success", "old_name": old_model_name, "new_name": new_model_name}


def merge_configs():
    """Merge local config into config.yaml (bedrock config no longer pre-merged)."""
    # Load local config
    local_config = {"model_list": []}
    if os.path.exists(LOCAL_CONFIG_PATH):
        try:
            with open(LOCAL_CONFIG_PATH, "r") as f:
                local_config = yaml.safe_load(f) or {"model_list": []}
        except Exception as e:
            print(f"[Merge] Error loading local config: {e}", file=sys.stderr)
            local_config = {"model_list": []}

    # Write merged config (local config only)
    try:
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(local_config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print(f"[Merge] Config merged. Total models: {len(local_config.get('model_list', []))}")
    except Exception as e:
        print(f"[Merge] Error writing merged config: {e}", file=sys.stderr)


def reload_litellm():
    """Reload LiteLLM config by sending SIGHUP to the process."""
    pid_file = "/tmp/litellm.pid"
    if not os.path.exists(pid_file):
        print("[Reload] PID file not found, skipping reload")
        return False

    try:
        with open(pid_file, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 1)  # SIGHUP = signal 1
        print(f"[Reload] Sent SIGHUP to LiteLLM (PID {pid})")
        return True
    except Exception as e:
        print(f"[Reload] Error reloading LiteLLM: {e}", file=sys.stderr)
        return False


@app.get("/")
async def dashboard():
    """Serve the management dashboard."""
    html = """
<!DOCTYPE html>
<html>
<head>
    <title>Claw Bedrock Management</title>
    <style>
        body { font-family: sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; transition: all 0.3s; }
        body.dark { background: #1a1a1a; color: #e0e0e0; }
        .section { margin-bottom: 30px; border: 1px solid #ddd; padding: 20px; border-radius: 8px; }
        body.dark .section { border-color: #444; background: #2a2a2a; }
        .auth-needed { background: #fff3cd; border-color: #ffc107; }
        body.dark .auth-needed { background: #3d3000; border-color: #ffc107; }
        button { padding: 8px 16px; margin: 5px; cursor: pointer; }
        body.dark button { background: #444; color: #e0e0e0; border: 1px solid #666; }
        input, select { padding: 8px; margin: 5px; width: 300px; }
        body.dark input, body.dark select { background: #333; color: #e0e0e0; border: 1px solid #666; }
        .model-item { padding: 10px; border-bottom: 1px solid #eee; display: flex; align-items: center; gap: 10px; }
        body.dark .model-item { border-color: #444; }
        body.dark pre { background: #2a2a2a !important; color: #e0e0e0; }
        .theme-toggle { position: fixed; top: 20px; right: 20px; padding: 8px 16px; cursor: pointer; border-radius: 4px; }
        .model-name { cursor: pointer; flex: 1; }
        .model-name:hover { text-decoration: underline; }
        .delete-btn { padding: 2px 8px; font-size: 12px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer; }
        body.dark .delete-btn { background: #842029; }
        /* Toast notifications */
        #toast-container { position: fixed; top: 60px; right: 20px; z-index: 1000; display: flex; flex-direction: column; gap: 10px; }
        .toast { padding: 12px 20px; border-radius: 6px; color: white; font-size: 14px; animation: fadeIn 0.3s ease-in; min-width: 250px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
        .toast-success { background: #28a745; }
        .toast-error { background: #dc3545; }
        .toast-info { background: #17a2b8; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div id="toast-container"></div>
    <button class="theme-toggle" onclick="toggleTheme()">🌙 Dark</button>
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
        <h2>Add New Model <button onclick="closeAddModel()" style="float: right;">✕ Close</button></h2>
        <select id="provider-select" onchange="loadProviderUI()">
            <option value="">Select Provider</option>
            <option value="openrouter">OpenRouter</option>
            <option value="ollama">Ollama (Remote)</option>
            <option value="bedrock">Bedrock (Mantle)</option>
            <option value="huggingface">HuggingFace</option>
            <option value="manual">Manual</option>
        </select>
        <div id="provider-ui"></div>
        </div>
        
        <!-- LiteLLM Logs Section -->
        <div class="section">
            <h2>LiteLLM Logs</h2>
            <button onclick="loadLogs()">↻ Refresh Logs</button>
            <select id="log-lines" onchange="loadLogs()">
                <option value="50">Last 50 lines</option>
                <option value="100">Last 100 lines</option>
                <option value="200">Last 200 lines</option>
            </select>
            <pre id="logs-output" style="background: #f5f5f5; padding: 10px; border-radius: 4px; max-height: 400px; overflow-y: auto; font-size: 12px;"></pre>
        </div>
        
        <script>
        // Theme toggle
        function toggleTheme() {
            const body = document.body;
            const btn = document.querySelector('.theme-toggle');
            body.classList.toggle('dark');
            const isDark = body.classList.contains('dark');
            btn.textContent = isDark ? '☀️ Light' : '🌙 Dark';
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
        }
        
        // Load saved theme
        if (localStorage.getItem('theme') === 'dark') {
            document.body.classList.add('dark');
            document.addEventListener('DOMContentLoaded', () => {
                document.querySelector('.theme-toggle').textContent = '☀️ Light';
            });
        }

        // Toast notification function
        function showToast(message, type = 'success', duration = 3000) {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast toast-${type}`;
            toast.textContent = message;
            container.appendChild(toast);
            setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transition = 'opacity 0.3s';
                setTimeout(() => toast.remove(), 300);
            }, duration);
        }
        
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
                    <button onclick="reloadAuth()" style="margin-top: 10px;">↻ Reload Auth URL</button>
                    <p>After authenticating, the token will refresh automatically.</p>
                `;
            } else {
                authSection.classList.remove('auth-needed');
                authDiv.innerHTML = '<p>AWS Authentication: OK</p>';
            }
        }
        
        async function reloadAuth() {
            const btn = event.target;
            btn.style.transform = 'rotate(360deg)';
            btn.style.transition = 'transform 0.5s';
            setTimeout(() => { btn.style.transform = ''; }, 500);
            
            const res = await fetch('/api/auth/status');
            const data = await res.json();
            if (data.auth_url) {
                window.open(data.auth_url, '_blank');
            }
            loadAuth();
        }
        
        // Load models
        async function loadModels() {
            const res = await fetch('/api/models');
            const data = await res.json();
            const modelsDiv = document.getElementById('models-list');

            // Group models by provider
            const groups = {};
            data.models.forEach(m => {
                const provider = m.litellm_params.model.split('/')[0] || 'other';
                if (!groups[provider]) groups[provider] = [];
                groups[provider].push(m);
            });

            // Build collapsible sections
            modelsDiv.innerHTML = Object.entries(groups).map(([provider, models]) => `
                <div class="provider-group" style="margin-bottom: 10px;">
                    <h3 onclick="toggleGroup('${provider}')" style="cursor: pointer; user-select: none;">
                        <span id="arrow-${provider}">▶</span> ${provider} (${models.length})
                    </h3>
                    <div id="group-${provider}" style="display: none; padding-left: 20px;">
${models.map(m => `
    <div class="model-item" data-model-name="${m.model_name}">
        <span class="model-name" onclick="startRename('${m.model_name.replace(/'/g, "\\'")}', this)">${m.model_name}</span>
        <span>: ${m.litellm_params.model}</span>
        <button class="delete-btn" onclick="deleteModel('${m.model_name.replace(/'/g, "\\'")}')">Delete</button>
    </div>
`).join('')}
                    </div>
                </div>
            `).join('');
        }
        
        function toggleGroup(provider) {
            const group = document.getElementById(`group-${provider}`);
            const arrow = document.getElementById(`arrow-${provider}`);
            if (group.style.display === 'none') {
                group.style.display = 'block';
                arrow.textContent = '▼';
            } else {
                group.style.display = 'none';
                arrow.textContent = '▶';
            }
        }
        
        // Inline rename model
        function startRename(oldName, element) {
            const input = document.createElement('input');
            input.type = 'text';
            input.value = oldName;
            input.style.flex = '1';
            input.onkeydown = (e) => {
                if (e.key === 'Enter') submitRename(oldName, input.value, element);
                if (e.key === 'Escape') cancelRename(oldName, element);
            };
            input.onblur = () => cancelRename(oldName, element);
            element.parentNode.replaceChild(input, element);
            input.focus();
            input.select();
        }

        function cancelRename(oldName, originalElement) {
            const input = originalElement.parentNode.querySelector('input');
            if (input) {
                const span = document.createElement('span');
                span.className = 'model-name';
                span.textContent = oldName;
                span.onclick = () => startRename(oldName, span);
                input.parentNode.replaceChild(span, input);
            }
        }

        async function submitRename(oldName, newName, originalElement) {
            if (!newName || newName === oldName) {
                cancelRename(oldName, originalElement);
                return;
            }

            try {
                const res = await fetch(`/api/models/${encodeURIComponent(oldName)}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({model_name: newName})
                });

                if (res.ok) {
                    showToast('Model renamed successfully');
                    loadModels();
                } else {
                    const error = await res.json();
                    showToast(`Error: ${error.detail || 'Failed to rename model'}`, 'error');
                    cancelRename(oldName, originalElement);
                }
            } catch (e) {
                showToast(`Error: ${e.message}`, 'error');
                cancelRename(oldName, originalElement);
            }
        }

        // Delete model
        async function deleteModel(modelName) {
            if (!confirm(`Delete model "${modelName}"?`)) return;

            try {
                const res = await fetch(`/api/models/${encodeURIComponent(modelName)}`, {
                    method: 'DELETE'
                });

                if (res.ok) {
                    showToast('Model deleted successfully');
                    loadModels();
                } else {
                    const error = await res.json();
                    showToast(`Error: ${error.detail || 'Failed to delete model'}`, 'error');
                }
            } catch (e) {
                showToast(`Error: ${e.message}`, 'error');
            }
        }
        
        // Show add model UI
        function showAddModel() {
            document.getElementById('add-model-section').style.display = 'block';
        }
        
        // Close add model UI
        function closeAddModel() {
            document.getElementById('add-model-section').style.display = 'none';
            document.getElementById('provider-ui').innerHTML = '';
            document.getElementById('provider-select').value = '';
        }
        
        // Load provider-specific UI
        async function loadProviderUI() {
            const provider = document.getElementById('provider-select').value;
            const uiDiv = document.getElementById('provider-ui');

            if (provider === 'openrouter') {
                uiDiv.innerHTML = `
                    <h3>OpenRouter Models</h3>
                    <div style="margin: 10px 0;">
                        <input id="or-search" placeholder="Search models..." style="width: 200px; padding: 8px;" oninput="loadOpenRouterModels()" />
                        <label style="margin-left: 10px;">
                            <input type="checkbox" id="or-free-only" checked onchange="loadOpenRouterModels()" /> Free only
                        </label>
                    </div>
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
            } else if (provider === 'bedrock') {
                uiDiv.innerHTML = `
                    <h3>Bedrock (Mantle) Models</h3>
                    <button onclick="loadBedrockModels()">Fetch Available Models</button>
                    <div id="bedrock-models"></div>
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
        
        // Store OpenRouter models data
        let openRouterModels = [];
        
        // Load OpenRouter models
        async function loadOpenRouterModels() {
            const search = document.getElementById('or-search').value;
            const freeOnly = document.getElementById('or-free-only').checked;
            const params = new URLSearchParams();
            if (search) params.append('search', search);
            if (freeOnly) params.append('include_free', 'true');
            else params.append('include_free', 'false');
            
            const res = await fetch(`/api/providers/openrouter/models?${params}`);
            const data = await res.json();
            openRouterModels = data.models;
            const modelsDiv = document.getElementById('openrouter-models');
            modelsDiv.innerHTML = data.models.map(m => {
                const isFree = parseFloat(m.pricing?.prompt || '1') === 0;
                return `<div>
                    <input type="checkbox" id="or-${m.id}" />
                    <label for="or-${m.id}">${m.name} (${m.id}) ${isFree ? '🆓' : ''}</label>
                </div>`;
            }).join('') + `
                <button onclick="addSelectedOpenRouterModels()" style="margin-top: 10px;">Add Selected Models</button>
            `;
        }
        
        // Add selected OpenRouter models
        async function addSelectedOpenRouterModels() {
            const checkboxes = document.querySelectorAll('#openrouter-models input[type="checkbox"]:checked');
            if (checkboxes.length === 0) return showToast('No models selected', 'error');

            const promises = [];
            checkboxes.forEach(cb => {
                const modelId = cb.id.replace('or-', '');
                const modelData = openRouterModels.find(m => m.id === modelId);
                if (modelData) {
                    const modelConfig = {
                        model_name: `claw-bedrock/${modelData.id.replace(/\//g, '-')}`,
                        litellm_params: {
                            model: `openrouter/${modelData.id}`
                        }
                    };
                    promises.push(
                        fetch('/api/models', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(modelConfig)
                        })
                    );
                    cb.checked = false;
                }
            });
            
            await Promise.all(promises);
            showToast(`Added ${checkboxes.length} model(s)`);
            loadModels();
        }

        // Store Ollama models data
        let ollamaModels = [];
        let ollamaApiBase = '';

        // Load Ollama models
        async function loadOllamaModels() {
            const apiBase = document.getElementById('ollama-api-base').value;
            if (!apiBase) return showToast('Enter Ollama API Base', 'error');
            ollamaApiBase = apiBase;
            const res = await fetch(`/api/providers/ollama/models?api_base=${encodeURIComponent(apiBase)}`);
            const data = await res.json();
            ollamaModels = data.models;
            const modelsDiv = document.getElementById('ollama-models');
            modelsDiv.innerHTML = data.models.map(m =>
                `<div>
                    <input type="checkbox" id="ol-${m.name}" />
                    <label for="ol-${m.name}">${m.name}</label>
                </div>`
            ).join('') + `
                <button onclick="addSelectedOllamaModels()" style="margin-top: 10px;">Add Selected Models</button>
            `;
        }

        // Add selected Ollama models
        async function addSelectedOllamaModels() {
            const checkboxes = document.querySelectorAll('#ollama-models input[type="checkbox"]:checked');
            if (checkboxes.length === 0) return showToast('No models selected', 'error');

            const promises = [];
            checkboxes.forEach(cb => {
                const modelName = cb.id.replace('ol-', '');
                const modelConfig = {
                    model_name: `claw-bedrock/ollama-${modelName}`,
                    litellm_params: {
                        model: `ollama/${modelName}`,
                        api_base: ollamaApiBase
                    }
                };
                promises.push(
                    fetch('/api/models', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(modelConfig)
                    })
                );
                cb.checked = false;
            });

            await Promise.all(promises);
            showToast(`Added ${checkboxes.length} model(s)`);
            loadModels();
        }

        // Store Bedrock models data
        let bedrockModels = [];

        // Load Bedrock models
        async function loadBedrockModels() {
            const res = await fetch('/api/providers/bedrock/models');
            const data = await res.json();
            bedrockModels = data.models;
            const modelsDiv = document.getElementById('bedrock-models');

            if (bedrockModels.length === 0) {
                modelsDiv.innerHTML = '<p>No available Bedrock models (all may already be added).</p>';
                return;
            }

            modelsDiv.innerHTML = bedrockModels.map(m =>
                `<div>
                    <input type="checkbox" id="br-${m.model_name}" />
                    <label for="br-${m.model_name}">${m.model_name} (${m.litellm_params.model})</label>
                </div>`
            ).join('') + `
                <button onclick="addSelectedBedrockModels()" style="margin-top: 10px;">Add Selected Models</button>
            `;
        }

        // Add selected Bedrock models
        async function addSelectedBedrockModels() {
            const checkboxes = document.querySelectorAll('#bedrock-models input[type="checkbox"]:checked');
            if (checkboxes.length === 0) return showToast('No models selected', 'error');

            const promises = [];
            checkboxes.forEach(cb => {
                const modelName = cb.id.replace('br-', '');
                const modelData = bedrockModels.find(m => m.model_name === modelName);
                if (modelData) {
                    promises.push(
                        fetch('/api/models', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(modelData)
                        })
                    );
                    cb.checked = false;
                }
            });

            await Promise.all(promises);
            showToast(`Added ${checkboxes.length} model(s)`);
            loadModels();
            // Refresh Bedrock models list
            loadBedrockModels();
        }
        
        // Load LiteLLM logs
        async function loadLogs() {
            const lines = document.getElementById('log-lines').value;
            const res = await fetch(`/api/logs?lines=${lines}`);
            const data = await res.json();
            const logsDiv = document.getElementById('logs-output');
            logsDiv.textContent = data.logs;
            logsDiv.scrollTop = logsDiv.scrollHeight;
        }
        
         // Initial load
         loadAuth();
         loadModels();
         setInterval(loadAuth, 30000); // Refresh auth status every 30s
     </script>
     <footer style="margin-top: 40px; padding: 20px; border-top: 1px solid #ddd; font-size: 12px; color: #666;">
         <p><strong>Persistence Note:</strong> To persist model configurations across container restarts, mount a host directory and set <code>CONFIG_DIR</code>:</p>
         <pre style="background: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto;">podman run -e CONFIG_DIR=/config -v ~/claw-bedrock:/config:Z -p 4000:4000 -p 8282:8282 claw-bedrock</pre>
         <p>Or use the provided <code>docker-compose.yml</code> or a systemd <code>.container</code> file with <code>Environment=CONFIG_DIR=/config</code> and <code>Volume=%h/claw-bedrock:/config:Z</code>.</p>
     </footer>
</body>
</html>
"""
    return HTMLResponse(content=html)
