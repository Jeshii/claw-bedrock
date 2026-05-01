# claw-bedrock

A [LiteLLM](https://docs.litellm.ai/docs/) proxy server that started as an [AWS Bedrock Mantle](https://docs.aws.amazon.com/bedrock/) wrapper and is evolving into a general model router — exposing models from multiple providers via a single OpenAI-compatible API. Attempts to handle AWS authentication and token refresh automatically. Useful for [claw-code](https://github.com/ultraworkers/claw-code), [opencode](https://github.com/anomalyco/opencode), or other apps that expect an OpenAI response.

## How It Works

1. The container starts LiteLLM with `config.yaml` and the Management UI on port 8282.
2. The `BedrockTokenRefresher` callback checks your AWS session. If expired, it triggers `aws login --remote`, which prints a URL and waits for you to paste back the authorization code shown in the browser.
3. Once authenticated, a short-lived Bedrock bearer token is fetched and injected as `BEDROCK_MANTLE_API_KEY`.
4. Models are added via the Management UI (port 8282), which writes to `config.local.yaml`. Supported providers: Bedrock (Mantle), OpenRouter, Ollama, HuggingFace, and manual entry.
5. When you add, rename, or delete models via the UI, `config.yaml` is automatically regenerated and LiteLLM reloads its config via SIGHUP - no container restart needed.
6. For persistence across container restarts, mount a host directory to `/app` using the `CONFIG_DIR` environment variable.

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

### Native (pipenv)
```bash
# Start the management UI (includes LiteLLM proxy)
pipenv run uvicorn management_app:app --host 0.0.0.0 --port 8282

# Or start LiteLLM directly (models managed via config.local.yaml)
pipenv run litellm --config config.yaml --port 4000
```

Access the Management UI at `http://localhost:8282` to add/manage models.

If your AWS session has expired, you will see:

```
[TokenRefresher] AWS session expired. Launching login for profile '<your-profile>'...
A URL will be printed — open it in any browser, then paste the authorization code back into this terminal.

Using a browser, open: https://device.sso.<region>.amazonaws.com/?user_code=XXXX-XXXX
Authorization code: _
```

Open the URL in any browser, approve the login, copy the code shown, paste it back into the terminal, and the server continues automatically.

### Container (Podman/Docker)

**Option A: Podman systemd `.container` file**

Prerequisite: Ensure `~/.aws` exists (run `aws configure` to set up AWS credentials).

Create `~/.config/containers/systemd/claw-bedrock.container`:

```ini
[Unit]
Description=claw-bedrock

[Service]
Restart=on-failure
TimeoutStartSec=900
CPUQuota=50%

[Container]
# Pre-built image from GitHub Container Registry:
Image=ghcr.io/jeshii/claw-bedrock:latest
ContainerName=claw-bedrock
PublishPort=4000:4000
PublishPort=8282:8282

Environment=AWS_PROFILE=your-profile
Environment=AWS_REGION=your-region
Environment=BEDROCK_MANTLE_API_BASE=https://bedrock-mantle.<region>.api.aws/v1
Environment=OPENROUTER_API_KEY=your-key
Environment=OLLAMA_API_BASE=http://your-ollama-host:11434
# CONFIG_DIR defaults to /app - mount your config directory:
Environment=CONFIG_DIR=/app

# Mount AWS credentials and config directory for persistence:
Volume=%h/.aws:/root/.aws:Z
Volume=%h/claw-bedrock:/app:Z

[Install]
WantedBy=default.target
```

Then reload and start:
```bash
systemctl --user daemon-reload
systemctl --user start claw-bedrock
systemctl --user enable claw-bedrock  # autostart on boot
```

**Option B: Compose**

1. **Start Podman (macOS only):**
   ```bash
   podman machine init  # first time only
   podman machine start
   ```

2. **Build the image:**
   ```bash
   podman build -t claw-bedrock .
   # or with docker-compose:
   podman-compose build
   ```

3. **Use the provided `docker-compose.yml`:**
   ```bash
   # Edit docker-compose.yml to add your API keys
   vim docker-compose.yml
   ```

4. **Run with compose:**
   ```bash
   podman-compose up -d
   # or with docker:
   docker-compose up -d
   ```

5. **Access:**
   - LiteLLM API: `http://localhost:4000`
   - Management UI: `http://localhost:8282` (for AWS auth, model management)

The management UI at port 8282 will:
- Display AWS auth URL when authentication is needed (click to open in browser)
- Let you add models from Bedrock (Mantle), OpenRouter, remote Ollama, HuggingFace, or manual entry
- Inline model renaming (click model name to edit)
- Delete models with confirmation
- Toast notifications instead of browser popups
- Replace the need for `update_model_config.py`

## SSH Usage

This setup works fully over SSH. `aws login --remote` never opens a browser on the remote machine — it prints a URL you open locally, then prompts for a code you paste back. No display forwarding (`-X`/`-Y`) required.

## Client Integrations

### opencode.ai

[opencode](https://opencode.ai) is an AI coding agent that runs in the terminal. It supports any OpenAI-compatible provider, so it can talk directly to this LiteLLM proxy.

1. Create the opencode config directory and file:

```bash
mkdir -p ~/.config/opencode
vim ~/.config/opencode/opencode.json
```

2. Add the following configuration:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["opencode-models-discovery@latest"],
  "provider": {
    "claw-bedrock": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Claw Bedrock",
      "options": {
        "baseURL": "http://localhost:4000/v1"
      }
    }
  }
}
```

> If LiteLLM is running on a different machine, replace `localhost` with that machine's IP address or hostname:
> ```
> "baseURL": "http://<IP_ADDRESS>:4000/v1"
> ```

3. Refresh the models list to discover available models from the proxy:

```bash
opencode models --refresh
```

4. List available models:

```bash
opencode models litellm
```

5. Run opencode with your desired model:

```bash
opencode --model claw-bedrock/<modelname>
```

Model names are managed via the Management UI at `http://localhost:8282`.

### claw-code

[claw-code](https://github.com/ultraworkers/claw-code) is another AI coding agent that works with OpenAI-compatible providers.

1. Configure claw-code to use the LiteLLM proxy by setting the API base and key:

```bash
export OPENAI_API_KEY="dummy"
export OPENAI_BASE_URL="http://localhost:4000/v1"
```

2. List available models:
```bash
claw-code models list
```

3. Run claw-code with your desired model:
```bash
claw-code --model claw-bedrock/<modelname>
```

> **Note:** Always use the `claw-bedrock/` prefix followed by the model's friendly name (e.g., `claw-bedrock/qwen3-235b`, `claw-bedrock/elephant-alpha`). Model names are managed via the Management UI at `http://localhost:8282`.

## Available Models

### Bedrock Models

Prices are [AWS Bedrock on-demand standard tier](https://aws.amazon.com/bedrock/pricing/), US East/West regions. Sorted by my perception of how functional they are in claw-code (subject to change as testing continues).

| Model name | Underlying model | Input ($/1M tokens) | Output ($/1M tokens) | Tested |
|---|---|---|---|---|
| `claw-bedrock/qwen3-next-80b` | `qwen.qwen3-next-80b-a3b-instruct` | $0.15 | $1.20 | ✅ |
| `claw-bedrock/kimi-k2.5` | `moonshotai.kimi-k2.5` | $0.60 | $3.00 | ✅ |
| `claw-bedrock/qwen3-235b` | `qwen.qwen3-235b-a22b-2507` | $0.23 ‡ | $0.91 ‡ | ⚠️ |
| `claw-bedrock/mistral-large-3` | `mistral.mistral-large-3-675b-instruct` | $0.50 | $1.50 | ⚠️ |
| `claw-bedrock/deepseek-v3.2` | `deepseek.v3.2` | $0.62 | $1.85 | ⚠️ |
| `claw-bedrock/nemotron-nano-30b` | `nvidia.nemotron-nano-3-30b` | $0.06 | $0.24 | ⚠️ |
| `claw-bedrock/deepseek-v3.1` | `deepseek.v3.1` | $0.60 ‡ | $1.73 ‡ | ⚠️ |
| `claw-bedrock/ministral-14b` | `mistral.ministral-3-14b-instruct` | $0.20 | $0.20 | ⚠️ |
| `claw-bedrock/ministral-8b` | `mistral.ministral-3-8b-instruct` | $0.15 | $0.15 | ⚠️ |
| `claw-bedrock/ministral-3b` | `mistral.ministral-3-3b-instruct` | $0.10 | $0.10 | ⚠️ |
| `claw-bedrock/qwen3-coder-480b` | `qwen.qwen3-coder-480b-a35b-instruct` | † | † | |
| `claw-bedrock/gpt-oss-20b` | `openai.gpt-oss-20b` | $0.06 | $0.24 | |
| `claw-bedrock/gpt-oss-120b` | `openai.gpt-oss-120b` | $0.35 | $1.40 | |
| `claw-bedrock/gemma-3-4b` | `google.gemma-3-4b-it` | $0.03 | $0.07 | |
| `claw-bedrock/gemma-3-12b` | `google.gemma-3-12b-it` | $0.06 | $0.17 | |
| `claw-bedrock/gemma-3-27b` | `google.gemma-3-27b-it` | $0.12 | $0.35 | |
| `claw-bedrock/glm-4.7` | `zai.glm-4.7` | $0.15 | $0.60 | |
| `claw-bedrock/glm-4.7-flash` | `zai.glm-4.7-flash` | $0.05 | $0.15 | |
| `claw-bedrock/minimax-m2` | `minimax.minimax-m2` | $0.30 | $1.10 | |
| `claw-bedrock/minimax-m2.1` | `minimax.minimax-m2.1` | $0.30 | $1.10 | |
| `claw-bedrock/magistral-small` | `mistral.magistral-small-2509` | $0.10 | $0.30 | |
| `claw-bedrock/devstral-2-123b` | `mistral.devstral-2-123b` | $0.50 | $1.50 | |
| `claw-bedrock/kimi-k2-thinking` | `moonshotai.kimi-k2-thinking` | $0.60 | $3.00 | |
| `claw-bedrock/nemotron-nano-9b` | `nvidia.nemotron-nano-9b-v2` | $0.04 | $0.15 | |
| `claw-bedrock/nemotron-nano-12b` | `nvidia.nemotron-nano-12b-v2` | $0.05 | $0.20 | |
| `claw-bedrock/qwen3-32b` | `qwen.qwen3-32b` | $0.10 | $0.40 | |
| `claw-bedrock/qwen3-coder-30b` | `qwen.qwen3-coder-30b-a3b-instruct` | $0.10 | $0.40 | |
| `claw-bedrock/qwen3-coder-next` | `qwen.qwen3-coder-next` | † | † | |

† Not yet listed on AWS Bedrock pricing page.  
‡ US on-demand pricing not yet listed for this region tier; price shown is AP Sydney standard.

> ⚠️ Reasoning models (`claw-bedrock/gpt-oss-*`, `claw-bedrock/minimax-m2`, `claw-bedrock/minimax-m2.1`, `claw-bedrock/kimi-k2-thinking`) require sufficiently high `max_tokens` or responses may return `null` content.

### Non-Bedrock Models

| Model name | Provider | Underlying model | Context Window | Max Output | Requires | Tested |
|---|---|---|---|---|---|---|
| `claw-bedrock/elephant-alpha` | [OpenRouter](https://openrouter.ai/) | `openrouter/elephant-alpha` | 256K | 32K | `OPENROUTER_API_KEY` | ⚠️ |

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
| `config.yaml` | LiteLLM proxy config — generated by merging `config.local.yaml` |
| `config.bedrock.yaml` | Bedrock Mantle model template — used by Management UI to add models |
| `config.local.yaml` | User-added models (all providers) — mount directory for persistence |
| `management_app.py` | Web management UI for auth and model configuration |
| `start_container.sh` | Container entrypoint — handles config init, PID writing, CONFIG_DIR |
| `Containerfile` | Podman/Docker image definition |
| `docker-compose.yml` | Container orchestration example with volume mount for persistence |
| `token_refresher.py` | LiteLLM callback — handles login and token auto-refresh |
| `update_model_config.py` | Interactive script to add models (native use only, deprecated) |
| `MODEL_UPDATE_GUIDE.md` | Guide for using `update_model_config.py` |
| `requirements.txt` | Python dependencies for container builds |
| `policy.json` | Sample IAM policy for Bedrock Mantle access |
| `Pipfile` | Python dependencies (native use) |
