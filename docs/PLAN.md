# Troubleshooting Plan: Models Unavailable After Rename/Reload

## Problem Statement
Renaming a model (or reloading LiteLLM) causes models to become unavailable. This document outlines the investigation steps and proposed fixes.

## Suspected Root Causes

After examining the codebase, I've identified these potential issues:

1. **Python Path Issue on Reload** - When `reload_litellm()` starts a new process, `token_refresher.py` may not be in the Python path
2. **Config File Path Mismatch** - CONFIG_PATH vs CONFIG_DIR inconsistency
3. **Model Rename Only Updates Display Name** - `db.rename_model()` only updates `model_name` field
4. **Health Check Timing** - Health check may timeout too quickly or not properly detect failure

---

## Investigation Steps

### Step 1: Check LiteLLM Logs After Reload

```bash
# View the LiteLLM log to see if it fails to start
cat /app/litellm.log | tail -100

# Or via API
curl http://localhost:8282/api/logs?lines=100
```

Look for import errors related to `token_refresher`.

### Step 2: Verify token_refresher Import Path

```bash
# Check if token_refresher.py is in CONFIG_DIR
ls -la /app/token_refresher.py

# Check Python path when LiteLLM restarts
echo $PYTHONPATH
```

The `start_container.sh` copies `token_refresher.py` to CONFIG_DIR, but the Python path may not include this directory when LiteLLM restarts via `reload_litellm()`.

### Step 3: Validate Generated config.yaml

```bash
# Check if config.yaml is valid and has correct structure
cat /app/config.yaml

# Validate YAML syntax
python3 -c "import yaml; yaml.safe_load(open('/app/config.yaml'))"

# Check model count
python3 -c "import yaml; c=yaml.safe_load(open('/app/config.yaml')); print(f'Models: {len(c.get(\"model_list\", []))}')"
```

### Step 4: Test Model Rename Database Update

Check if rename actually updates the database correctly:

```python
# The db.rename_model() function (db.py:59-64) only updates 'model_name' field
# Verify the model structure in TinyDB
python3 -c "from db import *; print(get_all_models())"
```

### Step 5: Check if LiteLLM Process is Running

```bash
# Check if LiteLLM is running
ps aux | grep litellm
curl http://localhost:4000/health
```

---

## Proposed Fixes

### Fix 1: Add CONFIG_DIR to Python Path in reload_litellm()

**File:** `src/management_app.py`

In the `reload_litellm()` function, modify the subprocess call to include the config directory in the Python path:

```python
def reload_litellm() -> dict:
    """Reload LiteLLM by restarting the process. Returns dict with status info."""
    pid_file = "/tmp/litellm.pid"
    config_path = os.environ.get("CONFIG_PATH", "/app/config.yaml")
    config_dir = os.environ.get("CONFIG_DIR", "/app")

    # ... existing code ...

    # Step 3: Start new LiteLLM process
    try:
        log_path = os.path.join(config_dir, "litellm.log")
        cmd = [
            "litellm",
            "--config",
            config_path,
            "--port",
            "4000",
            "--host",
            "0.0.0.0",
        ]
        print(f"[Reload] Starting LiteLLM: {' '.join(cmd)}")

        # Add CONFIG_DIR to PYTHONPATH so token_refresher can be imported
        env = os.environ.copy()
        python_path = env.get("PYTHONPATH", "")
        if config_dir not in python_path.split(":"):
            env["PYTHONPATH"] = f"{config_dir}:{python_path}" if python_path else config_dir

        with open(log_path, "a") as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=config_dir,
                env=env,  # Add this to pass modified environment
            )
        new_pid = process.pid
        with open(pid_file, "w") as f:
            f.write(str(new_pid))
        print(f"[Reload] LiteLLM started with PID {new_pid}")

        # ... rest of function ...
```

### Fix 2: Verify config.yaml is Written Correctly

**File:** `src/management_app.py`

Add a verification step in `merge_configs()`:

```python
def merge_configs():
    """Merge TinyDB models and settings into config.yaml for LiteLLM."""
    merged = db.get_models_for_litellm()

    try:
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(
                merged, f, default_flow_style=False, sort_keys=False, allow_unicode=True
            )

        # Verify the file was written correctly
        with open(CONFIG_PATH, "r") as f:
            verify = yaml.safe_load(f)
        print(f"[Merge] Config merged and verified. Total models: {len(verify.get('model_list', []))}")
    except Exception as e:
        print(f"[Merge] Error writing merged config: {e}", file=sys.stderr)
```

### Fix 3: Improve Health Check in reload_litellm()

**File:** `src/management_app.py`

The health check might be timing out too quickly:

```python
# In reload_litellm(), improve the health check:
# Step 5: Health check
for i in range(30):  # Increase retries from 10 to 30
    try:
        resp = requests.get("http://localhost:4000/health", timeout=2)
        if resp.status_code < 500:
            print(f"[Reload] LiteLLM health check passed (PID {new_pid})")
            return {
                "success": True,
                "pid": new_pid,
                "message": f"LiteLLM restarted (PID {new_pid})",
            }
    except Exception as e:
        if i == 5:  # Print error once
            print(f"[Reload] Health check attempt {i}: {e}")
    time.sleep(1)
```

### Fix 4: Ensure Model Rename Updates All Necessary Fields

**File:** `src/db.py`

The `rename_model()` function only updates `model_name`. If there are any cached references or if the model name is used elsewhere in the structure, they may not be updated:

```python
def rename_model(old_name, new_name):
    """Rename a model. Returns True if renamed."""
    # Get the full model to preserve all fields
    model = models_table.get(where("model_name") == old_name)
    if not model:
        return False

    # Update the model_name
    result = models_table.update(
        {"model_name": new_name}, where("model_name") == old_name
    )
    return len(result) > 0
```

---

## Quick Diagnostic Commands

Run these to gather more info:

```bash
# 1. Check if LiteLLM is running
ps aux | grep litellm
curl http://localhost:4000/health

# 2. Check config.yaml validity and content
python3 -c "import yaml; c=yaml.safe_load(open('/app/config.yaml')); print(f'Models: {len(c.get(\"model_list\", []))}'); import json; print(json.dumps(c, indent=2))"

# 3. Check if token_refresher can be imported
python3 -c "import sys; sys.path.insert(0, '/app'); from token_refresher import BedrockTokenRefresher; print('Import OK')"

# 4. View recent LiteLLM logs
tail -50 /app/litellm.log

# 5. Check TinyDB content
python3 -c "from tinydb import TinyDB; db = TinyDB('/app/models.db.json'); print(db.all())"

# 6. Test the complete flow manually
# Add a model, rename it, check config.yaml, then reload
```

---

## Recommended Implementation Order

1. **First:** Run the diagnostic commands to confirm the root cause
2. **Implement Fix 1** (PYTHONPATH) - Most likely cause based on code review
3. **Implement Fix 2** (config verification) - Adds safety check
4. **Implement Fix 3** (health check) - Improves reliability
5. **Test the complete flow:**
   - Add a model via the UI
   - Rename the model
   - Verify config.yaml is updated
   - Check if LiteLLM reloads successfully
   - Verify the model is still accessible via API

---

## Additional Notes

- The `start_container.sh` script copies `token_refresher.py` to CONFIG_DIR, but this only happens at container start
- When `reload_litellm()` is called, it starts a new process without ensuring the Python path includes CONFIG_DIR
- The `token_refresher.BedrockTokenRefresher` callback in `litellm_settings` requires the module to be importable
- If the import fails, LiteLLM may start but without the token refresher, or may fail to start entirely
