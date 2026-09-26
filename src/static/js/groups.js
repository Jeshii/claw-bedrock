let expandedGroup = null;

const STATUS_LABELS = {
	ok: "Ready",
	warn: "Check config",
	error: "Not in config",
};

async function loadGroups() {
	const list = document.getElementById("groups-list");
	const ungrouped = document.getElementById("groups-ungrouped");
	if (!list) return;
	list.innerHTML = '<p class="muted">Loading groups…</p>';

	try {
		const res = await fetch("/api/model-groups");
		if (!res.ok) throw new Error(`HTTP ${res.status}`);
		const data = await res.json();
		renderGroups(data);
	} catch (e) {
		list.innerHTML = `<p style="color: var(--danger); font-weight: 600;">Could not load groups: ${escapeHtml(e.message)}</p>`;
		if (ungrouped) ungrouped.classList.add("hidden");
	}
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

	return `
    <div class="group-card" data-group="${escapeAttr(group.name)}">
        <div class="group-card-header" data-action="toggle-group">
            <span class="model-chevron" data-chevron>${CHEVRON_RIGHT_SVG}</span>
            <span class="group-card-name">${escapeHtml(group.name)}</span>
            <span class="status-chip ${degraded ? "warn" : "ok"}">${escapeHtml(readiness)}</span>
        </div>
        <div class="group-members" data-members>
            ${group.members.map(renderMemberRow).join("")}
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
	const title = status.detail ? ` title="${escapeAttr(status.detail)}"` : "";

	return `
        <div class="member-row" data-action="goto-model" data-model="${escapeAttr(member.model_name)}" title="Open in Models">
            <span class="member-row-name">${escapeHtml(member.model_name)}</span>
            ${providerBadge}
            ${ctx ? `<span class="muted" style="font-size: 12px;">${escapeHtml(ctx)}</span>` : ""}
            <span class="status-chip ${status.level}"${title}>${escapeHtml(STATUS_LABELS[status.level] || status.level)}</span>
        </div>`;
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
			const toggle = e.target.closest('[data-action="toggle-group"]');
			if (toggle) {
				const card = toggle.closest(".group-card");
				if (card) toggleGroup(card.dataset.group);
				return;
			}
			const member = e.target.closest('[data-action="goto-model"]');
			if (member) gotoModel(member.dataset.model);
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
