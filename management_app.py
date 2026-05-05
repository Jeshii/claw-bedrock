from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
import yaml
import json
import os
import sys
import requests
import time
import subprocess
import psutil
import base64
from typing import Optional, Dict


def base64url_decode(s: str) -> str:
    """Decode a base64url-encoded string."""
    s += "=" * (4 - len(s) % 4)
    return base64.b64decode(s.replace("-", "+").replace("_", "/")).decode("utf-8")


import db
import token_refresher

app = FastAPI(title="Claw Bedrock Management")
templates = Jinja2Templates(directory="templates")

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
LOG_PATH = os.path.join(CONFIG_DIR, "litellm.log")
VERSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
BEDROCK_MODELS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bedrock_models.json"
)


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
    if "use_prefix" not in config:
        config["use_prefix"] = True
        db.set_setting("use_prefix", True)
    return config


@app.get("/api/settings")
async def get_settings():
    """Get current settings."""
    config = load_local_config()
    return {
        "use_prefix": config.get("use_prefix", True),
        "always_include_stream_usage": db.get_setting(
            "always_include_stream_usage", True
        ),
    }


@app.post("/api/settings")
async def update_settings(
    use_prefix: bool = Query(...), always_include_stream_usage: bool = Query(...)
):
    """Update settings."""
    db.set_setting("use_prefix", use_prefix)
    db.set_setting("always_include_stream_usage", always_include_stream_usage)

    # Re-merge configs with new settings
    merge_configs()

    return {
        "success": True,
        "use_prefix": use_prefix,
        "always_include_stream_usage": always_include_stream_usage,
    }


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
        "awaiting_code": token_refresher.token_refresher._awaiting_code,
        "auth_error": token_refresher.token_refresher.get_auth_error(),
        "openrouter": {"configured": openrouter_key},
        "ollama": {"configured": bool(ollama_host), "host": ollama_host},
    }


@app.post("/api/auth/submit-code")
async def submit_auth_code(body: dict):
    """Submit an authorization code to the running aws login process."""
    code = body.get("code", "")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' in request body")
    result = token_refresher.token_refresher.submit_code(code)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"success": True}


@app.post("/api/auth/retry")
async def retry_auth():
    """Reset failure state and start a new aws login --remote process."""
    token_refresher.token_refresher.retry_login()
    return {"success": True}


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
        provider = (
            m.get("litellm_params", {}).get("model", "").split("/")[0] or "unknown"
        )
        providers[provider] = providers.get(provider, 0) + 1
    return {
        "model_count": model_count,
        "providers": providers,
        "version": get_version(),
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


@app.get("/api/logs/debug")
async def get_debug_logs(lines: int = 100):
    """Return the last N lines of the TokenRefresher debug log."""
    debug_log = "/tmp/token_refresher_debug.log"
    if not os.path.exists(debug_log):
        return {"logs": "No debug logs yet. TokenRefresher may not be loaded."}
    try:
        result = subprocess.run(
            ["tail", f"-{lines}", debug_log],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {"logs": result.stdout or "Log is empty."}
    except Exception as e:
        return {"logs": f"Error reading debug logs: {str(e)}"}


@app.get("/api/debug/token-refresher")
async def get_token_refresher_state():
    """Read and return the TokenRefresher debug log to check internal state."""
    debug_log = "/tmp/token_refresher_debug.log"
    try:
        if os.path.exists(debug_log):
            with open(debug_log, "r") as f:
                content = f.read()
            return {"debug_log": content, "exists": True}
        return {"debug_log": None, "exists": False, "message": "Debug log not found"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/models")
async def list_models():
    """List all configured models."""
    return {"models": db.get_all_models()}


@app.post("/api/models/reload")
async def reload_models():
    """Manually trigger a LiteLLM restart to pick up new config."""
    result = reload_litellm()
    if result.get("success"):
        return {"status": "success", "message": result.get("message", "LiteLLM restarted"), "pid": result.get("pid")}
    return {
        "status": "warning",
        "message": f"LiteLLM restart failed: {result.get('error', 'Unknown error')}",
        "reloaded": False,
    }


@app.get("/api/providers/openrouter/models")
async def fetch_openrouter_models(
    include_free: bool = True,
    search: Optional[str] = None,
    api_key: Optional[str] = None,
):
    """Fetch available models from OpenRouter with optional filtering."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif os.environ.get("OPENROUTER_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['OPENROUTER_API_KEY']}"

    try:
        resp = requests.get(
            "https://openrouter.ai/api/v1/models", headers=headers, timeout=30
        )
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
            models = [
                m
                for m in models
                if search_lower in m.get("id", "").lower()
                or search_lower in m.get("name", "").lower()
            ]

        def sort_key(m):
            try:
                cost = float(m.get("pricing", {}).get("prompt", "inf"))
            except:
                cost = float("inf")
            return (cost, m.get("name", "").lower())

        models.sort(key=sort_key)

        # Enrich with context_length from OpenRouter response
        enriched = []
        for m in models:
            ctx = None
            if m.get("architecture"):
                ctx = m["architecture"].get("context_length")
            if not ctx and m.get("top_provider"):
                ctx = m["top_provider"].get("context_length")
            enriched.append(
                {
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "pricing": m.get("pricing"),
                    "context_length": int(ctx) if ctx else None,
                }
            )
        return {"models": enriched}
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch OpenRouter models: {str(e)}")


@app.get("/api/providers/ollama/models")
async def fetch_ollama_models(api_base: Optional[str] = Query(None)):
    """Fetch models from a remote Ollama instance."""
    api_base = api_base or os.environ.get("OLLAMA_API_BASE", "")
    if not api_base:
        raise HTTPException(
            400,
            "No Ollama API base provided. Set OLLAMA_API_BASE or provide api_base parameter.",
        )

    try:
        resp = requests.get(f"{api_base.rstrip('/')}/api/tags", timeout=10)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return {"models": sorted(models, key=lambda m: m.get("name", "").lower())}
    except requests.exceptions.ConnectionError as e:
        raise HTTPException(
            400,
            f"Cannot connect to Ollama at {api_base}. Check the address and ensure Ollama is running.",
        ) from e
    except requests.exceptions.Timeout as e:
        raise HTTPException(
            400,
            f"Connection to Ollama at {api_base} timed out. The server may be slow or unreachable.",
        ) from e
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            400, f"Error communicating with Ollama at {api_base}: {str(e)}"
        ) from e
    except Exception as e:
        raise HTTPException(400, f"Failed to fetch Ollama models: {str(e)}") from e


@app.get("/api/providers/ollama/model-details")
async def fetch_ollama_model_details(
    name: str = Query(...), api_base: Optional[str] = Query(None)
):
    """Fetch details for a specific Ollama model including context length."""
    api_base = api_base or os.environ.get("OLLAMA_API_BASE", "")
    if not api_base:
        raise HTTPException(400, "No Ollama API base provided.")
    try:
        resp = requests.post(
            f"{api_base.rstrip('/')}/api/show",
            json={"name": name},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        ctx = data.get("details", {}).get("context_length")
        return {"name": name, "context_length": ctx}
    except Exception as e:
        raise HTTPException(
            400, f"Failed to fetch Ollama model details: {str(e)}"
        ) from e


@app.get("/api/providers/bedrock/models")
async def fetch_bedrock_models():
    """Fetch available Bedrock Mantle models from static catalog."""
    try:
        with open(BEDROCK_MODELS_PATH, "r") as f:
            models = json.load(f)
        return {"models": models}
    except Exception as e:
        raise HTTPException(500, f"Error reading Bedrock models catalog: {str(e)}")


@app.delete("/api/models/{encoded_model_name:path}")
async def delete_model(encoded_model_name: str):
    """Delete a model from TinyDB."""
    try:
        model_name = base64url_decode(encoded_model_name)
    except Exception:
        raise HTTPException(400, "Invalid model name encoding")
    if not db.model_name_exists(model_name):
        raise HTTPException(404, f"Model {model_name} not found")

    db.delete_model(model_name)
    merge_configs()
    result = reload_litellm()

    return {"status": "success", "deleted": model_name, "reloaded": result.get("success"), "pid": result.get("pid")}


@app.post("/api/models")
async def add_model(model: Dict):
    """Add a new model to TinyDB."""
    db.add_model(model)
    merge_configs()
    result = reload_litellm()

    return {"status": "success", "model": model, "reloaded": result.get("success"), "pid": result.get("pid")}


@app.put("/api/models/{encoded_old_name:path}")
async def rename_model(encoded_old_name: str, update: Dict):
    """Rename a model in TinyDB."""
    try:
        old_model_name = base64url_decode(encoded_old_name)
    except Exception:
        raise HTTPException(400, "Invalid model name encoding")

    new_model_name = update.get("model_name")
    if not new_model_name:
        raise HTTPException(400, "model_name is required")

    renamed = db.rename_model(old_model_name, new_model_name)
    if not renamed:
        raise HTTPException(404, f"Model {old_model_name} not found")

    merge_configs()
    result = reload_litellm()

    return {
        "status": "success",
        "old_name": old_model_name,
        "new_name": new_model_name,
        "reloaded": result.get("success"),
        "pid": result.get("pid"),
    }


def merge_configs():
    """Merge TinyDB models and settings into config.yaml for LiteLLM."""
    merged = db.get_models_for_litellm()

    try:
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(
                merged, f, default_flow_style=False, sort_keys=False, allow_unicode=True
            )
        print(f"[Merge] Config merged. Total models: {len(merged['model_list'])}")
    except Exception as e:
        print(f"[Merge] Error writing merged config: {e}", file=sys.stderr)


def validate_config() -> tuple[bool, str]:
    """Validate the generated YAML config by parsing it. Returns (is_valid, error_message)."""
    try:
        with open(CONFIG_PATH, "r") as f:
            yaml.safe_load(f)
        return True, ""
    except Exception as e:
        return False, str(e)


def reload_litellm() -> dict:
    """Reload LiteLLM by restarting the process. Returns dict with status info."""
    pid_file = "/tmp/litellm.pid"
    config_path = os.environ.get("CONFIG_PATH", "/app/config.yaml")
    config_dir = os.environ.get("CONFIG_DIR", "/app")

    # Validate config before restarting
    is_valid, error = validate_config()
    if not is_valid:
        return {"success": False, "error": f"Invalid config: {error}"}

    # Step 1: Find and stop the existing LiteLLM process
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
            print(f"[Reload] PID {pid} is stale, will search for process...")
            pid = None

    if pid is None:
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                cmdline = proc.info["cmdline"]
                if cmdline and any("litellm" in arg.lower() for arg in cmdline):
                    pid = proc.info["pid"]
                    print(f"[Reload] Found LiteLLM process: PID {pid}")
                    break
        except Exception as e:
            print(f"[Reload] Error searching for LiteLLM process: {e}", file=sys.stderr)

    # Step 2: Stop the existing process
    if pid is not None:
        try:
            os.kill(pid, 15)  # SIGTERM
            print(f"[Reload] Sent SIGTERM to LiteLLM (PID {pid})")
            for _ in range(10):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.5)
                except OSError:
                    print("[Reload] LiteLLM process terminated")
                    break
            else:
                print("[Reload] Process did not terminate, sending SIGKILL")
                os.kill(pid, 9)
                time.sleep(1)
        except OSError as e:
            print(f"[Reload] Error stopping LiteLLM: {e}", file=sys.stderr)

    # Step 3: Start new LiteLLM process
    try:
        log_path = os.path.join(config_dir, "litellm.log")
        cmd = ["litellm", "--config", config_path, "--port", "4000", "--host", "0.0.0.0"]
        print(f"[Reload] Starting LiteLLM: {' '.join(cmd)}")
        with open(log_path, "a") as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=config_dir,
            )
        new_pid = process.pid
        with open(pid_file, "w") as f:
            f.write(str(new_pid))
        print(f"[Reload] LiteLLM started with PID {new_pid}")

        # Step 4: Verify process is running
        time.sleep(2)
        try:
            if not psutil.Process(new_pid).is_running():
                return {"success": False, "error": f"LiteLLM process died shortly after starting (PID {new_pid})"}
        except psutil.NoSuchProcess:
            return {"success": False, "error": f"LiteLLM process not found after starting (PID {new_pid})"}

        # Step 5: Health check
        for _ in range(10):
            try:
                resp = requests.get("http://localhost:4000/health", timeout=2)
                if resp.status_code < 500:
                    print(f"[Reload] LiteLLM health check passed (PID {new_pid})")
                    return {"success": True, "pid": new_pid, "message": f"LiteLLM restarted (PID {new_pid})"}
            except Exception:
                pass
            time.sleep(1)

        return {"success": True, "pid": new_pid, "warning": "LiteLLM started but health check timed out"}
    except Exception as e:
        print(f"[Reload] Error starting LiteLLM: {e}", file=sys.stderr)
        return {"success": False, "error": str(e)}


@app.get("/api/health/litellm")
async def health_litellm():
    """Proxy health check to LiteLLM."""
    try:
        resp = requests.get("http://localhost:4000/health", timeout=5)
        return {"status": "ok", "litellm_status": resp.status_code}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/")
async def dashboard(request: Request):
    """Serve the management dashboard."""
    version = get_version()
    config = load_local_config()
    use_prefix = config.get("use_prefix", True)
    return templates.TemplateResponse(
        request,
        "management.html",
        context={"version": version, "use_prefix": use_prefix},
    )
