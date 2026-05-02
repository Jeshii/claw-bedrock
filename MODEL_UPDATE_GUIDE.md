# Updating Models in `config.local.yaml`

This project includes an interactive script to help you add new models from various providers to `config.local.yaml`.

## The Script: `update_model_config.py`

A Python script that:
- Fetches available models from supported providers
- Provides an interactive menu for selection
- Formats and inserts the model configuration correctly
- Preserves the existing YAML structure and comments

## Supported Providers

### 1. OpenRouter
Fetches the full list of models available on [OpenRouter](https://openrouter.ai/), including free-tier models when selected.

**What You Need:**
- `OPENROUTER_API_KEY` environment variable (optional, but recommended for rate limiting)

**Features:**
- Shows pricing information
- Distinguishes free vs paid models
- Automatically adds required headers (`HTTP-Referer`, `X-OpenRouter-Title`)

### 2. Ollama / Local Models
Connects to a locally running [Ollama](https://ollama.com/) instance to list available models.

**What You Need:**
- Ollama running at `http://<local_host_or_IP>:11434`
- Models pulled locally (`ollama pull <model_name>`)

### 3. Manual / Custom
Allows you to fully customize the model configuration for any provider.

## Installation

The script requires Python packages that may not be installed by default:

```bash
python3 -m pip install --break-system-packages pyyaml requests inquirer
```

## Usage

```bash
python3 update_model_config.py
```

### Step-by-Step Flow

1. **Select a provider** — Choose from OpenRouter, Ollama, HuggingFace, or Manual.
2. **Fetch/list models** — The script queries the provider (if applicable).
3. **Select a model** — Use arrow keys to navigate, Enter to select.
4. **Review and confirm** — The script shows the generated config snippet.
5. **Save** — The model is inserted into `config.local.yaml`.

### Example Session (OpenRouter)

```
============================================================
🛠  Update config.local.yaml with a new model
============================================================
? Select model provider » OpenRouter (remote models)

📡 Fetching models from OpenRouter...
   Found 367 models (30 free)

? Select a model from OpenRouter » 
  ❯ Meta: Llama 3.2 3B Instruct  [ meta-llama/llama-3.2-3b-instruct ]
    Google: Gemma 3 4B  [ google/gemma-3-4b-it ]
    Microsoft: Phi-3 Mini 4k  [ microsoft/phi-3-mini-4k-instruct ]
   …

📝 Model: openrouter/meta-llama/llama-3.2-3b-instruct
? Model name (as it will appear in config) » llama-3.2-3b
? Add this model to config.local.yaml? » Yes

✅ Config written to config.local.yaml

✨ Added model 'llama-3.2-3b'

--- Next steps ---
1. Review changes: cat config.local.yaml
2. Set any required environment variables (e.g., OPENROUTER_API_KEY)
3. Restart the LiteLLM server to pick up changes
============================================================
```

## Configuration Details

### What Gets Added

For each provider, the script creates a properly formatted YAML entry:

**OpenRouter example:**
```yaml
- model_name: my-awesome-model
  litellm_params:
    model: openrouter/anthropic/claude-3
    api_base: "https://openrouter.ai/api/v1"
    api_key: "os.environ/OPENROUTER_API_KEY"
    extra_headers:
      HTTP-Referer: "https://github.com/Jeshii/claw-bedrock"
      X-OpenRouter-Title: "claw-bedrock"
```

**Ollama/local example:**
```yaml
- model_name: qwen3-4b
  litellm_params:
    model: ollama/qwen3:4b
    api_base: http://<IP_ADDRESS>:11434
    stream: false
```

### Model Naming

The script auto-generates a "model_name" from the model ID by:
- Converting to lowercase
- Replacing special characters with hyphens
- Stripping leading/trailing hyphens

You can override this during confirmation.

### Section Ordering

Models are inserted in logical sections:
1. **Local/Ollama models** — First
2. **OpenRouter models** — After local models
3. **Bedrock models** — After OpenRouter
4. Manual additions go at the end (can be moved manually)

## Environment Variables

After adding a model, ensure you have the required environment variables set:

### OpenRouter
```bash
export OPENROUTER_API_KEY="your-key-here"
```

### Ollama
No API key needed, but verify connectivity:
```bash
curl http://<IP_ADDRESS>:11434/api/tags
```

## Troubleshooting

### Script can't reach OpenRouter API
- Check your internet connection
- Verify OpenRouter is accessible: `curl https://openrouter.ai/api/v1/models`
- If using a proxy, configure it for Python requests

### Ollama models not showing up
- Verify Ollama is running: `ollama serve`
- Check the API endpoint: `curl http://<IP_ADDRESS>:11434/api/tags`
- Use the "custom host" option to specify a different URL

### YAML formatting looks wrong
The script preserves the original YAML structure. If something looks off:
```bash
python3 -c "import yaml; print(yaml.dump(yaml.safe_load(open('config.local.yaml')), default_flow_style=False))"
```

### Model not working after restart
- Check LiteLLM logs for errors
- Verify all environment variables are set
- Test the model directly with curl:
  ```bash
  curl http://localhost:4000/v1/models
  ```

## Advanced: Adding Multiple Models

Run the script multiple times to add several models. It will:
- Preserve all existing entries
- Insert new ones in the correct section
- Not create duplicates (warning only — verify manually)

## Advanced: Script as a Module

You can import and use the provider classes programmatically:

```python
from update_model_config import OpenRouterProvider, OllamaProvider

# Fetch OpenRouter models
provider = OpenRouterProvider()
models = provider.fetch_models(include_free=True)

# Get a config entry
entry = provider.build_config_entry("anthropic/claude-3", "claude-3")

# Manually add to config
import yaml
with open("config.local.yaml") as f:
    config = yaml.safe_load(f)
config["model_list"].append(entry)
with open("config.local.yaml", "w") as f:
    yaml.dump(config, f, default_flow_style=False)
```

## Notes

- **No duplicates check**: The script doesn't prevent adding duplicate entries. Review `config.local.yaml` after adding models.
- **Free models expire**: OpenRouter free-tier models often have limited availability. If a free model stops working, remove it or replace it.
- **Restart required**: LiteLLM needs to be restarted to pick up config changes.

## Contributing

To add support for a new provider:

1. Create a new provider class extending `BaseProvider`
2. Implement `fetch_models()` and `build_config_entry()`
3. Add it to the `select_provider()` choices
4. Add the workflow branch in `main()`

See existing providers for examples.
