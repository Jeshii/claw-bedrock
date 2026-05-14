# Backup & Restore Module

## Overview

The backup module lets you export the entire claw-bedrock configuration (models, tags, settings) as a single JSON file and restore from one — all from a dedicated **Backup** tab in the management UI. Since the data lives in TinyDB (`models.db.json`), exporting is simply a structured dump and importing is a validated merge or replace.

---

## What Gets Backed Up

| Table | Contents |
|---|---|
| `models` | All model records (model_name, litellm_params, tags, etc.) |
| `tags` | Tag definitions (name, color) |
| `settings` | All key/value settings (use_prefix, router_settings, etc.) |
| `providers` *(future)* | Provider records if implemented |

The backup format is self-describing JSON with a schema version field, so future migrations stay manageable.

---

## Backup File Format

```json
{
  "schema_version": 1,
  "created_at": "2026-05-14T08:00:00Z",
  "claw_version": "1.0.0",
  "data": {
    "models": [ ... ],
    "tags": [ ... ],
    "settings": { "use_prefix": true, "router_settings": {} }
  }
}
```

Using `settings` as a flat dict (not the raw `[{"key": ..., "value": ...}]` TinyDB format) makes backups human-readable and easy to hand-edit.

---

## Implementation Steps

### Step 1: DB Layer — Export & Import Functions

Add to `src/db.py`:

```python
import datetime

BACKUP_SCHEMA_VERSION = 1


def export_backup() -> dict:
    """Dump all tables into a portable backup dict."""
    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "data": {
            "models": [dict(m) for m in models_table.all()],
            "tags": [dict(t) for t in tags_table.all()],
            "settings": get_settings(),  # already returns a flat dict
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
    summary = {"models": 0, "tags": 0, "settings": 0, "skipped": 0}

    if mode == "replace":
        _auto_backup_before_replace()
        models_table.truncate()
        tags_table.truncate()
        settings_table.truncate()

    # Import models
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

    # Import tags
    for tag in data.get("tags", []):
        name = tag.get("name")
        color = tag.get("color", "#607D8B")
        if mode == "merge" and tags_table.contains(where("name") == name):
            summary["skipped"] += 1
            continue
        upsert_tag(name, color)
        summary["tags"] += 1

    # Import settings
    for key, value in data.get("settings", {}).items():
        if mode == "merge" and settings_table.contains(where("key") == key):
            summary["skipped"] += 1
            continue
        set_setting(key, value)
        summary["settings"] += 1

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


def _auto_backup_before_replace():
    """Write a timestamped JSON snapshot to CONFIG_DIR before a destructive import."""
    try:
        snapshot = export_backup()
        ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(CONFIG_DIR, f"auto-backup-{ts}.json")
        with open(path, "w") as f:
            json.dump(snapshot, f, indent=2)
        print(f"[Backup] Auto-backup written to {path}")
    except Exception as e:
        print(f"[Backup] Auto-backup failed: {e}")
```

---

### Step 2: API Routes

Add to `src/management_app.py`:

```python
import datetime

# ── Backup / Restore ──────────────────────────────────────────────────────

@app.get("/api/backup/export")
async def export_backup():
    """Download current config as a JSON backup file."""
    data = db.export_backup()
    data["claw_version"] = get_version()

    filename = f"claw-bedrock-backup-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"

    from fastapi.responses import JSONResponse
    response = JSONResponse(content=data)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@app.post("/api/backup/import")
async def import_backup(request: Request):
    """
    Import a backup file.
    Query param `mode`: "replace" (default) or "merge"
    """
    mode = request.query_params.get("mode", "replace")
    if mode not in ("replace", "merge"):
        raise HTTPException(400, "mode must be 'replace' or 'merge'")

    try:
        backup = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    try:
        summary = db.import_backup(backup, mode=mode)
    except ValueError as e:
        raise HTTPException(400, str(e))

    merge_configs()
    reload_result = reload_litellm()

    return {
        "success": True,
        "mode": mode,
        "imported": summary,
        "litellm_reloaded": reload_result.get("success"),
    }


@app.post("/api/backup/preview")
async def preview_backup(request: Request):
    """
    Parse an uploaded backup and return a summary without applying it.
    Used by the UI to show a confirmation dialog before import.
    """
    try:
        backup = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    try:
        db._validate_backup(backup)
    except ValueError as e:
        raise HTTPException(400, str(e))

    data = backup["data"]
    return {
        "valid": True,
        "schema_version": backup.get("schema_version"),
        "created_at": backup.get("created_at"),
        "claw_version": backup.get("claw_version", "unknown"),
        "counts": {
            "models": len(data.get("models", [])),
            "tags": len(data.get("tags", [])),
            "settings": len(data.get("settings", {})),
        },
    }
```

---

### Step 3: Backup Tab UI

Add a **Backup** tab/section to `templates/management.html`. The tab has two cards side by side: Export and Import.

#### Export Card

```html
<section class="settings-card" id="backup-export">
  <h2>Export</h2>
  <p class="muted">
    Downloads a complete snapshot of all models, tags, and settings
    as a JSON file. Keep this somewhere safe — it's your recovery point.
  </p>
  <div class="stat-row">
    <span id="export-model-count">— models</span>
    <span id="export-tag-count">— tags</span>
  </div>
  <button id="btn-export" class="btn btn-primary">
    ⬇ Download Backup
  </button>
</section>
```

```javascript
async function loadExportStats() {
  const [modelsRes, tagsRes] = await Promise.all([
    fetch("/api/models").then(r => r.json()),
    fetch("/api/tags").then(r => r.json()),
  ]);
  document.getElementById("export-model-count").textContent =
    `${modelsRes.models.length} models`;
  document.getElementById("export-tag-count").textContent =
    `${tagsRes.tags.length} tags`;
}

document.getElementById("btn-export").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = "/api/backup/export";
  a.download = "";
  a.click();
});

loadExportStats();
```

#### Import Card

```html
<section class="settings-card" id="backup-import">
  <h2>Import</h2>
  <p class="muted">
    Restore from a backup file. Choose <strong>Replace</strong> to
    overwrite all current data, or <strong>Merge</strong> to add
    records from the backup without removing existing ones.
  </p>

  <label for="import-file" class="file-drop-area" id="drop-zone">
    <span>Drop a backup JSON file here, or click to browse</span>
    <input type="file" id="import-file" accept=".json,application/json" hidden />
  </label>

  <div id="import-preview" class="preview-box" style="display:none"></div>

  <div class="radio-row" id="import-mode-row" style="display:none">
    <label>
      <input type="radio" name="import-mode" value="replace" checked />
      Replace — wipe current data and restore from backup
    </label>
    <label>
      <input type="radio" name="import-mode" value="merge" />
      Merge — add backup records, keep existing ones
    </label>
  </div>

  <button id="btn-import" class="btn btn-danger" style="display:none">
    ⬆ Import Backup
  </button>
</section>
```

```javascript
let pendingBackup = null;

const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("import-file");

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", e => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", e => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => handleFile(fileInput.files[0]));

async function handleFile(file) {
  if (!file) return;
  const text = await file.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    showImportError("File is not valid JSON.");
    return;
  }

  const res = await fetch("/api/backup/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(parsed),
  });
  const preview = await res.json();
  if (!res.ok) {
    showImportError(preview.detail || "Invalid backup file.");
    return;
  }

  pendingBackup = parsed;
  const previewEl = document.getElementById("import-preview");
  previewEl.innerHTML = `
    <p><strong>Backup valid ✓</strong></p>
    <ul>
      <li>Created: ${preview.created_at || "unknown"}</li>
      <li>Claw version: ${preview.claw_version}</li>
      <li>${preview.counts.models} models, ${preview.counts.tags} tags, ${preview.counts.settings} settings</li>
    </ul>`;
  previewEl.style.display = "block";
  document.getElementById("import-mode-row").style.display = "block";
  document.getElementById("btn-import").style.display = "inline-block";
}

document.getElementById("btn-import").addEventListener("click", async () => {
  if (!pendingBackup) return;
  const mode = document.querySelector('input[name="import-mode"]:checked').value;
  const warning = mode === "replace"
    ? "This will DELETE all current models, tags, and settings and replace them with the backup. Are you sure?"
    : "This will merge backup data into current config. Existing records are kept. Continue?";
  if (!confirm(warning)) return;

  const res = await fetch(`/api/backup/import?mode=${mode}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pendingBackup),
  });
  const result = await res.json();
  if (result.success) {
    alert(`Import complete! ${result.imported.models} models, ${result.imported.tags} tags imported. (${result.imported.skipped} skipped)`);
    location.reload();
  } else {
    alert("Import failed: " + (result.detail || "Unknown error"));
  }
});

function showImportError(msg) {
  const previewEl = document.getElementById("import-preview");
  previewEl.innerHTML = `<p class="error">✗ ${msg}</p>`;
  previewEl.style.display = "block";
  document.getElementById("btn-import").style.display = "none";
}
```

---

## UI Layout (Backup Tab)

```
┌─────────────────────────────────────────────────────────┐
│  BACKUP                                                 │
│                                                         │
│  ┌──────────────────────┐  ┌──────────────────────────┐ │
│  │  Export              │  │  Import                  │ │
│  │                      │  │                          │ │
│  │  12 models, 4 tags   │  │  ┌──────────────────┐   │ │
│  │                      │  │  │ Drop file here   │   │ │
│  │  [⬇ Download Backup] │  │  └──────────────────┘   │ │
│  │                      │  │                          │ │
│  └──────────────────────┘  │  ○ Replace  ● Merge      │ │
│                             │                          │ │
│                             │  [⬆ Import Backup]       │ │
│                             └──────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## Edge Cases & Notes

- **Empty DB import**: If the backup has 0 models, the import still succeeds — it's a valid state. The UI preview shows "0 models" so the user can catch this before confirming.
- **doc_id stripping**: TinyDB adds an internal `doc_id` field to each record. When exporting via `dict(m)`, this is included. On import, TinyDB's `insert()` ignores it. No special handling needed.
- **Concurrent writes**: TinyDB is not thread-safe for concurrent writes. Since the management app uses FastAPI (async), `import_backup` should be called with a lock if you later add background tasks. For now, the single-user management UI makes this a non-issue.
- **Config reload after import**: The import route calls `merge_configs()` + `reload_litellm()` automatically, so LiteLLM picks up restored models without manual intervention.
- **Auto-backups accumulate**: Consider adding a cleanup routine or capping auto-backups to the last 5 files in `CONFIG_DIR`.
