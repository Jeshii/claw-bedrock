# claw-bedrock

A [LiteLLM](https://docs.litellm.ai/docs/) proxy server that started as an [AWS Bedrock Mantle](https://docs.aws.amazon.com/bedrock/) wrapper and is evolving into a general model router — exposing models from multiple providers via a single OpenAI-compatible API. Handles AWS authentication and automatic token refresh automatically.

## How It Works

1. On startup, LiteLLM loads `config.yaml` and initializes the `BedrockTokenRefresher` callback.
2. The refresher checks your AWS session. If expired, it triggers `aws login --remote`, which prints a URL and waits for you to paste back the authorization code shown in the browser.
3. Once authenticated, a short-lived Bedrock bearer token is fetched and injected as `BEDROCK_MANTLE_API_KEY`.
4. Every ?? minutes the token is silently refreshed in the background before any request. (Still working on this part)

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
# Prevent LiteLLM from routing to Anthropic instead of Bedrock
unset ANTHROPIC_API_KEY
unset ANTHROPIC_BASE_URL

# AWS — used by token_refresher.py to authenticate and fetch a bearer token
export AWS_PROFILE="<your-aws-profile-name>"
export AWS_REGION="<your-aws-region>"          # e.g. ap-northeast-1
export BEDROCK_MANTLE_API_BASE="https://bedrock-mantle.<your-aws-region>.api.aws/v1"
# Note: BEDROCK_MANTLE_API_KEY is set automatically at runtime — do not set it here

# Client-side — point any OpenAI-compatible tool at the local proxy
export OPENAI_API_KEY="dummy"                  # LiteLLM requires a non-empty value
export OPENAI_BASE_URL="http://127.0.0.1:4000/v1"

# Optional: OpenRouter (required for non-Bedrock models like elephant-alpha)
export OPENROUTER_API_KEY="<your-openrouter-api-key>"
```

Then reload your shell:

```bash
source ~/.zshrc
```

> **`OPENAI_API_KEY` / `OPENAI_BASE_URL`** — Setting these globally means any OpenAI-compatible client (Cursor, Continue, shell scripts using the `openai` SDK) will automatically use the local proxy without additional configuration.

### 3. Configure your AWS profile

Ensure `~/.aws/config` has a profile matching the `AWS_PROFILE` value you set above. Your Bedrock Mantle account provider will supply the exact profile configuration.

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
[TokenRefresher] AWS session expired. Launching login for profile '<your-profile>'...
A URL will be printed — open it in any browser, then paste the authorization code back into this terminal.

Using a browser, open: https://device.sso.<region>.amazonaws.com/?user_code=XXXX-XXXX
Authorization code: _
```

Open the URL in any browser, approve the login, copy the code shown, paste it back into the terminal, and the server continues automatically.

## SSH Usage

This setup works fully over SSH. `aws login --remote` never opens a browser on the remote machine — it prints a URL you open locally, then prompts for a code you paste back. No display forwarding (`-X`/`-Y`) required.

## Available Models

### Bedrock Models

Prices are [AWS Bedrock on-demand standard tier](https://aws.amazon.com/bedrock/pricing/), US East/West regions. Sorted cheapest to most expensive by combined input+output cost.

| Model name | Underlying model | Input ($/1M tokens) | Output ($/1M tokens) | Tested |
|---|---|---|---|---|
| `nemotron-nano-30b` | `nvidia.nemotron-nano-3-30b` | $0.06 | $0.24 | ⚠️ |
| `ministral-3b` | `mistral.ministral-3-3b-instruct` | $0.10 | $0.10 | ⚠️ |
| `ministral-8b` | `mistral.ministral-3-8b-instruct` | $0.15 | $0.15 | ⚠️ |
| `ministral-14b` | `mistral.mistral-large-3-14b-instruct` | $0.20 | $0.20 | ⚠️ |
| `qwen3-235b` | `qwen.qwen3-235b-a22b-2507` | $0.23 ‡ | $0.91 ‡ | ✅ |
| `qwen3-next-80b` | `qwen.qwen3-next-80b-a3b-instruct` | $0.15 | $1.20 | ✅ |
| `qwen3-coder-480b` | `qwen.qwen3-coder-480b-a35b-instruct` | † | † | ✅ |
| `mistral-large-3` | `mistral.mistral-large-3-675b-instruct` | $0.50 | $1.50 | ⚠️ |
| `deepseek-v3.1` | `deepseek.v3.1` | $0.60 ‡ | $1.73 ‡ | ⚠️ |
| `deepseek-v3.2` | `deepseek.v3.2` | $0.62 | $1.85 | ⚠️ |
| `kimi-k2.5` | `moonshotai.kimi-k2.5` | $0.60 | $3.00 | ✅ |

† not yet listed on AWS Bedrock pricing page; assumed same tier as similar model.  
‡ US on-demand pricing not yet listed for this region tier; price shown is AP Sydney standard.

### Non-Bedrock Models

These models are routed through third-party providers rather than AWS Bedrock. Each requires its own API key set in your environment — see [Setup](#2-configure-environment-variables) above.

| Model name | Provider | Underlying model | Context Window | Max Output | Requires | Tested |
|---|---|---|---|---|---|---|
| `elephant-alpha` | [OpenRouter](https://openrouter.ai/) | `openrouter/elephant-alpha` | 256K | 32K | `OPENROUTER_API_KEY` | ✅ |

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
