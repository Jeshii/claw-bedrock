import os
import time
import boto3
from aws_bedrock_token_generator import BedrockTokenGenerator
from litellm.integrations.custom_logger import CustomLogger

class BedrockTokenRefresher(CustomLogger):
    TOKEN_TTL = 3300  # 55 min

    def __init__(self):
        self._fetched_at = 0
        self._generator = BedrockTokenGenerator()
        # Fallback to ap-northeast-1 if AWS_REGION isn't set in the environment
        self._region = os.environ.get("AWS_REGION", "ap-northeast-1")
        self._refresh()

    def _refresh(self):
        # Explicitly pass the region to boto3 to prevent NoRegionError
        session = boto3.Session(region_name=self._region)
        credentials = session.get_credentials()
        
        # Make sure credentials actually exist
        if not credentials:
            raise ValueError("Could not find AWS credentials. Check your ~/.aws/credentials or env vars.")

        token = self._generator.get_token(credentials, self._region)
        os.environ["BEDROCK_MANTLE_API_KEY"] = token
        self._fetched_at = time.time()
        print(f"[TokenRefresher] Token refreshed at {time.strftime('%H:%M:%S')}")

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if time.time() - self._fetched_at > self.TOKEN_TTL:
            self._refresh()
        return data

token_refresher = BedrockTokenRefresher()