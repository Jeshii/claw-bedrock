let _allSkills = [];
let _selectedSkillName = null;
let _skillCreateMode = false;

async function loadSkillsPage() {
	try {
		const res = await fetch("/api/skills");
		if (!res.ok) throw new Error("Failed to load skills");
		const data = await res.json();
		_allSkills = data.skills || [];
		filterSkills();
		if (_selectedSkillName && !_skillCreateMode) {
			const skill = _allSkills.find((s) => s.name === _selectedSkillName);
			if (skill) {
				renderSkillForm(skill);
			} else {
				_selectedSkillName = null;
				document.getElementById("skill-detail").innerHTML =
					'<p class="muted">Select a skill to view or edit, or create a new one.</p>';
			}
		}
	} catch (e) {
		const list = document.getElementById("skills-list");
		if (list)
			list.innerHTML = `<p style="color: var(--danger);">Could not load skills: ${escapeHtml(e.message)}</p>`;
	}
}

function filterSkills() {
	const q = (
		document.getElementById("skill-search")?.value || ""
	).toLowerCase();
	const type = document.getElementById("skill-type-filter")?.value || "";
	const filtered = _allSkills.filter((s) => {
		if (type && s.type !== type) return false;
		if (!q) return true;
		return `${s.name} ${s.description || ""} ${s.content || ""}`
			.toLowerCase()
			.includes(q);
	});
	renderSkillsList(filtered);
}

function renderSkillsList(skills) {
	const list = document.getElementById("skills-list");
	if (!list) return;
	if (!skills.length) {
		list.innerHTML = '<p class="muted">No skills yet. Create one above.</p>';
		return;
	}
	list.innerHTML = skills
		.map(
			(s) => `
		<div class="skill-row ${_selectedSkillName === s.name ? "selected" : ""}"
		     data-name="${escapeAttr(s.name)}"
		     onclick="selectSkill(this.dataset.name)">
			<span class="skill-row-name">${escapeHtml(s.name)}</span>
			<span class="skill-row-type">${escapeHtml(s.type)}</span>
		</div>`,
		)
		.join("");
}

function renderSkillForm(skill) {
	_skillCreateMode = !skill;
	_selectedSkillName = skill ? skill.name : null;
	const s = skill || {
		name: "",
		description: "",
		content: "",
		type: "prompt",
		tags: [],
		created_at: "",
		updated_at: "",
	};
	const meta = skill
		? `Created ${escapeHtml(skill.created_at)} · Updated ${escapeHtml(skill.updated_at)}`
		: "New skill — not yet saved";
	document.getElementById("skill-detail").innerHTML = `
		<div class="skill-detail-form">
			<div class="inline-row" style="margin-bottom: 8px; gap: 8px;">
				<label style="min-width: 90px;">Name</label>
				<input id="skill-name" style="width: 320px;" placeholder="skill-name"
				       value="${escapeAttr(s.name)}" ${skill ? "disabled" : ""} />
			</div>
			<div class="inline-row" style="margin-bottom: 8px; gap: 8px;">
				<label style="min-width: 90px;">Description</label>
				<input id="skill-description" style="width: 320px;" placeholder="What this skill does"
				       value="${escapeAttr(s.description || "")}" />
			</div>
			<div class="inline-row" style="margin-bottom: 8px; gap: 8px;">
				<label style="min-width: 90px;">Type</label>
				<select id="skill-type" style="width: 160px;">
					<option value="prompt">prompt</option>
					<option value="template">template</option>
					<option value="python">python</option>
					<option value="shell">shell</option>
				</select>
			</div>
			<div class="inline-row" style="margin-bottom: 8px; gap: 8px;">
				<label style="min-width: 90px;">Tags</label>
				<input id="skill-tags" style="width: 320px;" placeholder="comma-separated"
				       value="${escapeAttr((s.tags || []).join(", "))}" />
			</div>
			<div style="margin-bottom: 8px;">
				<label style="display: block; margin-bottom: 4px;">Content</label>
				<textarea id="skill-content" class="skill-editor" rows="16" placeholder="Skill content...">${escapeHtml(s.content || "")}</textarea>
			</div>
			<div id="skill-meta" class="muted" style="font-size: 12px; margin-bottom: 8px;">${meta}</div>
			<div class="inline-row" style="gap: 8px;">
				<button type="button" onclick="saveSkill()" id="skill-save-btn">Save</button>
				<button type="button" onclick="deleteSkill()" id="skill-delete-btn" class="danger-btn" ${skill ? "" : "disabled"}>Delete</button>
			</div>
		</div>`;
	setTimeout(() => {
		const t = document.getElementById("skill-type");
		if (t) t.value = s.type || "prompt";
	}, 0);
	renderSkillsList(_allSkills);
}

async function selectSkill(name) {
	try {
		const res = await fetch(`/api/skills/${encodeURIComponent(name)}`);
		if (!res.ok) throw new Error("Failed to load skill");
		const skill = await res.json();
		_skillCreateMode = false;
		_selectedSkillName = name;
		renderSkillForm(skill);
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

function newSkill() {
	_skillCreateMode = true;
	_selectedSkillName = null;
	renderSkillForm(null);
}

async function saveSkill() {
	const name = (document.getElementById("skill-name")?.value || "").trim();
	if (!name) return showToast("Skill name required", "error");
	const skillType = document.getElementById("skill-type")?.value || "prompt";
	const tags = (document.getElementById("skill-tags")?.value || "")
		.split(",")
		.map((t) => t.trim())
		.filter(Boolean);
	const body = {
		name,
		description: document.getElementById("skill-description")?.value || "",
		content: document.getElementById("skill-content")?.value || "",
		type: skillType,
		tags,
	};
	const url = _skillCreateMode
		? "/api/skills"
		: `/api/skills/${encodeURIComponent(_selectedSkillName)}`;
	const method = _skillCreateMode ? "POST" : "PUT";
	try {
		const res = await fetch(url, {
			method,
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		});
		if (res.ok) {
			const saved = await res.json();
			showToast(`Skill "${saved.name}" saved`);
			_selectedSkillName = saved.name;
			_skillCreateMode = false;
			await loadSkillsPage();
			const sk = _allSkills.find((s) => s.name === saved.name);
			if (sk) renderSkillForm(sk);
		} else {
			const err = await res.json().catch(() => ({}));
			showToast(`Error: ${err.detail || "Failed to save"}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

async function deleteSkill() {
	if (!_selectedSkillName || _skillCreateMode) return;
	if (!confirm(`Delete skill "${_selectedSkillName}"?`)) return;
	try {
		const res = await fetch(
			`/api/skills/${encodeURIComponent(_selectedSkillName)}`,
			{
				method: "DELETE",
			},
		);
		if (res.ok) {
			showToast(`Skill "${_selectedSkillName}" deleted`);
			_selectedSkillName = null;
			_skillCreateMode = false;
			document.getElementById("skill-detail").innerHTML =
				'<p class="muted">Select a skill to view or edit, or create a new one.</p>';
			await loadSkillsPage();
		} else {
			const err = await res.json().catch(() => ({}));
			showToast(`Error: ${err.detail || "Failed to delete"}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}
