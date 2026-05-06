# Task 1: Troubleshoot Models Unavailable After Rename/Reload

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

---

## Testing Plan

### Prerequisites
- Container can be rebuilt and redeployed with the new changes
- Access to the Management UI at http://localhost:8282
- Ability to check container logs

### Test 1: Verify PYTHONPATH Fix (Fix 1)

**Goal:** Confirm `token_refresher` is importable after reload

1. Build and start the updated container
2. Check the LiteLLM log after container start:
   ```bash
   docker logs <container_name> | grep -A5 -B5 "token_refresher"
   ```
3. Via Management UI, click "Reload LiteLLM"
4. Check the LiteLLM log for import errors:
   ```bash
   curl -s http://localhost:8282/api/logs?lines=100 | grep -i "import\|token_refresher\|error"
   ```
5. **Expected:** No import errors related to `token_refresher`

### Test 2: Verify Config Write Verification (Fix 2)

**Goal:** Confirm `config.yaml` is written and verified correctly

1. Add a new model via the Management UI
2. Check the Management UI logs for verification message:
   ```bash
   curl -s http://localhost:8282/api/logs?lines=50 | grep "merged and verified"
   ```
3. **Expected:** Log shows `"[Merge] Config merged and verified. Total models: N"` with correct count

### Test 3: Verify Health Check Improvement (Fix 3)

**Goal:** Confirm health check waits longer and logs debug info

1. Trigger a reload from the Management UI
2. Watch the logs during reload:
   ```bash
   curl -s http://localhost:8282/api/logs?lines=200 | grep "Health check"
   ```
3. **Expected:** If health check has issues, there should be a log entry at attempt 5

### Test 4: Complete Flow - Rename Model

**Goal:** Verify the full flow works without models becoming unavailable

1. Add a model via the Management UI (note the model name, e.g., `bedrock/us.anthropic.claude-3`)
2. Verify the model appears in the API:
   ```bash
   curl http://localhost:4000/v1/models -H "Authorization: Bearer test-key" | python3 -m json.tool
   ```
3. Rename the model via the UI (e.g., to `claude-3`)
4. Check that `config.yaml` was updated:
   ```bash
   docker exec <container_name> cat /app/config.yaml | grep -A2 "model_name"
   ```
5. Click "Reload LiteLLM" in the UI
6. Wait for the reload to complete (check the UI for success message)
7. Verify the renamed model is accessible:
   ```bash
   curl http://localhost:4000/v1/chat/completions \
     -H "Authorization: Bearer test-key" \
     -H "Content-Type: application/json" \
     -d '{"model": "claude-3", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 10}'
   ```
8. **Expected:** The API call should succeed with the renamed model

### Test 5: Multiple Reloads

**Goal:** Ensure stability across multiple reloads

1. After Test 4, click "Reload LiteLLM" again
2. Verify models are still accessible
3. Repeat 2-3 times
4. **Expected:** Models remain available after each reload

### Success Criteria

- [ ] No import errors for `token_refresher` in LiteLLM logs
- [ ] Config verification message appears in logs after merge
- [ ] Health check logs appear (if there are issues)
- [ ] Renamed models remain accessible after reload
- [ ] Multiple consecutive reloads work without issues

### Rollback Plan

If issues are found:
1. Check `git log develop` for commit `f8c16d0`
2. Revert with: `git revert f8c16d0`
3. Rebuild and redeploy container


# Task 2: Fix Management UI Entry Text Box Reload Bug

When entering the verification code into the text box on the Authentication page of the Mananagement UI, the page reloads and clears the input before it can be submitted.

# Task 3: Clean up logs page in Management UI

- The Hide Logs link should be a button
- The auto-refresh icon should be a clock or refresh icon, not a weird arc
- Hide Logs link should say "Show Logs" when logs are hidden, and "Hide Logs" when logs are visible
- Logs should be hidden by default, and only shown when the user clicks "Show Logs"
- Remove the word "Debug" from the Token Refresher logs section

## Task 4: Renaming model bug

When renaming a model, pressing enter does not make the text box return to non-edit mode. Even clicking elsewhere on the page does not exit edit mode. The user has to wait 45 seconds for the LiteLLM reload to complete before the text box exits edit mode. This is a bad user experience and should be fixed so that pressing enter or clicking outside the text box immediately exits edit mode, and the reload happens in the background without blocking the UI. A visual indicator (like a spinner) can show that the reload is in progress, but the user should be able to continue using the UI while waiting for the reload to complete.

## Task 5: Make a Free Filter for OpenRouter models

Add a check box that allows the user to filter for only free OpenRouter models.

## Task 6: Add reasoning effort levels as a dropdown next to each model

In passing API calls ot LiteLLM, we can add a `reasoning_effort` parameter that tells the system how much time/effort to put into reasoning for that call. This can be used to save costs on calls that don't need a lot of reasoning, while allowing more effort for calls that do. We can add a dropdown next to each model in the Management UI that allows the user to select the reasoning effort level for that model (e.g. Low, Medium, High). This selection would then be passed as a parameter in API calls that use that model.

```
response = litellm.completion(
    model="o3-mini",
    messages=[{"role": "user", "content": "..."}],
    reasoning_effort="medium"  # "low", "medium", or "high"
)
```