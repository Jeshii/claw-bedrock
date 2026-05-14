from tinydb import TinyDB, where, Query
import os
import secrets
import yaml

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app")
DB_PATH = os.path.join(CONFIG_DIR, "models.db.json")
LOCAL_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.local.yaml")

# Initialize TinyDB with caching for better performance
db = TinyDB(DB_PATH, indent=2, sort_keys=True)
models_table = db.table("models")
settings_table = db.table("settings")
tags_table = db.table("tags")


def _migrate_yaml_to_db():
    """Migrate existing YAML config to TinyDB if db is empty and YAML exists."""
    if models_table.all():
        return  # Already migrated

    if not os.path.exists(LOCAL_CONFIG_PATH):
        return  # Nothing to migrate

    try:
        with open(LOCAL_CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f) or {}

        # Migrate models
        model_list = config.get("model_list", [])
        if model_list:
            models_table.insert_multiple(model_list)
            print(f"[DB] Migrated {len(model_list)} models from YAML to TinyDB")

        # Migrate settings
        if "use_prefix" in config:
            settings_table.upsert(
                {"key": "use_prefix", "value": config["use_prefix"]},
                where("key") == "use_prefix",
            )
    except Exception as e:
        print(f"[DB] Migration error: {e}")

    migrate_infer_providers()


def get_all_models():
    """Get all configured models."""
    return models_table.all()


def add_model(model):
    """Add a new model. Returns the inserted id."""
    return models_table.insert(model)


def delete_model(model_name):
    """Delete a model by model_name. Returns True if deleted."""
    result = models_table.remove(where("model_name") == model_name)
    return len(result) > 0


def rename_model(old_name, new_name):
    """Rename a model. Returns True if renamed."""
    result = models_table.update(
        {"model_name": new_name}, where("model_name") == old_name
    )
    return len(result) > 0


def update_model_field(model_name, updates: dict):
    """Update fields on a model. Returns True if updated."""
    result = models_table.update(updates, where("model_name") == model_name)
    return len(result) > 0


def get_model_by_name(model_name):
    """Get a specific model by name."""
    return models_table.get(where("model_name") == model_name)


def get_setting(key, default=None):
    """Get a setting value by key."""
    record = settings_table.get(where("key") == key)
    return record["value"] if record else default


def set_setting(key, value):
    """Set a setting value."""
    settings_table.upsert({"key": key, "value": value}, where("key") == key)


def get_settings():
    """Get all settings as a dict."""
    records = settings_table.all()
    return {r["key"]: r["value"] for r in records}


def get_router_settings():
    """Get router_settings from DB or return defaults."""
    settings = get_setting("router_settings", {})
    # Remove always_include_stream_usage as it's now in litellm_settings
    settings.pop("always_include_stream_usage", None)
    return settings


def set_router_settings(router_settings):
    """Save router_settings to DB."""
    settings_table.upsert(
        {"key": "router_settings", "value": router_settings},
        where("key") == "router_settings",
    )


def get_master_key():
    """Get the LiteLLM master key, or None if not set."""
    return get_setting("litellm_master_key", None)


def generate_master_key():
    """Generate a new cryptographically secure master key and persist it."""
    key = "sk-claw-" + secrets.token_urlsafe(32)
    set_setting("litellm_master_key", key)
    return key


def clear_master_key():
    """Remove the master key (disables auth on next reload)."""
    settings_table.remove(where("key") == "litellm_master_key")


def get_litellm_settings():
    """Get litellm_settings with token_refresher and optional master_key."""
    settings = {
        "callbacks": ["token_refresher.BedrockTokenRefresher"],
    }
    key = get_master_key()
    if key:
        settings["master_key"] = key
    return settings


def model_name_exists(model_name):
    """Check if a model name already exists."""
    return models_table.contains(where("model_name") == model_name)


def get_models_for_litellm():
    """Get full config for LiteLLM including models, router_settings, and litellm_settings."""
    config = {"model_list": [dict(m) for m in models_table.all()]}

    # Add router_settings from DB
    router_settings = get_router_settings()
    if router_settings:
        config["router_settings"] = router_settings

    # Add litellm_settings (token_refresher is baked in)
    config["litellm_settings"] = get_litellm_settings()

    return config


def get_all_tags():
    """Get all tag definitions."""
    return tags_table.all()


def get_tag(name):
    """Get a single tag definition by name."""
    return tags_table.get(where("name") == name)


def upsert_tag(name, color):
    """Create or update a tag definition."""
    tags_table.upsert({"name": name, "color": color}, where("name") == name)


def delete_tag(name):
    """Delete a tag definition and remove it from all models."""
    tags_table.remove(where("name") == name)
    q = Query()
    for m in models_table.search(q.tags.any(name)):
        tags = [t for t in m.get("tags", []) if t != name]
        models_table.update({"tags": tags}, doc_ids=[m.doc_id])


def add_tag_to_model(model_name, tag_name):
    """Add a tag to a model's tag list. Returns True if updated."""
    m = models_table.get(where("model_name") == model_name)
    if not m:
        return False
    tags = m.get("tags", [])
    if tag_name not in tags:
        tags.append(tag_name)
        models_table.update({"tags": tags}, where("model_name") == model_name)
    return True


def remove_tag_from_model(model_name, tag_name):
    """Remove a tag from a model's tag list. Returns True if updated."""
    m = models_table.get(where("model_name") == model_name)
    if not m:
        return False
    tags = [t for t in m.get("tags", []) if t != tag_name]
    models_table.update({"tags": tags}, where("model_name") == model_name)
    return True


def rename_tag(old_name, new_name):
    """Rename a tag definition and update all models using it."""
    tag = tags_table.get(where("name") == old_name)
    if not tag:
        return False
    tags_table.remove(where("name") == old_name)
    tags_table.insert({"name": new_name, "color": tag["color"]})
    q = Query()
    for m in models_table.search(q.tags.any(old_name)):
        tags = [new_name if t == old_name else t for t in m.get("tags", [])]
        models_table.update({"tags": tags}, doc_ids=[m.doc_id])
    return True


def get_models_by_tag(tag_name):
    """Get all models that have a specific tag."""
    q = Query()
    return models_table.search(q.tags.any(tag_name))


providers_table = db.table("providers")


def get_all_providers():
    """Get all provider definitions."""
    return providers_table.all()


def get_provider(name):
    """Get a single provider by name."""
    return providers_table.get(where("name") == name)


def upsert_provider(provider: dict):
    """Create or update a provider. `provider` must include a `name` key."""
    providers_table.upsert(provider, where("name") == provider["name"])


def delete_provider(name):
    """Delete a provider definition.

    Does NOT remove the `provider` field from models — those models will
    show an 'unknown provider' state in the UI, prompting reassignment.
    """
    providers_table.remove(where("name") == name)


def rename_provider(old_name, new_name):
    """Rename a provider and update all model references."""
    provider = providers_table.get(where("name") == old_name)
    if not provider:
        return False
    providers_table.remove(where("name") == old_name)
    provider["name"] = new_name
    providers_table.insert(provider)
    models_table.update(
        {"provider": new_name},
        where("provider") == old_name,
    )
    return True


def get_models_by_provider(provider_name):
    """Get all models assigned to a specific provider."""
    return models_table.search(where("provider") == provider_name)


def set_model_provider(model_name, provider_name):
    """Assign a provider to a model."""
    return models_table.update(
        {"provider": provider_name},
        where("model_name") == model_name,
    )


def migrate_infer_providers():
    """Scan existing models and auto-create provider records based on
    patterns in litellm_params (aws_region, api_base, etc.).
    Only runs if providers_table is empty.
    """
    if providers_table.all():
        return

    providers_seen: dict[str, dict] = {}
    for model in models_table.all():
        params = model.get("litellm_params", {})
        region = params.get("aws_region_name") or params.get("aws_region")
        if region:
            key = f"bedrock-{region}"
            if key not in providers_seen:
                providers_seen[key] = {
                    "name": key,
                    "display_name": f"AWS Bedrock ({region})",
                    "type": "bedrock",
                    "aws_region": region,
                    "color": "#FF9900",
                }
            models_table.update(
                {"provider": key},
                where("model_name") == model["model_name"],
            )

    for p in providers_seen.values():
        providers_table.insert(p)

    if providers_seen:
        print(f"[DB] Inferred {len(providers_seen)} providers from existing models")


def close_db():
    """Close the database connection."""
    db.close()
