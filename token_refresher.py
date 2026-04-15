# token_refresher.py
import os
import time
import subprocess
from litellm.integrations.custom_logger import CustomLogger

class BedrockTokenRefresher(CustomLogger):
    TOKEN_TTL = 3300  # 55 minutes

    def __init__(self):
        self._fetched_at = 0
        self._refresh()

    def _refresh(self):
        token = subprocess.check_output(
            ["aws", "bedrock", "generate-bearer-token", "--output", "text"],
            text=True
        ).strip()
        os.environ["BEDROCK_MANTLE_API_KEY"] = token
        self._fetched_at = time.time()
        print(f"[TokenRefresher] Token refreshed at {time.strftime('%H:%M:%S')}")

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if time.time() - self._fetched_at > self.TOKEN_TTL:
            self._refresh()
        return data

token_refresher = BedrockTokenRefresher()