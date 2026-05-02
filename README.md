# claw-bedrock

A [LiteLLM](https://docs.litellm.ai/docs/) proxy server that started as a way to expose [AWS Bedrock Mantle](https://docs.aws.amazon.com/bedrock/) models to claw-code. Now it is more of containerized LiteLLM with a webapp front end for exposing models from multiple providers via a single OpenAI-compatible API. Useful for [claw-code](https://github.com/ultraworkers/claw-code), [opencode](https://github.com/anomalyco/opencode), or other apps that expect an OpenAI response.

## How It Works

1. The container starts LiteLLM with `config.yaml` and the Management UI on port 8282.
2. The `BedrockTokenRefresher` callback checks your AWS session. If expired, it triggers `aws login --remote`, which prints a URL and waits for you to paste back the authorization code shown in the browser.
3. Once authenticated, a short-lived Bedrock bearer token is fetched and injected as `BEDROCK_MANTLE_API_KEY`.
4. Models are added via the Management UI (port 8282), which stores them in TinyDB (`models.db.json`). Supported providers: Bedrock (Mantle), OpenRouter, Ollama, and manual entry.
5. When you add, rename, or delete models via the UI, `config.yaml` is automatically regenerated and LiteLLM reloads its config via SIGHUP - no container restart needed.
6. For persistence across container restarts, mount a host directory to `/app` using the `CONFIG_DIR` environment variable. Models are stored in `models.db.json` (TinyDB format).

## Prerequisites

- Python 3.12+
- [pipenv](https://pipenv.pypa.io/)
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) (`aws` on your PATH)
- An AWS profile configured in `~/.aws/config` with Bedrock Mantle access

## Setup

### 1. Configure your AWS profile

Ensure `~/.aws/config` has a profile matching the `AWS_PROFILE` value you set above. Your Bedrock Mantle account provider will supply the exact profile configuration.

### 2. (Optional) Attach the IAM policy

A sample IAM policy is provided in [`policy.json`](./policy.json) granting the minimum permissions required:
- Short-term bearer token usage for Bedrock Mantle
- Model discovery (`ListModels`, `GetModel`)
- Inference (`CreateInference`)

## Running the Server

### Option A: Podman systemd `.container` file

Prerequisites:
- Ensure `~/.aws` exists (run `aws configure` to set up AWS credentials).
- Optionally, create a directory for persistent config (e.g., `mkdir ~/claw-bedrock`).
- Optionally, create a secret called `openrouter-api-key` with your OpenRouter API key if you don't want to set it as an environment variable.

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
Environment=OPENROUTER_API_KEY=<your-key> 
# Or you can use podman secrets: Secret=openrouter-api-key,type=env,target=OPENROUTER_API_KEY
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

### Option B: Compose

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
- Let you add models from Bedrock (Mantle), OpenRouter, remote Ollama, or manual entry
- Inline model renaming (click model name to edit)
- Delete models with confirmation
- Toast notifications instead of browser popups


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

### Bedrock Models Baked In
Since Bedrock doesn't have a method for discovery via API, the following models have been baked in. If you would like another added, feel free to open an issue or PR.

| Model name | Underlying model |
|---|---|
| `claw-bedrock/qwen3-next-80b` | `qwen.qwen3-next-80b-a3b-instruct` |
| `claw-bedrock/kimi-k2.5` | `moonshotai.kimi-k2.5` |
| `claw-bedrock/qwen3-235b` | `qwen.qwen3-235b-a22b-2507` |
| `claw-bedrock/mistral-large-3` | `mistral.mistral-large-3-675b-instruct` |
| `claw-bedrock/deepseek-v3.2` | `deepseek.v3.2` |
| `claw-bedrock/nemotron-nano-30b` | `nvidia.nemotron-nano-3-30b` |
| `claw-bedrock/deepseek-v3.1` | `deepseek.v3.1` |
| `claw-bedrock/ministral-14b` | `mistral.ministral-3-14b-instruct` |
| `claw-bedrock/ministral-8b` | `mistral.ministral-3-8b-instruct` |
| `claw-bedrock/ministral-3b` | `mistral.ministral-3-3b-instruct` |
| `claw-bedrock/qwen3-coder-480b` | `qwen.qwen3-coder-480b-a35b-instruct` |
| `claw-bedrock/gpt-oss-20b` | `openai.gpt-oss-20b` |
| `claw-bedrock/gpt-oss-120b` | `openai.gpt-oss-120b` |
| `claw-bedrock/gemma-3-4b` | `google.gemma-3-4b-it` |
| `claw-bedrock/gemma-3-12b` | `google.gemma-3-12b-it` |
| `claw-bedrock/gemma-3-27b` | `google.gemma-3-27b-it` |
| `claw-bedrock/glm-4.7` | `zai.glm-4.7` |
| `claw-bedrock/glm-4.7-flash` | `zai.glm-4.7-flash` |
| `claw-bedrock/minimax-m2` | `minimax.minimax-m2` |
| `claw-bedrock/minimax-m2.1` | `minimax.minimax-m2.1` |
| `claw-bedrock/magistral-small` | `mistral.magistral-small-2509` |
| `claw-bedrock/devstral-2-123b` | `mistral.devstral-2-123b` |
| `claw-bedrock/kimi-k2-thinking` | `moonshotai.kimi-k2-thinking` |
| `claw-bedrock/nemotron-nano-9b` | `nvidia.nemotron-nano-9b-v2` |
| `claw-bedrock/nemotron-nano-12b` | `nvidia.nemotron-nano-12b-v2` |
| `claw-bedrock/qwen3-32b` | `qwen.qwen3-32b` |
| `claw-bedrock/qwen3-coder-30b` | `qwen.qwen3-coder-30b-a3b-instruct` |
| `claw-bedrock/qwen3-coder-next` | `qwen.qwen3-coder-next` |

> ⚠️ Reasoning models (`claw-bedrock/gpt-oss-*`, `claw-bedrock/minimax-m2`, `claw-bedrock/minimax-m2.1`, `claw-bedrock/kimi-k2-thinking`) require sufficiently high `max_tokens` or responses may return `null` content.

## Adding Models

1. Access the Management UI at `http://localhost:8282`
2. Use the UI to add models from Bedrock (Mantle), OpenRouter, Ollama, or enter manually
3. When you add/delete models via the UI, `config.yaml` is automatically regenerated and LiteLLM reloads its config via SIGHUP — no container restart needed.

**Supported Providers:**
- **OpenRouter**: Fetches models from [OpenRouter](https://openrouter.ai/). Requires `OPENROUTER_API_KEY` (optional but recommended).
- **Ollama**: Lists local Ollama models. Requires Ollama running at `http://<host>:11434` (set `OLLAMA_API_BASE` env var).
- **Manual**: Custom configuration for any provider.

**Environment Variables for Models:**
| Provider | Required Variable | Notes |
|----------|------------------|-------|
| OpenRouter | `OPENROUTER_API_KEY` | Optional but recommended for rate limits |
| Ollama | `OLLAMA_API_BASE` | Default: `http://localhost:11434` |
| Bedrock | `AWS_PROFILE`, `AWS_REGION`, `BEDROCK_MANTLE_API_BASE` | Configured in `~/.aws/config` |

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