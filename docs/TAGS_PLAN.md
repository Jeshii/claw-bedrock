# Tagging Feature Plan

## Data Layer (`src/db.py`)

New `tags` table in TinyDB. Add `tags: []` to model documents.

New functions:
- `get_all_tags()` → list all tag definitions
- `get_tag(name)` → get single tag
- `upsert_tag(name, color)` → create or update tag
- `delete_tag(name)` → remove tag definition + remove from all models
- `add_tag_to_model(model_name, tag_name)` → append to model's tags list
- `remove_tag_from_model(model_name, tag_name)` → remove from model's tags list
- `get_models_by_tag(tag_name)` → return models with specific tag

## API Endpoints (`src/management_app.py`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/tags` | List all tags |
| POST | `/api/tags` | Create tag `{name, color}` |
| PUT | `/api/tags/{name}` | Rename tag `{name: new_name}` |
| DELETE | `/api/tags/{name}` | Delete tag (removes from all models too) |
| PATCH | `/api/tags/{name}` | Update color `{color}` |
| POST | `/api/models/{enc_name}/tags` | Add tag to model `{tag_name}` |
| DELETE | `/api/models/{enc_name}/tags/{tag_name}` | Remove tag from model |

`GET /api/models` gains optional `?tag=` query param.

## UI Changes (`templates/management.html`)

### Tags page
- New nav item "Tags" between Models and Logs
- List tags as rows: color swatch, name (inline editable), usage count, color palette picker, delete
- "Create Tag" button at top
- Delete immediate (easy to re-add)

### Models page — filtering
- Filter bar above model list: "All" chip + tag chips for each tag
- Active filter highlighted; click toggles
- Only matching models shown; count updates
- Foundation for future filter dimensions

### Model row — tag display
- Colored tag pills after context length
- X on each pill removes tag immediately
- Pills draggable to other models

### Expanded model detail — tag input
- Between reasoning dropdown and rename: existing tag chips + text input
- On Enter/blur: if tag exists → add; if not → create with random palette color then add
- Autocomplete dropdown while typing

### Drag-and-drop
- Tags in Tags page are draggable
- Model rows are drop targets
- Drop adds tag + toast confirmation
- Visual highlight on drag-over

### Color palette (12 colors)
`#4CAF50`, `#2196F3`, `#FF9800`, `#9C27B0`, `#F44336`, `#00BCD4`, `#8BC34A`, `#795548`, `#607D8B`, `#E91E63`, `#3F51B5`, `#009688`

## Files to modify
- `src/db.py` — tag CRUD functions
- `src/management_app.py` — tag API endpoints, model tag endpoints, filter param
- `templates/management.html` — Tags page, filter bar, tag chips, tag input, drag-and-drop, CSS
