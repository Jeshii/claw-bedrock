import os
import subprocess
import sys
import time

import boto3
from aws_bedrock_token_generator import BedrockTokenGenerator
from litellm.integrations.custom_logger import CustomLogger


class BedrockTokenRefresher(CustomLogger):
    TOKEN_TTL = 2700  # 45 min — refresh before AWS tokens expire

    def __init__(self):
        self._fetched_at = 0
        self._force_refresh = False
        self._generator = BedrockTokenGenerator()
        self._region = os.environ.get("AWS_REGION", "ap-northeast-1")
        self._profile = os.environ.get("AWS_PROFILE", "bedrock-openai20b")
        self._refresh()

    def _ensure_login(self):
        """Trigger aws login --remote for SSH-safe authentication.

        Prints a URL to open in any browser, then prompts for the
        authorization code displayed after approving in the browser.
        Works over SSH with no display forwarding required.
        """
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
            session = boto3.Session(profile_name=self._profile, region_name=self._region)
            # Verify credentials are now valid after login
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
        credentials = session.get_credentials()
        token = self._generator.get_token(credentials, self._region)
        os.environ["BEDROCK_MANTLE_API_KEY"] = token
        self._fetched_at = time.time()
        print(f"[TokenRefresher] Token refreshed at {time.strftime('%H:%M:%S')}")

    def _is_expired_error(self, exception) -> bool:
        error_str = str(exception).lower()
        return "expired" in error_str or "invalid_api_key" in error_str or "security token" in error_str

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if self._force_refresh or time.time() - self._fetched_at > self.TOKEN_TTL:
            print("[TokenRefresher] Refreshing token before call...")
            self._refresh()
            self._force_refresh = False
        return data

    async def async_post_call_failure_hook(self, request_data, original_exception, user_api_key_dict):
        if self._is_expired_error(original_exception):
            print(
                f"[TokenRefresher] Detected expired/invalid token in error response — forcing refresh...\n"
                f"  Error: {original_exception}"
            )
            self._force_refresh = True
            self._refresh()
            raise original_exception


token_refresher = BedrockTokenRefresher()
