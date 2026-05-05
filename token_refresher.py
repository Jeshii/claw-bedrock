import os
import subprocess
import sys
import time
import threading
import re
import traceback

# Debug log file for TokenRefresher - bypasses stdout redirection
_DEBUG_LOG = "/tmp/token_refresher_debug.log"
_CODE_VERSION = "2025-05-05-v2"  # Update this when making changes to verify code is reloaded

def _debug(msg):
    """Write debug message to a file to bypass stdout redirection."""
    try:
        with open(_DEBUG_LOG, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass

_debug(f"Module loaded. Version={_CODE_VERSION}, Python path: {sys.path[:3]}")

import boto3
import botocore.exceptions
from aws_bedrock_token_generator import BedrockTokenGenerator
from litellm.integrations.custom_logger import CustomLogger

_TMP_AUTH_URL = "/tmp/auth_url"
_TMP_AUTH_NEEDED = "/tmp/auth_needed"
_TMP_AUTH_CODE = "/tmp/auth_code"

# Matches the XXXX-XXXX verification code in aws sso login output
_CODE_RE = re.compile(r'\b([A-Z0-9]{4}-[A-Z0-9]{4})\b')


def _write_auth_tmp(url: str, code: str | None = None):
    """Write auth URL, auth_needed flag, and optional code to /tmp for management UI."""
    _debug(f"_write_auth_tmp() called. url={url[:50] if url else None}, code={code}")
    try:
        with open(_TMP_AUTH_URL, "w") as f:
            f.write(url)
        with open(_TMP_AUTH_NEEDED, "w") as f:
            f.write("1")
        _debug(f"Wrote {_TMP_AUTH_URL} and {_TMP_AUTH_NEEDED}")
        if code:
            with open(_TMP_AUTH_CODE, "w") as f:
                f.write(code)
            _debug(f"Wrote {_TMP_AUTH_CODE}")
    except Exception as e:
        _debug(f"WARNING: Could not write auth tmp files: {e}")
        print(f"[TokenRefresher] WARNING: Could not write auth tmp files: {e}", file=sys.stderr)


def _clear_auth_tmp():
    """Remove /tmp auth files once login completes."""
    for path in (_TMP_AUTH_URL, _TMP_AUTH_NEEDED, _TMP_AUTH_CODE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[TokenRefresher] WARNING: Could not remove {path}: {e}", file=sys.stderr)


class BedrockTokenRefresher(CustomLogger):
    TOKEN_TTL = 2700  # 45 min — refresh before AWS tokens expire

    def __init__(self):
        _debug(f"BedrockTokenRefresher.__init__() called. Profile={os.environ.get('AWS_PROFILE', 'bedrock-openai20b')}")
        self._fetched_at = 0
        self._force_refresh = False
        self._needs_login = False  # set True when login required in non-interactive mode
        self._auth_url: str | None = None  # captured aws sso login --no-browser URL for web UI
        self._auth_code: str | None = None  # captured XXXX-XXXX verification code
        self._login_process: subprocess.Popen | None = None  # background aws login process
        self._generator = BedrockTokenGenerator()
        self._region = os.environ.get("AWS_REGION", "ap-northeast-1")
        self._profile = os.environ.get("AWS_PROFILE", "bedrock-openai20b")
        # Wrap startup refresh so a login/credential failure never crashes LiteLLM
        try:
            self._refresh()
        except Exception as e:
            _debug(f"WARNING: Initial token refresh failed ({e})")
            # Set needs_login flag and write auth_needed file on failure
            if not self._is_interactive():
                self._needs_login = True
                try:
                    with open(_TMP_AUTH_NEEDED, "w") as f:
                        f.write("1")
                    _debug(f"Wrote {_TMP_AUTH_NEEDED} from __init__ failure path")
                except Exception as e2:
                    _debug(f"Failed to write auth_needed: {e2}")
            print(
                f"[TokenRefresher] WARNING: Initial token refresh failed ({e}). "
                "Server will start without Bedrock credentials — authenticate via the web UI.",
                file=sys.stderr,
            )
        self._register_auth_endpoint()

    def _is_interactive(self) -> bool:
        result = sys.stdin.isatty()
        _debug(f"_is_interactive() -> {result}")
        return result

    def _profile_exists(self, profile_name):
        """Check if the given AWS profile exists in the AWS configuration."""
        try:
            result = subprocess.run(
                ["aws", "configure", "list-profiles"],
                capture_output=True, text=True, check=True
            )
            profiles = [p.strip() for p in result.stdout.splitlines() if p.strip()]
            return profile_name in profiles
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def _capture_auth_url_from_process(self, proc: subprocess.Popen):
        """Read stdout from aws login --remote in a background thread.

        The command outputs lines like:
            Please visit the following URL:
            https://device.sso.ap-northeast-1.amazonaws.com/
            Then enter the code:
            XXXX-XXXX
            Alternatively, you may visit the following URL which will autofill the code:
            https://device.sso.ap-northeast-1.amazonaws.com/?user_code=XXXX-XXXX

        We capture the autofill URL (contains '?user_code=') as it's directly usable,
        and also capture the standalone XXXX-XXXX code for display in the web UI.
        """
        _debug(f"_capture_auth_url_from_process() called. proc.pid={proc.pid if proc else 'None'}")
        def _read():
            print(f"[TokenRefresher] DEBUG: _read thread started. proc.pid={proc.pid}", flush=True)
            try:
                line_count = 0
                for line in proc.stdout:
                    line = line.strip()
                    line_count += 1
                    print(f"[TokenRefresher] DEBUG: stdout line #{line_count}: {repr(line)}", flush=True)

                    # Capture standalone verification code (XXXX-XXXX)
                    # Only match lines that are JUST the code (not inside a URL)
                    if not line.startswith("https://") and self._auth_code is None:
                        m = _CODE_RE.search(line)
                        if m:
                            self._auth_code = m.group(1)
                            print(f"[TokenRefresher] Auth code captured: {self._auth_code}")
                            # Write code to tmp even if URL not yet known
                            if self._auth_url:
                                _write_auth_tmp(self._auth_url, self._auth_code)
                            else:
                                try:
                                    with open(_TMP_AUTH_CODE, "w") as f:
                                        f.write(self._auth_code)
                                except Exception:
                                    pass

                    # Prefer the autofill URL with user_code embedded
                    if "user_code=" in line and line.startswith("https://"):
                        self._auth_url = line
                        _write_auth_tmp(line, self._auth_code)
                        print(f"[TokenRefresher] Auth URL captured for web UI: {self._auth_url}")
                    elif line.startswith("https://") and self._auth_url is None:
                        # Fallback: first https URL seen (base device auth URL)
                        self._auth_url = line
                        _write_auth_tmp(line, self._auth_code)
                        print(f"[TokenRefresher] Auth URL captured for web UI: {self._auth_url}")

                print(f"[TokenRefresher] DEBUG: stdout loop exhausted after {line_count} lines.", flush=True)
                print(f"[TokenRefresher] DEBUG: calling proc.wait()...", flush=True)
                proc.wait()
                print(f"[TokenRefresher] DEBUG: proc.wait() returned. returncode={proc.returncode}", flush=True)
                if proc.returncode == 0:
                    print("[TokenRefresher] AWS login completed — refreshing token...")
                    self._needs_login = False
                    self._auth_url = None
                    self._auth_code = None
                    self._login_process = None
                    _clear_auth_tmp()
                    try:
                        self._refresh()
                    except Exception as e:
                        print(
                            f"[TokenRefresher] WARNING: Token refresh after login failed: {e}",
                            file=sys.stderr,
                        )
                else:
                    print(
                        f"[TokenRefresher] aws sso login exited with code {proc.returncode}. "
                        "Login did not complete — auth still required.",
                        file=sys.stderr,
                    )
                    # Keep _needs_login=True and auth URL visible so user can retry
                    self._login_process = None
            except Exception as e:
                print(f"[TokenRefresher] Error reading aws sso login output: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                self._login_process = None

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        print(f"[TokenRefresher] DEBUG: _read thread launched. thread.is_alive={t.is_alive()}", flush=True)

    def _ensure_login(self):
        """Trigger aws login --remote for headless authentication.

        In interactive mode: falls back to normal aws sso login (opens browser).
        In non-interactive mode: launches aws login --remote in the background,
        captures the autofill URL, sets _needs_login, and surfaces it via /auth/status.
        """
        _debug(f"_ensure_login() called. is_interactive={self._is_interactive()}, profile={self._profile}")
        if not self._is_interactive():
            print(
                f"[TokenRefresher] AWS session expired or missing for profile '{self._profile}'. "
                f"Non-interactive mode — capturing auth URL for web UI.",
                file=sys.stderr,
            )
            self._needs_login = True

            # Write auth_needed file immediately so UI knows auth is required
            # (The URL file will be written later when captured from aws output)
            try:
                with open(_TMP_AUTH_NEEDED, "w") as f:
                    f.write("1")
                _debug(f"Wrote {_TMP_AUTH_NEEDED} file")
            except Exception as e:
                _debug(f"WARNING: Could not write auth_needed file: {e}")

            # NOTE: aws login --remote doesn't need an existing profile, so we skip the profile check
            # and let the command handle authentication directly

            # Only start one login process at a time
            if self._login_process is not None and self._login_process.poll() is None:
                print("[TokenRefresher] aws login already running, skipping duplicate launch.")
                return

            try:
                _debug(f"Starting aws command: aws login --remote --profile {self._profile}")
                # Try `aws login --remote` first, fall back to `aws sso login` if needed
                try:
                    proc = subprocess.Popen(
                        ["aws", "login", "--remote", "--profile", self._profile],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.PIPE,
                        text=True,
                    )
                    _debug(f"aws login --remote process started with pid={proc.pid}")
                except FileNotFoundError:
                    _debug("ERROR: 'aws' CLI not found")
                    raise
                except Exception as e:
                    _debug(f"aws login --remote failed ({e}), trying aws sso login...")
                    proc = subprocess.Popen(
                        ["aws", "sso", "login", "--profile", self._profile],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.PIPE,
                        text=True,
                    )
                    _debug(f"aws sso login process started with pid={proc.pid}")
                self._login_process = proc
                self._capture_auth_url_from_process(proc)
            except FileNotFoundError:
                _debug("ERROR: 'aws' CLI not found")
                print("[TokenRefresher] ERROR: 'aws' CLI not found. Is it installed and on PATH?", file=sys.stderr)
            except Exception as e:
                _debug(f"ERROR: failed to launch aws login: {e}")
                print(f"[TokenRefresher] ERROR: failed to launch aws login: {e}", file=sys.stderr)
            return  # do not block — background thread handles the rest

        print(
            f"[TokenRefresher] AWS session expired or missing. "
            f"Launching login for profile '{self._profile}'..."
        )
        try:
            subprocess.run(
                ["aws", "sso", "login", "--profile", self._profile],
                check=True,
            )
        except FileNotFoundError:
            print("[TokenRefresher] ERROR: 'aws' CLI not found. Is it installed and on PATH?", file=sys.stderr)
            raise
        except subprocess.CalledProcessError as e:
            print(f"[TokenRefresher] ERROR: aws sso login failed (exit {e.returncode}).", file=sys.stderr)
            raise

    def _get_valid_session(self) -> boto3.Session | None:
        """Return a boto3 Session with valid credentials, triggering login if needed.
        Returns None if login is required and the server should continue without credentials.
        """
        _debug(f"_get_valid_session() called. profile={self._profile}, region={self._region}")
        try:
            session = boto3.Session(profile_name=self._profile, region_name=self._region)
        except botocore.exceptions.ProfileNotFound:
            _debug(f"AWS profile '{self._profile}' not found")
            print(
                f"[TokenRefresher] AWS profile '{self._profile}' not found in AWS configuration. "
                "Please check your AWS config or set the correct profile via AWS_PROFILE environment variable.",
                file=sys.stderr,
            )
            self._needs_login = True
            return None
        except Exception as e:
            _debug(f"Error creating AWS session: {e}")
            print(f"[TokenRefresher] Error creating AWS session: {e}", file=sys.stderr)
            self._ensure_login()
            return None

        try:
            credentials = session.get_credentials()
        except Exception as e:
            print(f"[TokenRefresher] Could not get credentials for profile '{self._profile}': {e}", file=sys.stderr)
            self._ensure_login()
            return None

        if credentials is None:
            self._ensure_login()
            if self._needs_login:
                return None
            # Login completed synchronously (interactive mode) — rebuild session
            try:
                session = boto3.Session(profile_name=self._profile, region_name=self._region)
                credentials = session.get_credentials()
            except Exception as e:
                print(f"[TokenRefresher] Failed to rebuild session after login: {e}", file=sys.stderr)
                return None
            if credentials is None:
                print("[TokenRefresher] Could not obtain credentials after login.", file=sys.stderr)
                return None
            return session

        # Attempt to resolve credentials to catch expired tokens early
        try:
            credentials.get_frozen_credentials()
        except Exception:
            self._ensure_login()
            if self._needs_login:
                return None
            try:
                session = boto3.Session(profile_name=self._profile, region_name=self._region)
                credentials = session.get_credentials()
                if credentials is None:
                    print("[TokenRefresher] Could not obtain credentials after login.", file=sys.stderr)
                    return None
                credentials.get_frozen_credentials()
            except Exception as e:
                print(f"[TokenRefresher] Credentials still invalid after login: {e}", file=sys.stderr)
                return None

        return session

    def _refresh(self):
        _debug(f"_refresh() called. _needs_login={self._needs_login}")
        session = self._get_valid_session()
        _debug(f"_refresh(): _get_valid_session returned {type(session).__name__ if session else None}")
        if session is None:
            _debug(f"_refresh(): session is None, _needs_login={self._needs_login}")
            # Start the login process if we're in non-interactive mode
            if self._needs_login:
                try:
                    with open(_TMP_AUTH_NEEDED, "w") as f:
                        f.write("1")
                    _debug(f"Wrote {_TMP_AUTH_NEEDED} from _refresh (session=None path)")
                except Exception as e:
                    _debug(f"Failed to write auth_needed: {e}")
                # Actually start the aws login process to get the auth URL
                if self._login_process is None or self._login_process.poll() is not None:
                    _debug("_refresh(): calling _ensure_login() to start aws login process")
                    self._ensure_login()
            return  # login required — server stays up, /auth/status will surface the URL
        try:
            credentials = session.get_credentials()
            _debug(f"_refresh(): got credentials: {type(credentials).__name__ if credentials else None}")
            token = self._generator.get_token(credentials, self._region)
            os.environ["BEDROCK_MANTLE_API_KEY"] = token
            self._fetched_at = time.time()
            print(f"[TokenRefresher] Token refreshed at {time.strftime('%H:%M:%S')}")
            # Clear auth_needed flag on successful token refresh
            self._needs_login = False
            self._auth_url = None
            self._auth_code = None
            try:
                if os.path.exists(_TMP_AUTH_NEEDED):
                    os.remove(_TMP_AUTH_NEEDED)
                _clear_auth_tmp()
                _debug("Cleared auth_needed flag - token refresh successful")
            except Exception as e:
                _debug(f"Error clearing auth tmp files: {e}")
        except Exception as e:
            _debug(f"Token generation failed: {e}")
            print(f"[TokenRefresher] Token generation failed: {e}", file=sys.stderr)
            # Set needs_login flag and write auth_needed file so UI knows auth is required
            if not self._is_interactive():
                self._needs_login = True
                try:
                    with open(_TMP_AUTH_NEEDED, "w") as f:
                        f.write("1")
                    _debug(f"Wrote {_TMP_AUTH_NEEDED} from _refresh failure path")
                except Exception as e2:
                    _debug(f"Failed to write auth_needed: {e2}")
        # Don't re-raise — server stays up, will retry on next request

    def submit_code(self, code: str) -> dict:
        """Submit an authorization code to the running aws login process.

        This can be called directly (e.g., from the management app) without going through HTTP.
        """
        if self._login_process is None or self._login_process.poll() is not None:
            return {"error": "No active login process. Please restart the auth flow."}
        try:
            if self._login_process.stdin:
                self._login_process.stdin.write(code + "\n")
                self._login_process.stdin.flush()
                print(f"[TokenRefresher] Submitted code to login process.")
                return {"success": True}
            else:
                return {"error": "Login process stdin is not available."}
        except Exception as e:
            print(f"[TokenRefresher] Error submitting code: {e}", file=sys.stderr)
            return {"error": str(e)}

    def _register_auth_endpoint(self):
        """Register /auth/status and /auth/submit-code endpoints on LiteLLM's FastAPI app."""
        try:
            from litellm.proxy.proxy_server import app
            from fastapi.responses import JSONResponse
            from fastapi import Body

            _debug("_register_auth_endpoint(): Registering /auth/status and /auth/submit-code endpoints")

            @app.get("/auth/status", tags=["Authentication"])
            async def auth_status():
                _debug(f"/auth/status called: needs_login={self._needs_login}, auth_url={'set' if self._auth_url else None}, auth_code={'set' if self._auth_code else None}")
                return JSONResponse({
                    "needs_login": self._needs_login,
                    "auth_url": self._auth_url,
                    "auth_code": self._auth_code,
                    "profile": self._profile,
                })

            @app.post("/auth/submit-code", tags=["Authentication"])
            async def submit_code(code: str = Body(..., embed=True)):
                if self._login_process is None or self._login_process.poll() is not None:
                    return JSONResponse(
                        {"error": "No active login process. Please restart the auth flow."},
                        status_code=400
                    )
                try:
                    if self._login_process.stdin:
                        self._login_process.stdin.write(code + "\n")
                        self._login_process.stdin.flush()
                        print(f"[TokenRefresher] Submitted code to login process.")
                        return JSONResponse({"success": True})
                    else:
                        return JSONResponse(
                            {"error": "Login process stdin is not available."},
                            status_code=500
                        )
                except Exception as e:
                    print(f"[TokenRefresher] Error submitting code: {e}", file=sys.stderr)
                    return JSONResponse({"error": str(e)}, status_code=500)

            print("[TokenRefresher] Registered /auth/status and /auth/submit-code endpoints on LiteLLM proxy.")
        except Exception as e:
            print(f"[TokenRefresher] WARNING: Could not register auth endpoints: {e}", file=sys.stderr)

    def _is_expired_error(self, exception) -> bool:
        error_str = str(exception).lower()
        return "expired" in error_str or "invalid_api_key" in error_str or "security token" in error_str

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if self._needs_login:
            print(f"[TokenRefresher] Authentication required for profile '{self._profile}'. "
                  "Client must re-authenticate.", file=sys.stderr)
        if self._force_refresh or time.time() - self._fetched_at > self.TOKEN_TTL:
            print("[TokenRefresher] Refreshing token before call...")
            try:
                self._refresh()
            except Exception as e:
                print(f"[TokenRefresher] Token refresh failed in pre_call_hook: {e}", file=sys.stderr)
            self._force_refresh = False
        return data

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        """Fires on all LiteLLM failures, including auth errors mapped to APIConnectionError."""
        exception = kwargs.get("exception")
        if exception and self._is_expired_error(exception):
            print(
                f"[TokenRefresher] Detected expired/invalid token via failure log — forcing refresh...\n"
                f"  Error: {exception}"
            )
            self._force_refresh = True
            try:
                self._refresh()
            except Exception as e:
                print(f"[TokenRefresher] Token refresh failed in failure_event hook: {e}", file=sys.stderr)


token_refresher = BedrockTokenRefresher()
_debug(f"Module-level token_refresher instance created: {token_refresher}")
