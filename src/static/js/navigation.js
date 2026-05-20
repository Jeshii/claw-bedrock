function _showPage(pageId) {
	if (pageId !== "logs") {
		clearAutoRefresh();
		[
			"auto-refresh-toggle",
			"auto-refresh-debug-toggle",
			"auto-refresh-container-toggle",
		].forEach((id) => {
			const btn = document.getElementById(id);
			if (btn) {
				btn.style.background = "";
				btn.style.color = "";
			}
		});
	}
	document.querySelectorAll(".page").forEach((p) => {
		p.classList.remove("active");
	});
	document.getElementById(`page-${pageId}`).classList.add("active");
	document.querySelectorAll(".nav a").forEach((a) => {
		a.classList.remove("active");
	});
	event.target.classList.add("active");
	if (pageId === "dashboard") loadDashboard();
	if (pageId === "security") loadKeyStatus();
	if (pageId === "models") loadModels();
	if (pageId === "backup") loadExportStats();
	if (pageId === "providers") loadProvidersPage();
	if (pageId === "tags") loadTagsPage();
	if (pageId === "logs") {
		loadLogs();
		loadDebugLogs();
		loadContainerLogs();
		restoreAutoRefresh();
	}
	if (pageId === "auth") loadAuth();
}

function _showPage2(pageId) {
	document.querySelectorAll(".page").forEach((p) => {
		p.classList.remove("active");
	});
	document.getElementById(`page-${pageId}`).classList.add("active");
	document.querySelectorAll(".nav a").forEach((a) => {
		a.classList.toggle(
			"active",
			a.getAttribute("onclick") === `showPage('${pageId}')`,
		);
	});
	if (pageId === "auth") loadAuth();
	if (pageId === "security") loadKeyStatus();
}

function _hideLoadingOverlay() {
	const overlay = document.getElementById("loading-overlay");
	if (overlay) overlay.remove();
}
