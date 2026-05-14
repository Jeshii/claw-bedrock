# management.html Refactor Plan

## Motivation

`templates/management.html` is a single ~1,500-line file combining CSS, HTML, and JavaScript. Working on any one area means loading the entire file into the context window. Splitting it into focused files keeps each unit small, independently editable, and easy to reason about. A secondary goal is reducing HTML noise from inline styles so the structure of each page remains readable even after future feature growth.

---

## Current Structure

| Section | Approx. Lines | Content |
|---|---|---|
| `<style>` block | ~250 | All CSS: layout, dark mode, components |
| HTML body | ~350 | Sidebar + 7 page `<div>`s |
| `<script>` block | ~900 | All JS: API calls, DOM logic, state |
| **Total** | **~1,500** | |

---

## Target Structure

After refactoring, `management.html` becomes a ~80–100 line shell:
- `<head>` with asset `<link>` and `<script>` tags
- Sidebar HTML (unchanged)
- Jinja2 `{% include %}` calls for each page

---

## Step 0 — Inline Style Cleanup

**Effort:** Low | **Risk:** Very low | **Recommended before CSS extraction**

The current template contains many inline `style="..."` attributes, especially in:
- Security page buttons and status blocks
- Logs page toolbar controls and `<pre>` blocks
- Tags page layout rows
- Modal/dialog presentation
- Small flex/layout wrappers throughout the page

Cleaning these up first makes the later HTML partial split much cleaner and also resolves the H021 lint exception by replacing one-off inline styles with named CSS classes.

### Action
1. Audit all `style="..."` attributes in `templates/management.html`.
2. Move repeated visual patterns into `static/management.css`.
3. Introduce small reusable classes for common patterns.

### Suggested classes
- `.btn-primary`
- `.btn-danger`
- `.btn-secondary`
- `.btn-icon`
- `.toolbar-row`
- `.inline-row`
- `.stack-sm`
- `.muted-text`
- `.code-block`
- `.scroll-panel`
- `.hidden`
- `.section-header-row`

### Notes
- Prefer semantic component classes over many tiny utility classes.
- Keep one-off dynamic styling in JavaScript only when it truly depends on runtime state.
- This step reduces HTML noise and makes the later Jinja partials smaller and easier to scan.

### Result
- Smaller HTML partials
- Cleaner separation of structure vs presentation
- Less visual clutter in the main template
- H021 can be followed instead of ignored

## Step 1 — Extract CSS

**Effort:** Low | **Risk:** Very low | **Recommended first**

Move the entire `<style>` block to a new static file.

### Action
1. Create `static/management.css` with all CSS content from the `<style>` block.
2. Replace the `<style>` block in `management.html` with:

```html
<link rel="stylesheet" href="/static/management.css">
```

### Result
- `management.html` loses ~250 lines
- CSS is independently editable without touching the template
- Dark mode, component styles, and animations all live in one dedicated file

---

## Step 2 — Split JavaScript into Modules

**Effort:** Medium | **Risk:** Low (no logic changes, only reorganization) | **Biggest context win**

Create `static/js/` and split the `<script>` block into focused files.

### New Files

| File | Responsibility | Key Functions | ~Lines |
|---|---|---|---|
| `static/js/utils.js` | Shared helpers | `showToast`, `updateToast`, `base64urlEncode`, `base64urlDecode`, `ansiToHtml`, `simplifyErrorMessage`, `formatContextLength` | ~80 |
| `static/js/theme.js` | Dark/light mode, logout button | `toggleTheme`, init logic, logout button injection | ~30 |
| `static/js/navigation.js` | Page routing | `showPage`, `showPage2`, `hideLoadingOverlay` | ~30 |
| `static/js/auth.js` | AWS SSO + provider status | `loadAuth`, `loadDashboard`, `updateDashboardBanner`, `submitAWSCode`, `retryLogin`, `copyAuthCode` | ~100 |
| `static/js/security.js` | API key management | `loadKeyStatus`, `generateKey`, `revokeKey` | ~40 |
| `static/js/models.js` | Model list, CRUD, provider UIs | `loadModels`, `renderModelList`, `renderFilterBar`, `toggleModel`, `startRename`, `cancelRename`, `submitRename`, `deleteModel`, `resetDeleteBtn`, `showAddModel`, `closeAddModel`, `addModelCommon`, `addManualModel`, `addOpenRouterModel`, `addOllamaModel`, `addBedrockModel`, `loadBedrockModels`, `pollBedrockModels`, `loadOpenRouterModels`, `renderOpenRouterSelect`, `filterOpenRouterModels`, `onOpenRouterSelect`, `fetchOllamaModels`, `fetchOllamaContextLength`, `onOllamaSelect`, `onBedrockSelect`, `loadProviderUI`, `reloadLiteLLM`, `updateReasoningEffort`, `sortModels`, `toggleSortMenu`, `applySort`, `togglePrefix` | ~450 |
| `static/js/tags.js` | Tag CRUD, drag-drop, autocomplete | `loadTagsPage`, `showCreateTagInput`, `hideCreateTagInput`, `createTagFromInput`, `deleteTag`, `startTagRename`, `submitTagRename`, `showColorPalette`, `updateTagColor`, `addTagToModel`, `removeTagFromModel`, `handleTagDragStart`, `handleDragOver`, `handleDragLeave`, `handleTagDrop`, `handleTagInputKeydown`, `handleTagInputChange`, `handleTagInputBlur`, `hideTagAutocomplete`, `selectTagAutocomplete`, `renderNewTagPalette`, `selectNewTagColor` | ~200 |
| `static/js/logs.js` | Log fetching, display, auto-refresh | `loadLogs`, `loadDebugLogs`, `loadContainerLogs`, `copyLogs`, `copyDebugLogs`, `copyContainerLogs`, `toggleLog`, `toggleAutoRefresh`, `restoreAutoRefresh`, `clearAutoRefresh` | ~150 |
| `static/js/init.js` | Bootstrap on page load | `loadDashboard()`, `loadAuth()`, `loadModels()`, `setInterval` calls, prefix toggle init, URL detection | ~30 |

### Load Order in `management.html`

```html
<!-- Utilities must load first (used by all other modules) -->
<script src="/static/js/utils.js"></script>
<script src="/static/js/theme.js"></script>
<script src="/static/js/navigation.js"></script>

<!-- Feature modules (order within this group is flexible) -->
<script src="/static/js/auth.js"></script>
<script src="/static/js/security.js"></script>
<script src="/static/js/models.js"></script>
<script src="/static/js/tags.js"></script>
<script src="/static/js/logs.js"></script>

<!-- Init must load last (calls functions from all modules) -->
<script src="/static/js/init.js"></script>
```

### Notes
- All files use plain ES5-compatible vanilla JS (no bundler needed) — consistent with the current codebase.
- SVG constants (`CHECK_SVG`, `RELOAD_SVG`, etc.) and shared state (`TAG_PALETTE`, `currentSort`, `activeFilter`) should live in `utils.js` since multiple modules reference them.
- `autoRefreshIntervals` state object should move to `logs.js` since only that module uses it.

---

## Step 3 — Extract HTML Pages as Jinja2 Partials

**Effort:** Low | **Risk:** Very low | **Optional but tidy**

Move each page `<div>` into its own partial template under `templates/partials/`.

### New Files

| File | Page |
|---|---|
| `templates/partials/page_dashboard.html` | Dashboard (server info, auth banner) |
| `templates/partials/page_auth.html` | Authentication (AWS SSO flow) |
| `templates/partials/page_security.html` | Security (API key management) |
| `templates/partials/page_models.html` | Models (list, add, settings) |
| `templates/partials/page_tags.html` | Tags (manage tags) |
| `templates/partials/page_logs.html` | Logs (LiteLLM, TokenRefresher, Container) |
| `templates/partials/page_help.html` | Help (docs, endpoints, integrations) |

### Usage in `management.html`

```html
<div class="main">
    <div id="toast-container"></div>
    {% include 'partials/page_dashboard.html' %}
    {% include 'partials/page_auth.html' %}
    {% include 'partials/page_security.html' %}
    {% include 'partials/page_models.html' %}
    {% include 'partials/page_tags.html' %}
    {% include 'partials/page_logs.html' %}
    {% include 'partials/page_help.html' %}
</div>
```

### Notes
- Jinja2's `{% include %}` is already available since the app uses Jinja2 for `{{ version }}` and `{{ use_prefix }}` rendering.
- The `key-reveal-modal` `<dialog>` element (currently between Security and Models) should move into `page_security.html`.
- No Python/FastAPI changes required.

---

## Final `management.html` Skeleton

After all three steps:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Claw Bedrock Management</title>
    <script id="config-data" type="application/json">{"use_prefix": {{ use_prefix|lower }}}</script>
    <script>window.USE_PREFIX = JSON.parse(document.getElementById('config-data').textContent).use_prefix;</script>
    <script id="auth-data" type="application/json">{"auth_required": {{ auth_required|lower }}}</script>
    <script>window.AUTH_REQUIRED = JSON.parse(document.getElementById('auth-data').textContent).auth_required;</script>
    <link rel="stylesheet" href="/static/management.css">
</head>
<body>
    <div id="loading-overlay">
        <div class="loading-spinner"></div>
        <div class="loading-text">Loading…</div>
    </div>
    <div class="sidebar">
        <div class="sidebar-header">
            <h2>🦞claw-bedrock🪨</h2>
            <div class="version">v{{ version }}</div>
        </div>
        <ul class="nav">
            <li><a onclick="showPage('dashboard')" class="active">Dashboard</a></li>
            <li><a onclick="showPage('auth')">Authentication</a></li>
            <li><a onclick="showPage('security')">Security</a></li>
            <li><a onclick="showPage('models')">Models</a></li>
            <li><a onclick="showPage('tags')">Tags</a></li>
            <li><a onclick="showPage('logs')">Logs</a></li>
            <li><a onclick="showPage('help')">Help</a></li>
        </ul>
        <div class="sidebar-footer">
            <button type="button" class="theme-toggle" onclick="toggleTheme()"></button>
        </div>
    </div>
    <div class="main">
        <div id="toast-container"></div>
        {% include 'partials/page_dashboard.html' %}
        {% include 'partials/page_auth.html' %}
        {% include 'partials/page_security.html' %}
        {% include 'partials/page_models.html' %}
        {% include 'partials/page_tags.html' %}
        {% include 'partials/page_logs.html' %}
        {% include 'partials/page_help.html' %}
    </div>

    <script src="/static/js/utils.js"></script>
    <script src="/static/js/theme.js"></script>
    <script src="/static/js/navigation.js"></script>
    <script src="/static/js/auth.js"></script>
    <script src="/static/js/security.js"></script>
    <script src="/static/js/models.js"></script>
    <script src="/static/js/tags.js"></script>
    <script src="/static/js/logs.js"></script>
    <script src="/static/js/init.js"></script>
</body>
</html>
```

---

## Recommended Implementation Order

1. **Step 1 — CSS** (15 min): Move `<style>` to `static/management.css`. Zero logic risk.
2. **Step 2 — JS modules** (1–2 hrs): Extract by logical group, test each page after moving its functions.
3. **Step 3 — HTML partials** (30 min): Mechanical cut-and-paste into partial files, add `{% include %}` tags.

Each step is independently deployable and testable, so they can be done across separate PRs if preferred.

---

## File Count Summary

| Before | After |
|---|---|
| 1 file, ~1,500 lines | 1 template (~90 lines) + 7 partials + 1 CSS file + 9 JS files = **18 files** |
| Any edit loads 1,500 lines | Any edit loads ≤ 200 lines |
