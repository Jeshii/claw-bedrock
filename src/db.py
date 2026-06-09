from tinydb import TinyDB, where, Query
import os
import secrets
import yaml
import datetime
import json
import encryption_utils

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app")
DB_PATH = os.path.join(CONFIG_DIR, "clawbedrock.db.json")
LOCAL_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.local.yaml")

# Initialize TinyDB with caching for better performance
db = TinyDB(DB_PATH, indent=2, sort_keys=True)
# Set secure file permissions for the database
os.chmod(DB_PATH, 0o600)
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
    encrypted_key = get_setting("litellm_master_key", None)
    if encrypted_key:
        return encryption_utils.decrypt_data(encrypted_key)
    return None


def generate_master_key():
    """Generate a new cryptographically secure master key and persist it."""
    key = "sk-claw-" + secrets.token_urlsafe(32)
    encrypted_key = encryption_utils.encrypt_data(key)
    set_setting("litellm_master_key", encrypted_key)
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
    """Get full config for LiteLLM including models, router_settings, and litellm_settings.

    If a model has `model_group` set, its `model_name` in the LiteLLM config
    becomes the group name (with prefix applied if enabled). Multiple models
    sharing the same `model_group` form a failover group in LiteLLM.
    """
    use_prefix = get_setting("use_prefix", True)
    config = {"model_list": []}

    for m in models_table.all():
        entry = dict(m)
        if entry.get("model_group"):
            group = entry["model_group"]
            if use_prefix and not group.startswith("claw-bedrock/"):
                entry["model_name"] = f"claw-bedrock/{group}"
            else:
                entry["model_name"] = group
        config["model_list"].append(entry)

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


def seed_default_providers():
    if providers_table.all():
        return
    providers_table.insert(
        {
            "name": "bedrock",
            "display_name": "Bedrock (Mantle)",
            "type": "bedrock",
            "color": "#FF9900",
            "notes": "AWS Bedrock via Mantle — pre-configured on startup",
            "aws_region": "us-east-1",
            "aws_access_key_env": "AWS_ACCESS_KEY_ID",
            "aws_secret_key_env": "AWS_SECRET_ACCESS_KEY",
        }
    )


def _is_sensitive_field(field_name: str) -> bool:
    """Check if a field name is sensitive (contains key, secret, password, or token)."""
    sensitive_keywords = ["key", "secret", "password", "token"]
    field_lower = field_name.lower()
    return any(keyword in field_lower for keyword in sensitive_keywords)


def _encrypt_sensitive_fields(provider: dict) -> dict:
    """Encrypt sensitive fields in a provider dict before saving to DB."""
    encrypted = provider.copy()
    for field, value in provider.items():
        if isinstance(value, str) and _is_sensitive_field(field):
            encrypted[field] = encryption_utils.encrypt_data(value)
    return encrypted


def _decrypt_sensitive_fields(provider: dict) -> dict:
    """Decrypt sensitive fields in a provider dict after reading from DB."""
    decrypted = provider.copy()
    for field, value in provider.items():
        if isinstance(value, str) and _is_sensitive_field(field):
            decrypted[field] = encryption_utils.decrypt_data(value)
    return decrypted


def get_all_providers():
    """Get all provider definitions."""
    providers = providers_table.all()
    return [_decrypt_sensitive_fields(p) for p in providers]


def get_provider(name):
    """Get a single provider by name."""
    provider = providers_table.get(where("name") == name)
    if provider:
        return _decrypt_sensitive_fields(provider)
    return None


def upsert_provider(provider: dict):
    """Create or update a provider. `provider` must include a `name` key."""
    encrypted = _encrypt_sensitive_fields(provider)
    providers_table.upsert(encrypted, where("name") == provider["name"])


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


BACKUP_SCHEMA_VERSION = 1


def export_backup() -> dict:
    """Dump all tables into a portable backup dict with decrypted provider fields."""
    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "data": {
            "models": [dict(m) for m in models_table.all()],
            "tags": [dict(t) for t in tags_table.all()],
            "settings": get_settings(),
            "providers": [
                _decrypt_sensitive_fields(dict(p)) for p in providers_table.all()
            ],
        },
    }


def import_backup(backup: dict, mode: str = "replace") -> dict:
    """
    Restore from a backup dict.

    mode="replace"  — clears all existing data before importing (full restore)
    mode="merge"    — adds new records, skips existing ones (non-destructive)

    Returns a summary dict with counts of imported records.
    """
    _validate_backup(backup)
    data = backup["data"]
    summary = {"models": 0, "tags": 0, "settings": 0, "providers": 0, "skipped": 0}

    if mode == "replace":
        _auto_backup_before_replace()
        models_table.truncate()
        tags_table.truncate()
        settings_table.truncate()
        providers_table.truncate()

    for model in data.get("models", []):
        name = model.get("model_name")
        if mode == "merge" and model_name_exists(name):
            summary["skipped"] += 1
            continue
        if mode == "replace":
            models_table.insert(model)
        else:
            models_table.upsert(model, where("model_name") == name)
        summary["models"] += 1

    for tag in data.get("tags", []):
        name = tag.get("name")
        color = tag.get("color", "#607D8B")
        if mode == "merge" and tags_table.contains(where("name") == name):
            summary["skipped"] += 1
            continue
        upsert_tag(name, color)
        summary["tags"] += 1

    for key, value in data.get("settings", {}).items():
        if mode == "merge" and settings_table.contains(where("key") == key):
            summary["skipped"] += 1
            continue
        set_setting(key, value)
        summary["settings"] += 1

    for provider in data.get("providers", []):
        name = provider.get("name")
        if mode == "merge" and providers_table.contains(where("name") == name):
            summary["skipped"] += 1
            continue
        upsert_provider(provider)
        summary["providers"] += 1

    return summary


def _validate_backup(backup: dict):
    """Raise ValueError if the backup structure is invalid."""
    if not isinstance(backup, dict):
        raise ValueError("Backup must be a JSON object")
    if "data" not in backup:
        raise ValueError("Missing 'data' key in backup")
    schema = backup.get("schema_version", 0)
    if schema > BACKUP_SCHEMA_VERSION:
        raise ValueError(
            f"Backup schema version {schema} is newer than supported ({BACKUP_SCHEMA_VERSION}). "
            "Upgrade claw-bedrock before importing."
        )
    data = backup["data"]
    if not isinstance(data.get("models", []), list):
        raise ValueError("'data.models' must be a list")
    if not isinstance(data.get("tags", []), list):
        raise ValueError("'data.tags' must be a list")
    if not isinstance(data.get("settings", {}), dict):
        raise ValueError("'data.settings' must be an object")
    if not isinstance(data.get("providers", []), list):
        raise ValueError("'data.providers' must be a list")


def _auto_backup_before_replace():
    """Write a timestamped JSON snapshot to CONFIG_DIR before a destructive import."""
    try:
        snapshot = export_backup()
        ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(CONFIG_DIR, f"auto-backup-{ts}.json")
        with open(path, "w") as f:
            json.dump(snapshot, f, indent=2)
        os.chmod(path, 0o600)
        print(f"[Backup] Auto-backup written to {path}")
    except Exception as e:
        print(f"[Backup] Auto-backup failed: {e}")


def close_db():
    """Close the database connection."""
    db.close()
