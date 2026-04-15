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
export AWS_PROFILE="<your-aws-profile-name>"
export AWS_REGION="<your-aws-region>"
export BEDROCK_MANTLE_API_BASE="https://bedrock-mantle.<your-aws-region>.api.aws/v1"
```

Then reload your shell:

```bash
source ~/.zshrc
```

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

Prices are [AWS Bedrock on-demand standard tier](https://aws.amazon.com/bedrock/pricing/), US East/West regions. Sorted cheapest to most expensive by combined input+output cost.

| Model name | Underlying model | Input ($/1M tokens) | Output ($/1M tokens) |
|---|---|---|---|
| `voxtral-mini` | `mistral.voxtral-mini-3b-2507` | $0.04 | $0.04 |
| `gemma-3-4b` | `google.gemma-3-4b-it` | $0.04 | $0.08 |
| `nemotron-nano-12b` | `nvidia.nemotron-nano-12b-v2` | $0.06 | $0.23 |
| `nemotron-nano-9b` | `nvidia.nemotron-nano-9b-v2` | $0.06 † | $0.23 † |
| `nemotron-nano-30b` | `nvidia.nemotron-nano-3-30b` | $0.06 | $0.24 |
| `gpt-safeguard-20b` | `openai.gpt-oss-safeguard-20b` | $0.07 | $0.20 |
| `glm-4.7-flash` | `zai.glm-4.7-flash` | $0.07 | $0.40 |
| `gpt-20b` | `openai.gpt-oss-20b` | $0.07 ‡ | $0.31 ‡ |
| `gemma-3-12b` | `google.gemma-3-12b-it` | $0.09 | $0.29 |
| `ministral-3b` | `mistral.ministral-3-3b-instruct` | $0.10 | $0.10 |
| `voxtral-small` | `mistral.voxtral-small-24b-2507` | $0.10 | $0.30 |
| `ministral-8b` | `mistral.ministral-3-8b-instruct` | $0.15 | $0.15 |
| `gpt-safeguard-120b` | `openai.gpt-oss-safeguard-120b` | $0.15 | $0.60 |
| `qwen-coder` | `qwen.qwen3-coder-30b-a3b-instruct` | $0.15 ‡ | $0.62 ‡ |
| `qwen3-32b` | `qwen.qwen3-32b` | $0.15 ‡ | $0.62 ‡ |
| `gpt-120b` | `openai.gpt-oss-120b` | $0.15 ‡ | $0.62 ‡ |
| `ministral-14b` | `mistral.ministral-3-14b-instruct` | $0.20 | $0.20 |
| `gemma-3-27b` | `google.gemma-3-27b-it` | $0.23 | $0.38 |
| `qwen3-235b` | `qwen.qwen3-235b-a22b-2507` | $0.23 ‡ | $0.91 ‡ |
| `qwen3-next-80b` | `qwen.qwen3-next-80b-a3b-instruct` | $0.15 | $1.20 |
| `minimax-m2` | `minimax.minimax-m2` | $0.30 | $1.20 |
| `minimax-m2.1` | `minimax.minimax-m2.1` | $0.30 | $1.20 |
| `qwen3-coder-next` | `qwen.qwen3-coder-next` | $0.50 | $1.20 |
| `qwen3-coder-480b` | `qwen.qwen3-coder-480b-a35b-instruct` | $0.50 † | $1.20 † |
| `magistral-small` | `mistral.magistral-small-2509` | $0.50 | $1.50 |
| `mistral-large-3` | `mistral.mistral-large-3-675b-instruct` | $0.50 | $1.50 |
| `deepseek-v3.1` | `deepseek.v3.1` | $0.60 ‡ | $1.73 ‡ |
| `deepseek-v3` | `deepseek.v3.2` | $0.62 | $1.85 |
| `glm-4.7` | `zai.glm-4.7` | $0.60 | $2.20 |
| `glm-4.6` | `zai.glm-4.6` | TBD * | TBD * |
| `kimi-k2-thinking` | `moonshotai.kimi-k2-thinking` | $0.60 | $2.50 |
| `qwen3-vl-235b` | `qwen.qwen3-vl-235b-a22b-instruct` | $0.53 | $2.66 |
| `kimi-k2.5` | `moonshotai.kimi-k2.5` | $0.60 | $3.00 |

† Estimated pricing — not yet listed on AWS Bedrock pricing page; assumed same tier as similar model.  
‡ US on-demand pricing not yet listed for this region tier; price shown is AP Sydney standard.  
\* Pricing not yet available.

## Using the API

The server exposes an OpenAI-compatible API on port 4000. Point any OpenAI-compatible client at `http://localhost:4000`.

```bash
# List available models
curl http://localhost:4000/models

# Example chat completion
curl http://localhost:4000/v1/chat/completions \\
  -H "Content-Type: application/json" \\
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
