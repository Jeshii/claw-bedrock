import base64
import datetime
import hmac
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict

import psutil
import requests
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import encryption_utils
import password_utils
import settings_resolver
import token_refresher


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

LITELLM_BASE_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")

# Config-store keys for Bedrock auth. Kept distinct from the env var names so a
# restored backup cannot collide with them — config import writes keys verbatim.
_CONFIG_REGION_KEY = db.BEDROCK_REGION_SETTING
_CONFIG_PROFILE_KEY = db.BEDROCK_PROFILE_SETTING
_CONFIG_ACCESS_KEY = db.BEDROCK_ACCESS_KEY_SETTING
_CONFIG_SECRET_KEY = db.BEDROCK_SECRET_KEY_SETTING


def _bedrock_auth_state() -> dict:
    """The Bedrock auth settings block, shared by status and the settings card.

    Provenance comes from `settings_resolver`, so the values the UI shows and the
    values the engine uses cannot disagree. Credential *presence* is reported as
    a boolean; the values themselves never leave the server.
    """
    refresher = token_refresher.token_refresher
    region = settings_resolver.resolve(
        "AWS_REGION",
        config_key=_CONFIG_REGION_KEY,
        default=refresher._region,
    )
    profile = settings_resolver.resolve(
        "AWS_PROFILE",
        config_key=_CONFIG_PROFILE_KEY,
        default=refresher._profile,
    )
    access_key, access_key_state = db.get_secret_setting(_CONFIG_ACCESS_KEY)
    secret_key, secret_key_state = db.get_secret_setting(_CONFIG_SECRET_KEY)

    states = {access_key_state, secret_key_state}
    if "undecryptable" in states:
        # Reported rather than silently ignored: decrypt_data returns its input
        # unchanged on failure, so without this the raw Fernet ciphertext would
        # reach boto3 as if it were a secret. See BUGS.md #8.
        key_state = "undecryptable"
    elif states == {"absent"}:
        # Not configured at all, which is the normal case — the AWS profile is
        # then the credential source. Distinct from half-configured.
        key_state = "absent"
    elif states == {"ok"}:
        key_state = "ok"
    else:
        # One half stored. It cannot authenticate, and the profile is used
        # instead — which is baffling without being told.
        key_state = "incomplete"

    warnings = []
    if key_state == "undecryptable":
        warnings.append("stored_credentials_undecryptable")
    elif key_state == "incomplete":
        warnings.append("incomplete_static_key_pair")

    return {
        "region": region.as_dict(),
        "profile": profile.as_dict(),
        "static_keys": {
            "configured": bool(access_key and secret_key),
            "state": key_state,
        },
        "credential_env": {
            "access_key_set": bool(os.environ.get("AWS_ACCESS_KEY_ID")),
            "secret_key_set": bool(os.environ.get("AWS_SECRET_ACCESS_KEY")),
        },
        "warnings": warnings,
    }


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
    except Exception as e:  # noqa: BLE001 - caller checks the False return
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
AUTH_COOKIE = "__Host-management_auth"
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
def login(request: Request, body: dict):
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
def logout():
    """Logout by clearing the auth cookie."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=AUTH_COOKIE, path="/")
    response.headers["Clear-Site-Data"] = '"cookies", "storage", "cache"'
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
    # microphone=(self) is same-origin only, which is the correct scope for a
    # single-user management UI. camera and geolocation stay denied; there is no
    # reason to widen them. Without this the browser refuses SpeechRecognition
    # and getUserMedia before any feature code runs.
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(self), geolocation=()"
    )
    return response


@app.get("/login")
def login_page(request: Request):
    """Serve the login page."""
    if not is_auth_required():
        return RedirectResponse(url="/", status_code=302)
    with open(os.path.join(BASE_DIR, "templates", "login.html"), "r") as f:
        return HTMLResponse(content=f.read())


@app.on_event("startup")
def startup_event():
    db._migrate_yaml_to_db()
    db.seed_default_providers()
    # Drops provider fields that no runtime code reads. Idempotent, and a no-op
    # on a database that never had them.
    db.migrate_dead_bedrock_fields()
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
                with open(pid_file) as f:
                    pid = int(f.read().strip())
                if not psutil.pid_exists(pid):
                    print(f"[Watchdog] LiteLLM (PID {pid}) crashed, restarting...")
                    reload_litellm()
        except Exception as e:  # noqa: BLE001 - watchdog thread must never exit
            print(f"[Watchdog] Error: {e}", file=sys.stderr)
        time.sleep(10)


@app.on_event("shutdown")
def shutdown_event():
    db.close_db()
    print("[Shutdown] Database closed")


def load_config() -> dict:
    """Load merged config for LiteLLM (reads from generated config.yaml)."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {"model_list": []}
    return {"model_list": []}


def save_local_config(config: dict):
    """Legacy function - no longer writes YAML, settings saved via db module."""


def load_local_config() -> dict:
    """Load local config from TinyDB, creating with defaults if needed."""
    config = db.get_settings()
    # Set defaults
    if "use_prefix" not in config:
        config["use_prefix"] = True
        db.set_setting("use_prefix", True)
    return config


@app.get("/api/settings")
def get_settings():
    """Get current settings."""
    config = load_local_config()
    return {
        "use_prefix": config.get("use_prefix", True),
    }


@app.post("/api/settings")
def update_settings(
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
def get_router_settings_route():
    """Get current router settings for model groups."""
    return db.get_router_settings()


@app.post("/api/settings/router")
def set_router_settings_route(body: dict):
    """Update router settings for model groups."""
    allowed = {"routing_strategy", "allowed_fails", "num_retries"}
    filtered = {k: v for k, v in body.items() if k in allowed}

    # LiteLLM ignores a routing_strategy it does not recognize, so an invalid
    # one would look saved while routing silently stayed on the default.
    if "routing_strategy" in filtered:
        raw = filtered["routing_strategy"]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            filtered.pop("routing_strategy")
        else:
            strategy = db.normalize_routing_strategy(raw)
            if not strategy:
                raise HTTPException(
                    400,
                    f"Unknown routing strategy {raw!r}. Expected one of: "
                    f"{', '.join(db.ROUTING_STRATEGIES)}",
                )
            filtered["routing_strategy"] = strategy

    db.set_router_settings(filtered)
    merge_configs()
    return {"success": True}


@app.get("/api/settings/bedrock")
def get_bedrock_settings():
    """Effective Bedrock auth settings, with provenance and credential presence."""
    return _bedrock_auth_state()


@app.put("/api/settings/bedrock")
def update_bedrock_settings(body: dict):
    """Save Bedrock auth settings and re-mint a token, without a restart.

    Semantics for the key pair mirror the provider api_key field:
      omitted                    → keep whatever is stored
      present and empty          → keep whatever is stored
      present and non-empty      → replace
      clear_static_keys: true    → remove both halves

    Returns whether a token was actually minted. A false `token_refreshed`
    alongside `saved: true` means the settings persisted but AWS rejected them —
    a wrong region or a bad key — and the caller must not report success.
    """
    unknown = set(body) - {
        "region",
        "profile",
        "access_key_id",
        "secret_access_key",
        "clear_static_keys",
    }
    if unknown:
        raise HTTPException(400, f"Unknown field(s): {', '.join(sorted(unknown))}")

    region = body.get("region")
    profile = body.get("profile")

    # Soft validation: warn rather than reject. botocore can enumerate Bedrock
    # regions offline, but only for the public partition — it would falsely
    # reject GovCloud and any region AWS adds later. A wrong region fails loudly
    # at token-mint time, and a false rejection would be the worse failure.
    region_warning = None
    if region:
        cleaned = region.strip()
        if cleaned != cleaned.lower():
            region_warning = f"AWS regions are lowercase; using '{cleaned.lower()}'."
            cleaned = cleaned.lower()
        region = cleaned

    if region is not None:
        db.set_setting(_CONFIG_REGION_KEY, region)
    if profile is not None:
        db.set_setting(_CONFIG_PROFILE_KEY, profile.strip())

    access_key = body.get("access_key_id")
    secret_key = body.get("secret_access_key")
    if body.get("clear_static_keys"):
        db.clear_secret_setting(_CONFIG_ACCESS_KEY)
        db.clear_secret_setting(_CONFIG_SECRET_KEY)
        access_key = secret_key = None
    else:
        if access_key:
            db.set_secret_setting(_CONFIG_ACCESS_KEY, access_key.strip())
        if secret_key:
            db.set_secret_setting(_CONFIG_SECRET_KEY, secret_key.strip())

    # Read back through the same accessor the resolver uses, so what is applied
    # is what was stored rather than what was submitted.
    stored_access, access_state = db.get_secret_setting(_CONFIG_ACCESS_KEY)
    stored_secret, secret_state = db.get_secret_setting(_CONFIG_SECRET_KEY)

    refresher = token_refresher.token_refresher
    effective_region = settings_resolver.resolve(
        "AWS_REGION", config_key=_CONFIG_REGION_KEY, default=refresher._region
    ).value
    effective_profile = settings_resolver.resolve(
        "AWS_PROFILE", config_key=_CONFIG_PROFILE_KEY, default=refresher._profile
    ).value

    refreshed = refresher.configure(
        region=effective_region,
        profile=effective_profile,
        access_key_id=stored_access or "",
        secret_access_key=stored_secret or "",
    )

    # Region is baked into the generated config as aws_region_name, so the
    # config must be regenerated for a region change to reach LiteLLM.
    merge_configs()
    reloaded = _reload_litellm_config()

    state = _bedrock_auth_state()
    return {
        "saved": True,
        "token_refreshed": refreshed,
        "config_reloaded": reloaded,
        "warnings": [
            w for w in state["warnings"] if w != "stored_credentials_undecryptable"
        ]
        + ([region_warning] if region_warning else []),
        "bedrock": state,
        "credential_states": {
            "access_key": access_state,
            "secret_key": secret_state,
        },
    }


@app.get("/api/auth/status")
def auth_status():
    """Check if AWS auth is needed and get auth URL."""
    auth_needed = os.path.exists("/tmp/auth_needed")
    auth_url = None

    if os.path.exists("/tmp/auth_url"):
        with open("/tmp/auth_url", "r") as f:
            auth_url = f.read().strip()

    openrouter_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    bedrock_state = token_refresher.token_refresher.auth_status_payload()
    # Region/profile provenance is resolved in one place so the status endpoint
    # and the settings card cannot disagree about what is in effect.
    auth = _bedrock_auth_state()

    return {
        "auth_needed": auth_needed,
        "auth_url": auth_url,
        "awaiting_code": bedrock_state["awaiting_code"],
        "auth_error": bedrock_state["auth_error"],
        "needs_login": bedrock_state["needs_login"],
        "openrouter": {"configured": openrouter_key},
        "bedrock": {
            **auth,
            # The refresher's own view of the token, which is what was actually
            # minted and against which region.
            "token": bedrock_state["token"],
        },
    }


@app.post("/api/auth/submit-code")
def submit_auth_code(body: dict):
    """Submit an authorization code to the running aws login process."""
    code = body.get("code", "")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' in request body")
    result = token_refresher.token_refresher.submit_code(code)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"success": True}


@app.post("/api/auth/retry")
def retry_auth():
    """Reset failure state and start a new aws login --remote process."""
    token_refresher.token_refresher.retry_login()
    return {"success": True}


@app.get("/api/security/encryption-status")
def encryption_status():
    """Return encryption configuration status."""
    configured, mode = encryption_utils.get_encryption_mode()
    return {"configured": configured, "using": mode}


@app.get("/api/security/key")
def get_key_status():
    """Get current master key status (masked)."""
    key = db.get_master_key()
    if key:
        masked = key[:12] + "..." + key[-4:]
        return {"enabled": True, "masked_key": masked}
    return {"enabled": False}


@app.post("/api/security/key/generate")
def generate_key():
    """Generate a new master key and reload LiteLLM config."""
    key = db.generate_master_key()
    _reload_litellm_config()
    return {"success": True, "key": key}


@app.delete("/api/security/key")
def revoke_key():
    """Revoke the master key (disables auth on next reload)."""
    db.clear_master_key()
    _reload_litellm_config()
    return {"success": True}


@app.get("/api/version")
def version_endpoint():
    """Return the current version of claw-bedrock."""
    return {"version": get_version()}


@app.get("/api/chat/models")
def chat_models():
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
def chat_completion(body: dict):
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
            resp.iter_content(chunk_size=None),
            media_type=resp.headers.get("content-type", "text/event-stream"),
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    except requests.exceptions.HTTPError as e:
        detail = "Unknown error"
        try:
            detail = e.response.json().get("error", {}).get("message", str(e))
        except Exception:  # noqa: BLE001 - re-raised below as HTTPException
            detail = str(e)
        raise HTTPException(e.response.status_code, detail)
    except requests.exceptions.ConnectionError:
        raise HTTPException(502, "Cannot connect to LiteLLM")
    except requests.exceptions.Timeout:
        raise HTTPException(504, "LiteLLM request timed out")


@app.get("/api/dashboard")
def get_dashboard():
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
def get_logs(lines: int = 50):
    """Return the last N lines of the LiteLLM log."""
    if not os.path.exists(LOG_PATH):
        return {"logs": "No logs available yet."}
    try:
        result = subprocess.run(
            ["tail", f"-{lines}", LOG_PATH],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return {"logs": result.stdout or "Log is empty."}
    except Exception as e:  # noqa: BLE001 - log tail is best-effort
        return {"logs": f"Error reading logs: {e!s}"}


@app.get("/api/logs/debug")
def get_debug_logs(lines: int = 50):
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
            check=False,
        )
        return {"logs": result.stdout or "Log is empty."}
    except Exception as e:  # noqa: BLE001 - log tail is best-effort
        return {"logs": f"Error reading debug logs: {e!s}"}


@app.get("/api/logs/container")
def get_container_logs(lines: int = 50):
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
            check=False,
        )
        return {"logs": result.stdout or "Log is empty."}
    except Exception as e:  # noqa: BLE001 - log tail is best-effort
        return {"logs": f"Error reading container logs: {e!s}"}


@app.get("/api/debug/token-refresher")
def get_token_refresher_state():
    """Read and return the TokenRefresher debug log to check internal state."""
    debug_log = "/tmp/token_refresher_debug.log"
    try:
        if os.path.exists(debug_log):
            with open(debug_log, "r") as f:
                content = f.read()
            return {"debug_log": content, "exists": True}
        return {"debug_log": None, "exists": False, "message": "Debug log not found"}
    except Exception as e:  # noqa: BLE001 - state probe is best-effort
        return {"error": str(e)}


@app.get("/api/models")
def list_models(tag: str | None = Query(None)):
    """List all configured models, optionally filtered by tag."""
    if tag:
        models = db.get_models_by_tag(tag)
    else:
        models = db.get_all_models()
    return {"models": [enrich_model_with_provider(dict(m)) for m in models]}


def _member_status(model: dict) -> dict:
    """Derive a config-only health signal for a group member.

    No live probing: this reflects what will actually be written to
    config.yaml, not whether the provider is reachable right now.

    - error: provider missing or dangling. These models are dropped from the
      generated config by db._merge_provider_defaults.
    - warn:  litellm_params absent or missing the required `model` key.
    - ok:    the model will be emitted to the LiteLLM config as-is.
    """
    provider_name = model.get("provider")
    if not provider_name:
        return {"level": "error", "detail": "No provider assigned"}

    if not db._get_provider_raw(provider_name):
        return {
            "level": "error",
            "detail": f"Provider '{provider_name}' no longer exists",
        }

    litellm_params = model.get("litellm_params")
    if not litellm_params:
        return {"level": "warn", "detail": "No litellm_params configured"}
    if not litellm_params.get("model"):
        return {"level": "warn", "detail": "litellm_params.model is not set"}

    return {"level": "ok", "detail": ""}


@app.get("/api/model-groups")
def list_model_groups():
    """List models aggregated by model_group for the Groups dashboard.

    `active_member_count` counts members that survive config generation; a group
    can hold N models in TinyDB while shipping fewer to LiteLLM when a member's
    provider is dangling.
    """
    groups: dict[str, list[dict]] = {}
    ungrouped: list[dict] = []

    for raw in db.get_all_models():
        model = enrich_model_with_provider(dict(raw))
        group_name = model.get("model_group")
        if not group_name:
            ungrouped.append({"model_name": model.get("model_name", "")})
            continue
        model["status"] = _member_status(model)
        groups.setdefault(group_name, []).append(model)

    result = []
    for name in sorted(groups):
        members = groups[name]
        result.append(
            {
                "name": name,
                "member_count": len(members),
                "active_member_count": sum(
                    1 for m in members if m["status"]["level"] != "error"
                ),
                "members": members,
            }
        )

    return {
        "groups": result,
        "ungrouped_count": len(ungrouped),
        "ungrouped_models": sorted(u["model_name"] for u in ungrouped),
        # The dashboard needs to know the active strategy to warn that a
        # member with no cost will not be picked under cost-based routing.
        "routing_strategy": db.get_router_settings().get("routing_strategy"),
    }


_GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_group_name(name) -> str:
    """Normalize and validate a `model_group` value.

    A group name becomes the public LiteLLM `model_name` that clients call
    (see db.get_models_for_litellm), optionally prefixed with `claw-bedrock/`,
    so it has to be safe to embed in a model identifier, a YAML key and a URL
    path segment. Nothing validated this before, which meant a name containing
    whitespace produced a model_name no client could request.

    An allowlist rather than a denylist: the name round-trips through config,
    URLs and rendered markup, so anything outside a conservative set is
    rejected rather than reasoned about per sink.
    """
    cleaned = str(name or "").strip()
    if not cleaned:
        raise HTTPException(400, "Group name cannot be empty")
    if len(cleaned) > 64:
        raise HTTPException(400, "Group name must be 64 characters or fewer")
    if not _GROUP_NAME_RE.match(cleaned):
        raise HTTPException(
            400,
            "Group name may only contain letters, numbers, dots, dashes "
            "and underscores",
        )
    return cleaned


def _group_members(name: str) -> list:
    return [m for m in db.get_all_models() if m.get("model_group") == name]


@app.post("/api/model-groups/rename")
def rename_model_group(body: dict):
    """Rename a model group, moving every member in one atomic update.

    A group name is the model_name clients call, so this is a breaking
    change for anything referencing the old name -- the UI confirms
    explicitly. Applied as a single batch so a mid-way failure cannot
    leave a group split across two names.
    """
    old = str(body.get("from") or "").strip()
    new = _validate_group_name(body.get("to"))

    if not old:
        raise HTTPException(400, "Source group name cannot be empty")
    if old == new:
        return {"renamed": 0, "from": old, "to": new}

    members = _group_members(old)
    if not members:
        raise HTTPException(404, f"No group named '{old}'")

    if any(m.get("model_group") == new for m in db.get_all_models()):
        raise HTTPException(409, f"Group '{new}' already exists")

    for m in members:
        db.update_model_field(m["model_name"], {"model_group": new})

    merge_configs()
    _reload_litellm_config()
    return {"renamed": len(members), "from": old, "to": new}


@app.post("/api/model-groups/unassign")
def unassign_model_group(body: dict):
    """Clear a model group from all of its members, leaving them ungrouped.

    The models themselves are untouched; only the shared name is removed, so
    each member goes back to being served under its own model_name.
    """
    name = _validate_group_name(body.get("name"))

    members = _group_members(name)
    if not members:
        raise HTTPException(404, f"No group named '{name}'")

    for m in members:
        db.update_model_field(m["model_name"], {"model_group": None})

    merge_configs()
    _reload_litellm_config()
    return {"cleared": len(members), "name": name}


@app.post("/api/models/reload")
def reload_models():
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
def fetch_openrouter_models(
    include_free: str | None = None,
    search: str | None = None,
    api_key: str | None = None,
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
                # fmt: off
                # PEP 758 paren removal; see encryption_utils.decrypt_data
                try:
                    return float(m.get("pricing", {}).get("prompt", "1")) == 0
                except (ValueError, TypeError):
                    return False

                # fmt: on

            models = [m for m in models if _is_free(m)]
        elif include_free == "false":

            def _is_not_free(m):
                # fmt: off
                # PEP 758 paren removal; see encryption_utils.decrypt_data
                try:
                    return float(m.get("pricing", {}).get("prompt", "1")) != 0
                except (ValueError, TypeError):
                    return True

                # fmt: on

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
            # fmt: off
            # PEP 758 paren removal; see encryption_utils.decrypt_data
            try:
                cost = float(m.get("pricing", {}).get("prompt", "inf"))
            except (ValueError, TypeError):
                cost = float("inf")

            # fmt: on
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
    except Exception as e:  # noqa: BLE001 - re-raised below as HTTPException
        raise HTTPException(500, f"Failed to fetch OpenRouter models: {e!s}")


@app.get("/api/providers/{name}/models")
def fetch_provider_models(name: str):
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
        base = api_base.rstrip("/")
        models_url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        resp = requests.get(
            models_url,
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
        raise HTTPException(500, f"Failed to fetch models from '{name}': {e!s}") from e


@app.delete("/api/models/{encoded_model_name:path}")
def delete_model(encoded_model_name: str):
    """Delete a model from TinyDB."""
    try:
        model_name = base64url_decode(encoded_model_name)
    except Exception:  # noqa: BLE001 - base64 decode may raise; surface as 400
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
def add_model(model: dict):
    """Add a new model to TinyDB."""
    db.add_model(model)
    merge_configs()
    _reload_litellm_config()

    return {
        "status": "success",
        "model": model,
    }


@app.put("/api/models/{encoded_old_name:path}")
def rename_model(encoded_old_name: str, update: dict):
    """Rename a model in TinyDB."""
    try:
        old_model_name = base64url_decode(encoded_old_name)
    except Exception:  # noqa: BLE001 - base64 decode may raise; surface as 400
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
def update_model(encoded_name: str, update: dict):
    """Update fields on a model (e.g., reasoning_effort, litellm_params.thinking)."""
    try:
        model_name = base64url_decode(encoded_name)
    except Exception:  # noqa: BLE001 - base64 decode may raise; surface as 400
        raise HTTPException(400, "Invalid model name encoding")

    allowed_fields = {
        "reasoning_effort",
        "tags",
        "model_group",
        "litellm_params",
        "input_cost",
        "output_cost",
    }
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

    # A cost is a price, so it is validated rather than coerced: "3" from a
    # text input, -1 from a stray keystroke, and NaN from a bad parse all
    # reach the router as a real number if they are stored as-is.
    cleared = []
    for field in ("input_cost", "output_cost"):
        if field not in updates:
            continue
        raw = updates.pop(field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            cleared.append(field)
            continue
        if isinstance(raw, bool):
            raise HTTPException(400, f"{field} must be a number")
        # fmt: off
        # PEP 758 paren removal; see encryption_utils.decrypt_data
        try:
            cost = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{field} must be a number, got {raw!r}")

        # fmt: on
        if not math.isfinite(cost) or cost < 0:
            raise HTTPException(400, f"{field} must be a finite, non-negative number")
        updates[field] = cost

    if not db.get_model_by_name(model_name):
        raise HTTPException(404, f"Model {model_name} not found")

    # Clearing is checked after normalization, not before: a PATCH carrying
    # only a blank cost leaves nothing to write, but is still a valid edit.
    if not updates and not cleared:
        raise HTTPException(400, "No valid fields to update")

    if updates:
        db.update_model_field(model_name, updates)
    for field in cleared:
        db.unset_model_field(model_name, field)

    merge_configs()
    _reload_litellm_config()
    return {"status": "success", "updated": updates, "cleared": cleared}


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
def list_tags():
    """List all tag definitions."""
    return {"tags": db.get_all_tags()}


@app.post("/api/tags")
def create_tag(body: dict):
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
def rename_tag(tag_name: str, body: dict):
    """Rename a tag."""
    new_name = (body.get("name") or "").strip()
    if not new_name:
        raise HTTPException(400, "New tag name is required")
    ok = db.rename_tag(tag_name, new_name)
    if not ok:
        raise HTTPException(404, f"Tag '{tag_name}' not found")
    return {"old_name": tag_name, "new_name": new_name}


@app.delete("/api/tags/{tag_name:path}")
def delete_tag(tag_name: str):
    """Delete a tag and remove it from all models."""
    db.delete_tag(tag_name)
    return {"deleted": tag_name}


@app.patch("/api/tags/{tag_name:path}")
def update_tag_color(tag_name: str, body: dict):
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
def add_model_tag(encoded_name: str, body: dict):
    """Add a tag to a model. Creates the tag if it doesn't exist."""
    try:
        model_name = base64url_decode(encoded_name)
    except Exception:  # noqa: BLE001 - base64 decode may raise; surface as 400
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
def remove_model_tag(encoded_name: str, tag_name: str):
    """Remove a tag from a model."""
    try:
        model_name = base64url_decode(encoded_name)
    except Exception:  # noqa: BLE001 - base64 decode may raise; surface as 400
        raise HTTPException(400, "Invalid model name encoding")
    ok = db.remove_tag_from_model(model_name, tag_name)
    if not ok:
        raise HTTPException(404, f"Model '{model_name}' not found")
    return {"model": model_name, "tag": tag_name}


# ── Providers ──────────────────────────────────────────────────────────────


@app.get("/api/providers")
def list_providers():
    """List all provider definitions (sanitized)."""
    providers = db.get_all_providers()
    return {"providers": [db.sanitize_provider_for_response(p) for p in providers]}


@app.post("/api/providers")
def create_provider(body: dict):
    """Create a new provider."""
    if not body.get("name"):
        raise HTTPException(400, "name is required")
    if db.provider_exists(body["name"]):
        raise HTTPException(409, f"Provider '{body['name']}' already exists")
    try:
        db.upsert_provider(body)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    created = db.get_provider(body["name"])
    print(f"[Provider] Created name={body['name']} type={body.get('type')}")
    return {"provider": db.sanitize_provider_for_response(created)}


@app.get("/api/providers/{name}")
def get_provider_detail(name: str):
    """Get a single provider (sanitized) and its models."""
    provider = db.get_provider(name)
    if not provider:
        raise HTTPException(404, "Not found")
    models = db.get_models_by_provider(name)
    return {"provider": db.sanitize_provider_for_response(provider), "models": models}


ALLOWED_PROVIDER_FIELDS = frozenset(
    {
        "name",
        "display_name",
        "type",
        "color",
        "notes",
        "api_base",
        "api_key",
        "clear_api_key",
    }
)

VALID_PROVIDER_TYPES = frozenset({"bedrock", "openai-compatible", "custom"})


@app.put("/api/providers/{name}")
def update_provider(name: str, body: dict):
    """Update a provider with explicit field semantics.

    Flow:
      1. Validate and normalize submitted fields.
      2. Detect runtime changes BEFORE persisting.
      3. Persist to TinyDB.
      4. If runtime-relevant fields changed, regenerate config and reload LiteLLM.
      5. Return the sanitized persisted DTO (freshly read from DB).
      6. On merge/reload failure — return structured 503, NO DB rollback.

    Field semantics:
      - Non-sensitive fields (display_name, type, color, notes, api_base):
        updated when present in body.
      - api_key:
          omitted        → retains existing encrypted blob
          present, empty → treated as no-change (keep existing)
          non-empty      → encrypts and replaces
          clear_api_key  → removes the key field entirely

    The Bedrock auth fields this used to accept (aws_region,
    aws_access_key_env, aws_secret_key_env) are gone. They collected
    configuration no runtime code read; see db.DEAD_BEDROCK_PROVIDER_FIELDS.
    Region is a global setting at PUT /api/settings/bedrock.
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
        raise HTTPException(
            400,
            f"Invalid type '{body['type']}'; must be one of: {', '.join(sorted(VALID_PROVIDER_TYPES))}",
        )

    merged = dict(existing_raw)

    for field in ("display_name", "type", "color", "notes", "api_base"):
        if field in body:
            merged[field] = (
                (body[field] or "").strip()
                if isinstance(body[field], str)
                else body[field]
            )

    if body.get("clear_api_key"):
        merged["api_key"] = None
    elif "api_key" in body:
        val = body["api_key"]
        if val:
            merged["api_key"] = encryption_utils.encrypt_data(val)

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
def delete_provider_route(name: str):
    """Delete a provider definition."""
    db.delete_provider(name)
    return {"success": True}


@app.post("/api/providers/{old_name}/rename")
def rename_provider_route(old_name: str, body: dict):
    """Rename a provider and update all model references."""
    new_name = body.get("new_name")
    if not new_name:
        raise HTTPException(400, "new_name is required")
    success = db.rename_provider(old_name, new_name)
    return {"success": success}


@app.get("/api/backup/export")
def export_backup():
    """Download current config as a JSON backup file."""
    data = db.export_backup()
    data["claw_version"] = get_version()
    filename = f"claw-bedrock-backup-{datetime.datetime.now(datetime.UTC).strftime('%Y%m%d-%H%M%S')}.json"
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
    except Exception:  # noqa: BLE001 - malformed JSON body; surface as 400
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
    except Exception:  # noqa: BLE001 - malformed JSON body; surface as 400
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
        "api_key",
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
    except Exception as e:  # noqa: BLE001 - config write failure is logged and reported
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
    except Exception as e:  # noqa: BLE001 - merge failure is returned to the caller
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
    except Exception as e:  # noqa: BLE001 - validation failure is returned to the caller
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
        except (OSError, ValueError) as e:
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
        except Exception as e:  # noqa: BLE001 - process lookup failure is logged
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
            except Exception as e:  # noqa: BLE001 - health probe retries; failure is expected
                if i == 5:  # Print error once for debugging
                    print(f"[Reload] Health check attempt {i}: {e}")
            time.sleep(1)

        return {
            "success": True,
            "pid": new_pid,
            "warning": "LiteLLM started but health check timed out",
        }
    except Exception as e:  # noqa: BLE001 - start failure is returned to the caller
        print(f"[Reload] Error starting LiteLLM: {e}", file=sys.stderr)
        return {"success": False, "error": str(e)}


@app.get("/api/health/litellm")
def health_litellm():
    """Report whether LiteLLM can actually serve models.

    Note this proxies `/health`, which on LiteLLM is an *active* health check:
    it makes a real call to every model in `model_list` unless
    `general_settings.background_health_checks` is set, which this config does
    not. So this endpoint is coupled to provider auth — with Bedrock
    unauthenticated it reports `error` while the proxy itself is perfectly
    healthy. That coupling is deliberate and load-bearing: a red reading here
    means models genuinely cannot be served. For "is the proxy process alive",
    use /api/health/litellm/liveliness, which touches no provider.
    """
    try:
        resp = requests.get("http://localhost:4000/health", timeout=5)
        return {"status": "ok", "litellm_status": resp.status_code}
    except Exception as e:  # noqa: BLE001 - health probe failure is returned as status
        return {"status": "error", "detail": str(e)}


@app.get("/api/health/litellm/liveliness")
def health_litellm_liveliness():
    """Report whether the LiteLLM process is serving, independent of providers.

    LiteLLM's `/health/liveliness` only checks whether a graceful shutdown has
    begun — no model list, no outbound calls, no auth. That makes it the correct
    assertion for "did this image boot", and the wrong one for "can this serve a
    request"; `/api/health/litellm` answers the latter and is the one that goes
    red when provider credentials are missing.
    """
    try:
        resp = requests.get("http://localhost:4000/health/liveliness", timeout=5)
        return {"status": "ok", "litellm_status": resp.status_code}
    except Exception as e:  # noqa: BLE001 - probe failure is returned as status
        return {"status": "error", "detail": str(e)}


@app.get("/")
def dashboard(request: Request):
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
