let expandedGroup = null;
let groupData = {
	groups: [],
	ungrouped_count: 0,
	ungrouped_models: [],
	routing_strategy: null,
};

const STATUS_LABELS = {
	ok: "Ready",
	warn: "Check config",
	error: "Not in config",
};

/** Cost-based routing prices a deployment from the cost fields; without them
 * LiteLLM falls back to $5/$5, so the model is priced as one of the most
 * expensive and stops being picked. Flag it where the setting is made. */
function costsRequired() {
	return groupData.routing_strategy === "cost-based-routing";
}

/** Cheapest member by total in+out price. Only members with both prices
 * recorded are comparable; a half-priced or unpriced member is not a
 * ranking, and treating its missing side as $0 would crown it the winner. */
function cheapestMember(group) {
	const priced = (group.members || []).filter(
		(m) => hasCost(m.input_cost) && hasCost(m.output_cost),
	);
	if (priced.length < 2) return null;
	return priced.reduce((best, m) =>
		Number(m.input_cost) + Number(m.output_cost) <
		Number(best.input_cost) + Number(best.output_cost)
			? m
			: best,
	);
}

async function loadGroups() {
	const list = document.getElementById("groups-list");
	const ungrouped = document.getElementById("groups-ungrouped");
	if (!list) return;

	// A stale name here would leave the wrong card expanded after a rename,
	// or auto-expand a group that no longer exists.
	expandedGroup = null;
	list.innerHTML = '<p class="muted">Loading groups…</p>';

	try {
		const res = await fetch("/api/model-groups");
		if (!res.ok) throw new Error(`HTTP ${res.status}`);
		groupData = await res.json();
		populateGroupNameSuggestions();
		renderGroups(groupData);
	} catch (e) {
		list.innerHTML = `<p style="color: var(--danger); font-weight: 600;">Could not load groups: ${escapeHtml(e.message)}</p>`;
		if (ungrouped) ungrouped.classList.add("hidden");
	}
}

/** Feed the Models page group input so a typo cannot invent a near-duplicate. */
function populateGroupNameSuggestions() {
	const dl = document.getElementById("known-model-groups");
	if (!dl) return;
	dl.innerHTML = (groupData.groups || [])
		.map((g) => `<option value="${escapeAttr(g.name)}"></option>`)
		.join("");
}

async function refreshGroupNameSuggestions() {
	try {
		const res = await fetch("/api/model-groups");
		if (!res.ok) return;
		groupData = await res.json();
		populateGroupNameSuggestions();
	} catch (_e) {
		// Suggestions are a convenience; the input still accepts free text.
	}
}

function memberCount(name) {
	const g = (groupData.groups || []).find((x) => x.name === name);
	return g ? g.member_count : 0;
}

/** A group's name is the model_name clients call, so name both sides. */
function publicModelName(groupName) {
	return window.USE_PREFIX === false ? groupName : `claw-bedrock/${groupName}`;
}

function renderGroups(data) {
	const list = document.getElementById("groups-list");
	const ungrouped = document.getElementById("groups-ungrouped");
	const groups = data.groups || [];

	if (groups.length === 0) {
		list.innerHTML =
			'<p class="muted">No model groups yet. Set a <strong>Group</strong> on a model in the Models page to enable failover.</p>';
	} else {
		list.innerHTML = groups.map(renderGroupCard).join("");
	}

	if (!ungrouped) return;
	if (data.ungrouped_count > 0) {
		ungrouped.classList.remove("hidden");
		ungrouped.innerHTML = `<span class="muted">${data.ungrouped_count} model${
			data.ungrouped_count === 1 ? "" : "s"
		} not in any group</span>
		<button type="button" class="btn-secondary" data-action="goto-models">View in Models</button>`;
	} else {
		ungrouped.classList.add("hidden");
		ungrouped.innerHTML = "";
	}
}

function renderGroupCard(group) {
	const degraded = group.active_member_count < group.member_count;
	const readiness = degraded
		? `${group.active_member_count} of ${group.member_count} in config`
		: `${group.member_count} member${group.member_count === 1 ? "" : "s"}`;

	const cheapest = cheapestMember(group);
	const cheapestChip =
		cheapest && costsRequired()
			? `<span class="status-chip ok" title="Cheapest priced member — the one cost-based routing prefers">cheapest: ${escapeHtml(cheapest.model_name)} (${escapeHtml(formatCost(cheapest.input_cost))} in / ${escapeHtml(formatCost(cheapest.output_cost))} out)</span>`
			: "";

	const ungrouped = groupData.ungrouped_models || [];
	const addMember = ungrouped.length
		? `
            <div class="group-add-member">
                <select class="group-add-member-select"
                        data-action="add-member"
                        data-group="${escapeAttr(group.name)}"
                        aria-label="Add a model to ${escapeAttr(group.name)}">
                    <option value="">Add a model to this group…</option>
                    ${ungrouped
											.map(
												(n) =>
													`<option value="${escapeAttr(n)}">${escapeHtml(n)}</option>`,
											)
											.join("")}
                </select>
            </div>`
		: "";

	return `
    <div class="group-card" data-group="${escapeAttr(group.name)}">
        <div class="group-card-header">
            <div class="group-card-header-main" data-action="toggle-group">
                <span class="model-chevron" data-chevron>${CHEVRON_RIGHT_SVG}</span>
                <span class="group-card-name">${escapeHtml(group.name)}</span>
                <span class="status-chip ${degraded ? "warn" : "ok"}">${escapeHtml(readiness)}</span>
                ${cheapestChip}
            </div>
            <div class="group-card-actions">
                <button type="button" class="rename-btn" data-action="rename-group">Rename</button>
                <button type="button" class="delete-btn" data-action="unassign-group">Unassign</button>
            </div>
        </div>
        <div class="group-members" data-members>
            ${group.members.map(renderMemberRow).join("")}
            ${addMember}
        </div>
    </div>`;
}

function renderMemberRow(member) {
	const provider = member._provider;
	const color = safeColor(provider?.color);
	const providerBadge = color
		? `<span class="provider-badge" style="background:${color}20;border:1px solid ${color};color:${color}">${escapeHtml(provider.display_name || provider.name)}</span>`
		: "";
	const ctx = formatContextLength(member.litellm_params?.context_length);
	const status = member.status || { level: "ok", detail: "" };
	const costChip = renderCostChip(member);
	const title = status.detail ? ` title="${escapeAttr(status.detail)}"` : "";

	return `
        <div class="member-row" data-action="goto-model" data-model="${escapeAttr(member.model_name)}" title="Open in Models">
            <span class="member-row-name">${escapeHtml(member.model_name)}</span>
            ${providerBadge}
            ${ctx ? `<span class="muted" style="font-size: 12px;">${escapeHtml(ctx)}</span>` : ""}
            ${costChip}
            <span class="status-chip ${status.level}"${title}>${escapeHtml(STATUS_LABELS[status.level] || status.level)}</span>
        </div>`;
}

/** Per-1M price for a member, or a warning that cost-based routing will
 * price it at LiteLLM's $5/$5 fallback instead of using a real number. */
function renderCostChip(member) {
	const input = formatCost(member.input_cost);
	const output = formatCost(member.output_cost);
	if (!input && !output) {
		return costsRequired()
			? '<span class="status-chip warn" title="No cost recorded. Cost-based routing falls back to $5 per 1M in and out, so this member will usually not be picked.">no cost</span>'
			: '<span class="muted cost-unset">no cost</span>';
	}
	return `<span class="cost-chip" title="Price per 1M tokens">${escapeHtml(input || "—")} in / ${escapeHtml(output || "—")} out</span>`;
}

function toggleGroup(name) {
	const card = document.querySelector(
		`.group-card[data-group="${CSS.escape(name)}"]`,
	);
	if (!card) return;
	const members = card.querySelector("[data-members]");
	const chevron = card.querySelector("[data-chevron]");

	if (expandedGroup === name) {
		members.classList.remove("open");
		chevron.innerHTML = CHEVRON_RIGHT_SVG;
		expandedGroup = null;
		return;
	}

	if (expandedGroup) {
		const prev = document.querySelector(
			`.group-card[data-group="${CSS.escape(expandedGroup)}"]`,
		);
		if (prev) {
			prev.querySelector("[data-members]").classList.remove("open");
			prev.querySelector("[data-chevron]").innerHTML = CHEVRON_RIGHT_SVG;
		}
	}

	members.classList.add("open");
	chevron.innerHTML = CHEVRON_DOWN_SVG;
	expandedGroup = name;
}

function gotoModel(modelName) {
	showPage("models");
	if (expandedModel !== modelName) toggleModel(modelName);
}

function startGroupRename(name) {
	const card = document.querySelector(
		`.group-card[data-group="${CSS.escape(name)}"]`,
	);
	const label = card?.querySelector(".group-card-name");
	if (!label) return;

	const input = document.createElement("input");
	input.type = "text";
	input.value = name;
	input.className = "group-rename-input";
	input.setAttribute("aria-label", `Rename group ${name}`);

	// Enter commits, Escape reverts, blur commits -- but only once, since
	// Enter triggers a blur on the same input.
	let settled = false;
	const settle = (value) => {
		if (settled) return;
		settled = true;
		submitGroupRename(name, value);
	};

	input.addEventListener("keydown", (e) => {
		e.stopPropagation();
		if (e.key === "Enter") settle(input.value.trim());
		else if (e.key === "Escape") {
			settled = true;
			loadGroups();
		}
	});
	input.addEventListener("blur", () => settle(input.value.trim()));

	label.replaceWith(input);
	input.focus();
	input.select();
}

async function submitGroupRename(oldName, newName) {
	if (!newName || newName === oldName) {
		loadGroups();
		return;
	}

	const count = memberCount(oldName);
	const confirmed = window.confirm(
		`Rename group "${oldName}" to "${newName}"?\n\n` +
			`${count} model${count === 1 ? "" : "s"} will move with it.\n\n` +
			`This changes the model name clients call. Anything requesting ` +
			`${publicModelName(oldName)} will stop working and must switch to ` +
			`${publicModelName(newName)}.`,
	);
	if (!confirmed) {
		loadGroups();
		return;
	}

	try {
		const res = await fetch("/api/model-groups/rename", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ from: oldName, to: newName }),
		});
		if (!res.ok) {
			const err = await res.json();
			showToast(`Error: ${err.detail || "Rename failed"}`, "error");
			loadGroups();
			return;
		}
		showToast(
			`Group renamed to "${newName}" — clients must use ${publicModelName(newName)}`,
		);
		await loadGroups();
		loadModels(activeFilter);
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
		loadGroups();
	}
}

async function unassignGroup(name) {
	const count = memberCount(name);
	const confirmed = window.confirm(
		`Remove group "${name}" from ${count} model${count === 1 ? "" : "s"}?\n\n` +
			`The models are kept, but each goes back to being served under its ` +
			`own model name instead of ${publicModelName(name)}.`,
	);
	if (!confirmed) return;

	try {
		const res = await fetch("/api/model-groups/unassign", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ name }),
		});
		if (!res.ok) {
			const err = await res.json();
			showToast(`Error: ${err.detail || "Unassign failed"}`, "error");
			return;
		}
		showToast(
			`Group "${name}" removed from ${count} model${count === 1 ? "" : "s"}`,
		);
		await loadGroups();
		loadModels(activeFilter);
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

async function addMemberToGroup(groupName, modelName) {
	const { ok, detail } = await setModelGroup(modelName, groupName);
	if (!ok) {
		showToast(`Error: ${detail}`, "error");
		return;
	}
	showToast(`Added "${modelName}" to "${groupName}"`);
	const reloadBtn = document.getElementById("reload-litellm-btn");
	if (reloadBtn) reloadBtn.classList.add("needs-reload");
	await loadGroups();
	loadModels(activeFilter);
}

function escapeHtml(str) {
	return String(str ?? "").replace(
		/[&<>"']/g,
		(c) =>
			({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
				c
			],
	);
}

function escapeAttr(str) {
	return escapeHtml(str).replace(/`/g, "&#96;");
}

/** Provider colors land inside a style attribute, so allow-list the format. */
function safeColor(value) {
	return /^#[0-9a-fA-F]{3,8}$/.test(String(value ?? "")) ? value : null;
}

/* ── Event wiring ── */
document.addEventListener("DOMContentLoaded", () => {
	const list = document.getElementById("groups-list");
	if (list) {
		list.addEventListener("click", (e) => {
			// Card actions are checked before the header toggle, since the
			// buttons sit inside the header row.
			const rename = e.target.closest('[data-action="rename-group"]');
			if (rename) {
				startGroupRename(rename.closest(".group-card")?.dataset.group);
				return;
			}
			const unassign = e.target.closest('[data-action="unassign-group"]');
			if (unassign) {
				unassignGroup(unassign.closest(".group-card")?.dataset.group);
				return;
			}
			const toggle = e.target.closest('[data-action="toggle-group"]');
			if (toggle) {
				const card = toggle.closest(".group-card");
				if (card) toggleGroup(card.dataset.group);
				return;
			}
			const member = e.target.closest('[data-action="goto-model"]');
			if (member) gotoModel(member.dataset.model);
		});

		list.addEventListener("change", (e) => {
			const select = e.target.closest('[data-action="add-member"]');
			if (!select?.value) return;
			addMemberToGroup(select.dataset.group, select.value);
		});
	}

	const ungrouped = document.getElementById("groups-ungrouped");
	if (ungrouped) {
		ungrouped.addEventListener("click", (e) => {
			if (e.target.closest('[data-action="goto-models"]')) showPage("models");
		});
	}
});

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
		allowed_fails: parseInt(document.getElementById("allowed-fails").value, 10),
		num_retries: parseInt(document.getElementById("num-retries").value, 10),
	};
	try {
		const res = await fetch("/api/settings/router", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		});
		if (res.ok) {
			showToast("Router settings saved");
			// The strategy decides whether unpriced members get a warning, so
			// the cards are now showing stale information.
			loadGroups();
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
