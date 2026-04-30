# PLAN.md - Bedrock Login Management UI Updates
## Date: 2026-05-01
## Objective
Resolve 4 reported issues, implement requested workflow changes for Bedrock models, and improve overall UX of the LiteLLM proxy management interface.

---
## Background & Current State
### Tech Stack
- Backend: Python 3.12+, FastAPI (`management_app.py`)
- Proxy: LiteLLM (`litellm[proxy]`)
- Config Storage: YAML files (`config.yaml`, `config.local.yaml`, `config.bedrock.yaml`)
- Frontend: Embedded vanilla HTML/JS in `management_app.py`
- Container: Docker/Podman, started via `start_container.sh`

### Existing Pain Points
1. **Rename Bug**: Renamed models disappear from the list; bedrock models are baked into `config.bedrock.yaml` (read-only, merged at startup) and cannot be renamed.
2. **Native Popups**: Uses browser `alert()`/`prompt()` for all feedback/input, which is jarring and blocks interaction.
3. **No Persistence**: Config files are stored inside the container, lost on restart without volume mounts.
4. **No Delete**: No way to remove models from the config.

---
## Issue 1: Model Disappears After Rename
### Root Causes
1. Bedrock models are pre-merged from `config.bedrock.yaml` (baked into container image) and cannot be modified via the rename endpoint (only checks `config.local.yaml`).
2. LiteLLM proxy only reads config on startup; changes to `config.yaml` are not picked up until the proxy is reloaded.
3. No way to exclude original bedrock entries when renaming to `config.local.yaml`.

### Fixes
1. **Stop Pre-Merging Bedrock Configs**:
   - Modify `merge_configs()` in `management_app.py` to ONLY merge `config.local.yaml` into `config.yaml` (remove `config.bedrock.yaml` from the merge process).
   - Update `start_container.sh` to remove legacy merge logic.

2. **Make Bedrock Models Addable (User Request)**:
   - Do NOT merge bedrock models at startup; instead add a new "Bedrock (Mantle)" provider option to the "Add New Model" dropdown.
   - Implement `GET /api/providers/bedrock/models` endpoint in `management_app.py` to list available Bedrock Mantle models (source from the old `config.bedrock.yaml` template, excluding models already added to `config.local.yaml`).
   - Add UI to fetch, select, and add Bedrock models to `config.local.yaml` (same workflow as OpenRouter/Ollama).

3. **Fix Rename Endpoint**:
   - Update `PUT /api/models/{old_model_name}` to only operate on models in `config.local.yaml` (all addable models now live here).
   - After successful rename: call `merge_configs()`, then trigger LiteLLM reload.

4. **LiteLLM Auto-Reload**:
   - Update `start_container.sh` to write the LiteLLM process PID to `/tmp/litellm.pid` on startup.
   - Add `reload_litellm()` helper in `management_app.py` that sends `SIGHUP` to the PID in `/tmp/litellm.pid` (LiteLLM supports SIGHUP for config reload without full restart).
   - Call `reload_litellm()` after all config changes (add/rename/delete).

---
## Issue 2: Replace Confirmation Popups with Silent Toasts
### Current State
Uses 8 `alert()` calls and 1 `prompt()` call for all user feedback/input.

### Fixes
1. **Toast Container**:
   - Add a fixed `div#toast-container` positioned below the theme toggle button (top-right of page) in the HTML.

2. **Toast Styling**:
   - Add CSS for toast notifications: success (green background), error (red background), fade-in animation, auto-dismiss after 3 seconds.

3. **Toast JS Function**:
   - Implement `showToast(message, type = 'success', duration = 3000)` that creates toast elements, appends to the container, and auto-removes after the duration.

4. **Replace All Alerts**:
   - Swap every `alert()` call in the frontend JS with `showToast()`.

5. **Replace Rename Prompt**:
   - Make model names in the list clickable: clicking a name converts it to an inline text input pre-filled with the current name.
   - Pressing Enter submits the rename, Escape cancels, clicking outside cancels.
   - No native `prompt()` used.

---
## Issue 3: Model Persistence After Container Restarts
### Root Cause
Config files are stored at `/app/` inside the container; without a volume mount, changes are lost on container recreation.

### Fixes
1. **Config Directory Env Var**:
   - Add `CONFIG_DIR` environment variable support in `management_app.py` and `start_container.sh` (default: `/app`). All config paths (`config.yaml`, `config.local.yaml`) will use this directory.

2. **Default Config Initialization**:
   - Update `start_container.sh` to check if `config.local.yaml` exists in `CONFIG_DIR`; if not, create an empty `{"model_list": []}` template.
   - Store the old `config.bedrock.yaml` as a default template in the container image, copied to `CONFIG_DIR` on startup if not present (for the "Add Bedrock Model" workflow).

3. **Persistence Documentation**:
   - Create `docker-compose.yml` example with volume mount:
     ```yaml
     volumes:
       - ./local_config:/app:Z
     ```
   - Add a note in the management UI footer about required volume mounts for persistence.

---
## Issue 4: Delete Models Functionality
### Fixes
1. **Backend Endpoint**:
   - Add `DELETE /api/models/{model_name}` endpoint in `management_app.py`:
     - Load `config.local.yaml`
     - Remove the model with matching `model_name`
     - Save config, call `merge_configs()`, trigger LiteLLM reload
     - Return success/error response

2. **Frontend UI**:
   - Add a small "Delete" button next to each model in the list (no confirmation popup).
   - Implement `deleteModel(modelName)` JS function that calls the DELETE endpoint, shows a toast on success/failure, then reloads the model list.

3. **Bedrock Model Delete**:
   - Since Bedrock models are now added to `config.local.yaml`, deleting them removes the entry from the user config (no special handling needed).

---
## Implementation Order
1. **Backend Config & Endpoints** (`management_app.py`):
   - Update `merge_configs()` to stop merging bedrock config
   - Add `CONFIG_DIR` env var support
   - Add `GET /api/providers/bedrock/models` endpoint
   - Add `DELETE /api/models/{model_name}` endpoint
   - Fix `PUT /api/models/{old_model_name}` rename endpoint
   - Add `reload_litellm()` helper with SIGHUP support
   - Update all config change paths to call `merge_configs()` + `reload_litellm()`

2. **Frontend UI Updates** (`management_app.py` embedded HTML/JS):
   - Add Bedrock provider to "Add New Model" dropdown
   - Implement Bedrock model fetch/add UI
   - Add toast container, CSS, and `showToast()` function
   - Replace all `alert()` with `showToast()`
   - Replace `prompt()` rename with inline editable model names
   - Add delete buttons to model list items
   - Implement `deleteModel()` function

3. **Startup Script Updates** (`start_container.sh`):
   - Remove legacy config merge logic
   - Add LiteLLM PID writing to `/tmp/litellm.pid`
   - Add default config initialization for `CONFIG_DIR`
   - Add `CONFIG_DIR` env var support

4. **Persistence & Docs**:
   - Create `docker-compose.yml` example
   - Add volume mount instructions to UI footer

---
## File Changes Checklist
| File | Changes |
|------|---------|
| `management_app.py` | Update merge_configs, add endpoints, add reload helper, update frontend HTML/JS for toasts, Bedrock provider, delete, inline rename |
| `start_container.sh` | Remove merge logic, add PID writing, default config init, CONFIG_DIR support |
| `docker-compose.yml` | New file: example compose with volume mounts |
| `config.bedrock.yaml` | Keep as default template in container image for Bedrock add workflow |

---
## Success Criteria
- [ ] Renaming a model updates the list immediately, persists after LiteLLM reload
- [ ] No native `alert()` or `prompt()` popups remain
- [ ] Toasts appear near theme toggle, auto-dismiss after 3s
- [ ] Models added via UI persist after container restart with volume mount
- [ ] Delete button removes models from config, persists
- [ ] Bedrock models are only added via "Add New Model" section, no pre-merging at startup
- [ ] LiteLLM picks up config changes without full container restart (SIGHUP reload)
