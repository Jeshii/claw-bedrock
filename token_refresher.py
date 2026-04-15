import os
import subprocess
import sys
import time

import boto3
from aws_bedrock_token_generator import BedrockTokenGenerator
from litellm.integrations.custom_logger import CustomLogger


class BedrockTokenRefresher(CustomLogger):
    TOKEN_TTL = 3300  # 55 min

    def __init__(self):
        self._fetched_at = 0
        self._generator = BedrockTokenGenerator()
        self._region = os.environ.get("AWS_REGION", "ap-northeast-1")
        self._profile = os.environ.get("AWS_PROFILE", "bedrock-openai20b")
        self._refresh()

    def _ensure_sso_login(self):
        """Trigger aws sso login --no-browser if the session appears expired."""
        print(
            f"[TokenRefresher] AWS session expired or missing. "
            f"Launching SSO login for profile '{self._profile}'...\n"
            f"Open the printed URL in any browser to authenticate."
        )
        try:
            subprocess.run(
                ["aws", "sso", "login", "--profile", self._profile, "--no-browser"],
                check=True,
            )
        except FileNotFoundError:
            print("[TokenRefresher] ERROR: 'aws' CLI not found. Is it installed and on PATH?", file=sys.stderr)
            raise
        except subprocess.CalledProcessError as e:
            print(f"[TokenRefresher] ERROR: aws sso login failed (exit {e.returncode}).", file=sys.stderr)
            raise

    def _get_valid_session(self) -> boto3.Session:
        """Return a boto3 Session with valid credentials, triggering SSO login if needed."""
        session = boto3.Session(profile_name=self._profile, region_name=self._region)
        credentials = session.get_credentials()

        if credentials is None:
            self._ensure_sso_login()
            session = boto3.Session(profile_name=self._profile, region_name=self._region)
            credentials = session.get_credentials()
            if credentials is None:
                raise RuntimeError(
                    "Could not obtain AWS credentials even after SSO login. "
                    "Check your AWS config and profile name."
                )
            return session

        # Attempt to resolve credentials to catch expired SSO tokens early
        try:
            credentials.get_frozen_credentials()
        except Exception:
            self._ensure_sso_login()
            session = boto3.Session(profile_name=self._profile, region_name=self._region)

        return session

    def _refresh(self):
        session = self._get_valid_session()
        credentials = session.get_credentials()
        token = self._generator.get_token(credentials, self._region)
        os.environ["BEDROCK_MANTLE_API_KEY"] = token
        self._fetched_at = time.time()
        print(f"[TokenRefresher] Token refreshed at {time.strftime('%H:%M:%S')}")

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if time.time() - self._fetched_at > self.TOKEN_TTL:
            self._refresh()
        return data


token_refresher = BedrockTokenRefresher()
