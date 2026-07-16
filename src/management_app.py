from collections import defaultdict
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
import yaml
import json
import os
import sys
import requests
import time
import subprocess
import psutil
import base64
import threading
import datetime
import shutil
from typing import Optional, Dict
from tinydb import where
import db
import token_refresher
import password_utils
import encryption_utils
import hmac


def base64url_decode(s: str) -> str:
    """Decode a base64url-encoded string."""
    s += "=" * (4 - len(s) % 4)
    return base64.b64decode(s.replace("-", "+").replace("_", "/")).decode("utf-8")


app = FastAPI(title="Claw Bedrock Management")
templates = Jinja2Templates(directory="templates")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount(
    "/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static"
)

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
LOG_PATH = os.path.join(CONFIG_DIR, "litellm.log")
VERSION_PATH = os.path.join(BASE_DIR, "VERSION")
BEDROCK_MODELS_PATH = os.path.join(BASE_DIR, "bedrock_models.json")

LITELLM_BASE_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")


def _reload_litellm_config() -> bool:
    """Push updated config to LiteLLM without restart. Returns True on success."""
    config = db.get_models_for_litellm()
    try:
        key = db.get_master_key()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        resp = requests.post(
            f"{LITELLM_BASE_URL}/config/update",
            json=config,
            headers=headers,
            timeout=5,
        )
        success = resp.status_code < 500
        if not success:
            print(
                f"[reload] Config reload returned HTTP {resp.status_code}",
                file=sys.stderr,
            )
        return success
    except Exception as e:
        print(f"[reload] Config reload failed: {e}", file=sys.stderr)
        return False


def get_version():
    """Get version baked in at build time."""
    try:
        with open(VERSION_PATH, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "unknown"


# Authentication
MANAGEMENT_PASSWORD = os.environ.get("MANAGEMENT_UI_PASSWORD")
AUTH_COOKIE = "management_auth"
AUTH_TOKEN = (
    password_utils.hash_password(MANAGEMENT_PASSWORD) if MANAGEMENT_PASSWORD else None
)

_login_attempts: dict[str, list[float]] = defaultdict(list)
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW = 60


def is_auth_required():
    """Return True if password protection is enabled."""
    return MANAGEMENT_PASSWORD is not None


def verify_auth(request: Request) -> bool:
    """Verify authentication from cookie using constant-time comparison."""
    if not is_auth_required():
        return True
    token = request.cookies.get(AUTH_COOKIE)
    if token is None or AUTH_TOKEN is None:
        return False
    return hmac.compare_digest(token, AUTH_TOKEN)


@app.post("/api/login")
async def login(request: Request, body: Dict):
    """Login with password. Returns success or error."""
    if not is_auth_required():
        return {"success": True, "message": "Auth not required"}
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = _login_attempts[client_ip]
    attempts[:] = [t for t in attempts if now - t < LOGIN_RATE_WINDOW]
    if len(attempts) >= LOGIN_RATE_LIMIT:
        raise HTTPException(429, "Too many login attempts. Try again later.")
    attempts.append(now)
    password = body.get("password", "")
    if password_utils.verify_password(password, AUTH_TOKEN):
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key=AUTH_COOKIE,
            value=AUTH_TOKEN,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=86400,
            path="/",
        )
        return response
    raise HTTPException(401, "Invalid password")


@app.post("/api/logout")
async def logout():
    """Logout by clearing the auth cookie."""
    response = {"success": True}
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=AUTH_COOKIE, path="/")
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Middleware to check authentication for all routes."""
    # Skip auth for login page, login API, and static assets
    path = request.url.path
    if (
        not is_auth_required()
        or path in ["/login", "/api/login"]
        or path.startswith("/static")
    ):
        return await call_next(request)
    if not verify_auth(request):
        if path.startswith("/api/"):
            raise HTTPException(401, "Unauthorized")
        return RedirectResponse(url="/login", status_code=302)
    return await call_next(request)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.get("/login")
async def login_page(request: Request):
    """Serve the login page."""
    if not is_auth_required():
        return RedirectResponse(url="/", status_code=302)
    with open(os.path.join(BASE_DIR, "templates", "login.html"), "r") as f:
        return HTMLResponse(content=f.read())


@app.on_event("startup")
async def startup_event():
    db._migrate_yaml_to_db()
    db.seed_default_providers()
    merge_configs()
    print(f"[Startup] Merged configs on startup (CONFIG_DIR={CONFIG_DIR})")
    # Start watchdog in background thread
    t = threading.Thread(target=litellm_watchdog, daemon=True)
    t.start()
    print("[Startup] LiteLLM watchdog started")


def litellm_watchdog():
    """Monitor LiteLLM process and restart if it crashes."""
    pid_file = "/tmp/litellm.pid"
    while True:
        try:
            if os.path.exists(pid_file):
                pid = int(open(pid_file).read().strip())
                if not psutil.pid_exists(pid):
                    print(f"[Watchdog] LiteLLM (PID {pid}) crashed, restarting...")
                    reload_litellm()
        except Exception as e:
            print(f"[Watchdog] Error: {e}", file=sys.stderr)
        time.sleep(10)


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
    }


@app.post("/api/settings")
async def update_settings(
    use_prefix: bool = Query(...),
):
    """Update settings."""
    db.set_setting("use_prefix", use_prefix)

    # Re-merge configs with new settings
    merge_configs()

    return {
        "success": True,
        "use_prefix": use_prefix,
    }


@app.get("/api/settings/router")
async def get_router_settings_route():
    """Get current router settings for model groups."""
    return db.get_router_settings()


@app.post("/api/settings/router")
async def set_router_settings_route(body: Dict):
    """Update router settings for model groups."""
    allowed = {"routing_strategy", "allowed_fails", "num_retries"}
    filtered = {k: v for k, v in body.items() if k in allowed}
    db.set_router_settings(filtered)
    merge_configs()
    return {"success": True}


@app.get("/api/auth/status")
async def auth_status():
    """Check if AWS auth is needed and get auth URL."""
    auth_needed = os.path.exists("/tmp/auth_needed")
    auth_url = None

    if os.path.exists("/tmp/auth_url"):
        with open("/tmp/auth_url", "r") as f:
            auth_url = f.read().strip()

    openrouter_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    return {
        "auth_needed": auth_needed,
        "auth_url": auth_url,
        "awaiting_code": token_refresher.token_refresher._awaiting_code,
        "auth_error": token_refresher.token_refresher.get_auth_error(),
        "openrouter": {"configured": openrouter_key},
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


@app.get("/api/security/encryption-status")
async def encryption_status():
    """Return encryption configuration status."""
    configured, mode = encryption_utils.get_encryption_mode()
    return {"configured": configured, "using": mode}


@app.get("/api/security/key")
async def get_key_status():
    """Get current master key status (masked)."""
    key = db.get_master_key()
    if key:
        masked = key[:12] + "..." + key[-4:]
        return {"enabled": True, "masked_key": masked}
    return {"enabled": False}


@app.post("/api/security/key/generate")
async def generate_key():
    """Generate a new master key and reload LiteLLM config."""
    key = db.generate_master_key()
    _reload_litellm_config()
    return {"success": True, "key": key}


@app.delete("/api/security/key")
async def revoke_key():
    """Revoke the master key (disables auth on next reload)."""
    db.clear_master_key()
    _reload_litellm_config()
    return {"success": True}


@app.get("/api/version")
async def version_endpoint():
    """Return the current version of claw-bedrock."""
    return {"version": get_version()}


@app.get("/api/chat/models")
async def chat_models():
    """Return available model names for the playground selector.

    Uses TinyDB directly (same naming logic as get_models_for_litellm)
    so the playground can list models even before LiteLLM has been reloaded.
    """
    use_prefix = db.get_setting("use_prefix", True)
    names = []
    for m in db.get_all_models():
        group = m.get("model_group")
        if group:
            if use_prefix and not group.startswith("claw-bedrock/"):
                names.append(f"claw-bedrock/{group}")
            else:
                names.append(group)
        else:
            raw = m.get("model_name", "")
            if use_prefix and not raw.startswith("claw-bedrock/"):
                names.append(f"claw-bedrock/{raw}")
            else:
                names.append(raw)
    return {"models": sorted(set(names))}


@app.post("/api/chat/completions")
async def chat_completion(body: Dict):
    """Proxy to LiteLLM /v1/chat/completions with SSE streaming."""
    body["stream"] = True
    key = db.get_master_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        resp = requests.post(
            f"{LITELLM_BASE_URL}/v1/chat/completions",
            json=body,
            headers=headers,
            stream=True,
            timeout=(10, 300),
        )
        resp.raise_for_status()
        return StreamingResponse(
            resp.iter_lines(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )
    except requests.exceptions.HTTPError as e:
        detail = "Unknown error"
        try:
            detail = e.response.json().get("error", {}).get("message", str(e))
        except Exception:
            detail = str(e)
        raise HTTPException(e.response.status_code, detail)
    except requests.exceptions.ConnectionError:
        raise HTTPException(502, "Cannot connect to LiteLLM")
    except requests.exceptions.Timeout:
        raise HTTPException(504, "LiteLLM request timed out")


@app.get("/api/dashboard")
async def get_dashboard():
    """Return dashboard statistics."""
    models = db.get_all_models()
    model_count = len(models)
    provider_counts: dict[str, int] = {}
    for m in models:
        p = m.get("provider") or "unassigned"
        provider_counts[p] = provider_counts.get(p, 0) + 1
    return {
        "model_count": model_count,
        "providers": provider_counts,
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
async def get_debug_logs(lines: int = 50):
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


@app.get("/api/logs/container")
async def get_container_logs(lines: int = 50):
    """Return the last N lines of the container stdout/stderr log."""
    container_log = os.path.join(CONFIG_DIR, "container.log")
    if not os.path.exists(container_log):
        return {"logs": "No container logs available yet."}
    try:
        result = subprocess.run(
            ["tail", f"-{lines}", container_log],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {"logs": result.stdout or "Log is empty."}
    except Exception as e:
        return {"logs": f"Error reading container logs: {str(e)}"}


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
async def list_models(tag: Optional[str] = Query(None)):
    """List all configured models, optionally filtered by tag."""
    if tag:
        models = db.get_models_by_tag(tag)
    else:
        models = db.get_all_models()
    return {"models": [enrich_model_with_provider(dict(m)) for m in models]}


@app.post("/api/models/reload")
async def reload_models():
    """Manually trigger a LiteLLM restart to pick up new config."""
    result = reload_litellm()
    if result.get("success"):
        return {
            "status": "success",
            "message": result.get("message", "LiteLLM restarted"),
            "pid": result.get("pid"),
        }
    return {
        "status": "warning",
        "message": f"LiteLLM restart failed: {result.get('error', 'Unknown error')}",
        "reloaded": False,
    }


@app.get("/api/providers/openrouter/models")
async def fetch_openrouter_models(
    include_free: Optional[str] = None,
    search: Optional[str] = None,
    api_key: Optional[str] = None,
):
    """Fetch available models from OpenRouter with optional filtering.

    include_free: None (all), 'true' (free only), 'false' (non-free only)
    """
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

        if include_free == "true":

            def _is_free(m):
                try:
                    return float(m.get("pricing", {}).get("prompt", "1")) == 0
                except (ValueError, TypeError):
                    return False

            models = [m for m in models if _is_free(m)]
        elif include_free == "false":

            def _is_not_free(m):
                try:
                    return float(m.get("pricing", {}).get("prompt", "1")) != 0
                except (ValueError, TypeError):
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
            except (ValueError, TypeError):
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


@app.get("/api/providers/bedrock/models")
async def fetch_bedrock_models():
    """Fetch available Bedrock Mantle models from static catalog."""
    try:
        with open(BEDROCK_MODELS_PATH, "r") as f:
            models = json.load(f)
        return {"models": models}
    except Exception as e:
        raise HTTPException(500, f"Error reading Bedrock models catalog: {str(e)}")


@app.get("/api/providers/{name}/models")
async def fetch_provider_models(name: str):
    """Fetch available models from any OpenAI-compatible provider."""
    provider = db.get_provider(name)
    if not provider:
        raise HTTPException(404, f"Provider '{name}' not found")
    api_base = provider.get("api_base")
    if not api_base:
        raise HTTPException(
            400,
            f"Provider '{name}' has no api_base configured. Set it in the Providers page first.",
        )

    api_key = provider.get("api_key") or os.environ.get(f"{name.upper()}_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        resp = requests.get(
            f"{api_base.rstrip('/')}/v1/models",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        raw = data.get("data", data) if isinstance(data, dict) else data
        models = []
        for m in raw:
            if not isinstance(m, dict):
                continue
            model_id = m.get("id", m.get("name", ""))
            if not model_id:
                continue
            ctx = None
            if m.get("context_length"):
                ctx = m["context_length"]
            elif m.get("context_window"):
                ctx = m["context_window"]
            models.append(
                {
                    "id": model_id,
                    "name": m.get("name", model_id),
                    "context_length": int(ctx) if ctx else None,
                }
            )

        return {"models": sorted(models, key=lambda x: x["id"].lower())}
    except requests.exceptions.ConnectionError as e:
        raise HTTPException(
            400,
            f"Cannot connect to {api_base}. Check the address and ensure the server is running.",
        ) from e
    except requests.exceptions.Timeout as e:
        raise HTTPException(
            400,
            f"Connection to {api_base} timed out. The server may be slow or unreachable.",
        ) from e
    except requests.exceptions.HTTPError as e:
        raise HTTPException(
            400,
            f"Error from {api_base}: {e.response.status_code} {e.response.reason}",
        ) from e
    except Exception as e:
        raise HTTPException(
            500, f"Failed to fetch models from '{name}': {str(e)}"
        ) from e


@app.get("/api/providers/bedrock/mantle-models")
async def fetch_bedrock_mantle_models(
    token: Optional[str] = Query(None),
    region: str = Query("ap-northeast-1"),
):
    """Fetch available text models from Bedrock Mantle API (intersected with Bedrock foundation models)."""
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError

    api_key = token or os.environ.get("BEDROCK_MANTLE_API_KEY")
    if not api_key:
        raise HTTPException(
            400,
            "No Bedrock Mantle API key provided. Set BEDROCK_MANTLE_API_KEY or provide token parameter.",
        )

    # Step 1: Get text-only model IDs from Bedrock foundation models
    text_model_ids = set()
    try:
        bedrock_client = boto3.client("bedrock", region_name=region)
        try:
            paginator = bedrock_client.get_paginator("list_foundation_models")
            for page in paginator.paginate(
                byInputModality="TEXT",
                PaginationConfig={"MaxItems": 1000},
            ):
                for model in page.get("modelSummaries", []):
                    model_id = model.get("modelId", "")
                    if model_id:
                        text_model_ids.add(model_id)
        except ValueError:
            response = bedrock_client.list_foundation_models(byInputModality="TEXT")
            for model in response.get("modelSummaries", []):
                model_id = model.get("modelId", "")
                if model_id:
                    text_model_ids.add(model_id)
    except NoCredentialsError as e:
        raise HTTPException(
            400,
            "AWS credentials not configured. Run 'aws configure' or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY.",
        ) from e
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        raise HTTPException(
            400,
            f"AWS API error ({error_code}): {error_msg}. Check region and permissions for bedrock:ListFoundationModels.",
        ) from e
    except Exception as e:
        raise HTTPException(
            400,
            f"Failed to connect to Bedrock in region {region}: {str(e)}. Check network and region name.",
        ) from e

    if not text_model_ids:
        raise HTTPException(
            400,
            "No TEXT modality models found in Bedrock foundation models. Check AWS credentials and region.",
        )

    # Load static catalog to check which models are "new"
    catalog_model_ids = set()
    try:
        with open(BEDROCK_MODELS_PATH, "r") as f:
            catalog = json.load(f)
        catalog_model_ids = {m.get("model", "") for m in catalog if m.get("model")}
    except Exception:
        pass  # If catalog can't be loaded, all models will be "new"

    # Step 2: Fetch models from Mantle API
    mantle_url = f"https://bedrock-mantle.{region}.api.aws/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        resp = requests.get(mantle_url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # Transform and filter to only text models
        models = []
        mantle_models = data.get("data", data) if isinstance(data, dict) else data
        for model in mantle_models:
            if not isinstance(model, dict):
                continue
            model_id = model.get("id", model.get("modelId", ""))
            # Only include if in text_model_ids
            if model_id in text_model_ids:
                # Determine max_tokens based on model name
                model_lower = model_id.lower()
                if any(
                    word in model_lower for word in ["thinking", "reasoning", "coder"]
                ):
                    max_tokens = 16384
                else:
                    max_tokens = 8192

                models.append(
                    {
                        "id": model_id,
                        "model": model_id,
                        "context_length": model.get(
                            "context_length", model.get("inputTokenLimit")
                        ),
                        "max_tokens": max_tokens,
                        "in_catalog": model_id in catalog_model_ids,
                    }
                )

        return {
            "models": sorted(models, key=lambda x: x["model"]),
            "region": region,
            "total_mantle": len(mantle_models)
            if isinstance(mantle_models, list)
            else 0,
            "text_models": len(text_model_ids),
            "filtered": len(models),
        }
    except requests.exceptions.Timeout as e:
        raise HTTPException(
            400, f"Request to Bedrock Mantle API timed out: {str(e)}"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise HTTPException(
            400, f"Cannot connect to Bedrock Mantle API: {str(e)}"
        ) from e
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            400, f"Error fetching from Bedrock Mantle API: {str(e)}"
        ) from e
    except Exception as e:
        raise HTTPException(
            500, f"Failed to fetch Bedrock Mantle models: {str(e)}"
        ) from e


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
    _reload_litellm_config()

    return {
        "status": "success",
        "deleted": model_name,
    }


@app.post("/api/models")
async def add_model(model: Dict):
    """Add a new model to TinyDB."""
    db.add_model(model)
    merge_configs()
    _reload_litellm_config()

    return {
        "status": "success",
        "model": model,
    }


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
    _reload_litellm_config()

    return {
        "status": "success",
        "old_name": old_model_name,
        "new_name": new_model_name,
    }


@app.patch("/api/models/{encoded_name:path}")
async def update_model(encoded_name: str, update: Dict):
    """Update fields on a model (e.g., reasoning_effort, litellm_params.thinking)."""
    try:
        model_name = base64url_decode(encoded_name)
    except Exception:
        raise HTTPException(400, "Invalid model name encoding")

    allowed_fields = {"reasoning_effort", "tags", "model_group", "litellm_params"}
    updates = {k: v for k, v in update.items() if k in allowed_fields}
    if not updates:
        raise HTTPException(400, "No valid fields to update")

    # Deep-merge litellm_params to avoid replacing unrelated keys
    if "litellm_params" in updates:
        model = db.get_model_by_name(model_name)
        if model:
            existing_lp = dict(model.get("litellm_params", {}))
            new_lp = updates.pop("litellm_params")
            for key, value in new_lp.items():
                if value is None:
                    existing_lp.pop(key, None)
                else:
                    existing_lp[key] = value
            updates["litellm_params"] = existing_lp

    updated = db.update_model_field(model_name, updates)
    if not updated:
        raise HTTPException(404, f"Model {model_name} not found")

    merge_configs()
    _reload_litellm_config()
    return {"status": "success", "updated": updates}


TAG_PALETTE = [
    "#4CAF50",
    "#2196F3",
    "#FF9800",
    "#9C27B0",
    "#F44336",
    "#00BCD4",
    "#8BC34A",
    "#795548",
    "#607D8B",
    "#E91E63",
    "#3F51B5",
    "#009688",
]


@app.get("/api/tags")
async def list_tags():
    """List all tag definitions."""
    return {"tags": db.get_all_tags()}


@app.post("/api/tags")
async def create_tag(body: Dict):
    """Create a new tag. Color auto-assigned from palette if not provided."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Tag name is required")
    color = body.get("color")
    if not color:
        import random

        color = random.choice(TAG_PALETTE)
    db.upsert_tag(name, color)
    return {"name": name, "color": color}


@app.put("/api/tags/{tag_name:path}")
async def rename_tag(tag_name: str, body: Dict):
    """Rename a tag."""
    new_name = (body.get("name") or "").strip()
    if not new_name:
        raise HTTPException(400, "New tag name is required")
    ok = db.rename_tag(tag_name, new_name)
    if not ok:
        raise HTTPException(404, f"Tag '{tag_name}' not found")
    return {"old_name": tag_name, "new_name": new_name}


@app.delete("/api/tags/{tag_name:path}")
async def delete_tag(tag_name: str):
    """Delete a tag and remove it from all models."""
    db.delete_tag(tag_name)
    return {"deleted": tag_name}


@app.patch("/api/tags/{tag_name:path}")
async def update_tag_color(tag_name: str, body: Dict):
    """Update a tag's color."""
    color = body.get("color")
    if not color:
        raise HTTPException(400, "Color is required")
    tag = db.get_tag(tag_name)
    if not tag:
        raise HTTPException(404, f"Tag '{tag_name}' not found")
    db.upsert_tag(tag_name, color)
    return {"name": tag_name, "color": color}


@app.post("/api/models/{encoded_name:path}/tags")
async def add_model_tag(encoded_name: str, body: Dict):
    """Add a tag to a model. Creates the tag if it doesn't exist."""
    try:
        model_name = base64url_decode(encoded_name)
    except Exception:
        raise HTTPException(400, "Invalid model name encoding")
    tag_name = (body.get("tag_name") or "").strip()
    if not tag_name:
        raise HTTPException(400, "tag_name is required")
    if not db.get_tag(tag_name):
        import random

        db.upsert_tag(tag_name, random.choice(TAG_PALETTE))
    ok = db.add_tag_to_model(model_name, tag_name)
    if not ok:
        raise HTTPException(404, f"Model '{model_name}' not found")
    return {"model": model_name, "tag": tag_name}


@app.delete("/api/models/{encoded_name:path}/tags/{tag_name:path}")
async def remove_model_tag(encoded_name: str, tag_name: str):
    """Remove a tag from a model."""
    try:
        model_name = base64url_decode(encoded_name)
    except Exception:
        raise HTTPException(400, "Invalid model name encoding")
    ok = db.remove_tag_from_model(model_name, tag_name)
    if not ok:
        raise HTTPException(404, f"Model '{model_name}' not found")
    return {"model": model_name, "tag": tag_name}


# ── Providers ──────────────────────────────────────────────────────────────


@app.get("/api/providers")
async def list_providers():
    """List all provider definitions (sanitized)."""
    providers = db.get_all_providers()
    return {"providers": [db.sanitize_provider_for_response(p) for p in providers]}


@app.post("/api/providers")
async def create_provider(body: Dict):
    """Create a new provider."""
    if not body.get("name"):
        raise HTTPException(400, "name is required")
    if db.providers_table.contains(where("name") == body["name"]):
        raise HTTPException(409, f"Provider '{body['name']}' already exists")
    try:
        db.upsert_provider(body)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    created = db.get_provider(body["name"])
    print(f"[Provider] Created name={body['name']} type={body.get('type')}")
    return {"provider": db.sanitize_provider_for_response(created)}


@app.get("/api/providers/{name}")
async def get_provider_detail(name: str):
    """Get a single provider (sanitized) and its models."""
    provider = db.get_provider(name)
    if not provider:
        raise HTTPException(404, "Not found")
    models = db.get_models_by_provider(name)
    return {"provider": db.sanitize_provider_for_response(provider), "models": models}


ALLOWED_PROVIDER_FIELDS = frozenset({
    "name", "display_name", "type", "color", "notes",
    "api_base", "aws_region",
    "api_key", "clear_api_key",
    "aws_access_key_env", "aws_secret_key_env",
})

VALID_PROVIDER_TYPES = frozenset({"bedrock", "openai-compatible", "custom"})


@app.put("/api/providers/{name}")
async def update_provider(name: str, body: Dict):
    """Update a provider with explicit field semantics.

    Flow:
      1. Validate and normalize submitted fields.
      2. Detect runtime changes BEFORE persisting.
      3. Persist to TinyDB.
      4. If runtime-relevant fields changed, regenerate config and reload LiteLLM.
      5. Return the sanitized persisted DTO (freshly read from DB).
      6. On merge/reload failure — return structured 503, NO DB rollback.

    Field semantics:
      - Non-sensitive fields (display_name, type, color, notes, api_base, aws_region):
        updated when present in body.
      - api_key:
          omitted        → retains existing encrypted blob
          present, empty → treated as no-change (keep existing)
          non-empty      → encrypts and replaces
          clear_api_key  → removes the key field entirely
      - aws_access_key_env, aws_secret_key_env:
          present        → encrypted and stored
          omitted        → retains existing encrypted blob
    """
    existing_raw = db._get_provider_raw(name)
    if not existing_raw:
        raise HTTPException(404, "Provider not found")

    unknown = [k for k in body if k not in ALLOWED_PROVIDER_FIELDS]
    if unknown:
        raise HTTPException(400, f"Unknown field(s): {', '.join(unknown)}")

    if body.get("name") and body["name"] != name:
        raise HTTPException(
            400, "Renaming via PUT is not supported; use the rename endpoint"
        )

    if "type" in body and body["type"] not in VALID_PROVIDER_TYPES:
        raise HTTPException(400, f"Invalid type '{body['type']}'; must be one of: {', '.join(sorted(VALID_PROVIDER_TYPES))}")

    merged = dict(existing_raw)

    for field in ("display_name", "type", "color", "notes", "api_base", "aws_region"):
        if field in body:
            merged[field] = (body[field] or "").strip() if isinstance(body[field], str) else body[field]

    if "api_key" not in body:
        pass
    elif body.get("clear_api_key"):
        merged.pop("api_key", None)
    else:
        val = body["api_key"]
        if val:
            merged["api_key"] = encryption_utils.encrypt_data(val)

    for field in ("aws_access_key_env", "aws_secret_key_env"):
        if field in body:
            val = body[field]
            if val:
                merged[field] = encryption_utils.encrypt_data(val)
            else:
                merged.pop(field, None)

    merged["name"] = name

    runtime_changed = _detect_provider_runtime_change(existing_raw, merged)

    db._upsert_provider_raw(merged)
    print(f"[Provider] Updated name={name} runtime_changed={runtime_changed}")

    if runtime_changed:
        success, detail = merge_configs_atomic()
        if not success:
            print(
                f"[Provider] RECONCILIATION WARNING: provider '{name}' persisted but "
                f"config generation FAILED at merge stage: {detail}",
                file=sys.stderr,
            )
            raise HTTPException(
                503,
                detail={
                    "saved": True,
                    "applied": False,
                    "stage": "merge",
                    "message": f"Provider saved but config generation failed: {detail}",
                },
            )

        reload_result = _reload_litellm_config()
        if not reload_result:
            print(
                f"[Provider] RECONCILIATION WARNING: provider '{name}' persisted but "
                f"LiteLLM reload FAILED at reload stage",
                file=sys.stderr,
            )
            raise HTTPException(
                503,
                detail={
                    "saved": True,
                    "applied": False,
                    "stage": "reload",
                    "message": "Provider saved but LiteLLM was not reloaded. "
                    "The new config has been written but is not yet active.",
                },
            )

    persisted = db.get_provider(name)
    return {
        "provider": db.sanitize_provider_for_response(persisted),
        "runtime_changed": runtime_changed,
    }


@app.delete("/api/providers/{name}")
async def delete_provider_route(name: str):
    """Delete a provider definition."""
    db.delete_provider(name)
    return {"success": True}


@app.post("/api/providers/{old_name}/rename")
async def rename_provider_route(old_name: str, body: Dict):
    """Rename a provider and update all model references."""
    new_name = body.get("new_name")
    if not new_name:
        raise HTTPException(400, "new_name is required")
    success = db.rename_provider(old_name, new_name)
    return {"success": success}


@app.get("/api/backup/export")
async def export_backup():
    """Download current config as a JSON backup file."""
    data = db.export_backup()
    data["claw_version"] = get_version()
    filename = f"claw-bedrock-backup-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    response = JSONResponse(content=data)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@app.post("/api/backup/import")
async def import_backup(request: Request):
    """
    Import a backup file.
    Query param `mode`: "replace" (default) or "merge"
    """
    mode = request.query_params.get("mode", "replace")
    if mode not in ("replace", "merge"):
        raise HTTPException(400, "mode must be 'replace' or 'merge'")
    try:
        backup = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")
    try:
        summary = db.import_backup(backup, mode=mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    merge_configs()
    reload_result = reload_litellm()
    return {
        "success": True,
        "mode": mode,
        "imported": summary,
        "litellm_reloaded": reload_result.get("success"),
    }


@app.post("/api/backup/preview")
async def preview_backup(request: Request):
    """
    Parse an uploaded backup and return a summary without applying it.
    Used by the UI to show a confirmation dialog before import.
    """
    try:
        backup = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")
    try:
        db._validate_backup(backup)
    except ValueError as e:
        raise HTTPException(400, str(e))
    data = backup["data"]
    return {
        "valid": True,
        "schema_version": backup.get("schema_version"),
        "created_at": backup.get("created_at"),
        "claw_version": backup.get("claw_version", "unknown"),
        "counts": {
            "models": len(data.get("models", [])),
            "tags": len(data.get("tags", [])),
            "settings": len(data.get("settings", {})),
            "providers": len(data.get("providers", [])),
        },
    }


def enrich_model_with_provider(model: dict) -> dict:
    """Attach sanitized provider display info to a model for UI use."""
    provider_name = model.get("provider")
    if provider_name:
        provider = db.get_provider(provider_name)
        if provider:
            model["_provider"] = db.sanitize_provider_for_response(provider)
    return model


def _detect_provider_runtime_change(before_raw: dict, after_raw: dict) -> bool:
    """Return True if any runtime-relevant field differs between before and after."""
    runtime_fields = {
        "type",
        "api_base",
        "aws_region",
        "api_key",
        "aws_access_key_env",
        "aws_secret_key_env",
    }
    before_norm = {k: v for k, v in before_raw.items() if k in runtime_fields}
    after_norm = {k: v for k, v in after_raw.items() if k in runtime_fields}
    return before_norm != after_norm


def merge_configs():
    """Merge TinyDB models and settings into config.yaml for LiteLLM."""
    merged = db.get_models_for_litellm()

    try:
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(
                merged, f, default_flow_style=False, sort_keys=False, allow_unicode=True
            )

        # Verify the file was written correctly
        with open(CONFIG_PATH, "r") as f:
            verify = yaml.safe_load(f)
        print(
            f"[Merge] Config merged and verified. Total models: {len(verify.get('model_list', []))}"
        )
    except Exception as e:
        print(f"[Merge] Error writing merged config: {e}", file=sys.stderr)


def merge_configs_atomic() -> tuple[bool, str]:
    """Write merged config to a temp file, validate, then atomically replace.
    Returns (success, error_message).
    """
    merged = db.get_models_for_litellm()
    tmp_path = CONFIG_PATH + ".tmp"

    try:
        with open(tmp_path, "w") as f:
            yaml.dump(
                merged, f, default_flow_style=False, sort_keys=False, allow_unicode=True
            )
        with open(tmp_path, "r") as f:
            yaml.safe_load(f)
        shutil.move(tmp_path, CONFIG_PATH)
        print(
            f"[Merge] Atomic config merge complete. Total models: {len(merged.get('model_list', []))}"
        )
        return True, ""
    except yaml.YAMLError as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(f"[Merge] Config validation FAILED: {e}", file=sys.stderr)
        return False, f"Generated config is invalid: {e}"
    except OSError as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(f"[Merge] Config write FAILED: {e}", file=sys.stderr)
        return False, f"Config file write failed: {e}"
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(f"[Merge] Config merge FAILED: {e}", file=sys.stderr)
        return False, str(e)


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
    config_path = CONFIG_PATH  # Use module-level correct config path
    config_dir = CONFIG_DIR  # Use module-level correct config directory

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
        cmd = [
            "litellm",
            "--config",
            config_path,
            "--port",
            "4000",
            "--host",
            "0.0.0.0",
        ]
        print(f"[Reload] Starting LiteLLM: {' '.join(cmd)}")

        # Add CONFIG_DIR to PYTHONPATH so token_refresher can be imported
        env = os.environ.copy()
        python_path = env.get("PYTHONPATH", "")
        if config_dir not in python_path.split(":"):
            env["PYTHONPATH"] = (
                f"{config_dir}:{python_path}" if python_path else config_dir
            )

        with open(log_path, "a") as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=config_dir,
                env=env,
            )
        new_pid = process.pid
        with open(pid_file, "w") as f:
            f.write(str(new_pid))
        print(f"[Reload] LiteLLM started with PID {new_pid}")

        # Step 4: Verify process is running
        time.sleep(2)
        try:
            if not psutil.Process(new_pid).is_running():
                return {
                    "success": False,
                    "error": f"LiteLLM process died shortly after starting (PID {new_pid})",
                }
        except psutil.NoSuchProcess:
            return {
                "success": False,
                "error": f"LiteLLM process not found after starting (PID {new_pid})",
            }

        # Step 5: Health check (wait up to 60s for slow LiteLLM startup)
        for i in range(60):
            try:
                resp = requests.get("http://localhost:4000/health", timeout=2)
                if resp.status_code < 500:
                    print(f"[Reload] LiteLLM health check passed (PID {new_pid})")
                    return {
                        "success": True,
                        "pid": new_pid,
                        "message": f"LiteLLM restarted (PID {new_pid})",
                    }
            except Exception as e:
                if i == 5:  # Print error once for debugging
                    print(f"[Reload] Health check attempt {i}: {e}")
            time.sleep(1)

        return {
            "success": True,
            "pid": new_pid,
            "warning": "LiteLLM started but health check timed out",
        }
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
        context={
            "version": version,
            "use_prefix": use_prefix,
            "auth_required": is_auth_required(),
        },
    )
