let pendingBackup = null;

async function loadExportStats() {
	const [modelsRes, tagsRes, providersRes] = await Promise.all([
		fetch("/api/models").then((r) => r.json()),
		fetch("/api/tags").then((r) => r.json()),
		fetch("/api/providers").then((r) => r.json()),
	]);
	document.getElementById("export-model-count").textContent =
		`${modelsRes.models.length} models`;
	document.getElementById("export-tag-count").textContent =
		`${tagsRes.tags.length} tags`;
	document.getElementById("export-provider-count").textContent =
		`${providersRes.providers.length} providers`;
}

function exportBackup() {
	const a = document.createElement("a");
	a.href = "/api/backup/export";
	a.download = "";
	a.click();
}

const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("import-file");

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => {
	e.preventDefault();
	dropZone.style.borderColor = "#007bff";
});
dropZone.addEventListener("dragleave", () => {
	dropZone.style.borderColor = "#ccc";
});
dropZone.addEventListener("drop", (e) => {
	e.preventDefault();
	dropZone.style.borderColor = "#ccc";
	if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
	if (fileInput.files[0]) handleFile(fileInput.files[0]);
});

async function handleFile(file) {
	const text = await file.text();
	let parsed;
	try {
		parsed = JSON.parse(text);
	} catch {
		showImportError("File is not valid JSON.");
		return;
	}
	const res = await fetch("/api/backup/preview", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(parsed),
	});
	const preview = await res.json();
	if (!res.ok) {
		showImportError(preview.detail || "Invalid backup file.");
		return;
	}
	pendingBackup = parsed;
	const previewEl = document.getElementById("import-preview");
	previewEl.innerHTML = `<p><strong>Backup valid ✓</strong></p>
        <ul>
            <li>Created: ${preview.created_at || "unknown"}</li>
            <li>Claw version: ${preview.claw_version}</li>
            <li>${preview.counts.models} models, ${preview.counts.tags} tags, ${preview.counts.providers} providers, ${preview.counts.settings} settings</li>
        </ul>`;
	previewEl.style.display = "";
	document.getElementById("import-mode-row").style.display = "";
	document.getElementById("btn-import").style.display = "";
}

function showImportError(msg) {
	const previewEl = document.getElementById("import-preview");
	previewEl.innerHTML = `<p style="color:#dc3545;">✗ ${msg}</p>`;
	previewEl.style.display = "";
	document.getElementById("btn-import").style.display = "none";
}

async function submitImport() {
	if (!pendingBackup) return;
	const mode = document.querySelector(
		'input[name="import-mode"]:checked',
	).value;
	const warning =
		mode === "replace"
			? "This will DELETE all current models, tags, providers, and settings and replace them with the backup. Are you sure?"
			: "This will merge backup data into current config. Existing records are kept. Continue?";
	if (!confirm(warning)) return;
	const res = await fetch(`/api/backup/import?mode=${mode}`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(pendingBackup),
	});
	const result = await res.json();
	if (result.success) {
		const i = result.imported;
		alert(
			`Import complete! ${i.models} models, ${i.tags} tags, ${i.providers} providers imported. (${i.skipped} skipped)`,
		);
		location.reload();
	} else {
		alert(`Import failed: ${result.detail || "Unknown error"}`);
	}
}
