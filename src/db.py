from tinydb import TinyDB, where
import os
import yaml

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app")
DB_PATH = os.path.join(CONFIG_DIR, "models.db.json")
LOCAL_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.local.yaml")

# Initialize TinyDB with caching for better performance
db = TinyDB(DB_PATH, indent=2, sort_keys=True)
models_table = db.table("models")
settings_table = db.table("settings")


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


def get_litellm_settings():
    """Get litellm_settings with token_refresher baked in."""
    always_stream = get_setting("always_include_stream_usage", True)
    return {
        "callbacks": ["token_refresher.BedrockTokenRefresher"],
        "always_include_stream_usage": always_stream,
    }


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


def close_db():
    """Close the database connection."""
    db.close()
