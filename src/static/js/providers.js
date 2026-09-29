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
	loadBedrockSettings();
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
	const apiKeyField = provider.has_api_key
		? `<div class="api-key-set" id="api-key-set-indicator">
			 <span class="api-key-masked">&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;</span>
			 <span class="api-key-saved-label">(saved)</span>
			 <button type="button" class="delete-btn" id="clear-api-key-btn" onclick="clearApiKeyConfirm('${escName}')">Clear</button>
			 <button type="button" class="rename-btn" id="change-api-key-btn" onclick="showChangeApiKey()">Change</button>
		   </div>
		   <input id="prov-api-key" type="password" class="hidden" placeholder="New API key" />`
		: `<input id="prov-api-key" type="password" value="" placeholder="Enter API key" />`;
	const openaiFields = `
        <div class="provider-field-row"><label>API Base</label><input id="prov-api-base" value="${provider.api_base || ""}" /></div>
        <div class="provider-field-row"><label>API Key</label>${apiKeyField}</div>
    `;
	const modelChips =
		models.length > 0
			? models
					.map(
						(m) => `
        <span class="provider-model-chip">${m.model_name}</span>
    `,
					)
					.join("")
			: '<span style="color:var(--text-faint);font-size:13px;">No models use this provider</span>';
	detail.innerHTML = `
        <div class="provider-detail-header">
            <span class="provider-color-swatch" id="prov-color-swatch-${provider.name}" data-color="${provider.color || "#888"}" style="background:${provider.color || "#888"};width:20px;height:20px;border-radius:4px;flex-shrink:0;cursor:pointer;border:1px solid var(--swatch-border);" onclick="showProviderColorPalette('${escName}', this)"></span>
            <input id="prov-display-name" value="${provider.display_name || provider.name}" placeholder="Display Name" />
        </div>
        <div class="provider-field-row"><label>Type</label>
            <select id="prov-type" onchange="toggleDetailProviderFields()">
                <option value="bedrock" ${provider.type === "bedrock" ? "selected" : ""}>Bedrock</option>
                <option value="openai-compatible" ${provider.type === "openai-compatible" ? "selected" : ""}>OpenAI Compatible</option>
                <option value="custom" ${provider.type === "custom" ? "selected" : ""}>Custom</option>
            </select>
        </div>
        <div id="prov-openai-fields" class="${provider.type === "openai-compatible" ? "" : "hidden"}">${openaiFields}</div>
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

// Warning codes from /api/settings/bedrock. Codes rather than prose so the
// wording can change without touching Python.
const BEDROCK_SETTING_WARNINGS = {
	stored_credentials_undecryptable:
		"Stored credentials cannot be decrypted with the current ENCRYPTION_KEY. " +
		"They were probably restored from a backup taken under a different key. " +
		"Re-enter them, or the values cannot be used.",
	incomplete_static_key_pair:
		"Only one half of the static key pair is stored. Both are needed — the " +
		"profile is being used until they are.",
};

function bedrockSettingRow(label, setting) {
	if (!setting) return "";
	const badge = `<span class="source-badge source-${setting.source}">${setting.source}</span>`;
	let shadowed = "";
	if (setting.shadowed && setting.shadowed.length > 0) {
		shadowed = `<span class="shadowed-note">overrides ${setting.shadowed.join(", ")}</span>`;
	}
	return (
		`<div class="resolved-row"><span class="resolved-key">${label}</span>` +
		`<span class="resolved-value">${setting.value ?? "—"}</span>${badge}${shadowed}</div>`
	);
}

async function loadBedrockSettings() {
	const wrap = document.getElementById("bedrock-settings-body");
	if (!wrap) return;
	let data;
	try {
		const res = await fetch("/api/settings/bedrock");
		data = await res.json();
	} catch (_e) {
		wrap.innerHTML = '<p class="muted">Could not load Bedrock settings.</p>';
		return;
	}

	// Values come from the API, so the inputs show what is actually in effect
	// rather than what was last typed. Source badges make it obvious when the
	// environment is winning and these fields are inert.
	const region = data.region?.value || "";
	const profile = data.profile?.value || "";
	const keysConfigured = data.static_keys?.configured;

	let warnings = "";
	for (const code of data.warnings || []) {
		const text = BEDROCK_SETTING_WARNINGS[code];
		if (text) warnings += `<div class="resolved-warning">${text}</div>`;
	}

	wrap.innerHTML = `
        <div class="resolved-grid">
            ${bedrockSettingRow("Region", data.region)}
            ${bedrockSettingRow("Profile", data.profile)}
        </div>
        ${warnings}
        <div class="stack-sm" style="margin-top:12px;">
            <label class="inline-row" style="gap:8px;">
                <span style="min-width:150px;font-size:13px;">Region:</span>
                <input type="text" id="bedrock-region-input" value="${region}"
                       placeholder="e.g. us-east-1" style="width:220px;" />
            </label>
            <label class="inline-row" style="gap:8px;">
                <span style="min-width:150px;font-size:13px;">AWS profile:</span>
                <input type="text" id="bedrock-profile-input" value="${profile}"
                       placeholder="e.g. bedrock-openai20b" style="width:220px;" />
            </label>
        </div>
        <details style="margin-top:12px;">
            <summary style="cursor:pointer;font-size:13px;">
                Advanced &mdash; static IAM keys
            </summary>
            <div class="stack-sm" style="margin-top:10px;">
                <p class="muted" style="font-size:12px;margin:0 0 6px;">
                    Optional. With no key pair, the AWS profile above is used and
                    the browser login flow applies. With a key pair, the profile is
                    ignored. The equivalent environment variables
                    (<code>AWS_ACCESS_KEY_ID</code> / <code>AWS_SECRET_ACCESS_KEY</code>)
                    also work and take precedence over values stored here. Keys are
                    encrypted at rest and are never shown again after saving.
                </p>
                <div id="bedrock-keys-set" class="api-key-set ${keysConfigured ? "" : "hidden"}">
                    <span class="api-key-masked">&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;</span>
                    <span class="api-key-saved-label">(saved)</span>
                    <button type="button" class="rename-btn" onclick="showBedrockKeyInputs()">Change</button>
                    <button type="button" class="delete-btn" id="clear-bedrock-keys-btn"
                            onclick="clearBedrockKeys()">Clear</button>
                </div>
                <div id="bedrock-key-inputs" class="${keysConfigured ? "hidden" : ""}">
                    <label class="inline-row" style="gap:8px;">
                        <span style="min-width:150px;font-size:13px;">Access key ID:</span>
                        <input type="password" id="bedrock-access-key-input"
                               placeholder="AKIA..." style="width:320px;" />
                    </label>
                    <label class="inline-row" style="gap:8px;margin-top:6px;">
                        <span style="min-width:150px;font-size:13px;">Secret access key:</span>
                        <input type="password" id="bedrock-secret-key-input"
                               placeholder="secret access key" style="width:320px;" />
                    </label>
                </div>
            </div>
        </details>
        <div class="inline-row" style="margin-top:14px;">
            <button type="button" class="btn-primary" id="save-bedrock-settings-btn"
                    onclick="saveBedrockSettings()">Save &amp; Apply</button>
        </div>
    `;
}

function showBedrockKeyInputs() {
	document.getElementById("bedrock-keys-set")?.classList.add("hidden");
	document.getElementById("bedrock-key-inputs")?.classList.remove("hidden");
}

async function clearBedrockKeys() {
	const btn = document.getElementById("clear-bedrock-keys-btn");
	if (!btn || btn.dataset.confirming === "true") return;
	btn.dataset.confirming = "true";
	btn.textContent = "Confirm Clear";
	btn.className = "confirm-btn";
	btn.disabled = true;
	setTimeout(() => {
		btn.disabled = false;
	}, 1000);
	setTimeout(() => {
		if (btn.dataset.confirming === "true") clearBedrockKeysReset(btn);
	}, 5000);

	const toast = showToast("Clearing static keys...", "info", 0, true);
	try {
		const res = await fetch("/api/settings/bedrock", {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ clear_static_keys: true }),
		});
		if (res.ok) {
			const data = await res.json();
			updateToast(
				toast,
				bedrockSaveMessage(data),
				data.token_refreshed ? "success" : "warning",
				true,
				5000,
			);
			loadBedrockSettings();
		} else {
			const err = await res.json();
			updateToast(
				toast,
				`Error: ${err.detail || "Failed to clear keys"}`,
				"error",
				true,
				8000,
			);
			clearBedrockKeysReset(btn);
		}
	} catch (e) {
		updateToast(toast, `Error: ${e.message}`, "error", true, 8000);
		clearBedrockKeysReset(btn);
	}
}

function clearBedrockKeysReset(btn) {
	if (!btn) return;
	btn.dataset.confirming = "false";
	btn.textContent = "Clear";
	btn.className = "delete-btn";
	btn.disabled = false;
}

// A save can persist and still fail to produce a token — a wrong region or a
// bad key fails at token-mint time, not at validation. Reporting that as plain
// success is the exact failure mode this workstream exists to remove.
function bedrockSaveMessage(data) {
	if (data.token_refreshed) return "Saved — token refreshed";
	if (data.warnings?.length)
		return `Saved, but not applied: ${data.warnings.join(", ")}`;
	return "Saved, but no token could be minted. Check the region and credentials.";
}

async function saveBedrockSettings() {
	const body = {
		region: document.getElementById("bedrock-region-input")?.value.trim(),
		profile: document.getElementById("bedrock-profile-input")?.value.trim(),
	};
	const accessKey = document
		.getElementById("bedrock-access-key-input")
		?.value.trim();
	const secretKey = document
		.getElementById("bedrock-secret-key-input")
		?.value.trim();
	if (accessKey) body.access_key_id = accessKey;
	if (secretKey) body.secret_access_key = secretKey;

	const toast = showToast("Applying Bedrock settings...", "info", 0, true);
	try {
		const res = await fetch("/api/settings/bedrock", {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		});
		if (res.ok) {
			const data = await res.json();
			updateToast(
				toast,
				bedrockSaveMessage(data),
				data.token_refreshed ? "success" : "warning",
				true,
				5000,
			);
			loadBedrockSettings();
		} else {
			const err = await res.json();
			updateToast(
				toast,
				`Error: ${typeof err.detail === "string" ? err.detail : "Failed to save"}`,
				"error",
				true,
				8000,
			);
		}
	} catch (e) {
		updateToast(toast, `Error: ${e.message}`, "error", true, 8000);
	}
}

function toggleDetailProviderFields() {
	const type = document.getElementById("prov-type").value;
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
			}
		} catch (e) {
			updateToast(toast, `Error: ${e.message}`, "error", true, 8000);
			resetProviderDeleteBtn(btn, name);
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
	if (type === "openai-compatible") {
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
	if (type === "openai-compatible") {
		const apiBase = document.getElementById("prov-api-base");
		if (apiBase) provider.api_base = apiBase.value.trim();
		const apiKeyInput = document.getElementById("prov-api-key");
		if (apiKeyInput && !apiKeyInput.classList.contains("hidden")) {
			const val = apiKeyInput.value.trim();
			if (val) provider.api_key = val;
		}
	}
	const toast = showToast("Saving provider...", "info", 0, true);
	try {
		const res = await fetch(`/api/providers/${encodeURIComponent(name)}`, {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(provider),
		});
		if (res.ok) {
			const data = await res.json();
			const runtimeChanged = data.runtime_changed;
			updateToast(
				toast,
				runtimeChanged
					? "Provider saved and LiteLLM reloaded"
					: "Provider saved",
				"success",
				true,
				3000,
			);
			loadProvidersPage();
		} else if (res.status >= 500) {
			const err = await res.json();
			const detail =
				typeof err.detail === "object" && err.detail !== null
					? err.detail.message || "Provider saved but not applied"
					: err.detail;
			updateToast(toast, detail, "warning", true, 5000);
		} else {
			const err = await res.json();
			updateToast(
				toast,
				`Error: ${typeof err.detail === "string" ? err.detail : "Failed to save provider"}`,
				"error",
				true,
				8000,
			);
		}
	} catch (e) {
		updateToast(toast, `Error: ${e.message}`, "error", true, 8000);
	}
}

function showChangeApiKey() {
	document.getElementById("api-key-set-indicator")?.classList.add("hidden");
	const input = document.getElementById("prov-api-key");
	if (input) {
		input.classList.remove("hidden");
		input.focus();
	}
}

async function clearApiKeyConfirm(name) {
	const btn = document.getElementById("clear-api-key-btn");
	if (!btn) return;

	if (btn.dataset.confirming === "true") {
		const toast = showToast("Clearing API key...", "info", 0, true);
		try {
			const res = await fetch(`/api/providers/${encodeURIComponent(name)}`, {
				method: "PUT",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ clear_api_key: true }),
			});
			if (res.ok) {
				updateToast(toast, "API key cleared", "success");
				expandedProvider = null;
				loadProvidersPage();
			} else {
				const err = await res.json();
				updateToast(
					toast,
					`Error: ${err.detail || "Failed to clear API key"}`,
					"error",
				);
				resetClearApiKeyBtn(btn);
			}
		} catch (e) {
			updateToast(toast, `Error: ${e.message}`, "error");
			resetClearApiKeyBtn(btn);
		}
		return;
	}

	btn.dataset.confirming = "true";
	btn.textContent = "Confirm Clear";
	btn.className = "confirm-btn";
	btn.disabled = true;

	setTimeout(() => {
		btn.disabled = false;
	}, 1000);

	setTimeout(() => {
		if (btn.dataset.confirming === "true") resetClearApiKeyBtn(btn);
	}, 5000);
}

function resetClearApiKeyBtn(btn) {
	btn.dataset.confirming = "false";
	btn.textContent = "Clear";
	btn.className = "delete-btn";
	btn.disabled = false;
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
