from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import yaml
import json
import os
import sys
import requests
import subprocess
import psutil
from typing import Optional, Dict

import db

app = FastAPI(title="Claw Bedrock Management")
templates = Jinja2Templates(directory="templates")

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
LOG_PATH = os.path.join(CONFIG_DIR, "litellm.log")
VERSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
BEDROCK_MODELS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bedrock_models.json")


def get_version():
    """Get version baked in at build time."""
    try:
        with open(VERSION_PATH, "r") as f:
            return f.read().strip()
    except:
        return "unknown"


@app.on_event("startup")
async def startup_event():
    db._migrate_yaml_to_db()
    merge_configs()
    print(f"[Startup] Merged configs on startup (CONFIG_DIR={CONFIG_DIR})")


@app.on_event("shutdown")
async def shutdown_event():
    db.close_db()
    print("[Shutdown] Database closed")


def load_config() -> Dict:
    """Load merged config for LiteLLM (reads from generated config.yaml)."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {"model_list": []}
    return {"model_list": []}


def save_local_config(config: Dict):
    """Legacy function - no longer writes YAML, settings saved via db module."""
    pass


def load_local_config() -> Dict:
    """Load local config from TinyDB, creating with defaults if needed."""
    config = db.get_settings()
    # Set defaults
    if 'use_prefix' not in config:
        config['use_prefix'] = True
        db.set_setting('use_prefix', True)
    return config


@app.get("/api/settings")
async def get_settings():
    """Get current settings."""
    config = load_local_config()
    router_settings = db.get_router_settings()
    return {
        "use_prefix": config.get('use_prefix', True),
        "always_include_stream_usage": router_settings.get("always_include_stream_usage", True)
    }


@app.post("/api/settings")
async def update_settings(
    use_prefix: bool = Query(...),
    always_include_stream_usage: bool = Query(...)
):
    """Update settings."""
    db.set_setting('use_prefix', use_prefix)

    # Update router_settings
    router_settings = db.get_router_settings()
    router_settings["always_include_stream_usage"] = always_include_stream_usage
    db.set_router_settings(router_settings)

    # Re-merge configs with new settings
    merge_configs()

    return {
        "success": True,
        "use_prefix": use_prefix,
        "always_include_stream_usage": always_include_stream_usage
    }


@app.get("/api/auth/status")
async def auth_status():
    """Check if AWS auth is needed and get auth URL + verification code."""
    auth_needed = os.path.exists("/tmp/auth_needed")
    auth_url = None
    auth_code = None

    if os.path.exists("/tmp/auth_url"):
        with open("/tmp/auth_url", "r") as f:
            auth_url = f.read().strip()

    if os.path.exists("/tmp/auth_code"):
        with open("/tmp/auth_code", "r") as f:
            auth_code = f.read().strip()

    openrouter_key = bool(os.environ.get("OPENROUTER_API_KEY"))
    ollama_host = os.environ.get("OLLAMA_API_BASE", "")

    return {
        "auth_needed": auth_needed,
        "auth_url": auth_url,
        "auth_code": auth_code,
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
    models = db.get_all_models()
    model_count = len(models)
    providers = {}
    for m in models:
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
    return {"models": db.get_all_models()}


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
    """Fetch available Bedrock Mantle models from static catalog."""
    try:
        with open(BEDROCK_MODELS_PATH, "r") as f:
            models = json.load(f)
        return {"models": models}
    except Exception as e:
        raise HTTPException(500, f"Error reading Bedrock models catalog: {str(e)}")


@app.delete("/api/models/{model_name}")
async def delete_model(model_name: str):
    """Delete a model from TinyDB."""
    if not db.model_name_exists(model_name):
        raise HTTPException(404, f"Model {model_name} not found")

    db.delete_model(model_name)
    merge_configs()
    reloaded = reload_litellm()

    return {"status": "success", "deleted": model_name, "reloaded": reloaded}


@app.post("/api/models")
async def add_model(model: Dict):
    """Add a new model to TinyDB."""
    db.add_model(model)
    merge_configs()
    reloaded = reload_litellm()

    return {"status": "success", "model": model, "reloaded": reloaded}


@app.put("/api/models/{old_model_name}")
async def rename_model(old_model_name: str, update: Dict):
    """Rename a model in TinyDB."""
    new_model_name = update.get("model_name")
    if not new_model_name:
        raise HTTPException(400, "model_name is required")

    renamed = db.rename_model(old_model_name, new_model_name)
    if not renamed:
        raise HTTPException(404, f"Model {old_model_name} not found")

    merge_configs()
    reloaded = reload_litellm()

    return {"status": "success", "old_name": old_model_name, "new_name": new_model_name, "reloaded": reloaded}


def merge_configs():
    """Merge TinyDB models and settings into config.yaml for LiteLLM."""
    merged = db.get_models_for_litellm()

    try:
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(merged, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print(f"[Merge] Config merged. Total models: {len(merged['model_list'])}")
    except Exception as e:
        print(f"[Merge] Error writing merged config: {e}", file=sys.stderr)


def reload_litellm() -> bool:
    """Reload LiteLLM config by sending SIGHUP to the process. Returns True on success."""
    pid_file = "/tmp/litellm.pid"

    pid = None
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
        except (ValueError, IOError) as e:
            print(f"[Reload] Error reading PID file: {e}", file=sys.stderr)
            pid = None

    if pid is not None:
        try:
            os.kill(pid, 0)
        except OSError:
            print(f"[Reload] PID {pid} is stale, searching for LiteLLM process...")
            pid = None

    if pid is None:
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                cmdline = proc.info['cmdline']
                if cmdline and any('litellm' in arg.lower() for arg in cmdline):
                    pid = proc.info['pid']
                    print(f"[Reload] Found LiteLLM process: PID {pid}")
                    with open(pid_file, "w") as f:
                        f.write(str(pid))
                    break
        except Exception as e:
            print(f"[Reload] Error searching for LiteLLM process: {e}", file=sys.stderr)

    if pid is None:
        print("[Reload] No LiteLLM process found, skipping reload")
        return False

    try:
        os.kill(pid, 1)  # SIGHUP
        print(f"[Reload] Sent SIGHUP to LiteLLM (PID {pid})")
        return True
    except OSError as e:
        if e.errno == 3:
            print(f"[Reload] Process {pid} no longer exists", file=sys.stderr)
        else:
            print(f"[Reload] Error reloading LiteLLM: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[Reload] Error reloading LiteLLM: {e}", file=sys.stderr)
        return False


@app.get("/")
async def dashboard(request: Request):
    """Serve the management dashboard."""
    version = get_version()
    config = load_local_config()
    use_prefix = config.get('use_prefix', True)
    return templates.TemplateResponse(
        request,
        "management.html",
        context={"version": version, "use_prefix": use_prefix}
    )
