from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
import yaml
import os
import sys
import requests
import subprocess
import psutil
from typing import Optional, Dict

app = FastAPI(title="Claw Bedrock Management")

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
LOCAL_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.local.yaml")
BEDROCK_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.bedrock.yaml")
LOG_PATH = os.path.join(CONFIG_DIR, "litellm.log")
VERSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")


def get_version():
    """Get version baked in at build time."""
    try:
        with open(VERSION_PATH, "r") as f:
            return f.read().strip()
    except:
        return "unknown"


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


def load_local_config() -> Dict:
    """Load local config, creating with defaults if needed."""
    if os.path.exists(LOCAL_CONFIG_PATH):
        with open(LOCAL_CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
    # Set defaults
    if 'use_prefix' not in config:
        config['use_prefix'] = True
    return config


@app.get("/api/settings")
async def get_settings():
    """Get current settings."""
    config = load_local_config()
    return {"use_prefix": config.get('use_prefix', True)}


@app.post("/api/settings")
async def update_settings(use_prefix: bool = Query(...)):
    """Update settings."""
    config = load_local_config()
    config['use_prefix'] = use_prefix
    save_local_config(config)
    return {"success": True, "use_prefix": use_prefix}


@app.get("/api/auth/status")
async def auth_status():
    """Check if AWS auth is needed and get auth URL."""
    auth_needed = os.path.exists("/tmp/auth_needed")
    auth_url = None
    if os.path.exists("/tmp/auth_url"):
        with open("/tmp/auth_url", "r") as f:
            auth_url = f.read().strip()

    openrouter_key = bool(os.environ.get("OPENROUTER_API_KEY"))
    ollama_host = os.environ.get("OLLAMA_API_BASE", "")

    return {
        "auth_needed": auth_needed,
        "auth_url": auth_url,
        "openrouter": {"configured": openrouter_key},
        "ollama": {"configured": bool(ollama_host), "host": ollama_host}
    }


@app.get("/api/version")
async def version_endpoint():
    """Return the current version of claw-bedrock."""
    return {"version": get_version()}


@app.get("/api/dashboard")
async def get_dashboard():
    """Return dashboard statistics."""
    config = load_config()
    model_count = len(config.get("model_list", []))
    providers = {}
    for m in config.get("model_list", []):
        provider = m.get("litellm_params", {}).get("model", "").split("/")[0] or "unknown"
        providers[provider] = providers.get(provider, 0) + 1
    return {
        "model_count": model_count,
        "providers": providers,
        "version": get_version()
    }


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


@app.post("/api/models/reload")
async def reload_models():
    """Manually trigger a LiteLLM config reload via SIGHUP."""
    reloaded = reload_litellm()
    if reloaded:
        return {"status": "success", "message": "LiteLLM reloaded"}
    return {"status": "warning", "message": "LiteLLM reload failed - PID file not found or process unreachable. Try restarting the container.", "reloaded": False}


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

        if include_free:
            def _is_free(m):
                try:
                    return float(m.get("pricing", {}).get("prompt", "1")) == 0
                except:
                    return False
            models = [m for m in models if _is_free(m)]
        else:
            def _is_not_free(m):
                try:
                    return float(m.get("pricing", {}).get("prompt", "1")) != 0
                except:
                    return True
            models = [m for m in models if _is_not_free(m)]

        if search:
            search_lower = search.lower()
            models = [m for m in models if search_lower in m.get("id", "").lower() or search_lower in m.get("name", "").lower()]

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
async def fetch_ollama_models(api_base: Optional[str] = Query(None)):
    """Fetch models from a remote Ollama instance."""
    api_base = api_base or os.environ.get("OLLAMA_API_BASE", "")
    if not api_base:
        raise HTTPException(400, "No Ollama API base provided. Set OLLAMA_API_BASE or provide api_base parameter.")

    try:
        resp = requests.get(f"{api_base.rstrip('/')}/api/tags", timeout=10)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return {"models": sorted(models, key=lambda m: m.get("name", "").lower())}
    except requests.exceptions.ConnectionError as e:
        raise HTTPException(400, f"Cannot connect to Ollama at {api_base}. Check the address and ensure Ollama is running.") from e
    except requests.exceptions.Timeout as e:
        raise HTTPException(400, f"Connection to Ollama at {api_base} timed out. The server may be slow or unreachable.") from e
    except requests.exceptions.RequestException as e:
        raise HTTPException(400, f"Error communicating with Ollama at {api_base}: {str(e)}") from e
    except Exception as e:
        raise HTTPException(400, f"Failed to fetch Ollama models: {str(e)}") from e


@app.get("/api/providers/bedrock/models")
async def fetch_bedrock_models():
    """Fetch available Bedrock Mantle models from template, excluding already added ones."""
    if not os.path.exists(BEDROCK_CONFIG_PATH):
        raise HTTPException(
            404,
            f"Bedrock template not found at {BEDROCK_CONFIG_PATH}. "
            f"Seed it by running: podman cp <container>:/app/config.bedrock.yaml {BEDROCK_CONFIG_PATH}"
        )

    bedrock_config = {"model_list": []}
    try:
        with open(BEDROCK_CONFIG_PATH, "r") as f:
            bedrock_config = yaml.safe_load(f) or {"model_list": []}
    except Exception as e:
        raise HTTPException(500, f"Error loading Bedrock template: {e}")

    local_config = {"model_list": []}
    if os.path.exists(LOCAL_CONFIG_PATH):
        try:
            with open(LOCAL_CONFIG_PATH, "r") as f:
                local_config = yaml.safe_load(f) or {"model_list": []}
        except Exception as e:
            print(f"[Bedrock] Error loading local config: {e}", file=sys.stderr)

    added_model_names = {m.get("model_name") for m in local_config.get("model_list", [])}
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
    reloaded = reload_litellm()

    return {"status": "success", "deleted": model_name, "reloaded": reloaded}


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
    merge_configs()
    reloaded = reload_litellm()

    return {"status": "success", "model": model, "reloaded": reloaded}


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
    reloaded = reload_litellm()

    return {"status": "success", "old_name": old_model_name, "new_name": new_model_name, "reloaded": reloaded}


def merge_configs():
    """Merge local config into config.yaml, including litellm_settings from bedrock template
    if any bedrock_mantle models are present."""
    local_config = {"model_list": []}
    if os.path.exists(LOCAL_CONFIG_PATH):
        try:
            with open(LOCAL_CONFIG_PATH, "r") as f:
                local_config = yaml.safe_load(f) or {"model_list": []}
        except Exception as e:
            print(f"[Merge] Error loading local config: {e}", file=sys.stderr)
            local_config = {"model_list": []}

    merged = {"model_list": local_config.get("model_list", [])}

    # Include litellm_settings from bedrock template if any bedrock_mantle models are in use
    has_bedrock = any(
        "bedrock_mantle" in (m.get("litellm_params", {}).get("model", ""))
        for m in merged["model_list"]
    )
    if has_bedrock and os.path.exists(BEDROCK_CONFIG_PATH):
        try:
            with open(BEDROCK_CONFIG_PATH, "r") as f:
                bedrock_config = yaml.safe_load(f) or {}
            if "litellm_settings" in bedrock_config:
                merged["litellm_settings"] = bedrock_config["litellm_settings"]
                print(f"[Merge] Included litellm_settings from bedrock template (BedrockTokenRefresher)")
        except Exception as e:
            print(f"[Merge] Error loading bedrock template for litellm_settings: {e}", file=sys.stderr)

    try:
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(merged, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print(f"[Merge] Config merged. Total models: {len(merged['model_list'])}")
    except Exception as e:
        print(f"[Merge] Error writing merged config: {e}", file=sys.stderr)


def reload_litellm() -> bool:
    """Reload LiteLLM config by sending SIGHUP to the process. Returns True on success."""
    pid_file = "/tmp/litellm.pid"

    # Read PID from file
    pid = None
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
        except (ValueError, IOError) as e:
            print(f"[Reload] Error reading PID file: {e}", file=sys.stderr)
            pid = None

    # Verify PID is running, or find LiteLLM process using psutil
    if pid is not None:
        try:
            os.kill(pid, 0)  # Check if process exists (signal 0 doesn't actually send a signal)
        except OSError:
            print(f"[Reload] PID {pid} is stale, searching for LiteLLM process...")
            pid = None

    if pid is None:
        # Try to find LiteLLM process using psutil
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                cmdline = proc.info['cmdline']
                if cmdline and any('litellm' in arg.lower() for arg in cmdline):
                    pid = proc.info['pid']
                    print(f"[Reload] Found LiteLLM process: PID {pid}")
                    # Update PID file
                    with open(pid_file, "w") as f:
                        f.write(str(pid))
                    break
        except Exception as e:
            print(f"[Reload] Error searching for LiteLLM process: {e}", file=sys.stderr)

    if pid is None:
        print("[Reload] No LiteLLM process found, skipping reload")
        return False

    # Send SIGHUP to reload config
    try:
        os.kill(pid, 1)  # SIGHUP = signal 1
        print(f"[Reload] Sent SIGHUP to LiteLLM (PID {pid})")
        return True
    except OSError as e:
        if e.errno == 3:  # No such process
            print(f"[Reload] Process {pid} no longer exists", file=sys.stderr)
        else:
            print(f"[Reload] Error reloading LiteLLM: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[Reload] Error reloading LiteLLM: {e}", file=sys.stderr)
        return False


@app.get("/")
async def dashboard():
    """Serve the management dashboard."""
    version = get_version()
    config = load_local_config()
    use_prefix = config.get('use_prefix', True)
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Claw Bedrock Management</title>
    <script>window.USE_PREFIX = {str(use_prefix).lower()};</script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        ul {{ padding-left: 20px; }}
        body {{ font-family: sans-serif; display: flex; min-height: 100vh; transition: all 0.3s; }}
        body.dark {{ background: #1a1a1a; color: #e0e0e0; }}

        .sidebar {{ width: 250px; background: #f8f9fa; padding: 20px; display: flex; flex-direction: column; border-right: 1px solid #ddd; transition: all 0.3s; }}
        body.dark .sidebar {{ background: #252525; border-color: #444; }}
        .sidebar-header {{ margin-bottom: 30px; }}
        .sidebar-header h2 {{ font-size: 18px; font-weight: 600; }}
        .sidebar-header .version {{ font-size: 12px; color: #666; margin-top: 4px; }}
        body.dark .sidebar-header .version {{ color: #999; }}
        .nav {{ list-style: none; flex: 1; }}
        .nav li {{ margin-bottom: 5px; }}
        .nav a {{ display: block; padding: 10px 15px; text-decoration: none; color: #333; border-radius: 6px; transition: background 0.2s; cursor: pointer; }}
        body.dark .nav a {{ color: #e0e0e0; }}
        .nav a:hover {{ background: #e9ecef; }}
        body.dark .nav a:hover {{ background: #333; }}
        .nav a.active {{ background: #007bff; color: white; }}
        body.dark .nav a.active {{ background: #0056b3; }}
        .sidebar-footer {{ margin-top: auto; padding-top: 20px; border-top: 1px solid #ddd; }}
        body.dark .sidebar-footer {{ border-color: #444; }}
        .theme-toggle {{ width: 100%; padding: 10px; cursor: pointer; border: 1px solid #ddd; border-radius: 6px; background: white; font-size: 14px; transition: all 0.2s; }}
        body.dark .theme-toggle {{ background: #333; color: #e0e0e0; border-color: #666; }}
        .theme-toggle:hover {{ background: #f8f9fa; }}
        body.dark .theme-toggle:hover {{ background: #444; }}

        .main {{ flex: 1; padding: 30px; max-width: 1200px; overflow-y: auto; }}
        .page {{ display: none; }}
        .page.active {{ display: block; }}
        h1 {{ margin-bottom: 20px; font-size: 24px; }}
        h2 {{ margin-bottom: 15px; font-size: 20px; }}
        h3 {{ margin-bottom: 10px; font-size: 16px; }}

        .section {{ margin-bottom: 30px; border: 1px solid #ddd; padding: 20px; border-radius: 8px; }}
        body.dark .section {{ border-color: #444; background: #2a2a2a; }}
        .auth-needed {{ background: #fff3cd; border-color: #ffc107; }}
        body.dark .auth-needed {{ background: #3d3000; border-color: #ffc107; }}

        button {{ padding: 8px 16px; margin: 5px; cursor: pointer; border: 1px solid #ddd; border-radius: 4px; background: white; font-size: 14px; }}
        body.dark button {{ background: #444; color: #e0e0e0; border-color: #666; }}
        button:hover {{ background: #f8f9fa; }}
        body.dark button:hover {{ background: #555; }}

        input, select {{ padding: 8px; margin: 5px; width: 300px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }}
        body.dark input, body.dark select {{ background: #333; color: #e0e0e0; border-color: #666; }}

        .model-item {{ padding: 10px; border-bottom: 1px solid #eee; display: flex; align-items: center; gap: 10px; }}
        body.dark .model-item {{ border-color: #444; }}
        body.dark pre {{ background: #2a2a2a !important; color: #e0e0e0; }}
        .model-name {{ cursor: pointer; flex: 1; }}
        .model-name:hover {{ text-decoration: underline; }}
        .delete-btn {{ padding: 2px 8px; font-size: 12px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer; }}
        body.dark .delete-btn {{ background: #842029; }}

        #toast-container {{ position: fixed; top: 20px; right: 20px; z-index: 1000; display: flex; flex-direction: column; gap: 10px; }}
        .toast {{ padding: 12px 20px; border-radius: 6px; color: white; font-size: 14px; animation: fadeIn 0.3s ease-in; min-width: 250px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); }}
        .toast-success {{ background: #28a745; }}
        .toast-error {{ background: #dc3545; }}
        .toast-info {{ background: #17a2b8; }}
        .toast-warning {{ background: #fd7e14; }}

        .help-section {{ line-height: 1.6; }}
        .help-section pre {{ background: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto; margin: 10px 0; }}
        body.dark .help-section pre {{ background: #333; }}

        @keyframes fadeIn {{{{ from {{{{ opacity: 0; transform: translateY(-10px); }}}} to {{{{ opacity: 1; transform: translateY(0); }}}} }}}}
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="sidebar-header">
            <h2>claw-bedrock</h2>
            <div class="version">v""" + version + """</div>
        </div>
        <ul class="nav">
            <li><a onclick="showPage('dashboard')" class="active">Dashboard</a></li>
            <li><a onclick="showPage('auth')">Authentication</a></li>
            <li><a onclick="showPage('models')">Models</a></li>
            <li><a onclick="showPage('logs')">Logs</a></li>
            <li><a onclick="showPage('help')">Help</a></li>
        </ul>
        <div class="sidebar-footer">
            <button class="theme-toggle" onclick="toggleTheme()"></button>
        </div>
    </div>

    <div class="main">
        <div id="toast-container"></div>

        <!-- Dashboard Page -->
        <div id="page-dashboard" class="page active">
            <h1>Dashboard</h1>
            <div class="section" id="dashboard-info">
                <h2>Server Information</h2>
                <div id="server-status">Loading...</div>
            </div>
        </div>

        <!-- Authentication Page -->
        <div id="page-auth" class="page">
            <h1>Authentication</h1>
            <div class="section" id="auth-section">
                <div id="auth-status"></div>
            </div>
        </div>

        <!-- Models Page -->
        <div id="page-models" class="page">
            <h1>Models</h1>
            <div class="section">
                <p>
                    <label>
                        <input type="checkbox" id="use-prefix-toggle" onchange="togglePrefix()">
                        Add <code>claw-bedrock/</code> prefix to model names
                    </label>
                    <br><small>Disable to use shorter model names (e.g., <code>qwen3-235b</code> instead of <code>claw-bedrock/qwen3-235b</code>)</small>
                </p>
            </div>
            <div class="section">
                <h2>Configured Models</h2>
                <div id="models-list"></div>
                <button onclick="showAddModel()">Add New Model</button>
                <button onclick="reloadLiteLLM()" title="Manually trigger LiteLLM config reload">` + RELOAD_SVG + ` Reload LiteLLM</button>
            </div>
            <div class="section" id="add-model-section" style="display:none;">
                <h2>Add New Model <button onclick="closeAddModel()" style="float: right;">` + CLOSE_SVG + ` Close</button></h2>
                <select id="provider-select" onchange="loadProviderUI()">
                    <option value="">Select Provider</option>
                    <option value="openrouter">OpenRouter</option>
                    <option value="ollama">Ollama (Remote)</option>
                    <option value="bedrock">Bedrock (Mantle)</option>
                    <option value="manual">Manual</option>
                </select>
                <div id="provider-ui"></div>
            </div>
        </div>

        <!-- Logs Page -->
        <div id="page-logs" class="page">
            <h1>Logs</h1>
            <div class="section">
                <h2>LiteLLM Logs</h2>
                <button onclick="loadLogs()">` + RELOAD_SVG + ` Refresh Logs</button>
                <select id="log-lines" onchange="loadLogs()">
                    <option value="50">Last 50 lines</option>
                    <option value="100">Last 100 lines</option>
                    <option value="200">Last 200 lines</option>
                </select>
                <pre id="logs-output" style="background: #f5f5f5; padding: 10px; border-radius: 4px; max-height: 400px; overflow-y: auto; font-size: 12px;"></pre>
            </div>
        </div>

        <!-- Help Page -->
        <div id="page-help" class="page">
            <h1>Help</h1>
            <div class="section help-section">
                <h2>About</h2>
                <p>A LiteLLM proxy server that exposes AWS Bedrock Mantle models and other providers via a single OpenAI-compatible API. Useful for <a href="https://github.com/ultraworkers/claw-code">claw-code</a>, <a href="https://github.com/anomalyco/opencode">opencode</a>, or other apps expecting an OpenAI response.</p>
                <p>GitHub: <a href="https://github.com/jeshii/claw-bedrock">https://github.com/jeshii/claw-bedrock</a></p>
            </div>
            <div class="section help-section">
                <h2>Endpoints</h2>
                <p><strong>LiteLLM API:</strong> <code id="litellm-url">detecting...</code> (OpenAI-compatible)</p>
                <p><strong>Management UI:</strong> <code id="mgmt-url">detecting...</code> (this page)</p>
                <p><strong>List models:</strong> <code id="models-curl">detecting...</code></p>
            </div>
            <div class="section help-section">
                <h2>Client Integrations</h2>
                <p><strong>opencode.ai:</strong> Set baseURL to <code id="opencode-url">detecting...</code> in <code>~/.config/opencode/opencode.json</code></p>
                <p><strong>claw-code:</strong> Set <code>OPENAI_API_KEY="dummy"</code> and <code>OPENAI_BASE_URL="<span id="clawcode-url">detecting...</span>"</code></p>
                <p>Model names use the <code>claw-bedrock/</code> prefix (e.g., <code>claw-bedrock/qwen3-235b</code>)</p>
            </div>
            <div class="section help-section">
                <h2>Adding Models</h2>
                <p>Use the Models page to add from Bedrock, OpenRouter, Ollama, or manually. Changes auto-reload LiteLLM via SIGHUP.</p>
                <p><strong>Required Environment Variables:</strong></p>
                <ul>
                    <li>OpenRouter: <code>OPENROUTER_API_KEY</code></li>
                    <li>Ollama: <code>OLLAMA_API_BASE</code> (default: <code>http://localhost:11434</code>)</li>
                    <li>Bedrock: <code>AWS_PROFILE</code>, <code>AWS_REGION</code>, <code>BEDROCK_MANTLE_API_BASE</code></li>
                </ul>
            </div>
            <div class="section help-section">
                <h2>Persistence</h2>
                <p>To persist model configurations across container restarts, mount a host directory to <code>/app</code> and set <code>CONFIG_DIR</code>:</p>
                <pre>podman run -e CONFIG_DIR=/app -v ~/claw-bedrock:/app:Z -p 4000:4000 -p 8282:8282 claw-bedrock</pre>
                <p>Or use the provided <code>docker-compose.yml</code> or a systemd <code>.container</code> file with <code>Environment=CONFIG_DIR=/app</code> and <code>Volume=%h/claw-bedrock:/app:Z</code>.</p>
            </div>
            <div class="section help-section">
                <h2>Version</h2>
                <p>Current version: <strong>""" + version + """</strong></p>
                <p>Versioning is done via git tags. To create a new release:</p>
                <pre>git tag -a v0.1.0 -m "Initial release"
git push origin v0.1.0</pre>
            </div>
        </div>
    </div>

    <script>
    const CHECK_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
    const X_CIRCLE_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
    const RELOAD_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle"><path d="M1 4v6h6M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>';
    const CHEVRON_RIGHT_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle"><path d="M9 18l6-6-6-6"/></svg>';
    const CHEVRON_DOWN_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle"><path d="M6 9l6 6 6-6"/></svg>';
    const CLOSE_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    const FREE_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle"><path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2z"/></svg>';
    const WARNING_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';

    function showPage(pageId) {
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        document.getElementById('page-' + pageId).classList.add('active');
        document.querySelectorAll('.nav a').forEach(a => a.classList.remove('active'));
        event.target.classList.add('active');
        if (pageId === 'dashboard') loadDashboard();
        if (pageId === 'models') loadModels();
        if (pageId === 'logs') loadLogs();
        if (pageId === 'auth') loadAuth();
    }

    async function loadDashboard() {
        const res = await fetch('/api/dashboard');
        const data = await res.json();
        const serverStatus = document.getElementById('server-status');
        let html = `<p><strong>Version:</strong> ${data.version}</p>`;
        html += `<p><strong>Configured Models:</strong> ${data.model_count}</p>`;
        if (Object.keys(data.providers).length > 0) {
            html += '<p><strong>Providers:</strong></p><ul>';
            for (const [provider, count] of Object.entries(data.providers)) {
                html += `<li>${provider}: ${count} model(s)</li>`;
            }
            html += '</ul>';
        }
        serverStatus.innerHTML = html;
    }

    const MOON_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;margin-right:4px"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
    const SUN_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;margin-right:4px"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>';

    function toggleTheme() {
        const body = document.body;
        const btn = document.querySelector('.theme-toggle');
        body.classList.toggle('dark');
        const isDark = body.classList.contains('dark');
        btn.innerHTML = isDark ? SUN_SVG + 'Light' : MOON_SVG + 'Dark';
        localStorage.setItem('theme', isDark ? 'dark' : 'light');
    }
    if (localStorage.getItem('theme') === 'dark') {
        document.body.classList.add('dark');
        document.addEventListener('DOMContentLoaded', () => {
            document.querySelector('.theme-toggle').innerHTML = SUN_SVG + 'Light';
        });
    }

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

    function showReloadToast(reloaded) {
        if (reloaded) {
            showToast('LiteLLM reloaded', 'info');
        } else {
            showToast('LiteLLM reload failed - try restarting the container', 'warning', 6000);
        }
    }

    async function reloadLiteLLM() {
        try {
            const res = await fetch('/api/models/reload', { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success') {
                showToast('LiteLLM reloaded', 'info');
            } else {
                showToast(`Reload failed: ${data.message || 'unknown error'}`, 'warning', 6000);
            }
        } catch (e) {
            showToast(`Reload error: ${e.message}`, 'error');
        }
    }

    async function loadAuth() {
        const res = await fetch('/api/auth/status');
        window.authData = await res.json();

        let html = '';

        // AWS Auth
        const authSection = document.getElementById('auth-section');
        if (data.auth_needed) {
            authSection.classList.add('auth-needed');
            html += `
                <div style="margin-bottom: 15px;">
                    <h3>AWS</h3>
                    <p>Authentication Required</p>
                    <p>Visit this URL to authenticate: <a href="${data.auth_url}" target="_blank">${data.auth_url}</a></p>
                    <button onclick="reloadAuth()" style="margin-top: 10px;">` + RELOAD_SVG + ` Reload Auth URL</button>
                    <p>After authenticating, the token will refresh automatically.</p>
                </div>
            `;
        } else {
            authSection.classList.remove('auth-needed');
            html += '<div style="margin-bottom: 15px;"><h3>AWS</h3><p>' + CHECK_SVG + ' Authenticated</p></div>';
        }

        // OpenRouter Auth
        html += `<div style="margin-bottom: 15px;"><h3>OpenRouter</h3>`;
        if (data.openrouter.configured) {
            html += '<p>' + CHECK_SVG + ' API Key configured</p>';
        } else {
            html += '<p>' + X_CIRCLE_SVG + ' API Key not set (OPENROUTER_API_KEY)</p>';
        }
        html += '</div>';

        // Ollama Auth
        html += `<div style="margin-bottom: 15px;"><h3>Ollama</h3>`;
        if (data.ollama.configured) {
            html += `<p>` + CHECK_SVG + ` Host configured: ${data.ollama.host}</p>`;
        } else {
            html += '<p>' + X_CIRCLE_SVG + ' Host not set (OLLAMA_API_BASE)</p>';
        }
        html += '</div>';

        document.getElementById('auth-status').innerHTML = html;
    }

    async function reloadAuth() {
        const btn = event.target;
        btn.style.transform = 'rotate(360deg)';
        btn.style.transition = 'transform 0.5s';
        setTimeout(() => { btn.style.transform = ''; }, 500);
        const res = await fetch('/api/auth/status');
        const data = await res.json();
        if (data.auth_url) window.open(data.auth_url, '_blank');
        loadAuth();
    }

    async function loadModels() {
        const res = await fetch('/api/models');
        const data = await res.json();
        const modelsDiv = document.getElementById('models-list');
        const groups = {};
        data.models.forEach(m => {
            const provider = m.litellm_params.model.split('/')[0] || 'other';
            if (!groups[provider]) groups[provider] = [];
            groups[provider].push(m);
        });
        modelsDiv.innerHTML = Object.entries(groups).map(([provider, models]) => `
            <div class="provider-group" style="margin-bottom: 10px;">
                    <h3 onclick="toggleGroup('${provider}')" style="cursor: pointer; user-select: none;">
                        <span id="arrow-${provider}">` + CHEVRON_RIGHT_SVG + `</span> ${provider} (${models.length})
                    </h3>
                <div id="group-${provider}" style="display: none; padding-left: 20px;">
                    ${models.map(m => `
                    <div class="model-item" data-model-name="${m.model_name}">
                        <span class="model-name" onclick="startRename('${m.model_name.replace(/'/g, "\\'") }', this)">${m.model_name}</span>
                        <span>: ${m.litellm_params.model}</span>
                        <button class="delete-btn" onclick="deleteModel('${m.model_name.replace(/'/g, "\\'")}')" >Delete</button>
                    </div>`).join('')}
                </div>
            </div>
        `).join('');
    }

    function toggleGroup(provider) {
        const group = document.getElementById(`group-${provider}`);
        const arrow = document.getElementById(`arrow-${provider}`);
        if (group.style.display === 'none') {
            group.style.display = 'block';
            arrow.innerHTML = CHEVRON_DOWN_SVG;
        } else {
            group.style.display = 'none';
            arrow.innerHTML = CHEVRON_RIGHT_SVG;
        }
    }

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
        const input = originalElement.parentNode ? originalElement.parentNode.querySelector('input') : null;
        if (input) {
            const span = document.createElement('span');
            span.className = 'model-name';
            span.textContent = oldName;
            span.onclick = () => startRename(oldName, span);
            input.parentNode.replaceChild(span, input);
        }
    }

    async function submitRename(oldName, newName, originalElement) {
        if (!newName || newName === oldName) { cancelRename(oldName, originalElement); return; }
        try {
            const res = await fetch(`/api/models/${encodeURIComponent(oldName)}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({model_name: newName})
            });
            if (res.ok) {
                const data = await res.json();
                showToast('Model renamed successfully');
                showReloadToast(data.reloaded);
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

    async function deleteModel(modelName) {
        if (!confirm(`Delete model "${modelName}"?`)) return;
        try {
            const res = await fetch(`/api/models/${encodeURIComponent(modelName)}`, { method: 'DELETE' });
            if (res.ok) {
                const data = await res.json();
                showToast('Model deleted successfully');
                showReloadToast(data.reloaded);
                loadModels();
            } else {
                const error = await res.json();
                showToast(`Error: ${error.detail || 'Failed to delete model'}`, 'error');
            }
        } catch (e) {
            showToast(`Error: ${e.message}`, 'error');
        }
    }

    function showAddModel() { document.getElementById('add-model-section').style.display = 'block'; }

    function closeAddModel() {
        document.getElementById('add-model-section').style.display = 'none';
        document.getElementById('provider-ui').innerHTML = '';
        document.getElementById('provider-select').value = '';
    }

    async function addManualModel() {
        const name = document.getElementById('manual-name').value;
        const modelPath = document.getElementById('manual-model-path').value;
        const apiBase = document.getElementById('manual-api-base').value;
        if (!name || !modelPath) return showToast('Model Name and Model Path are required', 'error');
        const modelConfig = {
            model_name: (window.USE_PREFIX ? 'claw-bedrock/' : '') + name,
            litellm_params: { model: modelPath }
        };
        if (apiBase) modelConfig.litellm_params.api_base = apiBase;
        try {
            const res = await fetch('/api/models', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(modelConfig)
            });
            const data = await res.json();
            if (res.ok) {
                showToast('Model added successfully');
                showReloadToast(data.reloaded);
                closeAddModel();
                loadModels();
            } else {
                showToast(`Error: ${data.detail || 'Failed to add model'}`, 'error');
            }
        } catch (e) {
            showToast(`Error: ${e.message}`, 'error');
        }
    }

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
                <input id="ollama-api-base" placeholder="Ollama API Base (e.g., http://192.168.1.1:11434)" />
                <button onclick="loadOllamaModels()">Fetch Models</button>
                <div id="ollama-models"></div>
            `;
            if (window.authData && window.authData.ollama && window.authData.ollama.host) {
                document.getElementById('ollama-api-base').value = window.authData.ollama.host;
            }
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

    let openRouterModels = [];
    async function loadOpenRouterModels() {
        const search = document.getElementById('or-search').value;
        const freeOnly = document.getElementById('or-free-only').checked;
        const params = new URLSearchParams();
        if (search) params.append('search', search);
        params.append('include_free', freeOnly ? 'true' : 'false');
        const res = await fetch(`/api/providers/openrouter/models?${params}`);
        const data = await res.json();
        openRouterModels = data.models;
        const modelsDiv = document.getElementById('openrouter-models');
        modelsDiv.innerHTML = data.models.map(m => {
            const isFree = parseFloat(m.pricing && m.pricing.prompt || '1') === 0;
                return `<div>
                    <input type="checkbox" id="or-${m.id}" />
                    <label for="or-${m.id}">${m.name} (${m.id}) ${isFree ? FREE_SVG : ''}</label>
                </div>`;
        }).join('') + `<button onclick="addSelectedOpenRouterModels()" style="margin-top: 10px;">Add Selected Models</button>`;
    }

    async function addSelectedOpenRouterModels() {
        const checkboxes = document.querySelectorAll('#openrouter-models input[type="checkbox"]:checked');
        if (checkboxes.length === 0) return showToast('No models selected', 'error');
        const promises = [];
        let lastReloaded = false;
        checkboxes.forEach(cb => {
            const modelId = cb.id.replace('or-', '');
            const modelData = openRouterModels.find(m => m.id === modelId);
            if (modelData) {
                const safeName = modelData.id.replace(/[/]/g, '-');
                const modelConfig = {
                    model_name: (window.USE_PREFIX ? 'claw-bedrock/' : '') + safeName,
                    litellm_params: { model: 'openrouter/' + modelData.id }
                };
                promises.push(
                    fetch('/api/models', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(modelConfig)
                    }).then(r => r.json()).then(d => { lastReloaded = d.reloaded; })
                );
                cb.checked = false;
            }
        });
        await Promise.all(promises);
        showToast(`Added ${checkboxes.length} model(s)`);
        showReloadToast(lastReloaded);
        loadModels();
    }

    let ollamaModels = [], ollamaApiBase = '';
    async function loadOllamaModels() {
        const apiBaseInput = document.getElementById('ollama-api-base');
        const apiBase = apiBaseInput.value || (window.authData && window.authData.ollama && window.authData.ollama.host) || '';
        if (!apiBase) return showToast('Enter Ollama API Base', 'error');
        apiBaseInput.value = apiBase;
        ollamaApiBase = apiBase;
        const res = await fetch(`/api/providers/ollama/models?api_base=${encodeURIComponent(apiBase)}`);
        if (!res.ok) {
            const err = await res.json();
            showToast(`Error: ${err.detail || 'Failed to fetch models'}`, 'error');
            return;
        }
        const data = await res.json();
        ollamaModels = data.models;
        const modelsDiv = document.getElementById('ollama-models');
        modelsDiv.innerHTML = data.models.map(m =>
            `<div>
                <input type="checkbox" id="ol-${m.name}" />
                <label for="ol-${m.name}">${m.name}</label>
            </div>`
        ).join('') + `<button onclick="addSelectedOllamaModels()" style="margin-top: 10px;">Add Selected Models</button>`;
    }

    async function addSelectedOllamaModels() {
        const checkboxes = document.querySelectorAll('#ollama-models input[type="checkbox"]:checked');
        if (checkboxes.length === 0) return showToast('No models selected', 'error');
        const promises = [];
        let lastReloaded = false;
        checkboxes.forEach(cb => {
            const modelName = cb.id.replace('ol-', '');
            const modelConfig = {
                model_name: (window.USE_PREFIX ? 'claw-bedrock/' : '') + 'ollama-' + modelName,
                litellm_params: { model: 'ollama/' + modelName, api_base: ollamaApiBase }
            };
            promises.push(
                fetch('/api/models', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(modelConfig)
                }).then(r => r.json()).then(d => { lastReloaded = d.reloaded; })
            );
            cb.checked = false;
        });
        await Promise.all(promises);
        showToast(`Added ${checkboxes.length} model(s)`);
        showReloadToast(lastReloaded);
        loadModels();
    }

    let bedrockModels = [];
    async function loadBedrockModels() {
        const modelsDiv = document.getElementById('bedrock-models');
        modelsDiv.innerHTML = '<p>Loading...</p>';
        const res = await fetch('/api/providers/bedrock/models');
        if (!res.ok) {
            const err = await res.json();
            modelsDiv.innerHTML = `<p style="color: #dc3545;">` + WARNING_SVG + ` ${err.detail}</p>`;
            return;
        }
        const data = await res.json();
        bedrockModels = data.models;
        if (bedrockModels.length === 0) {
            modelsDiv.innerHTML = '<p>No available Bedrock models (all may already be added).</p>';
            return;
        }
        modelsDiv.innerHTML = bedrockModels.map(m =>
            `<div>
                <input type="checkbox" id="br-${m.model_name}" />
                <label for="br-${m.model_name}">${m.model_name} (${m.litellm_params.model})</label>
            </div>`
        ).join('') + `<button onclick="addSelectedBedrockModels()" style="margin-top: 10px;">Add Selected Models</button>`;
    }

    async function addSelectedBedrockModels() {
        const checkboxes = document.querySelectorAll('#bedrock-models input[type="checkbox"]:checked');
        if (checkboxes.length === 0) return showToast('No models selected', 'error');
        const promises = [];
        let lastReloaded = false;
        checkboxes.forEach(cb => {
            const modelName = cb.id.replace('br-', '');
            const modelData = bedrockModels.find(m => m.model_name === modelName);
            if (modelData) {
                const modelToAdd = { ...modelData };
                if (!window.USE_PREFIX) {
                    modelToAdd.model_name = modelToAdd.model_name.replace(/^claw-bedrock\\//, '');
                }
                promises.push(
                    fetch('/api/models', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(modelToAdd)
                    }).then(r => r.json()).then(d => { lastReloaded = d.reloaded; })
                );
                cb.checked = false;
            }
        });
        await Promise.all(promises);
        showToast(`Added ${checkboxes.length} model(s)`);
        showReloadToast(lastReloaded);
        loadModels();
        loadBedrockModels();
    }

    async function loadLogs() {
        const lines = document.getElementById('log-lines').value;
        const res = await fetch(`/api/logs?lines=${lines}`);
        const data = await res.json();
        const logsDiv = document.getElementById('logs-output');
        logsDiv.textContent = data.logs;
        logsDiv.scrollTop = logsDiv.scrollHeight;
    }

     loadDashboard();
     loadAuth();
     loadModels();
     setInterval(loadAuth, 30000);

     // Initialize prefix toggle
     const prefixToggle = document.getElementById('use-prefix-toggle');
     if (prefixToggle) {
         prefixToggle.checked = window.USE_PREFIX !== false;
     }

     async function togglePrefix() {
         const toggle = document.getElementById('use-prefix-toggle');
         const usePrefix = toggle.checked;
         const res = await fetch(`/api/settings?use_prefix=${usePrefix}`, { method: 'POST' });
         if (res.ok) {
             window.USE_PREFIX = usePrefix;
             showToast(`Model prefix ${usePrefix ? 'enabled' : 'disabled'}`);
         } else {
             showToast('Failed to update setting', 'error');
             toggle.checked = !usePrefix;
         }
     }

     // Detect and update URLs based on actual access location
     (function() {
         const mgmtUrl = window.location.origin;
         const apiHost = window.location.hostname;
         const apiUrl = `${window.location.protocol}//${apiHost}:4000`;
         const apiUrlV1 = `${apiUrl}/v1`;
         document.getElementById('mgmt-url').textContent = mgmtUrl;
         document.getElementById('litellm-url').textContent = apiUrl;
         document.getElementById('models-curl').textContent = `curl ${apiUrl}/models`;
         document.getElementById('opencode-url').textContent = apiUrlV1;
         document.getElementById('clawcode-url').textContent = apiUrlV1;
     })();
     </script>
</body>
</html>
"""
    html = html.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')
    return HTMLResponse(content=html)
