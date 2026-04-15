import asyncio
import os
import subprocess
import sys
import time

import boto3
from aws_bedrock_token_generator import BedrockTokenGenerator
from litellm.integrations.custom_logger import CustomLogger

_LOGIN_REQUIRED_MSG = (
    "AWS authentication required — the LiteLLM server needs to be restarted "
    "and logged in. Run './start.sh' in the repo directory to re-authenticate."
)


class BedrockTokenRefresher(CustomLogger):
    TOKEN_TTL = 2700  # 45 min — refresh before AWS tokens expire
    EXIT_CODE_LOGIN_REQUIRED = 42  # sentinel: distinguish auth exit from crash
    EXIT_GRACE_SECONDS = 5  # give in-flight requests time to return the error

    def __init__(self):
        self._fetched_at = 0
        self._force_refresh = False
        self._needs_login = False  # set True when login required in non-interactive mode
        self._generator = BedrockTokenGenerator()
        self._region = os.environ.get("AWS_REGION", "ap-northeast-1")
        self._profile = os.environ.get("AWS_PROFILE", "bedrock-openai20b")
        self._refresh()

    def _is_interactive(self) -> bool:
        return sys.stdin.isatty()

    def _schedule_exit(self):
        """Exit after a grace period so in-flight requests can return their error first."""
        def _do_exit():
            time.sleep(self.EXIT_GRACE_SECONDS)
            print(
                f"[TokenRefresher] Exiting with code {self.EXIT_CODE_LOGIN_REQUIRED} "
                f"— AWS login required. Restart start.sh to re-authenticate.",
                file=sys.stderr,
            )
            os._exit(self.EXIT_CODE_LOGIN_REQUIRED)

        import threading
        t = threading.Thread(target=_do_exit, daemon=True)
        t.start()

    def _ensure_login(self):
        """Trigger aws login --remote for SSH-safe authentication.

        In interactive mode: prints a URL, prompts for authorization code.
        In non-interactive mode: sets _needs_login flag and schedules a clean
        exit so process managers and tools both get a clear signal.
        """
        if not self._is_interactive():
            print(
                f"[TokenRefresher] AWS session expired or missing for profile '{self._profile}'. "
                f"Non-interactive mode — cannot prompt for login.\n"
                f"Flagging auth failure for callers, then exiting in {self.EXIT_GRACE_SECONDS}s.\n"
                f"Run './start.sh' manually to re-authenticate.",
                file=sys.stderr,
            )
            self._needs_login = True
            self._schedule_exit()
            return  # do not attempt interactive aws login

        print(
            f"[TokenRefresher] AWS session expired or missing. "
            f"Launching login for profile '{self._profile}'...\n"
            f"A URL will be printed — open it in any browser, "
            f"then paste the authorization code back into this terminal."
        )
        try:
            subprocess.run(
                ["aws", "login", "--profile", self._profile, "--region", self._region, "--remote"],
                check=True,
            )
        except FileNotFoundError:
            print("[TokenRefresher] ERROR: 'aws' CLI not found. Is it installed and on PATH?", file=sys.stderr)
            raise
        except subprocess.CalledProcessError as e:
            print(f"[TokenRefresher] ERROR: aws login failed (exit {e.returncode}).", file=sys.stderr)
            raise

    def _get_valid_session(self) -> boto3.Session:
        """Return a boto3 Session with valid credentials, triggering login if needed."""
        session = boto3.Session(profile_name=self._profile, region_name=self._region)
        credentials = session.get_credentials()

        if credentials is None:
            self._ensure_login()
            if self._needs_login:
                return session  # will be unusable; pre_call_hook will block callers
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
                return session  # unusable; pre_call_hook will block callers
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
        if self._needs_login:
            return  # don't attempt to generate a token with invalid credentials
        credentials = session.get_credentials()
        token = self._generator.get_token(credentials, self._region)
        os.environ["BEDROCK_MANTLE_API_KEY"] = token
        self._fetched_at = time.time()
        print(f"[TokenRefresher] Token refreshed at {time.strftime('%H:%M:%S')}")

    def _is_expired_error(self, exception) -> bool:
        error_str = str(exception).lower()
        return "expired" in error_str or "invalid_api_key" in error_str or "security token" in error_str

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if self._needs_login:
            raise Exception(_LOGIN_REQUIRED_MSG)
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
