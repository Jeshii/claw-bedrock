"""
Interactive script to update config.local.yaml with models from various providers.

Supports:
- Local models (via Ollama API)
- OpenRouter models (fetches from their API)
- HuggingFace models
- Manual/custom model entry

Usage:
    python3 update_model_config.py

Dependencies:
    pyyaml, requests, inquirer
"""

import json
import os
import re
import sys
from typing import Dict, List, Optional, Any

import yaml
import requests
import inquirer


# ============================================================
# Provider API clients
# ============================================================

class BaseProvider:
    """Base class for model providers."""
    
    def __init__(self, config_path: str = "config.local.yaml"):
        self.config_path = config_path
    
    def fetch_models(self) -> List[Dict[str, str]]:
        """Fetch available models from this provider.
        
        Returns:
            List of dicts with at least 'id' key.
        """
        raise NotImplementedError
    
    def build_config_entry(self, model_id: str, display_name: Optional[str] = None) -> Dict:
        """Build a config list entry for the given model.
        
        Args:
            model_id: The provider's model identifier.
            display_name: Optional friendly name for the model.
        
        Returns:
            A dict suitable for insertion into config.local.yaml's model_list.
        """
        raise NotImplementedError
    
    @staticmethod
    def _sanitize_name(model_id: str) -> str:
        """Convert a model ID into a filesystem-/YAML-friendly name."""
        return re.sub(r'[^a-zA-Z0-9_.-]', '-', model_id).strip('-').lower()


class OpenRouterProvider(BaseProvider):
    """Fetch and configure OpenRouter models."""
    
    API_URL = "https://openrouter.ai/api/v1/models"
    
    def fetch_models(self, include_free: bool = True) -> List[Dict[str, Any]]:
        """Fetch models from OpenRouter.
        
        Args:
            include_free: Whether to include free-tier models.
        
        Returns:
            Sorted list of model dicts with keys: id, name, pricing, context_length, etc.
        """
        # Optional API key — gives more accurate pricing/limits when provided
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        
        try:
            resp = requests.get(self.API_URL, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"\n⚠️  Could not reach OpenRouter API: {e}")
            print("   You can still add models manually or enter a cached list.\n")
            return []
        
        data = resp.json()
        models = data.get("data", [])
        
        # Filter out if requested
        if not include_free:
            models = [m for m in models if not m.get("pricing", {}).get("prompt", "0").startswith("0")]
        
        # Sort: free first, then by name
        def sort_key(m):
            pricing = m.get("pricing", {})
            prompt_cost = float(pricing.get("prompt", "inf").replace("inf", "999999"))
            return (prompt_cost, m.get("name", "").lower())
        
        models.sort(key=sort_key)
        return models
    
    def build_config_entry(self, model_id: str, display_name: Optional[str] = None) -> Dict:
        """Build a YAML entry for an OpenRouter model.
        
        The model_id should be in the form "provider/model" (e.g. "anthropic/claude-3").
        """
        safe_name = display_name or self._sanitize_name(model_id)
        
        # Check if it's a free-tier model (has :free suffix in model id or known free models)
        is_free = ":free" in model_id or any(
            free_model in model_id.lower()
            for free_model in ["ling-2.6-1t:free", "hy3-preview:free"]
        )
        
        if is_free:
            # Strip :free for actual model reference but keep note
            actual_model = model_id.replace(":free", "")
            openrouter_model = f"openrouter/{actual_model}"
        else:
            openrouter_model = f"openrouter/{model_id}"
        
        entry: Dict[str, Any] = {
            "model_name": safe_name,
            "litellm_params": {
                "model": openrouter_model,
                "api_base": "https://openrouter.ai/api/v1",
                "api_key": "os.environ/OPENROUTER_API_KEY",
                "extra_headers": {
                    "HTTP-Referer": "https://github.com/Jeshii/claw-bedrock",
                    "X-OpenRouter-Title": "claw-bedrock"
                }
            }
        }
        return entry


class OllamaProvider(BaseProvider):
    """Fetch models from a locally running Ollama instance."""
    
    def __init__(self, api_base: str = "http://localhost:11434", **kwargs):
        super().__init__(**kwargs)
        self.api_base = api_base.rstrip("/")
    
    def fetch_models(self) -> List[Dict[str, Any]]:
        """Query the Ollama API for available local models."""
        url = f"{self.api_base}/api/tags"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            # Fallback to common models if API is unavailable
            print(f"\n⚠️  Could not reach Ollama at {self.api_base}.")
            return self._fallback_models()
        
        data = resp.json()
        models = data.get("models", [])
        
        # Return sorted by name
        return sorted(models, key=lambda m: m.get("name", "").lower())
    
    def _fallback_models(self) -> List[Dict[str, Any]]:
        """Return a small list of common models for manual selection."""
        return [
            {"name": "ollama/qwen2.5-coder:3b"},
            {"name": "ollama/phi3:mini"},
            {"name": "ollama/qwen3:4b"},
            {"name": "ollama/llama3.1:8b"},
            {"name": "ollama/mistral:7b"},
            {"name": "ollama/gemma2:9b"},
        ]
    
    def build_config_entry(self, model_id: str, display_name: Optional[str] = None) -> Dict:
        """Build a YAML entry for an Ollama/local model."""
        # model_id might be like "qwen2.5-coder:3b" or "ollama/qwen2.5-coder:3b"
        clean_model = model_id.replace("ollama/", "")
        safe_name = display_name or self._sanitize_name(clean_model).replace(":", "-")
        
        return {
            "model_name": safe_name,
            "litellm_params": {
                "model": f"ollama/{clean_model}",
                "api_base": self.api_base,
                "stream": False
            }
        }


class HuggingFaceProvider(BaseProvider):
    """Fetch text-generation-inference models from HuggingFace HUB."""
    
    # Note: Actual HF TGI endpoints vary. We provide a curated list.
    # Users can also specify a custom HF model ID.
    
    PRESET_MODELS = [
        {"id": "meta-llama/Llama-3.2-1B-Instruct", "name": "Llama 3.2 1B"},
        {"id": "meta-llama/Llama-3.2-3B-Instruct", "name": "Llama 3.2 3B"},
        {"id": "microsoft/Phi-3-mini-4k-instruct", "name": "Phi-3 Mini 4k"},
        {"id": "Qwen/Qwen2.5-0.5B-Instruct", "name": "Qwen 2.5 0.5B"},
        {"id": "Qwen/Qwen2.5-1.5B-Instruct", "name": "Qwen 2.5 1.5B"},
        {"id": "mistralai/Mistral-7B-Instruct-v0.3", "name": "Mistral 7B Instruct"},
    ]
    
    def fetch_models(self, search_query: str = "") -> List[Dict[str, str]]:
        """Return preset models (optionally filtered).
        
        In the future this could query the HuggingFace Hub API.
        """
        if not search_query:
            return self.PRESET_MODELS
        
        query_lc = search_query.lower()
        return [
            m for m in self.PRESET_MODELS
            if query_lc in m["name"].lower() or query_lc in m["id"].lower()
        ]
    
    def build_config_entry(self, model_id: str, display_name: Optional[str] = None) -> Dict:
        """Build a YAML entry for a HuggingFace model served via TGI."""
        safe_name = display_name or self._sanitize_name(model_id)
        
        return {
            "model_name": safe_name,
            "litellm_params": {
                "model": f"huggingface/{model_id}",
                "api_base": "os.environ/HF_INFERENCE_API_BASE",  # User must set this
                "api_key": "os.environ/HUGGINGFACE_API_KEY"
            }
        }


class ManualProvider(BaseProvider):
    """Allow manual/custom model entry."""
    
    def fetch_models(self) -> List[Dict[str, str]]:
        """Return common examples for reference."""
        return [
            {"id": "custom", "name": "Enter custom model manually..."},
            {"id": "bedrock_mantle/openai.gpt-oss-20b", "name": "Example: Bedrock OSS 20B"},
            {"id": "openrouter/anthropic/claude-3", "name": "Example: Claude 3 via OpenRouter"},
        ]
    
    def build_config_entry(self, model_id: str, display_name: Optional[str] = None) -> Dict:
        """Build a YAML entry from detailed user input."""
        print("\n--- Manual Model Configuration ---")
        
        answers = inquirer.prompt([
            inquirer.Text("provider_type",
                         message="Provider type (e.g., openrouter, bedrock_mantle, ollama, huggingface)",
                         default="openrouter"),
            inquirer.Text("model_path",
                         message="Full model path (e.g., openrouter/anthropic/claude-3)",
                         default=model_id),
            inquirer.Text("display_name",
                         message="Friendly name for this model",
                         default=(display_name or self._sanitize_name(model_id))),
        ])
        
        provider = answers["provider_type"]
        model_path = answers["model_path"]
        name = answers["display_name"]
        
        litellm_params = {"model": model_path}
        
        # Add common fields based on provider
        if provider == "openrouter":
            litellm_params.update({
                "api_base": "https://openrouter.ai/api/v1",
                "api_key": "os.environ/OPENROUTER_API_KEY",
                "extra_headers": {
                    "HTTP-Referer": "https://github.com/Jeshii/claw-bedrock",
                    "X-OpenRouter-Title": "claw-bedrock"
                }
            })
        elif provider == "ollama":
            litellm_params.update({
                "api_base": "http://localhost:11434",
                "stream": False
            })
        elif provider == "bedrock_mantle":
            litellm_params.update({
                "api_base": "os.environ/BEDROCK_MANTLE_API_BASE",
                "max_tokens": 32768
            })
        
        return {
            "model_name": name,
            "litellm_params": litellm_params
        }


# ============================================================
# Config file management
# ============================================================

def load_config(config_path: str) -> Dict:
    """Load YAML config file. Creates minimal structure if missing."""
    if not os.path.exists(config_path):
        return {"model_list": []}
    
    with open(config_path, "r") as f:
        return yaml.safe_load(f) or {"model_list": []}


def save_config(config: Dict, config_path: str) -> None:
    """Save YAML config file with nice formatting."""
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"\n✅ Config written to {config_path}")


def add_model_to_config(config: Dict, entry: Dict) -> None:
    """Insert a model entry at the appropriate position in config."""
    model_list = config.setdefault("model_list", [])
    
    # Determine section: local models vs OpenRouter vs others
    model_name = entry["model_name"]
    litellm_params = entry["litellm_params"]
    model_path = litellm_params.get("model", "")
    api_base = litellm_params.get("api_base", "")
    
    # Simple heuristic for ordering
    is_local = "ollama" in model_path or "local" in model_name
    is_openrouter = "openrouter" in model_path or "openrouter.ai" in api_base
    
    # Find insertion point
    insert_pos = len(model_list)
    for i, existing in enumerate(model_list):
        ex_name = existing.get("model_name", "")
        ex_params = existing.get("litellm_params", {})
        ex_model = ex_params.get("model", "")
        ex_api = ex_params.get("api_base", "")
        
        if is_local and ("openrouter" in ex_model or "bedrock" in ex_model):
            insert_pos = i
            break
        if is_openrouter and "bedrock" in ex_model and "openrouter" not in ex_model:
            insert_pos = i
            # Keep looking past other openrouter entries
            while insert_pos < len(model_list):
                next_params = model_list[insert_pos].get("litellm_params", {})
                if "openrouter" not in next_params.get("model", ""):
                    break
                insert_pos += 1
            break
    
    model_list.insert(insert_pos, entry)


# ============================================================
# Interactive UI
# ============================================================

def select_provider() -> str:
    """Ask user which provider to use."""
    questions = [
        inquirer.List("provider",
            message="Select model provider",
            choices=[
                ("OpenRouter (remote models)", "openrouter"),
                ("Ollama / Local models", "ollama"),
                ("HuggingFace (text-generation-inference)", "huggingface"),
                ("Manual / Custom", "manual"),
            ],
        ),
    ]
    answers = inquirer.prompt(questions)
    return answers["provider"]


def pick_from_fetched(models: List[Dict[str, Any]], provider_name: str) -> Optional[Dict]:
    """Display fetched models and let user pick one (with fuzzy search via CLI)."""
    if not models:
        print("\n❌ No models available from this provider.")
        return None
    
    # Format choices for inquirer
    choices = []
    for m in models:
        if "name" in m and "id" in m:
            label = f"{m['name']}  [ {m['id']} ]"
        elif "name" in m:
            label = m["name"]
        elif "id" in m:
            label = m["id"]
        else:
            continue
        choices.append((label, m))
    
    if not choices:
        return None
    
    questions = [
        inquirer.List("selected",
            message=f"Select a model from {provider_name}",
            choices=choices,
            carousel=True,
        ),
    ]
    answers = inquirer.prompt(questions)
    return answers["selected"] if answers else None


def confirm_and_rename(entry: Dict) -> Dict:
    """Let user confirm/rename before saving."""
    current_name = entry["model_name"]
    current_model = entry["litellm_params"].get("model", "N/A")
    
    questions = [
        inquirer.Text("display_name",
                     message="Model name (as it will appear in config)",
                     default=current_name),
        inquirer.Confirm("confirm",
                        message="Add this model to config.local.yaml?",
                        default=True),
    ]
    answers = inquirer.prompt(questions)
    
    if not answers or not answers.get("confirm"):
        print("⏭  Skipped.")
        return None
    
    entry["model_name"] = answers["display_name"]
    return entry


def main() -> None:
    """Main interactive workflow."""
    print("="*60)
    print("🛠  Update config.local.yaml with a new model")
    print("="*60)
    
    config_path = "config.local.yaml"
    
    # Step 1: Choose provider
    provider_key = select_provider()
    
    # Step 2: Create provider instance
    if provider_key == "openrouter":
        provider = OpenRouterProvider(config_path)
        
        # Optional: include/exclude free models
        include_free_q = inquirer.prompt([
            inquirer.Confirm("include_free",
                           message="Include free-tier models?",
                           default=True),
        ])
        include_free = include_free_q["include_free"] if include_free_q else True
        
        print("\n📡 Fetching models from OpenRouter...")
        models = provider.fetch_models(include_free=include_free)
        
        if models:
            # Show some stats
            free_count = sum(1 for m in models if float(m.get("pricing", {}).get("prompt", "1").replace("inf", "1")) == 0)
            print(f"   Found {len(models)} models ({free_count} free)")
        
        selected = pick_from_fetched(models, "OpenRouter")
        
        if not selected:
            return
        
        model_id = selected["id"]
        display_name = selected.get("name")
        
        # Show pricing info if available
        pricing = selected.get("pricing", {})
        if pricing:
            prompt_cost = pricing.get("prompt", "N/A")
            completion_cost = pricing.get("completion", "N/A")
            print(f"\n   💰 Pricing - Prompt: ${prompt_cost}/1M, Completion: ${completion_cost}/1M")
        
        entry = provider.build_config_entry(model_id, display_name)
    
    elif provider_key == "ollama":
        # Allow custom API base
        custom_host_q = inquirer.prompt([
            inquirer.Confirm("custom",
                           message="Use custom Ollama host?",
                           default=False),
        ])
        api_base = "http://localhost:11434"
        if custom_host_q and custom_host_q["custom"]:
            host_q = inquirer.prompt([
                inquirer.Text("host",
                             message="Ollama API base URL",
                             default=api_base),
            ])
            api_base = host_q["host"] if host_q else api_base
        
        provider = OllamaProvider(config_path, api_base=api_base)
        
        print(f"\n📡 Querying Ollama at {api_base}...")
        models = provider.fetch_models()
        
        if models and "name" in models[0]:
            print(f"   Found {len(models)} local models")
        
        selected = pick_from_fetched(models, "Ollama")
        
        if not selected:
            # Fallback to manual entry
            manual_provider = ManualProvider(config_path)
            entry = manual_provider.build_config_entry("", None)
        else:
            model_id = selected.get("name", selected.get("id", ""))
            entry = provider.build_config_entry(model_id, None)
    
    elif provider_key == "huggingface":
        provider = HuggingFaceProvider(config_path)
        
        search_q = inquirer.prompt([
            inquirer.Text("query",
                         message="Search query (or press Enter to see all)",
                         default=""),
        ])
        query = search_q["query"] if search_q else ""
        
        print("\n📡 Searching HuggingFace models...")
        models = provider.fetch_models(search_query=query)
        
        print(f"   Found {len(models)} models")
        
        selected = pick_from_fetched(models, "HuggingFace")
        
        if not selected:
            return
        
        # For HF, also ask about the TGI endpoint
        endpoint_q = inquirer.prompt([
            inquirer.Text("endpoint",
                         message="HF Inference API base URL (leave empty to set via env var later)",
                         default=""),
        ])
        
        entry = provider.build_config_entry(selected["id"], selected["name"])
    
    elif provider_key == "manual":
        provider = ManualProvider(config_path)
        dummy_models = provider.fetch_models()
        selected = pick_from_fetched(dummy_models, "Manual")
        
        if not selected or selected["id"] != "custom":
            # Use as template
            model_id = selected.get("id", "") if selected else ""
        else:
            model_id = ""
        
        entry = provider.build_config_entry(model_id, None)
    
    else:
        print("\n❌ Unknown provider.")
        return
    
    if not entry:
        return
    
    # Step 3: Confirm and possibly rename
    print(f"\n📝 Model: {entry['litellm_params'].get('model', 'N/A')}")
    final_entry = confirm_and_rename(entry)
    
    if not final_entry:
        return
    
    # Step 4: Update config file
    config = load_config(config_path)
    add_model_to_config(config, final_entry)
    save_config(config, config_path)
    
    print(f"\n✨ Added model '{final_entry['model_name']}'")
    print("\n--- Next steps ---")
    print(f"1. Review changes: cat {config_path}")
    print("2. Set any required environment variables (e.g., OPENROUTER_API_KEY)")
    print("3. Restart the LiteLLM server to pick up changes")
    print("="*60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
