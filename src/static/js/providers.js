let expandedProvider = null;

async function loadProvidersPage() {
	const [provRes, modRes] = await Promise.all([
		fetch("/api/providers"),
		fetch("/api/models"),
	]);
	const provData = await provRes.json();
	const modData = await modRes.json();
	window._allProviders = provData.providers || [];
	window._allModels = modData.models || [];
	renderProvidersList(window._allProviders);
}

function renderProvidersList(providers) {
	const list = document.getElementById("providers-list");
	if (providers.length === 0) {
		list.innerHTML =
			'<p class="muted">No providers yet. Create one above or add providers via the Models page.</p>';
		return;
	}
	list.innerHTML = providers
		.map((p) => {
			const modelCount = (window._allModels || []).filter(
				(m) => m.provider === p.name,
			).length;
			const escName = p.name.replace(/'/g, "\\'");
			return `
        <div class="provider-item" data-provider-name="${p.name}">
            <div class="provider-row" onclick="toggleProvider('${escName}')">
                <span class="provider-chevron" id="provider-chevron-${p.name}">${CHEVRON_RIGHT_SVG}</span>
                <span class="provider-card-color" style="background:${p.color || "#888"}"></span>
                <span class="provider-card-name">${p.display_name || p.name}</span>
                <span class="provider-card-type">${p.type || "custom"}</span>
                <span class="provider-card-count">${modelCount} model${modelCount !== 1 ? "s" : ""}</span>
            </div>
            <div class="provider-detail" id="provider-detail-${p.name}"></div>
        </div>`;
		})
		.join("");
}

async function toggleProvider(name) {
	const detail = document.getElementById(`provider-detail-${name}`);
	const chevron = document.getElementById(`provider-chevron-${name}`);
	if (expandedProvider === name) {
		detail.classList.remove("open");
		chevron.innerHTML = CHEVRON_RIGHT_SVG;
		expandedProvider = null;
		return;
	}
	if (expandedProvider) {
		const prevDetail = document.getElementById(
			`provider-detail-${expandedProvider}`,
		);
		const prevChevron = document.getElementById(
			`provider-chevron-${expandedProvider}`,
		);
		if (prevDetail) prevDetail.classList.remove("open");
		if (prevChevron) prevChevron.innerHTML = CHEVRON_RIGHT_SVG;
	}
	expandedProvider = name;
	detail.classList.add("open");
	chevron.innerHTML = CHEVRON_DOWN_SVG;
	const res = await fetch(`/api/providers/${encodeURIComponent(name)}`);
	const data = await res.json();
	renderProviderDetail(data.provider, data.models);
}

function renderProviderDetail(provider, models) {
	const detail = document.getElementById(`provider-detail-${provider.name}`);
	const escName = provider.name.replace(/'/g, "\\'");
	const typeFields =
		provider.type === "bedrock"
			? `
        <div class="provider-field-row"><label>AWS Region</label><input id="prov-aws-region" value="${provider.aws_region || ""}" /></div>
        <div class="provider-field-row"><label>Access Key Env</label><input id="prov-aws-key-env" value="${provider.aws_access_key_env || ""}" /></div>
        <div class="provider-field-row"><label>Secret Key Env</label><input id="prov-aws-secret-env" value="${provider.aws_secret_key_env || ""}" /></div>
    `
			: provider.type === "openai-compatible"
				? `
        <div class="provider-field-row"><label>API Base</label><input id="prov-api-base" value="${provider.api_base || ""}" /></div>
        <div class="provider-field-row"><label>API Key</label><input id="prov-api-key" type="password" value="${provider.api_key || ""}" /></div>
    `
				: "";
	const modelChips =
		models.length > 0
			? models
					.map(
						(m) => `
        <span class="provider-model-chip">${m.model_name}</span>
    `,
					)
					.join("")
			: '<span style="color:#888;font-size:13px;">No models use this provider</span>';
	detail.innerHTML = `
        <div class="provider-detail-header">
            <span class="provider-color-swatch" id="prov-color-swatch-${provider.name}" data-color="${provider.color || "#888"}" style="background:${provider.color || "#888"};width:20px;height:20px;border-radius:4px;flex-shrink:0;cursor:pointer;border:1px solid rgba(0,0,0,0.15);" onclick="showProviderColorPalette('${escName}', this)"></span>
            <input id="prov-display-name" value="${provider.display_name || provider.name}" placeholder="Display Name" />
        </div>
        <div class="provider-field-row"><label>Type</label>
            <select id="prov-type" onchange="toggleDetailProviderFields()">
                <option value="bedrock" ${provider.type === "bedrock" ? "selected" : ""}>Bedrock</option>
                <option value="openai-compatible" ${provider.type === "openai-compatible" ? "selected" : ""}>OpenAI Compatible</option>
                <option value="custom" ${provider.type === "custom" ? "selected" : ""}>Custom</option>
            </select>
        </div>
        <div id="prov-bedrock-fields" class="${provider.type === "bedrock" ? "" : "hidden"}">${typeFields}</div>
        <div id="prov-openai-fields" class="${provider.type === "openai-compatible" ? "" : "hidden"}">${provider.type === "openai-compatible" ? typeFields : ""}</div>
        <div class="provider-field-row"><label>Notes</label><input id="prov-notes" value="${provider.notes || ""}" /></div>
        <div style="margin-top:16px;">
            <h3>Models Using This Provider</h3>
            <div class="provider-models-list" style="margin-top:8px;">${modelChips}</div>
        </div>
        <div class="inline-row" style="margin-top:16px;flex-wrap:wrap;">
            <button type="button" class="btn-primary" onclick="saveProviderDetail('${escName}')">Save Changes</button>
            <button type="button" class="rename-btn" id="rename-btn-${provider.name}" onclick="startProviderRename('${escName}')">Rename</button>
            <button type="button" class="delete-btn" id="delete-btn-${provider.name}" onclick="deleteProviderConfirm('${escName}')">Delete</button>
        </div>
    `;
}

function toggleDetailProviderFields() {
	const type = document.getElementById("prov-type").value;
	document.getElementById("prov-bedrock-fields").style.display =
		type === "bedrock" ? "" : "none";
	document.getElementById("prov-openai-fields").style.display =
		type === "openai-compatible" ? "" : "none";
}

function showProviderColorPalette(name, swatchEl) {
	const existing = document.getElementById(`prov-palette-${name}`);
	if (existing) {
		existing.remove();
		return;
	}
	const palette = document.createElement("div");
	palette.id = `prov-palette-${name}`;
	palette.className = "color-palette";
	palette.style.position = "absolute";
	palette.style.zIndex = "100";
	palette.style.bottom = "100%";
	palette.style.left = "0";
	palette.style.marginBottom = "2px";
	palette.innerHTML = TAG_PALETTE.map(
		(c) =>
			`<span class="color-palette-swatch" style="background:${c}" onclick="updateProviderColor('${name}', '${c}')"></span>`,
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

async function updateProviderColor(name, color) {
	try {
		const res = await fetch(`/api/providers/${encodeURIComponent(name)}`, {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ color }),
		});
		if (res.ok) {
			showToast("Color updated");
			const swatch = document.getElementById(`prov-color-swatch-${name}`);
			if (swatch) {
				swatch.style.background = color;
				swatch.dataset.color = color;
			}
			loadProvidersPage();
		} else {
			const err = await res.json();
			showToast(`Error: ${err.detail}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

function startProviderRename(name) {
	const btn = document.getElementById(`rename-btn-${name}`);
	const input = document.getElementById(`prov-display-name`);
	if (btn.dataset.renaming === "true") {
		const newName = input.value.trim();
		if (newName && newName !== name) {
			submitProviderRename(name, newName);
		} else {
			cancelProviderRename(name);
		}
		return;
	}
	btn.dataset.renaming = "true";
	btn.textContent = "Confirm";
	btn.classList.add("confirming");
	input.dataset.originalName = name;
	input.focus();
	input.select();
}

function cancelProviderRename(name) {
	const btn = document.getElementById(`rename-btn-${name}`);
	if (btn) {
		btn.dataset.renaming = "false";
		btn.textContent = "Rename";
		btn.classList.remove("confirming");
	}
	const input = document.getElementById(`prov-display-name`);
	if (input?.dataset.originalName) {
		input.value = input.dataset.originalName;
		delete input.dataset.originalName;
	}
}

async function submitProviderRename(oldName, newName) {
	const btn = document.getElementById(`rename-btn-${oldName}`);
	if (btn) {
		btn.dataset.renaming = "false";
		btn.textContent = "Rename";
		btn.classList.remove("confirming");
	}
	try {
		const res = await fetch(
			`/api/providers/${encodeURIComponent(oldName)}/rename`,
			{
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ new_name: newName }),
			},
		);
		if (res.ok) {
			showToast(`Provider renamed to "${newName}"`);
			expandedProvider = null;
			loadProvidersPage();
		} else {
			const err = await res.json();
			showToast(`Error: ${err.detail}`, "error");
			const input = document.getElementById(`prov-display-name`);
			if (input) input.value = oldName;
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
		const input = document.getElementById(`prov-display-name`);
		if (input) input.value = oldName;
	}
}

async function deleteProviderConfirm(name) {
	const btn = document.getElementById(`delete-btn-${name}`);
	if (!btn) return;

	if (btn.dataset.confirming === "true") return;

	btn.dataset.confirming = "true";
	btn.textContent = "Confirm";
	btn.className = "confirm-btn";
	btn.disabled = true;

	setTimeout(() => {
		btn.disabled = false;
	}, 1000);

	btn.onclick = async () => {
		const toast = showToast("Deleting provider...", "info", 0, true);
		try {
			const res = await fetch(`/api/providers/${encodeURIComponent(name)}`, {
				method: "DELETE",
			});
			if (res.ok) {
				updateToast(toast, `Provider "${name}" deleted`, "success");
				expandedProvider = null;
				loadProvidersPage();
				loadModels(activeFilter);
			} else {
				const err = await res.json();
				updateToast(
					toast,
					`Error: ${err.detail || "Failed to delete provider"}`,
					"error",
				);
				resetProviderDeleteBtn(btn, name);
				setTimeout(() => {
					toast.style.opacity = "0";
					toast.style.transition = "opacity 0.3s";
					setTimeout(() => toast.remove(), 300);
				}, 3000);
			}
		} catch (e) {
			updateToast(toast, `Error: ${e.message}`, "error");
			resetProviderDeleteBtn(btn, name);
			setTimeout(() => {
				toast.style.opacity = "0";
				toast.style.transition = "opacity 0.3s";
				setTimeout(() => toast.remove(), 300);
			}, 3000);
		}
	};
}

function resetProviderDeleteBtn(btn, name) {
	btn.dataset.confirming = "false";
	btn.textContent = "Delete";
	btn.className = "delete-btn";
	btn.disabled = false;
	btn.onclick = () => deleteProviderConfirm(name);
}

function showCreateProviderForm() {
	document.getElementById("create-provider-row").style.display = "block";
	document.getElementById("new-provider-name").focus();
}

function hideCreateProviderForm() {
	document.getElementById("create-provider-row").style.display = "none";
}

function toggleNewProviderFields() {
	const type = document.getElementById("new-provider-type").value;
	document.getElementById("new-provider-bedrock-fields").style.display =
		type === "bedrock" ? "flex" : "none";
	document.getElementById("new-provider-openai-fields").style.display =
		type === "openai-compatible" ? "flex" : "none";
}

async function createProvider() {
	const name = document.getElementById("new-provider-name").value.trim();
	if (!name) return showToast("Provider name is required", "error");
	const type = document.getElementById("new-provider-type").value;
	const provider = {
		name,
		display_name:
			document.getElementById("new-provider-display").value.trim() || name,
		type,
		color: document.getElementById("new-provider-color").value,
		notes: document.getElementById("new-provider-notes").value.trim(),
	};
	if (type === "bedrock") {
		provider.aws_region = document
			.getElementById("new-provider-aws-region")
			.value.trim();
		provider.aws_access_key_env = document
			.getElementById("new-provider-aws-key-env")
			.value.trim();
		provider.aws_secret_key_env = document
			.getElementById("new-provider-aws-secret-env")
			.value.trim();
	} else if (type === "openai-compatible") {
		provider.api_base = document
			.getElementById("new-provider-api-base")
			.value.trim();
		provider.api_key = document
			.getElementById("new-provider-api-key")
			.value.trim();
	}
	try {
		const res = await fetch("/api/providers", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(provider),
		});
		if (res.ok) {
			hideCreateProviderForm();
			showToast(`Provider "${name}" created`);
			loadProvidersPage();
		} else {
			const err = await res.json();
			showToast(`Error: ${err.detail}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

async function saveProviderDetail(name) {
	const type = document.getElementById("prov-type").value;
	const swatch = document.getElementById(`prov-color-swatch-${name}`);
	const provider = {
		name,
		display_name: document.getElementById("prov-display-name").value.trim(),
		type,
		color: swatch?.dataset?.color || "#888888",
		notes: document.getElementById("prov-notes").value.trim(),
	};
	if (type === "bedrock") {
		provider.aws_region =
			document.getElementById("prov-aws-region")?.value.trim() || "";
		provider.aws_access_key_env =
			document.getElementById("prov-aws-key-env")?.value.trim() || "";
		provider.aws_secret_key_env =
			document.getElementById("prov-aws-secret-env")?.value.trim() || "";
	} else if (type === "openai-compatible") {
		const apiBase = document.getElementById("prov-api-base");
		if (apiBase) provider.api_base = apiBase.value.trim();
		const apiKey = document.getElementById("prov-api-key");
		if (apiKey) {
			const val = apiKey.value.trim();
			if (val) provider.api_key = val;
		}
	}
	try {
		const res = await fetch(`/api/providers/${encodeURIComponent(name)}`, {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(provider),
		});
		if (res.ok) {
			showToast("Provider updated");
			loadProvidersPage();
		} else {
			const err = await res.json();
			showToast(`Error: ${err.detail}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

function renderProviderSelector() {
	const wrap = document.getElementById("model-provider-selector-wrap");
	if (!wrap) return;
	const providers = window._allProviders || [];
	if (providers.length === 0) {
		wrap.innerHTML = "";
		return;
	}
	wrap.innerHTML = `
        <label for="model-provider-select" class="muted" style="font-size:13px;">Provider</label><br>
        <select id="model-provider-select" onchange="onModelProviderSelect()">
            <option value="">-- Select a provider --</option>
            ${providers.map((p) => `<option value="${p.name}">${p.display_name || p.name}</option>`).join("")}
        </select>
        <span id="provider-autofill-hint" class="hidden">Provider fields pre-filled below</span>
    `;
}

async function onModelProviderSelect() {
	const sel = document.getElementById("model-provider-select");
	const name = sel.value;
	const hint = document.getElementById("provider-autofill-hint");
	if (!name) {
		hint.style.display = "none";
		return;
	}
	const res = await fetch(`/api/providers/${encodeURIComponent(name)}`);
	const data = await res.json();
	loadProviderUIForProvider(data.provider);
	hint.style.display = "";
}
