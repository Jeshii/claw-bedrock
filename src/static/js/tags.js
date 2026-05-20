async function loadTagsPage() {
	const res = await fetch("/api/tags");
	const data = await res.json();
	window._allTags = data.tags || [];
	const list = document.getElementById("tags-list");
	if (window._allTags.length === 0) {
		list.innerHTML =
			'<p class="muted">No tags yet. Create one above or add tags to models.</p>';
		return;
	}
	const modelsRes = await fetch("/api/models");
	const modelsData = await modelsRes.json();
	const allModels = modelsData.models || [];
	list.innerHTML = window._allTags
		.map((t) => {
			const count = allModels.filter((m) =>
				(m.tags || []).includes(t.name),
			).length;
			return `
        <div class="tag-row" data-tag="${t.name}">
            <span class="tag-color-swatch" style="background:${t.color}" onclick="showColorPalette('${t.name}', this)"></span>
            <span class="tag-row-name" id="tag-name-${t.name}" onclick="startTagRename('${t.name}')">${t.name}</span>
            <span class="tag-row-count">${count} model${count !== 1 ? "s" : ""}</span>
            <div class="tag-row-actions">
                <button type="button" class="tag-delete-btn" onclick="deleteTag('${t.name}')">Delete</button>
            </div>
        </div>`;
		})
		.join("");
}

function showCreateTagInput() {
	document.getElementById("create-tag-row").style.display = "";
	document.getElementById("new-tag-name").focus();
	renderNewTagPalette();
}

function hideCreateTagInput() {
	document.getElementById("create-tag-row").style.display = "none";
	document.getElementById("new-tag-name").value = "";
}

function renderNewTagPalette() {
	const palette = document.getElementById("new-tag-palette");
	palette.innerHTML = TAG_PALETTE.map(
		(c) =>
			`<span class="color-palette-swatch" style="background:${c}" onclick="selectNewTagColor('${c}')"></span>`,
	).join("");
}

let _selectedNewTagColor = TAG_PALETTE[0];

function selectNewTagColor(color) {
	_selectedNewTagColor = color;
}

async function createTagFromInput() {
	const name = document.getElementById("new-tag-name").value.trim();
	if (!name) return showToast("Tag name required", "error");
	try {
		const res = await fetch("/api/tags", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ name, color: _selectedNewTagColor }),
		});
		if (res.ok) {
			hideCreateTagInput();
			showToast(`Tag "${name}" created`);
			loadTagsPage();
		} else {
			const err = await res.json();
			showToast(`Error: ${err.detail}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

async function deleteTag(name) {
	try {
		const res = await fetch(`/api/tags/${encodeURIComponent(name)}`, {
			method: "DELETE",
		});
		if (res.ok) {
			showToast(`Tag "${name}" deleted`);
			loadTagsPage();
			loadModels(activeFilter);
		} else {
			const err = await res.json();
			showToast(`Error: ${err.detail}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

function startTagRename(name) {
	const span = document.getElementById(`tag-name-${name}`);
	const input = document.createElement("input");
	input.type = "text";
	input.value = name;
	input.className = "tag-row-name-input";
	input.onkeydown = (e) => {
		e.stopPropagation();
		if (e.key === "Enter") submitTagRename(name, input.value.trim());
		if (e.key === "Escape") loadTagsPage();
	};
	input.onblur = () => submitTagRename(name, input.value.trim());
	span.replaceWith(input);
	input.focus();
	input.select();
}

async function submitTagRename(oldName, newName) {
	if (!newName || newName === oldName) {
		loadTagsPage();
		return;
	}
	try {
		const res = await fetch(`/api/tags/${encodeURIComponent(oldName)}`, {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ name: newName }),
		});
		if (res.ok) {
			showToast(`Tag renamed to "${newName}"`);
			loadTagsPage();
			loadModels(activeFilter);
		} else {
			const err = await res.json();
			showToast(`Error: ${err.detail}`, "error");
			loadTagsPage();
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
		loadTagsPage();
	}
}

function showColorPalette(tagName, swatchEl) {
	const existing = swatchEl.nextElementSibling;
	if (existing?.classList.contains("color-palette")) {
		existing.remove();
		return;
	}
	const palette = document.createElement("div");
	palette.className = "color-palette";
	palette.style.position = "absolute";
	palette.style.zIndex = "100";
	palette.style.bottom = "100%";
	palette.style.left = "0";
	palette.style.marginBottom = "2px";
	palette.innerHTML = TAG_PALETTE.map(
		(c) =>
			`<span class="color-palette-swatch" style="background:${c}" onclick="updateTagColor('${tagName}', '${c}', this)"></span>`,
	).join("");
	swatchEl.style.position = "relative";
	swatchEl.parentNode.style.position = "relative";
	swatchEl.parentNode.insertBefore(palette, swatchEl.nextSibling);
	setTimeout(() => {
		document.addEventListener("click", function handler(e) {
			if (!palette.contains(e.target) && e.target !== swatchEl) {
				palette.remove();
				document.removeEventListener("click", handler);
			}
		});
	}, 0);
}

async function updateTagColor(tagName, color, _swatch) {
	try {
		const res = await fetch(`/api/tags/${encodeURIComponent(tagName)}`, {
			method: "PATCH",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ color }),
		});
		if (res.ok) {
			showToast(`Color updated`);
			loadTagsPage();
			loadModels(activeFilter);
		} else {
			const err = await res.json();
			showToast(`Error: ${err.detail}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}
