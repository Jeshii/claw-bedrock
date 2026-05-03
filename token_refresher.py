import os
import subprocess
import sys
import time
import threading

import boto3
from aws_bedrock_token_generator import BedrockTokenGenerator
from litellm.integrations.custom_logger import CustomLogger

_TMP_AUTH_URL = "/tmp/auth_url"
_TMP_AUTH_NEEDED = "/tmp/auth_needed"


def _write_auth_tmp(url: str):
    """Write auth URL and auth_needed flag to /tmp for management UI."""
    try:
        with open(_TMP_AUTH_URL, "w") as f:
            f.write(url)
        with open(_TMP_AUTH_NEEDED, "w") as f:
            f.write("1")
    except Exception as e:
        print(f"[TokenRefresher] WARNING: Could not write auth tmp files: {e}", file=sys.stderr)


def _clear_auth_tmp():
    """Remove /tmp auth files once login completes."""
    for path in (_TMP_AUTH_URL, _TMP_AUTH_NEEDED):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[TokenRefresher] WARNING: Could not remove {path}: {e}", file=sys.stderr)


class BedrockTokenRefresher(CustomLogger):
    TOKEN_TTL = 2700  # 45 min — refresh before AWS tokens expire

    def __init__(self):
        self._fetched_at = 0
        self._force_refresh = False
        self._needs_login = False  # set True when login required in non-interactive mode
        self._auth_url: str | None = None  # captured aws sso login --no-browser URL for web UI
        self._login_process: subprocess.Popen | None = None  # background aws login process
        self._generator = BedrockTokenGenerator()
        self._region = os.environ.get("AWS_REGION", "ap-northeast-1")
        self._profile = os.environ.get("AWS_PROFILE", "bedrock-openai20b")
        self._refresh()
        self._register_auth_endpoint()

    def _is_interactive(self) -> bool:
        return sys.stdin.isatty()

    def _capture_auth_url_from_process(self, proc: subprocess.Popen):
        """Read stdout from aws sso login --no-browser in a background thread.

        The command outputs lines like:
            Please visit the following URL:
            https://device.sso.ap-northeast-1.amazonaws.com/
            Then enter the code:
            XXXX-XXXX
            Alternatively, you may visit the following URL which will autofill the code:
            https://device.sso.ap-northeast-1.amazonaws.com/?user_code=XXXX-XXXX

        We capture the autofill URL (contains '?user_code=') as it's directly usable.
        """
        def _read():
            try:
                for line in proc.stdout:
                    line = line.strip()
                    # Prefer the autofill URL with user_code embedded
                    if "user_code=" in line and line.startswith("https://"):
                        self._auth_url = line
                        _write_auth_tmp(line)
                        print(f"[TokenRefresher] Auth URL captured for web UI: {self._auth_url}")
                    elif line.startswith("https://") and self._auth_url is None:
                        # Fallback: first https URL seen (base device auth URL)
                        self._auth_url = line
                        _write_auth_tmp(line)
                        print(f"[TokenRefresher] Auth URL captured for web UI: {self._auth_url}")
                proc.wait()
                if proc.returncode == 0:
                    print("[TokenRefresher] AWS login completed — refreshing token...")
                    self._needs_login = False
                    self._auth_url = None
                    self._login_process = None
                    _clear_auth_tmp()
                    self._refresh()
                else:
                    print(
                        f"[TokenRefresher] aws sso login exited with code {proc.returncode}.",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(f"[TokenRefresher] Error reading aws sso login output: {e}", file=sys.stderr)

        t = threading.Thread(target=_read, daemon=True)
        t.start()

    def _ensure_login(self):
        """Trigger aws sso login --no-browser for headless authentication.

        In interactive mode: falls back to normal aws sso login (opens browser).
        In non-interactive mode: launches aws sso login --no-browser in the background,
        captures the autofill URL, sets _needs_login, and surfaces it via /auth/status.
        """
        if not self._is_interactive():
            print(
                f"[TokenRefresher] AWS session expired or missing for profile '{self._profile}'. "
                f"Non-interactive mode — capturing auth URL for web UI.",
                file=sys.stderr,
            )
            self._needs_login = True

            # Only start one login process at a time
            if self._login_process is not None and self._login_process.poll() is None:
                print("[TokenRefresher] aws sso login already running, skipping duplicate launch.")
                return

            try:
                proc = subprocess.Popen(
                    ["aws", "sso", "login", "--profile", self._profile, "--no-browser"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                self._login_process = proc
                self._capture_auth_url_from_process(proc)
            except FileNotFoundError:
                print("[TokenRefresher] ERROR: 'aws' CLI not found. Is it installed and on PATH?", file=sys.stderr)
            except Exception as e:
                print(f"[TokenRefresher] ERROR: failed to launch aws sso login: {e}", file=sys.stderr)
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
        try:
            session = boto3.Session(profile_name=self._profile, region_name=self._region)
        except Exception:
            print(
                f"[TokenRefresher] AWS profile '{self._profile}' not found. Auth will be required via web UI.",
                file=sys.stderr,
            )
            self._ensure_login()
            return None  # caller must check for None and skip token generation

        credentials = session.get_credentials()

        if credentials is None:
            self._ensure_login()
            if self._needs_login:
                return None
            # Login completed synchronously (interactive mode) — rebuild session
            session = boto3.Session(profile_name=self._profile, region_name=self._region)
            credentials = session.get_credentials()
            if credentials is None:
                raise RuntimeError(
                    "Could not obtain AWS credentials even after login. "
                    "Check your AWS config and profile name."
                )
            return session

        # Attempt to resolve credentials to catch expired tokens early
        try:
            credentials.get_frozen_credentials()
        except Exception:
            self._ensure_login()
            if self._needs_login:
                return None
            session = boto3.Session(profile_name=self._profile, region_name=self._region)
            credentials = session.get_credentials()
            if credentials is None:
                raise RuntimeError(
                    "Could not obtain AWS credentials even after login. "
                    "Check your AWS config and profile name."
                )
            try:
                credentials.get_frozen_credentials()
            except Exception as e:
                raise RuntimeError(f"Credentials still invalid after login: {e}") from e

        return session

    def _refresh(self):
        session = self._get_valid_session()
        if session is None:
            return  # login required — server stays up, /auth/status will surface the URL
        credentials = session.get_credentials()
        token = self._generator.get_token(credentials, self._region)
        os.environ["BEDROCK_MANTLE_API_KEY"] = token
        self._fetched_at = time.time()
        print(f"[TokenRefresher] Token refreshed at {time.strftime('%H:%M:%S')}")

    def _register_auth_endpoint(self):
        """Register GET /auth/status on LiteLLM's FastAPI app."""
        try:
            from litellm.proxy.proxy_server import app
            from fastapi.responses import JSONResponse

            @app.get("/auth/status", tags=["Authentication"])
            async def auth_status():
                return JSONResponse({
                    "needs_login": self._needs_login,
                    "auth_url": self._auth_url,
                    "profile": self._profile,
                })

            print("[TokenRefresher] Registered /auth/status endpoint on LiteLLM proxy.")
        except Exception as e:
            print(f"[TokenRefresher] WARNING: Could not register /auth/status endpoint: {e}", file=sys.stderr)

    def _is_expired_error(self, exception) -> bool:
        error_str = str(exception).lower()
        return "expired" in error_str or "invalid_api_key" in error_str or "security token" in error_str

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if self._needs_login:
            print(f"[TokenRefresher] Authentication required for profile '{self._profile}'. "
                  "Client must re-authenticate.", file=sys.stderr)
        if self._force_refresh or time.time() - self._fetched_at > self.TOKEN_TTL:
            print("[TokenRefresher] Refreshing token before call...")
            self._refresh()
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
            self._refresh()


token_refresher = BedrockTokenRefresher()
