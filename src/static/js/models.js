let expandedModel = null;
let openRouterModels = [];
let bedrockNewModels = [];
let currentSort = "default";
let activeFilter = "all";
let needsReload = false;

const SORT_LABELS = {
	default: "Default",
	alpha: "A-Z",
	provider: "Provider",
	context: "Context",
	reasoning: "Reasoning",
};

async function loadModels(filterTag) {
	let url = "/api/models";
	if (filterTag && filterTag !== "all") {
		url += `?tag=${encodeURIComponent(filterTag)}`;
	}
	const [modelsRes, tagsRes, providersRes] = await Promise.all([
		fetch(url),
		fetch("/api/tags"),
		fetch("/api/providers"),
	]);
	const data = await modelsRes.json();
	const tagsData = await tagsRes.json();
	const providersData = await providersRes.json();
	window._allTags = tagsData.tags || [];
	window._allProviders = providersData.providers || [];
	window._allModels = data.models;
	renderFilterBar(filterTag || "all");
	renderModelList(data.models);
}

function renderModelList(models, preserveExpanded) {
	window._renderedModels = models;
	const sorted = sortModels(models, currentSort);
	const modelsDiv = document.getElementById("models-list");
	if (sorted.length === 0) {
		modelsDiv.innerHTML = "<p>No models configured yet.</p>";
		return;
	}
	if (!preserveExpanded) expandedModel = null;
	modelsDiv.innerHTML = sorted
		.map((m) => {
			const ctx = m.litellm_params.context_length;
			const ctxStr = formatContextLength(ctx);
			const reasoning = m.reasoning_effort
				? m.reasoning_effort.charAt(0).toUpperCase() +
					m.reasoning_effort.slice(1)
				: m.litellm_params?.thinking?.type === "enabled"
					? `Thinking (${m.litellm_params.thinking.budget_tokens})`
					: "";
			const escName = m.model_name.replace(/'/g, "\\'");
			const tags = m.tags || [];
			const tagChips = tags
				.map((t) => {
					const tagInfo = (window._allTags || []).find((x) => x.name === t);
					const color = tagInfo ? tagInfo.color : "#6c757d";
					return `<span class="tag-chip" style="background:${color}" draggable="true" ondragstart="handleTagDragStart(event, '${t}')" onclick="event.stopPropagation()" onmousedown="event.stopPropagation()">${t}<span class="tag-remove" onclick="removeTagFromModel('${escName}', '${t}')">×</span></span>`;
				})
				.join("");
			const providerBadge = m._provider
				? `<span class="provider-badge" style="background:${m._provider.color}20;border:1px solid ${m._provider.color};color:${m._provider.color}">${m._provider.display_name || m._provider.name}</span>`
				: "";
			const groupBadge = m.model_group
				? `<span class="group-badge">${m.model_group}</span>`
				: "";
			return `
        <div class="model-item" data-model-name="${m.model_name}">
            <div class="model-row" onclick="toggleModel('${escName}')" ondragover="handleDragOver(event)" ondragleave="handleDragLeave(event)" ondrop="handleTagDrop(event, '${escName}')">
                <span class="model-chevron" id="chevron-${escName}">${CHEVRON_RIGHT_SVG}</span>
                <span class="model-row-name">${m.model_name}</span>
                 ${ctxStr ? `<span class="muted" style="font-size: 12px;">${ctxStr}</span>` : ""}
                 ${reasoning ? `<span style="color: #007bff; font-size: 12px;">${reasoning}</span>` : ""}
                ${providerBadge}
                ${groupBadge}
                ${tagChips}
            </div>
            <div class="model-detail" id="detail-${escName}">
                <div class="model-detail-path">${m.litellm_params.model}</div>
                <div class="model-detail-actions">
                    <label style="font-size:12px;display:flex;align-items:center;gap:4px;">
                        Group:
                        <input type="text" value="${m.model_group || ""}"
                               onchange="updateModelGroup('${escName}', this.value)"
                               placeholder="none"
                               style="width:120px;font-size:12px;padding:2px 4px;margin:0;" />
                    </label>
                    <select onchange="updateReasoningEffort('${escName}', this)">
                        <option value="">Reasoning: default</option>
                        <option value="low" ${m.reasoning_effort === "low" ? "selected" : ""}>Low</option>
                        <option value="medium" ${m.reasoning_effort === "medium" ? "selected" : ""}>Medium</option>
                        <option value="high" ${m.reasoning_effort === "high" ? "selected" : ""}>High</option>
                    </select>
                    <div class="model-tag-input-wrap" id="tag-input-wrap-${escName}" style="position:relative;">
                        <input class="model-tag-input" id="tag-input-${escName}" placeholder="Add tag..." autocomplete="off" onkeydown="handleTagInputKeydown(event, '${escName}')" oninput="handleTagInputChange('${escName}')" onblur="handleTagInputBlur('${escName}')" />
                    </div>
                    <button type="button" class="rename-btn" id="rename-btn-${escName}" onclick="startRename('${escName}')">Rename</button>
                    <button type="button" class="delete-btn" onclick="deleteModel(this, '${escName}')">Delete</button>
                </div>
            </div>
        </div>`;
		})
		.join("");
}

function renderFilterBar(active) {
	activeFilter = active;
	const bar = document.getElementById("model-filter-bar");
	if (!bar) return;
	let html = `<span class="filter-chip filter-all ${active === "all" ? "active" : ""}" onclick="setFilter('all')">All</span>`;
	for (const tag of window._allTags || []) {
		const isActive = active === tag.name ? "active" : "";
		html += `<span class="filter-chip ${isActive}" style="background:${tag.color}" onclick="setFilter('${tag.name}')">${tag.name}</span>`;
	}
	html += `<span class="sort-btn" id="sort-btn" onclick="event.stopPropagation(); toggleSortMenu()">Sort: ${SORT_LABELS[currentSort]} ▾</span>`;
	bar.innerHTML = html;
}

function toggleModel(modelName) {
	if (expandedModel === modelName) {
		document.getElementById(`detail-${modelName}`).classList.remove("open");
		document.getElementById(`chevron-${modelName}`).innerHTML =
			CHEVRON_RIGHT_SVG;
		expandedModel = null;
	} else {
		if (expandedModel) {
			const prevDetail = document.getElementById(`detail-${expandedModel}`);
			const prevChevron = document.getElementById(`chevron-${expandedModel}`);
			if (prevDetail) prevDetail.classList.remove("open");
			if (prevChevron) prevChevron.innerHTML = CHEVRON_RIGHT_SVG;
		}
		document.getElementById(`detail-${modelName}`).classList.add("open");
		document.getElementById(`chevron-${modelName}`).innerHTML =
			CHEVRON_DOWN_SVG;
		expandedModel = modelName;
	}
}

function startRename(modelName) {
	const btn = document.getElementById(`rename-btn-${modelName}`);
	const row = btn.closest(".model-item").querySelector(".model-row-name");
	if (btn.dataset.renaming === "true") {
		const input = btn.closest(".model-item").querySelector(".inline-rename");
		const newName = input.value.trim();
		if (newName && newName !== modelName) {
			submitRename(modelName, newName);
		} else {
			cancelRename(modelName);
		}
		return;
	}
	btn.dataset.renaming = "true";
	btn.textContent = "Confirm";
	btn.classList.add("confirming");
	const input = document.createElement("input");
	input.type = "text";
	input.value = modelName;
	input.className = "inline-rename";
	input.onkeydown = (e) => {
		e.stopPropagation();
		if (e.key === "Enter") startRename(modelName);
		if (e.key === "Escape") cancelRename(modelName);
	};
	row.replaceWith(input);
	input.focus();
	input.select();
}

function cancelRename(modelName) {
	const btn = document.getElementById(`rename-btn-${modelName}`);
	if (btn) {
		btn.dataset.renaming = "false";
		btn.textContent = "Rename";
		btn.classList.remove("confirming");
	}
	const item = document.querySelector(
		`.model-item[data-model-name="${modelName}"]`,
	);
	if (item) {
		const input = item.querySelector(".inline-rename");
		if (input) {
			const span = document.createElement("span");
			span.className = "model-row-name";
			span.textContent = modelName;
			input.replaceWith(span);
		}
	}
}

async function submitRename(oldName, newName) {
	const btn = document.getElementById(`rename-btn-${oldName}`);
	if (btn) {
		btn.dataset.renaming = "false";
		btn.textContent = "Rename";
		btn.classList.remove("confirming");
	}
	const item = document.querySelector(
		`.model-item[data-model-name="${oldName}"]`,
	);
	if (item) {
		const input = item.querySelector(".inline-rename");
		if (input) {
			const span = document.createElement("span");
			span.className = "model-row-name";
			span.textContent = oldName;
			input.replaceWith(span);
		}
	}
	const toast = showToast("Renaming model...", "info", 0, true);
	try {
		const encoded = base64urlEncode(oldName);
		const res = await fetch(`/api/models/${encoded}`, {
			method: "PUT",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ model_name: newName }),
		});
		if (res.ok) {
			updateToast(toast, "Model renamed — reload LiteLLM to apply", "success");
			const reloadBtn = document.getElementById("reload-litellm-btn");
			if (reloadBtn) reloadBtn.classList.add("needs-reload");
			loadModels();
			setTimeout(() => {
				toast.style.opacity = "0";
				toast.style.transition = "opacity 0.3s";
				setTimeout(() => toast.remove(), 300);
			}, 3000);
		} else {
			const error = await res.json();
			updateToast(
				toast,
				`Error: ${error.detail || "Failed to rename model"}`,
				"error",
			);
			setTimeout(() => {
				toast.style.opacity = "0";
				toast.style.transition = "opacity 0.3s";
				setTimeout(() => toast.remove(), 300);
			}, 3000);
		}
	} catch (e) {
		updateToast(toast, `Error: ${e.message}`, "error");
		setTimeout(() => {
			toast.style.opacity = "0";
			toast.style.transition = "opacity 0.3s";
			setTimeout(() => toast.remove(), 300);
		}, 3000);
	}
}

async function updateReasoningEffort(modelName, selectElement) {
	const effort = selectElement.value || null;
	const model = (window._allModels || []).find(
		(x) => x.model_name === modelName,
	);
	const isBedrock =
		model?.litellm_params?.model?.startsWith("bedrock_mantle/") ||
		model?.provider === "bedrock";

	let payload;
	if (isBedrock && effort) {
		const budgetMap = { low: 1024, medium: 8000, high: 16000 };
		payload = {
			litellm_params: {
				thinking: {
					type: "enabled",
					budget_tokens: budgetMap[effort],
				},
			},
			reasoning_effort: null,
		};
	} else if (isBedrock && !effort) {
		payload = {
			litellm_params: { thinking: null },
			reasoning_effort: null,
		};
	} else {
		payload = { reasoning_effort: effort };
	}

	try {
		const encoded = base64urlEncode(modelName);
		const res = await fetch(`/api/models/${encoded}`, {
			method: "PATCH",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(payload),
		});
		if (res.ok) {
			const m = (window._allModels || []).find(
				(x) => x.model_name === modelName,
			);
			if (m) {
				m.reasoning_effort = effort;
				if (isBedrock) {
					if (effort) {
						const budgetMap = { low: 1024, medium: 8000, high: 16000 };
						m.litellm_params = m.litellm_params || {};
						m.litellm_params.thinking = {
							type: "enabled",
							budget_tokens: budgetMap[effort],
						};
					} else if (m.litellm_params) {
						delete m.litellm_params.thinking;
					}
				}
			}
			const rendered = window._renderedModels || [];
			const idx = rendered.findIndex((x) => x.model_name === modelName);
			if (idx !== -1) {
				rendered[idx].reasoning_effort = effort;
				if (isBedrock && rendered[idx].litellm_params) {
					if (effort) {
						const budgetMap = { low: 1024, medium: 8000, high: 16000 };
						rendered[idx].litellm_params.thinking = {
							type: "enabled",
							budget_tokens: budgetMap[effort],
						};
					} else {
						delete rendered[idx].litellm_params.thinking;
					}
				}
			}
			renderModelList(rendered, true);
			showToast(`Reasoning effort updated for ${modelName}`);
		} else {
			const error = await res.json();
			showToast(`Error: ${error.detail || "Failed to update"}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

async function updateModelGroup(modelName, groupName) {
	const toast = showToast("Updating group...", "info", 0, true);
	try {
		const encoded = base64urlEncode(modelName);
		const res = await fetch(`/api/models/${encoded}`, {
			method: "PATCH",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ model_group: groupName || null }),
		});
		if (res.ok) {
			updateToast(toast, "Group updated — reload LiteLLM to apply", "success");
			const reloadBtn = document.getElementById("reload-litellm-btn");
			if (reloadBtn) reloadBtn.classList.add("needs-reload");
		} else {
			const error = await res.json();
			updateToast(
				toast,
				`Error: ${error.detail || "Failed to update group"}`,
				"error",
			);
			setTimeout(() => {
				toast.style.opacity = "0";
				toast.style.transition = "opacity 0.3s";
				setTimeout(() => toast.remove(), 300);
			}, 3000);
		}
	} catch (e) {
		updateToast(toast, `Error: ${e.message}`, "error");
		setTimeout(() => {
			toast.style.opacity = "0";
			toast.style.transition = "opacity 0.3s";
			setTimeout(() => toast.remove(), 300);
		}, 3000);
	}
}

async function deleteModel(btn, modelName) {
	const modelItem = btn.closest(".model-item");
	if (!btn || !modelItem) return;

	if (btn.dataset.confirming === "true") return;

	btn.dataset.confirming = "true";
	modelItem.classList.add("deleting");
	btn.textContent = "Confirm";
	btn.className = "confirm-btn";
	btn.disabled = true;

	setTimeout(() => {
		btn.disabled = false;
	}, 1000);

	btn.onclick = async () => {
		const toast = showToast("Deleting model...", "info", 0, true);
		try {
			const encoded = base64urlEncode(modelName);
			const res = await fetch(`/api/models/${encoded}`, { method: "DELETE" });
			if (res.ok) {
				updateToast(
					toast,
					"Model deleted — reload LiteLLM to apply",
					"success",
				);
				const reloadBtn = document.getElementById("reload-litellm-btn");
				if (reloadBtn) reloadBtn.classList.add("needs-reload");
				loadModels();
				setTimeout(() => {
					toast.style.opacity = "0";
					toast.style.transition = "opacity 0.3s";
					setTimeout(() => toast.remove(), 300);
				}, 3000);
			} else {
				const error = await res.json();
				updateToast(
					toast,
					`Error: ${error.detail || "Failed to delete model"}`,
					"error",
				);
				resetDeleteBtn(btn, modelItem, modelName);
				setTimeout(() => {
					toast.style.opacity = "0";
					toast.style.transition = "opacity 0.3s";
					setTimeout(() => toast.remove(), 300);
				}, 3000);
			}
		} catch (e) {
			updateToast(toast, `Error: ${e.message}`, "error");
			resetDeleteBtn(btn, modelItem, modelName);
			setTimeout(() => {
				toast.style.opacity = "0";
				toast.style.transition = "opacity 0.3s";
				setTimeout(() => toast.remove(), 300);
			}, 3000);
		}
	};
}

function resetDeleteBtn(btn, modelItem, modelName) {
	btn.dataset.confirming = "false";
	modelItem.classList.remove("deleting");
	btn.textContent = "Delete";
	btn.className = "delete-btn";
	btn.disabled = false;
	btn.onclick = () => deleteModel(btn, modelItem, modelName);
}

function showAddModel() {
	document.getElementById("add-model-section").style.display = "block";
	renderProviderSelector();
	document.getElementById("provider-ui").innerHTML = "";
}

function closeAddModel() {
	document.getElementById("add-model-section").style.display = "none";
	document.getElementById("provider-ui").innerHTML = "";
}

let genericProviderModels = [];
let selectedGenericModel = null;

async function loadProviderUIForProvider(provider) {
	if (provider.type === "bedrock") {
		loadProviderUI("bedrock");
		setTimeout(() => {
			const regionEl = document.getElementById("bedrock-region");
			if (regionEl && provider.aws_region) regionEl.value = provider.aws_region;
		}, 100);
	} else {
		loadGenericProviderUI(provider);
	}
}

function loadGenericProviderUI(provider) {
	const ui = document.getElementById("provider-ui");
	const hasApiBase = !!provider.api_base;
	ui.innerHTML = `
		<div style="margin-bottom:12px;">
			<span class="provider-badge" style="background:${provider.color}20;border:1px solid ${provider.color};color:${provider.color}">${provider.display_name || provider.name}</span>
			<span class="muted" style="font-size:12px;margin-left:8px;">${provider.type || "custom"}</span>
		</div>
		<div class="muted" style="font-size:12px;margin-bottom:12px;">API Base: ${provider.api_base || "Not configured"}</div>
		${
			hasApiBase
				? `
			<div class="inline-row" style="margin-bottom:12px;">
				<button type="button" id="generic-poll-btn" onclick="pollGenericProviderModels('${provider.name.replace(/'/g, "\\'")}')">Poll Models</button>
				<span id="generic-poll-status" style="font-size:12px;margin-left:8px;"></span>
			</div>
			<input id="generic-search" placeholder="Search models..." style="width:400px;margin-bottom:8px;" oninput="filterGenericModels()" />
			<select id="generic-model-select" style="width:400px;padding:5px;" size="10" onchange="onGenericModelSelect()">
				<option value="">-- Poll for models to see available options --</option>
			</select>
			<div id="generic-context-info" class="muted" style="font-size:12px;margin:8px 0;"></div>
			<input id="generic-context-length" type="number" placeholder="Context Length (auto-filled from selection)" style="width:400px;" />
			<br><br>
			<button type="button" onclick="addGenericModel('${provider.name.replace(/'/g, "\\'")}')">Add Model</button>
		`
				: `
			<p class="muted" style="font-size:13px;">Configure <strong>api_base</strong> in the Providers page to enable model polling.</p>
			<input id="generic-manual-model-id" placeholder="Model ID (e.g., gpt-4o)" style="width:400px;" />
			<input id="generic-context-length" type="number" placeholder="Context Length" style="width:400px;" />
			<br><br>
			<button type="button" onclick="addGenericModel('${provider.name.replace(/'/g, "\\'")}')">Add Model</button>
		`
		}
	`;
}

async function pollGenericProviderModels(providerName) {
	const btn = document.getElementById("generic-poll-btn");
	const status = document.getElementById("generic-poll-status");
	btn.disabled = true;
	status.textContent = "Polling...";
	status.style.color = "";
	genericProviderModels = [];
	selectedGenericModel = null;

	try {
		const res = await fetch(
			`/api/providers/${encodeURIComponent(providerName)}/models`,
		);
		if (!res.ok) {
			const error = await res.json();
			throw new Error(error.detail || "Failed to poll models");
		}
		const data = await res.json();
		genericProviderModels = data.models || [];
		renderGenericModelSelect(genericProviderModels);
		status.textContent = `Found ${genericProviderModels.length} models`;
		status.style.color = "#28a745";
	} catch (e) {
		status.textContent = `Error: ${e.message}`;
		status.style.color = "#dc3545";
		showToast(`Failed to poll models: ${e.message}`, "error");
	} finally {
		btn.disabled = false;
	}
}

function renderGenericModelSelect(models) {
	const select = document.getElementById("generic-model-select");
	select.innerHTML =
		models.length === 0
			? '<option value="">No models found</option>'
			: '<option value="">-- Select a model --</option>' +
				models
					.map((m) => {
						const ctx = m.context_length
							? ` (${formatContextLength(m.context_length)})`
							: "";
						return `<option value="${m.id}" data-context-length="${m.context_length || ""}" data-name="${m.name || m.id}">${m.id}${ctx}</option>`;
					})
					.join("");
}

function filterGenericModels() {
	const search = document.getElementById("generic-search").value.toLowerCase();
	const filtered = genericProviderModels.filter(
		(m) =>
			m.id.toLowerCase().includes(search) ||
			(m.name || "").toLowerCase().includes(search),
	);
	renderGenericModelSelect(filtered);
}

function onGenericModelSelect() {
	const select = document.getElementById("generic-model-select");
	const option = select.options[select.selectedIndex];
	const contextInfo = document.getElementById("generic-context-info");
	const contextInput = document.getElementById("generic-context-length");

	if (!option.value) {
		contextInfo.textContent = "";
		contextInput.value = "";
		selectedGenericModel = null;
		return;
	}

	selectedGenericModel = {
		id: option.value,
		name:
			option.dataset.name && option.dataset.name !== "undefined"
				? option.dataset.name
				: option.value,
		context_length: option.dataset.contextLength
			? parseInt(option.dataset.contextLength, 10)
			: null,
	};

	if (selectedGenericModel.context_length) {
		contextInput.value = selectedGenericModel.context_length;
		contextInfo.textContent = `Context Length: ${formatContextLength(selectedGenericModel.context_length)}`;
	} else {
		contextInput.value = "";
		contextInfo.textContent = "Context length not available - enter manually";
	}
}

async function addGenericModel(providerName) {
	const contextLength = document.getElementById("generic-context-length").value;
	const manualInput = document.getElementById("generic-manual-model-id");
	const provider = (window._allProviders || []).find(
		(p) => p.name === providerName,
	);

	let modelId;
	if (manualInput) {
		modelId = manualInput.value.trim();
		if (!modelId) return showToast("Model ID is required", "error");
	} else {
		if (!selectedGenericModel)
			return showToast("Please select a model", "error");
		modelId = selectedGenericModel.id;
	}

	const modelConfig = {
		model_name: (window.USE_PREFIX ? "claw-bedrock/" : "") + modelId,
		litellm_params: {
			model: `openai/${modelId}`,
		},
		provider: providerName,
	};

	if (provider?.api_base) {
		modelConfig.litellm_params.api_base = provider.api_base;
	}
	if (provider?.api_key) {
		modelConfig.litellm_params.api_key = provider.api_key;
	}

	if (contextLength)
		modelConfig.litellm_params.context_length = parseInt(contextLength, 10);

	await addModelCommon(modelConfig, providerName);
}

async function addManualModel() {
	const name = document.getElementById("manual-name").value;
	const modelPath = document.getElementById("manual-model-path").value;
	const apiBase = document.getElementById("manual-api-base").value;
	const contextLength = document.getElementById("manual-context-length").value;
	if (!name || !modelPath)
		return showToast("Model Name and Model Path are required", "error");
	const modelConfig = {
		model_name: (window.USE_PREFIX ? "claw-bedrock/" : "") + name,
		litellm_params: { model: modelPath },
	};
	if (apiBase) modelConfig.litellm_params.api_base = apiBase;
	if (contextLength)
		modelConfig.litellm_params.context_length = parseInt(contextLength, 10);
	await addModelCommon(modelConfig, "manual");
}

async function addOpenRouterModel() {
	const name = document.getElementById("or-name").value;
	const contextLength = document.getElementById(
		"or-context-length-input",
	).value;
	if (!name) return showToast("Model Name is required", "error");
	const modelConfig = {
		model_name: (window.USE_PREFIX ? "claw-bedrock/" : "") + name,
		litellm_params: { model: `openrouter/${name}` },
	};
	if (contextLength)
		modelConfig.litellm_params.context_length = parseInt(contextLength, 10);
	await addModelCommon(modelConfig, "openrouter");
}

async function addOllamaModel() {
	const name = document.getElementById("ollama-name").value;
	const host = document.getElementById("ollama-host").value;
	const contextLength = document.getElementById("ollama-context-length").value;
	if (!name) return showToast("Model Name is required", "error");
	const modelConfig = {
		model_name: (window.USE_PREFIX ? "claw-bedrock/" : "") + name,
		litellm_params: {
			model: `ollama/${name}`,
			api_base: host || "http://localhost:11434",
		},
		model_info: { supports_tool_calling: true },
	};
	if (contextLength)
		modelConfig.litellm_params.context_length = parseInt(contextLength, 10);
	await addModelCommon(modelConfig, "ollama");
}

async function addBedrockModel() {
	const select = document.getElementById("bedrock-select");
	const selected = select.value;
	if (!selected) return showToast("Please select a model", "error");
	const modelData = JSON.parse(selected);
	const modelConfig = {
		model_name: (window.USE_PREFIX ? "claw-bedrock/" : "") + modelData.model,
		litellm_params: {
			model: `bedrock_mantle/${modelData.model}`,
			api_base: "os.environ/BEDROCK_MANTLE_API_BASE",
			max_tokens: modelData.max_tokens,
			context_length: modelData.context_length,
		},
		provider: "bedrock",
	};
	await addModelCommon(modelConfig, "bedrock");
	select.selectedIndex = 0;
}

async function addModelCommon(modelConfig, _provider) {
	const toast = showToast("Adding model...", "info", 0, true);
	try {
		const res = await fetch("/api/models", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(modelConfig),
		});
		if (res.ok) {
			updateToast(toast, "Model added — reload LiteLLM to apply", "success");
			const reloadBtn = document.getElementById("reload-litellm-btn");
			if (reloadBtn) reloadBtn.classList.add("needs-reload");
			needsReload = true;
			closeAddModel();
			loadModels();
			setTimeout(() => {
				toast.style.opacity = "0";
				toast.style.transition = "opacity 0.3s";
				setTimeout(() => toast.remove(), 300);
			}, 3000);
		} else {
			const error = await res.json();
			updateToast(
				toast,
				`Error: ${error.detail || "Failed to add model"}`,
				"error",
			);
			setTimeout(() => {
				toast.style.opacity = "0";
				toast.style.transition = "opacity 0.3s";
				setTimeout(() => toast.remove(), 300);
			}, 3000);
		}
	} catch (e) {
		updateToast(toast, `Error: ${e.message}`, "error");
		setTimeout(() => {
			toast.style.opacity = "0";
			toast.style.transition = "opacity 0.3s";
			setTimeout(() => toast.remove(), 300);
		}, 3000);
	}
}

async function loadBedrockModels() {
	try {
		const res = await fetch("/api/providers/bedrock/models");
		const data = await res.json();
		const select = document.getElementById("bedrock-select");
		select.innerHTML =
			'<option value="">-- Select a model (from catalog) --</option>';
		data.models.forEach((m) => {
			const option = document.createElement("option");
			option.value = JSON.stringify(m);
			option.textContent = `${m.model} (${m.context_length || "?"} ctx)`;
			select.appendChild(option);
		});
	} catch (_e) {
		showToast("Failed to load Bedrock models catalog", "error");
	}
}

async function pollBedrockModels() {
	const token = document.getElementById("bedrock-token").value;
	const region = document.getElementById("bedrock-region").value;
	const statusSpan = document.getElementById("bedrock-poll-status");

	statusSpan.textContent = "Polling...";
	statusSpan.style.color = "";
	bedrockNewModels = [];

	try {
		const url = `/api/providers/bedrock/mantle-models?region=${encodeURIComponent(region)}${token ? `&token=${encodeURIComponent(token)}` : ""}`;
		const res = await fetch(url);
		if (!res.ok) {
			const error = await res.json();
			throw new Error(error.detail || "Failed to poll models");
		}
		const data = await res.json();
		const select = document.getElementById("bedrock-select");
		select.innerHTML =
			'<option value="">-- Select a model (from Mantle API) --</option>';
		(data.models || []).forEach((m) => {
			const option = document.createElement("option");
			option.value = JSON.stringify(m);
			const ctx = m.context_length
				? ` (${formatContextLength(m.context_length)})`
				: "";
			const newBadge = m.in_catalog === false ? " [NEW]" : "";
			option.textContent = `${m.model || m.id}${ctx}${newBadge}`;
			if (m.in_catalog === false) {
				option.style.color = "#dc3545";
				bedrockNewModels.push(m.model || m.id);
			}
			select.appendChild(option);
		});
		statusSpan.textContent = `Found ${data.models?.length || 0} models`;
		statusSpan.style.color = "#28a745";

		const contextInfo = document.getElementById("bedrock-context-info");
		if (bedrockNewModels.length > 0) {
			contextInfo.innerHTML = `<span style="color: #dc3545;">${bedrockNewModels.length} model(s) not in default catalog. <a href="https://github.com/Jeshii/claw-bedrock/issues" target="_blank">This model is not included by default, please open an Issue to have it added.</a></span>`;
		}
	} catch (e) {
		statusSpan.textContent = `Error: ${e.message}`;
		statusSpan.style.color = "#dc3545";
		showToast(`Failed to poll Bedrock models: ${e.message}`, "error");
	}
}

function onBedrockSelect() {
	const select = document.getElementById("bedrock-select");
	const selectedOption = select.options[select.selectedIndex];
	const contextInfo = document.getElementById("bedrock-context-info");

	if (!selectedOption.value) {
		contextInfo.textContent = "";
		return;
	}

	try {
		const modelData = JSON.parse(selectedOption.value);
		let info = "";
		if (modelData.context_length) {
			info = `Context Length: ${formatContextLength(modelData.context_length)}`;
		} else {
			info = "Context length not available";
		}
		if (modelData.max_tokens) {
			info += ` | Max Tokens: ${modelData.max_tokens}`;
		}
		if (modelData.in_catalog === false) {
			info += `<br><span style="color: #dc3545;">This model is not included by default. <a href="https://github.com/Jeshii/claw-bedrock/issues" target="_blank">Please open an Issue to have it added.</a></span>`;
		}
		contextInfo.innerHTML = info;
	} catch (_e) {
		contextInfo.textContent = "";
	}
}

async function loadOpenRouterModels() {
	try {
		const freeOnly = document.getElementById("or-free-only")?.checked;
		const url = freeOnly
			? "/api/providers/openrouter/models?include_free=true"
			: "/api/providers/openrouter/models";
		const res = await fetch(url);
		const data = await res.json();
		openRouterModels = data.models || [];
		renderOpenRouterSelect(openRouterModels);
	} catch (_e) {
		showToast("Failed to load OpenRouter models", "error");
	}
}

function renderOpenRouterSelect(models) {
	const select = document.getElementById("or-select");
	if (!select) return;
	select.innerHTML = models
		.map((m) => {
			const ctx = m.context_length
				? ` (${formatContextLength(m.context_length)})`
				: "";
			return `<option value="${m.id}" data-context-length="${m.context_length || ""}">${m.id}${ctx}</option>`;
		})
		.join("");
}

function filterOpenRouterModels() {
	const search = document.getElementById("or-search").value.toLowerCase();
	const filtered = openRouterModels.filter(
		(m) =>
			m.id.toLowerCase().includes(search) ||
			(m.name || "").toLowerCase().includes(search),
	);
	renderOpenRouterSelect(filtered);
}

function onOpenRouterSelect() {
	const select = document.getElementById("or-select");
	const selectedOption = select.options[select.selectedIndex];
	const modelId = selectedOption.value;
	const contextLength = selectedOption.dataset.contextLength;

	document.getElementById("or-name").value = modelId;
	if (contextLength) {
		document.getElementById("or-context-length-input").value = contextLength;
		document.getElementById("or-context-length").textContent =
			`Context Length: ${formatContextLength(parseInt(contextLength, 10))}`;
	} else {
		document.getElementById("or-context-length-input").value = "";
		document.getElementById("or-context-length").textContent = "";
	}
}

async function fetchOllamaContextLength() {
	const name = document.getElementById("ollama-name").value;
	const host = document.getElementById("ollama-host").value;
	if (!name) return;

	try {
		const url = `/api/providers/ollama/model-details?name=${encodeURIComponent(name)}${host ? `&api_base=${encodeURIComponent(host)}` : ""}`;
		const res = await fetch(url);
		if (res.ok) {
			const data = await res.json();
			if (data.context_length) {
				document.getElementById("ollama-context-length").value =
					data.context_length;
				document.getElementById("ollama-context-info").textContent =
					`Context Length: ${formatContextLength(data.context_length)}`;
			} else {
				document.getElementById("ollama-context-info").textContent =
					"Context length not available for this model";
			}
		} else {
			document.getElementById("ollama-context-info").textContent =
				"Could not fetch model details";
			setTimeout(() => {
				const el = document.getElementById("ollama-context-info");
				if (el && el.textContent === "Could not fetch model details")
					el.textContent = "";
			}, 5000);
		}
	} catch (_e) {
		document.getElementById("ollama-context-info").textContent =
			"Error fetching model details";
		setTimeout(() => {
			const el = document.getElementById("ollama-context-info");
			if (el && el.textContent === "Error fetching model details")
				el.textContent = "";
		}, 5000);
	}
}

async function fetchOllamaModels() {
	const host = document.getElementById("ollama-host").value;
	const select = document.getElementById("ollama-model-select");
	select.innerHTML = '<option value="">Loading...</option>';
	select.style.display = "";
	try {
		const url = `/api/providers/ollama/models${host ? `?api_base=${encodeURIComponent(host)}` : ""}`;
		const res = await fetch(url);
		if (res.ok) {
			const data = await res.json();
			const models = data.models || [];
			if (models.length === 0) {
				select.innerHTML = '<option value="">No models found</option>';
			} else {
				select.innerHTML =
					'<option value="">-- Select a model --</option>' +
					models
						.map((m) => `<option value="${m.name}">${m.name}</option>`)
						.join("");
			}
		} else {
			const err = await res.json();
			select.innerHTML = `<option value="">Error: ${err.detail || "Failed to fetch"}</option>`;
			setTimeout(() => {
				select.style.display = "none";
			}, 5000);
		}
	} catch (e) {
		select.innerHTML = `<option value="">Error: ${e.message}</option>`;
		setTimeout(() => {
			select.style.display = "none";
		}, 5000);
	}
}

function onOllamaSelect() {
	const select = document.getElementById("ollama-model-select");
	const nameInput = document.getElementById("ollama-name");
	if (select.value) {
		nameInput.value = select.value;
	}
}

function loadProviderUI(type) {
	const ui = document.getElementById("provider-ui");
	if (type === "openrouter") {
		ui.innerHTML = `
            <h3>Add OpenRouter Model</h3>
            <div style="margin-bottom: 8px;">
                <input id="or-search" placeholder="Search OpenRouter models..." style="width: 400px;" oninput="filterOpenRouterModels()" />
            </div>
            <label class="inline-row" style="margin-bottom: 8px; cursor: pointer;">
                <input type="checkbox" id="or-free-only" onchange="loadOpenRouterModels()" style="width: 16px;" />
                <span>Show free models only</span>
            </label>
            <select id="or-select" style="width: 400px; padding: 5px; margin-bottom: 8px;" size="10" onchange="onOpenRouterSelect()">
                <option value="">Loading models...</option>
            </select>
            <div id="or-context-length" class="muted" style="font-size: 12px; margin-bottom: 8px;"></div>
            <input id="or-name" placeholder="Or type model ID manually" style="width: 400px;" />
            <input id="or-context-length-input" type="number" placeholder="Context Length (auto-filled from selection)" style="width: 400px;" />
            <button type="button" onclick="addOpenRouterModel()">Add Model</button>
            <p class="muted" style="font-size: 12px; margin-top: 5px;">See <a href="https://openrouter.ai/models" target="_blank">OpenRouter models</a>.</p>
        `;
		loadOpenRouterModels();
	} else if (type === "ollama") {
		ui.innerHTML = `
            <h3>Add Ollama Model</h3>
            <div class="inline-row" style="margin-bottom: 8px;">
                <input id="ollama-host" placeholder="http://ollama-host:11434" style="width: 400px;" />
                <button type="button" onclick="fetchOllamaModels()">Fetch Models</button>
            </div>
            <select id="ollama-model-select" style="width: 400px; padding: 5px; margin-bottom: 8px; display: none;" onchange="onOllamaSelect()"></select>
            <input id="ollama-name" placeholder="Model name (e.g., qwen2.5:14b)" style="width: 400px;" />
            <button type="button" onclick="fetchOllamaContextLength()" title="Fetch context length from Ollama" style="padding: 8px 12px; margin: 5px;">Get Details</button>
            <div id="ollama-context-info" class="muted" style="font-size: 12px; margin: 5px 0;"></div>
            <input id="ollama-context-length" type="number" placeholder="Context Length (auto-filled)" style="width: 400px;" />
            <button type="button" onclick="addOllamaModel()">Add Model</button>
            <p class="muted" style="font-size: 12px; margin-top: 5px;">Ensure the remote Ollama instance is running and accessible.</p>
        `;
	} else if (type === "bedrock") {
		ui.innerHTML = `
            <h3>Add Bedrock Model</h3>
            <div style="margin-bottom: 8px;">
                <input id="bedrock-token" type="password" placeholder="Bedrock Mantle API Key (or set BEDROCK_MANTLE_API_KEY)" style="width: 400px;" />
            </div>
            <div class="inline-row" style="margin-bottom: 8px;">
                <select id="bedrock-region" style="width: 200px; padding: 5px;">
                    <option value="ap-northeast-1">ap-northeast-1</option>
                    <option value="us-east-1">us-east-1</option>
                    <option value="us-west-2">us-west-2</option>
                    <option value="eu-west-1">eu-west-1</option>
                </select>
                <button type="button" onclick="pollBedrockModels()">Poll Models</button>
                <span id="bedrock-poll-status" style="font-size: 12px; margin-left: 8px;"></span>
            </div>
            <select id="bedrock-select" style="width: 400px; padding: 5px;" size="10" onchange="onBedrockSelect()"></select>
            <div id="bedrock-context-info" class="muted" style="font-size: 12px; margin: 8px 0;"></div>
            <button type="button" onclick="addBedrockModel()">Add Model</button>
            <button type="button" onclick="loadBedrockModels()">Load from Catalog</button>
            <p class="muted" style="font-size: 12px; margin-top: 5px;">Poll fetches live models from Mantle API. "Load from Catalog" uses the static catalog.</p>
        `;
		loadBedrockModels();
	} else if (type === "manual") {
		ui.innerHTML = `
            <h3>Add Model Manually</h3>
            <input id="manual-name" placeholder="Model Name (e.g., my-model)" style="width: 400px;" /><br>
            <input id="manual-model-path" placeholder="Model Path (e.g., openrouter/google/gemini-2.0-flash-001)" style="width: 400px;" /><br>
            <input id="manual-api-base" placeholder="API Base (optional, e.g., https://openrouter.ai/api/v1)" style="width: 400px;" /><br>
            <input id="manual-context-length" type="number" placeholder="Context Length (e.g., 131072 for 128k)" style="width: 400px;" /><br>
            <button type="button" onclick="addManualModel()">Add Model</button>
        `;
	} else {
		ui.innerHTML = "";
	}
}

async function reloadLiteLLM() {
	const toast = showToast("Restarting LiteLLM...", "info", 0, true);
	try {
		const res = await fetch("/api/models/reload", { method: "POST" });
		const data = await res.json();
		if (data.status === "success") {
			updateToast(
				toast,
				data.message || "LiteLLM reloaded successfully",
				"success",
			);
			const reloadBtn = document.getElementById("reload-litellm-btn");
			if (reloadBtn) reloadBtn.classList.remove("needs-reload");
			needsReload = false;
		} else {
			updateToast(toast, data.message || "LiteLLM reload failed", "warning");
		}
	} catch (e) {
		updateToast(toast, `Error: ${e.message}`, "error");
	}
	setTimeout(() => {
		toast.style.opacity = "0";
		toast.style.transition = "opacity 0.3s";
		setTimeout(() => toast.remove(), 300);
	}, 3000);
}

async function loadRouterSettings() {
	try {
		const res = await fetch("/api/settings/router");
		const s = await res.json();
		const strategyEl = document.getElementById("routing-strategy");
		const failsEl = document.getElementById("allowed-fails");
		const retriesEl = document.getElementById("num-retries");
		if (strategyEl && s.routing_strategy) strategyEl.value = s.routing_strategy;
		if (failsEl && s.allowed_fails != null) failsEl.value = s.allowed_fails;
		if (retriesEl && s.num_retries != null) retriesEl.value = s.num_retries;
	} catch (_e) {
		// Router settings UI may not be rendered yet; that's fine
	}
}

async function saveRouterSetting() {
	const body = {
		routing_strategy: document.getElementById("routing-strategy").value,
		allowed_fails: parseInt(document.getElementById("allowed-fails").value),
		num_retries: parseInt(document.getElementById("num-retries").value),
	};
	try {
		const res = await fetch("/api/settings/router", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		});
		if (res.ok) {
			showToast("Router settings saved");
		} else {
			const error = await res.json();
			showToast(
				`Error: ${error.detail || "Failed to save router settings"}`,
				"error",
			);
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

function sortModels(models, sortKey) {
	const sorted = [...models];
	switch (sortKey) {
		case "alpha":
			sorted.sort((a, b) => a.model_name.localeCompare(b.model_name));
			break;
		case "provider":
			sorted.sort((a, b) => {
				const pa = (a.litellm_params?.model || "").split("/")[0];
				const pb = (b.litellm_params?.model || "").split("/")[0];
				return pa.localeCompare(pb) || a.model_name.localeCompare(b.model_name);
			});
			break;
		case "context":
			sorted.sort(
				(a, b) =>
					(b.litellm_params?.context_length || 0) -
					(a.litellm_params?.context_length || 0),
			);
			break;
		case "reasoning": {
			const order = { low: 1, medium: 2, high: 3 };
			const effortLevel = (m) => {
				if (m.reasoning_effort) return order[m.reasoning_effort] || 0;
				if (m.litellm_params?.thinking?.type === "enabled") {
					const t = m.litellm_params.thinking.budget_tokens;
					return t >= 12000 ? 3 : t >= 2000 ? 2 : 1;
				}
				return 0;
			};
			sorted.sort((a, b) => effortLevel(b) - effortLevel(a));
			break;
		}
	}
	return sorted;
}

function toggleSortMenu() {
	const bar = document.getElementById("model-filter-bar");
	let menu = document.getElementById("sort-menu");
	if (menu) {
		menu.remove();
		return;
	}
	const btn = bar.querySelector(".sort-btn");
	menu = document.createElement("div");
	menu.id = "sort-menu";
	menu.className = "sort-menu";
	menu.innerHTML = Object.entries(SORT_LABELS)
		.map(
			([key, label]) =>
				`<div class="sort-menu-item ${currentSort === key ? "active" : ""}" onclick="applySort('${key}')">${currentSort === key ? "✓ " : ""}${label}</div>`,
		)
		.join("");
	bar.appendChild(menu);
	menu.style.top = `${btn.offsetTop + btn.offsetHeight + 2}px`;
	menu.style.left = `${btn.offsetLeft}px`;
	setTimeout(() => {
		document.addEventListener("click", function handler(e) {
			if (
				!menu.contains(e.target) &&
				e.target !== bar.querySelector(".sort-btn")
			) {
				menu.remove();
				document.removeEventListener("click", handler);
			}
		});
	}, 0);
}

function applySort(key) {
	currentSort = key;
	document.getElementById("sort-menu")?.remove();
	const btn = document.getElementById("sort-btn");
	if (btn) btn.innerHTML = `Sort: ${SORT_LABELS[key]} ▾`;
	renderModelList(window._renderedModels || []);
}

function setFilter(tag) {
	loadModels(tag);
}

function handleTagDragStart(event, tagName) {
	event.dataTransfer.setData("text/plain", tagName);
	event.dataTransfer.effectAllowed = "copy";
}

function handleDragOver(event) {
	event.preventDefault();
	event.dataTransfer.dropEffect = "copy";
	event.currentTarget.classList.add("drag-over");
}

function handleDragLeave(event) {
	event.currentTarget.classList.remove("drag-over");
}

function handleTagDrop(event, modelName) {
	event.preventDefault();
	event.currentTarget.classList.remove("drag-over");
	const tagName = event.dataTransfer.getData("text/plain");
	if (tagName) {
		addTagToModel(modelName, tagName);
	}
}

async function addTagToModel(modelName, tagName) {
	try {
		const encoded = base64urlEncode(modelName);
		const res = await fetch(`/api/models/${encoded}/tags`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ tag_name: tagName }),
		});
		if (res.ok) {
			showToast(`Tag "${tagName}" added to ${modelName}`);
			loadModels(activeFilter);
		} else {
			const err = await res.json();
			showToast(`Error: ${err.detail || "Failed to add tag"}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

async function removeTagFromModel(modelName, tagName) {
	try {
		const encoded = base64urlEncode(modelName);
		const res = await fetch(
			`/api/models/${encoded}/tags/${encodeURIComponent(tagName)}`,
			{
				method: "DELETE",
			},
		);
		if (res.ok) {
			loadModels(activeFilter);
		} else {
			const err = await res.json();
			showToast(`Error: ${err.detail || "Failed to remove tag"}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

function handleTagInputKeydown(event, modelName) {
	if (event.key === "Enter") {
		event.preventDefault();
		const input = document.getElementById(`tag-input-${modelName}`);
		const val = input.value.trim();
		if (val) {
			addTagToModel(modelName, val);
			input.value = "";
		}
		hideTagAutocomplete(modelName);
	} else if (event.key === "Escape") {
		hideTagAutocomplete(modelName);
		document.getElementById(`tag-input-${modelName}`).blur();
	}
}

function handleTagInputChange(modelName) {
	const input = document.getElementById(`tag-input-${modelName}`);
	const val = input.value.trim().toLowerCase();
	if (!val) {
		hideTagAutocomplete(modelName);
		return;
	}
	const matches = (window._allTags || []).filter((t) =>
		t.name.toLowerCase().includes(val),
	);
	if (matches.length === 0) {
		hideTagAutocomplete(modelName);
		return;
	}
	const wrap = document.getElementById(`tag-input-wrap-${modelName}`);
	let dd = document.getElementById(`tag-ac-${modelName}`);
	if (!dd) {
		dd = document.createElement("div");
		dd.id = `tag-ac-${modelName}`;
		dd.className = "tag-autocomplete";
		wrap.appendChild(dd);
	}
	dd.innerHTML = matches
		.map(
			(t) =>
				`<div class="tag-autocomplete-item" onmousedown="event.preventDefault(); selectTagAutocomplete('${modelName}', '${t.name}')"><span class="tag-autocomplete-swatch" style="background:${t.color}"></span>${t.name}</div>`,
		)
		.join("");
	dd.style.display = "block";
}

function handleTagInputBlur(modelName) {
	setTimeout(() => {
		hideTagAutocomplete(modelName);
		const input = document.getElementById(`tag-input-${modelName}`);
		const val = input.value.trim();
		if (val) {
			addTagToModel(modelName, val);
			input.value = "";
		}
	}, 200);
}

function hideTagAutocomplete(modelName) {
	const dd = document.getElementById(`tag-ac-${modelName}`);
	if (dd) dd.remove();
}

function selectTagAutocomplete(modelName, tagName) {
	hideTagAutocomplete(modelName);
	addTagToModel(modelName, tagName);
	const input = document.getElementById(`tag-input-${modelName}`);
	input.value = "";
}

async function togglePrefix() {
	const toggle = document.getElementById("use-prefix-toggle");
	const usePrefix = toggle.checked;
	const res = await fetch(`/api/settings?use_prefix=${usePrefix}`, {
		method: "POST",
	});
	if (res.ok) {
		window.USE_PREFIX = usePrefix;
		showToast(`Model prefix ${usePrefix ? "enabled" : "disabled"}`);
	} else {
		showToast("Failed to update setting", "error");
		toggle.checked = !usePrefix;
	}
}
