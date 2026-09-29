import asyncio
import os
import subprocess
import sys
import threading
import time
import traceback

import boto3
from aws_bedrock_token_generator import BedrockTokenGenerator
from litellm.integrations.custom_logger import CustomLogger

# Debug log file for TokenRefresher - bypasses stdout redirection
_DEBUG_LOG = "/tmp/token_refresher_debug.log"
_CODE_VERSION = (
    "2025-05-05-v2"  # Update this when making changes to verify code is reloaded
)


def _debug(msg):
    """Write debug message to a file to bypass stdout redirection."""
    try:
        with open(_DEBUG_LOG, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:  # noqa: BLE001, S110
        # Debug logging is best-effort and is itself called from error handlers;
        # raising here would mask the original failure. Never log from the handler.
        pass


_debug(f"Module loaded. Version={_CODE_VERSION}, Python path: {sys.path[:3]}")

_TMP_AUTH_URL = "/tmp/auth_url"
_TMP_AUTH_NEEDED = "/tmp/auth_needed"


def _secure_write(path: str, data: str) -> None:
    """Write data to a file with restrictive permissions (owner-only read/write).

    Uses O_NOFOLLOW to prevent symlink attacks and O_CREAT|O_WRONLY|O_TRUNC
    to atomically create or replace the file.
    """
    import stat

    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        stat.S_IRUSR | stat.S_IWUSR,  # 0o600
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
    except Exception:
        os.close(fd)
        raise


def _write_auth_tmp(url: str):
    """Write auth URL and auth_needed flag to /tmp for management UI."""
    _debug(f"_write_auth_tmp() called. url={url[:50] if url else None}")
    try:
        _secure_write(_TMP_AUTH_URL, url)
        _secure_write(_TMP_AUTH_NEEDED, "1")
        _debug(f"Wrote {_TMP_AUTH_URL} and {_TMP_AUTH_NEEDED}")
    except Exception as e:  # noqa: BLE001 - auth tmp files are best-effort
        _debug(f"WARNING: Could not write auth tmp files: {e}")
        print(
            f"[TokenRefresher] WARNING: Could not write auth tmp files: {e}",
            file=sys.stderr,
        )


def _clear_auth_tmp():
    """Remove /tmp auth files once login completes."""
    for path in (_TMP_AUTH_URL, _TMP_AUTH_NEEDED):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001 - auth tmp cleanup is best-effort
            print(
                f"[TokenRefresher] WARNING: Could not remove {path}: {e}",
                file=sys.stderr,
            )


class BedrockTokenRefresher(CustomLogger):
    TOKEN_TTL = 2700  # 45 min — refresh before AWS tokens expire
    LOGIN_RETRY_COOLDOWN = 60  # seconds between login retries after failure
    # Coalescing window. Without it, N concurrent auth failures each run a
    # refresh: with no lock that meant six token fetches in fifteen seconds.
    # One refresh satisfies the whole burst, and this bounds how soon the next
    # one may start.
    REFRESH_COALESCE_SECONDS = 5

    # Auth-specific error shapes, matched outright. Deliberately not just the
    # word "expired": see _is_expired_error.
    AUTH_ERROR_PATTERNS = (
        "api key expired",
        "expiredtoken",
        "expired token",
        "token has expired",
        "token is expired",
        "the security token",
        "invalid_api_key",
        "invalid api key",
        "unrecognizedclientexception",
        "invalidtoken",
        "request expired",
    )
    # Words that make "expired" meaningful — a credential, as opposed to any
    # old thing that can expire.
    AUTH_CONTEXT_WORDS = (
        "token",
        "api key",
        "apikey",
        "credential",
        "signature",
        "session",
    )
    EXPIRY_WORDS = ("expired", "expiry")

    def __init__(self):
        _debug(
            f"BedrockTokenRefresher.__init__() called. Profile={os.environ.get('AWS_PROFILE', 'bedrock-openai20b')}"
        )
        self._fetched_at = 0
        self._force_refresh = False
        # Serializes refreshes and coalesces concurrent callers. Both are needed:
        # the lock stops interleaved writes to os.environ, the coalescing window
        # stops N waiters each doing redundant work.
        self._refresh_lock = threading.Lock()
        self._last_refresh_attempt = 0.0
        self._needs_login = (
            False  # set True when login required in non-interactive mode
        )
        self._auth_url: str | None = (
            None  # captured aws sso login --no-browser URL for web UI
        )
        self._auth_error: str | None = None  # captured auth error message for UI
        self._login_process: subprocess.Popen | None = (
            None  # background aws login process
        )
        self._awaiting_code = (
            False  # True when CLI is waiting for authorization code on stdin
        )
        self._login_failed_time = 0  # timestamp of last login failure
        self._login_error = None  # error message from last failure
        self._generator = BedrockTokenGenerator()
        self._region = os.environ.get("AWS_REGION", "ap-northeast-1")
        self._profile = os.environ.get("AWS_PROFILE", "bedrock-openai20b")
        # Long-lived IAM key pair, when one is configured. Empty means "use the
        # profile", which is the SSO path. See _build_session for why passing a
        # profile and also expecting env credentials cannot both work.
        self._access_key_id = ""
        self._secret_access_key = ""
        # Attempt startup refresh — if it fails, wait for user to initiate login via web UI
        self._refresh()
        self._register_auth_endpoint()

    def configure(
        self,
        region: str | None = None,
        profile: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> bool:
        """Re-resolve auth settings and refresh, without restarting anything.

        Called by the management UI when the Bedrock settings card is saved.
        Previously, changing any of these meant editing the unit file and
        restarting the container.

        Returns True if a token was minted with the new settings. False is a
        normal outcome rather than an error: a wrong region or a bad key fails
        at token-mint time, and the caller reports that instead of pretending
        the save applied.
        """
        _debug(
            f"configure() region={region!r} profile={profile!r} "
            f"key_pair={'yes' if access_key_id and secret_access_key else 'no'}"
        )
        changed = (
            (region is not None and region != self._region)
            or (profile is not None and profile != self._profile)
            or (access_key_id or "") != self._access_key_id
            or (secret_access_key or "") != self._secret_access_key
        )
        if region is not None:
            self._region = region
        if profile is not None:
            self._profile = profile
        self._access_key_id = access_key_id or ""
        self._secret_access_key = secret_access_key or ""

        if not changed:
            _debug("configure(): no effective change, skipping forced refresh")
            return bool(self._fetched_at)

        # Credentials changed, so a token minted against the old ones is no
        # longer trustworthy. Drop it before refreshing, so a failed refresh
        # cannot leave the previous token in place looking current.
        self._fetched_at = 0
        self._needs_login = False
        self._auth_error = None
        self._auth_url = None
        # The coalescing window exists to collapse a burst of failures. Here we
        # are the deliberate trigger, so a recent failed attempt must not
        # absorb the refresh we were explicitly asked for.
        return self._refresh(force=True, coalesce=False)

    def _is_interactive(self) -> bool:
        result = sys.stdin.isatty()
        _debug(f"_is_interactive() -> {result}")
        return result

    def _capture_auth_url_from_process(self, proc: subprocess.Popen):
        """Read stdout from aws login --remote in a background thread.

        The command outputs:
            <blank line>
            Please visit the following URL:
            https://ap-northeast-1.signin.aws.amazon.com/v1/authorize?...
            Enter the authorization code displayed in user's browser: <waits for stdin>

        We capture the URL for the web UI. The CLI then blocks waiting for the
        authorization code on stdin, which the user submits via the web UI.
        """
        _debug(
            f"_capture_auth_url_from_process() called. proc.pid={proc.pid if proc else 'None'}"
        )

        def _read():
            print(
                f"[TokenRefresher] DEBUG: _read thread started. proc.pid={proc.pid}",
                flush=True,
            )
            try:
                line_count = 0
                buffer = ""
                while True:
                    char = proc.stdout.read(1)
                    if not char:
                        break
                    buffer += char
                    if char == "\n":
                        line = buffer.strip()
                        buffer = ""
                        line_count += 1
                        print(
                            f"[TokenRefresher] DEBUG: stdout line #{line_count}: {line!r}",
                            flush=True,
                        )

                        if line.startswith("https://") and self._auth_url is None:
                            self._auth_url = line
                            _write_auth_tmp(line)
                            print(
                                f"[TokenRefresher] Auth URL captured for web UI: {self._auth_url}"
                            )

                    # Detect prompt in buffer (may not have newline yet)
                    buffer_lower = buffer.lower()
                    if (
                        "enter the authorization code displayed in your browser:"
                        in buffer_lower
                        or (
                            "authorization code" in buffer_lower
                            and "browser" in buffer_lower
                        )
                    ) and not self._awaiting_code:
                        self._awaiting_code = True
                        _debug(
                            f"CLI is awaiting authorization code on stdin (buffer={buffer!r})"
                        )

                print(
                    f"[TokenRefresher] DEBUG: stdout loop exhausted after {line_count} lines.",
                    flush=True,
                )
                print("[TokenRefresher] DEBUG: calling proc.wait()...", flush=True)
                proc.wait()
                print(
                    f"[TokenRefresher] DEBUG: proc.wait() returned. returncode={proc.returncode}",
                    flush=True,
                )
                self._awaiting_code = False
                if proc.returncode == 0:
                    print("[TokenRefresher] AWS login completed — refreshing token...")
                    self._needs_login = False
                    self._auth_url = None
                    self._login_process = None
                    _clear_auth_tmp()
                    try:
                        # Credentials just changed, so this must not be
                        # coalesced away by a recent failed attempt.
                        self._refresh(force=True, coalesce=False)
                    except Exception as e:  # noqa: BLE001 - refresh failure is logged and retried
                        print(
                            f"[TokenRefresher] WARNING: Token refresh after login failed: {e}",
                            file=sys.stderr,
                        )
                else:
                    error_msg = f"aws login --remote exited with code {proc.returncode}"
                    print(
                        f"[TokenRefresher] {error_msg}. "
                        "Login did not complete — auth still required.",
                        file=sys.stderr,
                    )
                    self._login_failed_time = time.time()
                    self._login_error = error_msg
                    self._auth_url = None
                    # Remove stale auth_url file so UI doesn't show old URL
                    try:
                        os.remove(_TMP_AUTH_URL)
                    except FileNotFoundError:
                        pass
                    self._login_process = None
            except Exception as e:  # noqa: BLE001 - login output read failure is logged
                print(
                    f"[TokenRefresher] Error reading aws login output: {e}",
                    file=sys.stderr,
                )
                traceback.print_exc(file=sys.stderr)
                self._awaiting_code = False
                self._login_process = None

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        print(
            f"[TokenRefresher] DEBUG: _read thread launched. thread.is_alive={t.is_alive()}",
            flush=True,
        )

    def _ensure_login(self):
        """Launch aws login --remote for headless authentication.

        Starts aws login --remote in the background, captures the URL,
        sets _needs_login, and surfaces it via /auth/status.
        The CLI handles the OAuth callback internally — no stdin input needed.
        """
        # Only start one login process at a time
        if self._login_process is not None and self._login_process.poll() is None:
            _debug("aws login already running, skipping duplicate launch.")
            return

        # Cooldown: don't retry too soon after failure
        if self._login_failed_time > 0:
            elapsed = time.time() - self._login_failed_time
            if elapsed < self.LOGIN_RETRY_COOLDOWN:
                _debug(
                    f"Login cooldown: {elapsed:.0f}s < {self.LOGIN_RETRY_COOLDOWN}s, skipping"
                )
                return
            # Cooldown expired, reset failure state
            self._login_failed_time = 0
            self._login_error = None

        _debug(f"_ensure_login() launching login. profile={self._profile}")
        print(
            f"[TokenRefresher] AWS session expired or missing for profile '{self._profile}'. "
            f"Starting aws login --remote for web-based authentication.",
            file=sys.stderr,
        )
        self._needs_login = True

        try:
            with open(_TMP_AUTH_NEEDED, "w") as f:
                f.write("1")
            _debug(f"Wrote {_TMP_AUTH_NEEDED} file")
        except Exception as e:  # noqa: BLE001 - auth_needed flag is best-effort
            _debug(f"WARNING: Could not write auth_needed file: {e}")

        try:
            _debug(
                f"Starting aws command: aws login --remote --profile {self._profile}"
            )
            proc = subprocess.Popen(
                ["aws", "login", "--remote", "--profile", self._profile],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
            )
            _debug(f"aws login --remote process started with pid={proc.pid}")
            self._login_process = proc
            self._capture_auth_url_from_process(proc)
        except FileNotFoundError:
            _debug("ERROR: 'aws' CLI not found")
            self._auth_error = "'aws' CLI not found. Is it installed and on PATH?"
            print(f"[TokenRefresher] ERROR: {self._auth_error}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - launch failure surfaces via _auth_error
            _debug(f"ERROR: failed to launch aws login: {e}")
            self._auth_error = f"Failed to launch aws login: {e}"
            print(f"[TokenRefresher] ERROR: {self._auth_error}", file=sys.stderr)

    def retry_login(self):
        """Reset failure state and start a new login process."""
        _debug("retry_login() called")
        self._login_failed_time = 0
        self._login_error = None
        self._auth_url = None
        self._awaiting_code = False
        # The previous attempt's error is about that attempt. Clearing it here
        # keeps peek_auth_error() safe to poll — see the note on that method.
        self._auth_error = None
        if self._login_process is not None:
            try:
                self._login_process.kill()
            except Exception:  # noqa: BLE001, S110
                # The process may already have exited; killing it is best-effort
                # and must not block a retry.
                pass
            self._login_process = None
        self._ensure_login()

    def _get_valid_session(self) -> boto3.Session | None:
        """Return a boto3 Session with valid credentials.
        Returns None if login is required — server stays up, user authenticates via web UI.
        """
        if self._needs_login:
            return None
        try:
            session = self._build_session()
            credentials = session.get_credentials()
        except Exception as e:  # noqa: BLE001 - no usable session; caller triggers login
            _debug(f"Session creation failed: {e}")
            self._needs_login = True
            self._write_auth_needed_flag()
            return None
        if credentials is None:
            _debug("No credentials found — login required")
            self._needs_login = True
            self._write_auth_needed_flag()
            return None
        return session

    def _build_session(self) -> boto3.Session:
        """Construct the boto3 session for the configured credential source.

        `profile_name` is the pivot. botocore sets `disable_env_vars` whenever a
        profile is passed explicitly (`credentials.py:95`), which removes the
        EnvProvider from the credential chain — so passing a profile made
        AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY set-but-inert. The non-SSO path
        was therefore broken for every install, and the only way to reach env
        credentials was to omit the profile entirely.

        With a key pair configured we pass the credentials directly and omit the
        profile, which both honours the keys and leaves botocore's env, IMDS and
        container providers in the chain. With no key pair we pass the profile,
        because that is the SSO path the login flow drives.
        """
        if self._access_key_id and self._secret_access_key:
            _debug(
                "_build_session(): using configured key pair, omitting profile_name "
                "so botocore honours them and the env provider"
            )
            return boto3.Session(
                aws_access_key_id=self._access_key_id,
                aws_secret_access_key=self._secret_access_key,
                region_name=self._region,
            )
        return boto3.Session(profile_name=self._profile, region_name=self._region)

    def _write_auth_needed_flag(self):
        """Write the auth_needed flag file if it doesn't already exist."""
        try:
            if not os.path.exists(_TMP_AUTH_NEEDED):
                with open(_TMP_AUTH_NEEDED, "w") as f:
                    f.write("1")
        except Exception as e:  # noqa: BLE001 - auth_needed flag is best-effort
            _debug(f"Failed to write auth_needed: {e}")

    def _refresh(self, force: bool = False, coalesce: bool = True) -> bool:
        """Refresh BEDROCK_MANTLE_API_KEY. Returns True if a token was fetched.

        `force` skips the TTL check, for callers that have just seen a real auth
        failure. `coalesce` makes a refresh that started within
        REFRESH_COALESCE_SECONDS satisfy the caller instead of redoing the work,
        so a burst of failures costs one token fetch rather than one per failure.
        Callers that must refresh regardless pass coalesce=False — notably the
        refresh after a completed login, where a recent failed attempt would
        otherwise be coalesced away and leave a stale token in place.
        """
        _debug(
            f"_refresh(force={force}, coalesce={coalesce}) called. "
            f"_needs_login={self._needs_login}"
        )
        with self._refresh_lock:
            # Re-checked under the lock: a concurrent caller may have refreshed
            # while this one waited, which is the common case during a burst.
            if coalesce and self._recently_attempted():
                _debug("_refresh(): coalesced with a recent refresh, skipping")
                return False
            if not force and time.time() - self._fetched_at <= self.TOKEN_TTL:
                _debug("_refresh(): token still within TTL, skipping")
                return False
            # Stamped before the work so callers arriving mid-flight coalesce
            # too. Set even if the attempt then fails, which also throttles
            # retries against a broken auth path.
            self._last_refresh_attempt = time.time()
            return self._refresh_locked()

    def _recently_attempted(self) -> bool:
        return time.time() - self._last_refresh_attempt < self.REFRESH_COALESCE_SECONDS

    def _refresh_locked(self) -> bool:
        """Do the actual token fetch. Caller must hold _refresh_lock."""
        session = self._get_valid_session()
        _debug(
            f"_refresh(): _get_valid_session returned {type(session).__name__ if session else None}"
        )
        if session is None:
            return False  # login required — server stays up, /auth/status will surface the URL
        try:
            credentials = session.get_credentials()
            _debug(
                f"_refresh(): got credentials: {type(credentials).__name__ if credentials else None}"
            )
            token = self._generator.get_token(credentials, self._region)
            os.environ["BEDROCK_MANTLE_API_KEY"] = token
            self._fetched_at = time.time()
            print(f"[TokenRefresher] Token refreshed at {time.strftime('%H:%M:%S')}")
            # Clear auth_needed flag on successful token refresh
            self._needs_login = False
            self._auth_url = None
            # A successful refresh means the previous failure is resolved. Without
            # this, peek_auth_error() would surface a stale error indefinitely.
            self._auth_error = None
            try:
                if os.path.exists(_TMP_AUTH_NEEDED):
                    os.remove(_TMP_AUTH_NEEDED)
                _clear_auth_tmp()
                _debug("Cleared auth_needed flag - token refresh successful")
            except Exception as e:  # noqa: BLE001 - cleanup failure must not mask the error
                _debug(f"Error clearing auth tmp files: {e}")
            return True
        except Exception as e:  # noqa: BLE001 - token failure is reported to the user
            _debug(f"Token generation failed: {e}")
            print(f"[TokenRefresher] Token generation failed: {e}", file=sys.stderr)
            if not self._is_interactive():
                self._needs_login = True
                self._write_auth_needed_flag()
            return False
        # Don't re-raise — server stays up, will retry on next request

    def peek_auth_error(self) -> str | None:
        """Return the current auth error without clearing it.

        The auth UI polls this state every second during a login flow, so a
        read-and-clear accessor here means an error is consumed by a poll before
        anyone sees it. The error is instead cleared on a successful token
        refresh and at the start of a new login attempt, which are the only two
        points where it genuinely stops being true.
        """
        return self._auth_error

    def auth_status_payload(self) -> dict:
        """The auth state shared by the management app and the proxy route.

        Both endpoints previously built this by hand and disagreed: the proxy
        read `_auth_error` directly, the management app drained it, and only the
        proxy reported `profile`.
        """
        now = time.time()
        fetched_at = self._fetched_at
        has_token = fetched_at > 0
        age = int(now - fetched_at) if has_token else None
        return {
            "needs_login": self._needs_login,
            "auth_url": self._auth_url,
            "awaiting_code": self._awaiting_code,
            "auth_error": self._auth_error,
            "profile": self._profile,
            "region": self._region,
            # Reported as a boolean only. An AWS access key ID is half a
            # credential — the prefix alone identifies the account and key
            # vintage — so unlike the locally generated LiteLLM master key,
            # there is nothing to gain from revealing any of it.
            "static_keys": {
                "configured": bool(self._access_key_id and self._secret_access_key),
                "source": "config"
                if (self._access_key_id and self._secret_access_key)
                else None,
            },
            "token": {
                "present": has_token,
                "age_seconds": age,
                "ttl_seconds": self.TOKEN_TTL,
                "expires_in": max(0, self.TOKEN_TTL - age) if has_token else None,
                "stale": (not has_token) or age > self.TOKEN_TTL,
            },
        }

    def submit_code(self, code: str) -> dict:
        """Submit an authorization code to the running aws login process.

        The code is the value displayed in the browser after the user authorizes.
        If the user pastes a full URL, we extract the code parameter from it.
        """
        if self._login_process is None or self._login_process.poll() is not None:
            return {"error": "No active login process. Please restart the auth flow."}
        # If the user pasted a full URL, extract the code= parameter
        extracted = code
        if "code=" in code:
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(code) if "://" in code else None
            if parsed:
                qs = parse_qs(parsed.query)
            else:
                qs = parse_qs(code)
            codes = qs.get("code", [])
            if codes:
                extracted = codes[0]
                _debug(f"Extracted code from URL: {extracted[:20]}...")
        try:
            if self._login_process.stdin:
                self._login_process.stdin.write(extracted + "\n")
                self._login_process.stdin.flush()
                self._awaiting_code = False
                print("[TokenRefresher] Submitted code to login process.")
                return {"success": True}
            else:
                return {"error": "Login process stdin is not available."}
        except Exception as e:  # noqa: BLE001 - returned to the web UI as an error
            print(f"[TokenRefresher] Error submitting code: {e}", file=sys.stderr)
            return {"error": str(e)}

    def _register_auth_endpoint(self):
        """Register /auth/status and /auth/submit-code endpoints on LiteLLM's FastAPI app."""
        try:
            from fastapi import Body
            from fastapi.responses import JSONResponse
            from litellm.proxy.proxy_server import app

            _debug(
                "_register_auth_endpoint(): Registering /auth/status and /auth/submit-code endpoints"
            )

            @app.get("/auth/status", tags=["Authentication"])
            async def auth_status():
                _debug(
                    f"/auth/status called: needs_login={self._needs_login}, auth_url={'set' if self._auth_url else None}, awaiting_code={self._awaiting_code}"
                )
                return JSONResponse(self.auth_status_payload())

            @app.post("/auth/submit-code", tags=["Authentication"])
            async def submit_code(code: str = Body(..., embed=True)):
                if (
                    self._login_process is None
                    or self._login_process.poll() is not None
                ):
                    return JSONResponse(
                        {
                            "error": "No active login process. Please restart the auth flow."
                        },
                        status_code=400,
                    )
                try:
                    if self._login_process.stdin:
                        self._login_process.stdin.write(code + "\n")
                        self._login_process.stdin.flush()
                        self._awaiting_code = False
                        print("[TokenRefresher] Submitted code to login process.")
                        return JSONResponse({"success": True})
                    else:
                        return JSONResponse(
                            {"error": "Login process stdin is not available."},
                            status_code=500,
                        )
                except Exception as e:  # noqa: BLE001 - returned to the web UI as an error
                    print(
                        f"[TokenRefresher] Error submitting code: {e}", file=sys.stderr
                    )
                    return JSONResponse({"error": str(e)}, status_code=500)

            print(
                "[TokenRefresher] Registered /auth/status and /auth/submit-code endpoints on LiteLLM proxy."
            )
        except Exception as e:  # noqa: BLE001 - hook registration is best-effort
            print(
                f"[TokenRefresher] WARNING: Could not register auth endpoints: {e}",
                file=sys.stderr,
            )

    def _is_expired_error(self, exception) -> bool:
        """True when an error plausibly means our bearer token is stale.

        Matching the bare word "expired" is too broad — it also fires on
        "the model 'x' has expired" and on unrelated non-auth errors, so the
        refresher burns token fetches on failures a new token cannot fix. Match
        the known auth shapes outright, otherwise require expiry *and* some
        credential context.
        """
        error_str = str(exception).lower()
        if any(p in error_str for p in self.AUTH_ERROR_PATTERNS):
            return True
        has_expiry = any(w in error_str for w in self.EXPIRY_WORDS)
        has_auth = any(w in error_str for w in self.AUTH_CONTEXT_WORDS)
        return has_expiry and has_auth

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if self._needs_login:
            print(
                f"[TokenRefresher] Authentication required for profile '{self._profile}'. "
                "Client must re-authenticate.",
                file=sys.stderr,
            )
        if self._force_refresh or time.time() - self._fetched_at > self.TOKEN_TTL:
            print("[TokenRefresher] Refreshing token before call...")
            try:
                # Off the event loop: credential resolution can be slow enough
                # to stall every concurrent request.
                await asyncio.to_thread(self._refresh, self._force_refresh)
            except Exception as e:  # noqa: BLE001 - hook must not break the request
                print(
                    f"[TokenRefresher] Token refresh failed in pre_call_hook: {e}",
                    file=sys.stderr,
                )
            self._force_refresh = False
        return data

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        """Fires on all LiteLLM failures, including auth errors mapped to APIConnectionError.

        Only raises a flag — the refresh happens in the next pre-call hook.
        Refreshing here blocked the event loop, and having both hooks refresh
        for a single failure is what turned one expiry into a stampede.
        """
        exception = kwargs.get("exception")
        if exception and self._is_expired_error(exception):
            print(
                f"[TokenRefresher] Detected expired/invalid token via failure log — will refresh before next call...\n"
                f"  Error: {exception}"
            )
            self._force_refresh = True


token_refresher = BedrockTokenRefresher()
_debug(f"Module-level token_refresher instance created: {token_refresher}")
