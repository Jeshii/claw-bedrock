# claw-bedrock

A [LiteLLM](https://docs.litellm.ai/docs/) proxy server for [AWS Bedrock Mantle](https://docs.aws.amazon.com/bedrock/), exposing models via an OpenAI-compatible API. Handles AWS SSO authentication and automatic token refresh automatically.

## How It Works

1. On startup, LiteLLM loads `config.yaml` and initializes the `BedrockTokenRefresher` callback.
2. The refresher checks your AWS SSO session. If expired, it triggers `aws sso login --no-browser` and prints a URL for you to authenticate in any browser (including over SSH).
3. Once authenticated, a short-lived Bedrock bearer token is fetched and injected as `BEDROCK_MANTLE_API_KEY`.
4. Every 55 minutes the token is silently refreshed in the background before any request.

## Prerequisites

- Python 3.12+
- [pipenv](https://pipenv.pypa.io/)
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) (`aws` on your PATH)
- An AWS SSO profile configured in `~/.aws/config` with Bedrock Mantle access

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/Jeshii/claw-bedrock.git
cd claw-bedrock
pipenv install
```

### 2. Configure environment variables

Add the following to your `~/.zshrc` (or `~/.bashrc`):

```bash
export AWS_PROFILE="bedrock-openai20b"         # Your AWS SSO profile name
export AWS_REGION="ap-northeast-1"             # Your AWS region
export BEDROCK_MANTLE_API_BASE="https://bedrock-mantle.ap-northeast-1.api.aws/v1"  # Update region if needed
```

Then reload your shell:

```bash
source ~/.zshrc
```

### 3. Configure your AWS SSO profile

Ensure `~/.aws/config` has an SSO profile matching `AWS_PROFILE`. Example:

```ini
[profile bedrock-openai20b]
sso_start_url = https://your-sso-portal.awsapps.com/start
sso_region = ap-northeast-1
sso_account_id = 123456789012
sso_role_name = BedrockAccess
region = ap-northeast-1
```

### 4. (Optional) Attach the IAM policy

A sample IAM policy is provided in [`policy.json`](./policy.json) granting the minimum permissions required:
- Short-term bearer token usage for Bedrock Mantle
- Model discovery (`ListModels`, `GetModel`)
- Inference (`CreateInference`)

## Running the Server

```bash
pipenv run litellm --config config.yaml --port 4000
```

If your AWS SSO session has expired, you will see:

```
[TokenRefresher] AWS session expired. Launching SSO login for profile 'bedrock-openai20b'...
Open the printed URL in any browser to authenticate.

Using a browser, open: https://device.sso.ap-northeast-1.amazonaws.com/?user_code=XXXX-XXXX
```

Open the URL in any browser (works over SSH), approve the login, and the server will continue starting up automatically.

## Available Models

| Model name | Underlying model |
|---|---|
| `gpt-20b` | `openai.gpt-oss-20b` |
| `gpt-120b` | `openai.gpt-oss-120b` |
| `deepseek-v3` | `deepseek.v3.2` |
| `qwen-coder` | `qwen.qwen3-coder-30b-a3b-instruct` |

## Using the API

The server exposes an OpenAI-compatible API on port 4000. Point any OpenAI-compatible client at `http://localhost:4000`.

```bash
# List available models
curl http://localhost:4000/models

# Example chat completion
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-120b",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## SSH Usage

This setup works over SSH. The `--no-browser` flag means `aws sso login` prints a URL instead of opening a browser. Copy the URL from your terminal and open it locally.

## Files

| File | Purpose |
|---|---|
| `config.yaml` | LiteLLM proxy config — model list and callback registration |
| `token_refresher.py` | LiteLLM callback — handles SSO login and token auto-refresh |
| `bedrock_login.py` | Standalone script to verify AWS auth and manually refresh token |
| `policy.json` | Sample IAM policy for Bedrock Mantle access |
| `Pipfile` | Python dependencies |
