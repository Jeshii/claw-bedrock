# claw-bedrock

A [LiteLLM](https://docs.litellm.ai/docs/) proxy server for [AWS Bedrock Mantle](https://docs.aws.amazon.com/bedrock/), exposing models via an OpenAI-compatible API. Handles AWS authentication and automatic token refresh automatically.

## How It Works

1. On startup, LiteLLM loads `config.yaml` and initializes the `BedrockTokenRefresher` callback.
2. The refresher checks your AWS session. If expired, it triggers `aws login --remote`, which prints a URL and waits for you to paste back the authorization code shown in the browser.
3. Once authenticated, a short-lived Bedrock bearer token is fetched and injected as `BEDROCK_MANTLE_API_KEY`.
4. Every 55 minutes the token is silently refreshed in the background before any request.

## Prerequisites

- Python 3.12+
- [pipenv](https://pipenv.pypa.io/)
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) (`aws` on your PATH)
- An AWS profile configured in `~/.aws/config` with Bedrock Mantle access

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
export AWS_PROFILE="bedrock-openai20b"         # Your AWS profile name
export AWS_REGION="ap-northeast-1"             # Your AWS region
export BEDROCK_MANTLE_API_BASE="https://bedrock-mantle.ap-northeast-1.api.aws/v1"  # Update region if needed
```

Then reload your shell:

```bash
source ~/.zshrc
```

### 3. Configure your AWS profile

Ensure `~/.aws/config` has a profile matching `AWS_PROFILE`. Example:

```ini
[profile bedrock-openai20b]
login_session = arn:aws:iam::<account-id>:user/<username>
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

If your AWS session has expired, you will see:

```
[TokenRefresher] AWS session expired. Launching login for profile 'bedrock-openai20b'...
A URL will be printed — open it in any browser, then paste the authorization code back into this terminal.

Using a browser, open: https://device.sso.ap-northeast-1.amazonaws.com/?user_code=XXXX-XXXX
Authorization code: _
```

Open the URL in any browser, approve the login, copy the code shown, paste it back into the terminal, and the server continues automatically.

## SSH Usage

This setup works fully over SSH. `aws login --remote` never opens a browser on the remote machine — it prints a URL you open locally, then prompts for a code you paste back. No display forwarding (`-X`/`-Y`) required.

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

## Files

| File | Purpose |
|---|---|
| `config.yaml` | LiteLLM proxy config — model list and callback registration |
| `token_refresher.py` | LiteLLM callback — handles login and token auto-refresh |
| `policy.json` | Sample IAM policy for Bedrock Mantle access |
| `Pipfile` | Python dependencies |
