async function loadProvidersPage() {
	const res = await fetch("/api/providers");
	const data = await res.json();
	window._allProviders = data.providers || [];
	renderProvidersList(window._allProviders);
	document.getElementById("provider-detail-section").style.display = "none";
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
			return `
        <div class="provider-card" id="provider-card-${p.name}" onclick="selectProvider('${p.name}')">
            <span class="provider-card-color" style="background:${p.color || "#888"}"></span>
            <span class="provider-card-name">${p.display_name || p.name}</span>
            <span class="provider-card-type">${p.type || "custom"}</span>
            <span class="provider-card-count">${modelCount} model${modelCount !== 1 ? "s" : ""}</span>
        </div>`;
		})
		.join("");
}

async function selectProvider(name) {
	document
		.querySelectorAll(".provider-card")
		.forEach((c) => c.classList.remove("active"));
	document.getElementById(`provider-card-${name}`)?.classList.add("active");
	const res = await fetch(`/api/providers/${encodeURIComponent(name)}`);
	const data = await res.json();
	renderProviderDetail(data.provider, data.models);
}

function renderProviderDetail(provider, models) {
	const section = document.getElementById("provider-detail-section");
	const body = document.getElementById("provider-detail-body");
	section.style.display = "";
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
	body.innerHTML = `
        <div class="provider-detail-header">
            <span class="provider-card-color" style="background:${provider.color || "#888"};width:16px;height:16px;border-radius:50%;flex-shrink:0;"></span>
            <input id="prov-display-name" value="${provider.display_name || provider.name}" placeholder="Display Name" />
        </div>
         <div class="provider-field-row"><label>Name</label><input id="prov-name" value="${provider.name}" readonly style="background:#f5f5f5;" /></div>
         <div class="provider-field-row"><label>Type</label>
             <select id="prov-type" onchange="toggleDetailProviderFields()">
                 <option value="bedrock" ${provider.type === "bedrock" ? "selected" : ""}>Bedrock</option>
                 <option value="openai-compatible" ${provider.type === "openai-compatible" ? "selected" : ""}>OpenAI Compatible</option>
                 <option value="custom" ${provider.type === "custom" ? "selected" : ""}>Custom</option>
             </select>
         </div>
         <div class="provider-field-row"><label>Color</label><input id="prov-color" type="color" value="${provider.color || "#888888"}" style="width:40px;height:34px;padding:2px;cursor:pointer;" /></div>
         <div id="prov-bedrock-fields" class="${provider.type === "bedrock" ? "" : "hidden"}">${typeFields}</div>
         <div id="prov-openai-fields" class="${provider.type === "openai-compatible" ? "" : "hidden"}">${provider.type === "openai-compatible" ? typeFields : ""}</div>
        <div class="provider-field-row"><label>Notes</label><input id="prov-notes" value="${provider.notes || ""}" /></div>
        <div style="margin-top:16px;">
            <h3>Models Using This Provider</h3>
            <div class="provider-models-list" style="margin-top:8px;">${modelChips}</div>
        </div>
        <div class="inline-row" style="margin-top:16px;flex-wrap:wrap;">
            <button type="button" class="btn-primary" onclick="saveProviderDetail('${provider.name}')">Save Changes</button>
            <button type="button" class="btn-secondary" onclick="showRenameProvider('${provider.name}')">Rename</button>
            <button type="button" class="btn-danger" onclick="deleteProviderConfirm('${provider.name}')">Delete</button>
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
	const provider = {
		name,
		display_name: document.getElementById("prov-display-name").value.trim(),
		type,
		color: document.getElementById("prov-color").value,
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
		if (apiKey) provider.api_key = apiKey.value.trim();
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

function showRenameProvider(name) {
	const newName = prompt(`Rename provider "${name}" to:`, name);
	if (!newName || newName === name) return;
	renameProviderSubmit(name, newName);
}

async function renameProviderSubmit(oldName, newName) {
	try {
		const res = await fetch(
			`/api/providers/${encodeURIComponent(oldName)}/rename`,
			{
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ new_name: newName }),
			},
		);
		const data = await res.json();
		if (res.ok && data.success) {
			showToast(`Provider renamed to "${newName}"`);
			loadProvidersPage();
		} else {
			showToast(`Error: ${data.detail || "Failed to rename"}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

async function deleteProviderConfirm(name) {
	const models = (window._allModels || []).filter((m) => m.provider === name);
	const msg =
		models.length > 0
			? `${models.length} model(s) use this provider. They will show as unassigned. Delete anyway?`
			: `Delete provider "${name}"?`;
	if (!confirm(msg)) return;
	try {
		const res = await fetch(`/api/providers/${encodeURIComponent(name)}`, {
			method: "DELETE",
		});
		if (res.ok) {
			showToast(`Provider "${name}" deleted`);
			document.getElementById("provider-detail-section").style.display = "none";
			loadProvidersPage();
			loadModels(activeFilter);
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
	const providers = window._allProviders || [];
	if (providers.length === 0) {
		wrap.innerHTML = "";
		return;
	}
	wrap.innerHTML = `
        <label for="model-provider-select" class="muted" style="font-size:13px;">Provider</label><br>
        <select id="model-provider-select" onchange="onModelProviderSelect()">
            <option value="">— None / manual config —</option>
            ${providers.map((p) => `<option value="${p.name}">${p.display_name || p.name}</option>`).join("")}
        </select>
        <span id="provider-autofill-hint" class="hint hidden">Provider fields pre-filled below</span>
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
	const p = data.provider;
	if (p.type === "bedrock") {
		loadProviderUIForProvider(name, "bedrock");
	} else {
		loadProviderUIForProvider(name, "openai-compatible");
	}
	hint.style.display = "";
}
