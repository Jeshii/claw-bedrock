function showPage(pageId) {
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
	if (pageId !== "models" && needsReload) {
		const modal = document.getElementById("reload-warning-modal");
		if (modal) {
			modal.showModal();
			window._pendingPage = pageId;
			// Load playground models from TinyDB even before reload completes
			if (pageId === "playground") loadPlayground();
			return;
		}
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
	if (pageId === "playground") loadPlayground();
	if (pageId === "tags") loadTagsPage();
	if (pageId === "logs") {
		loadLogs();
		loadDebugLogs();
		loadContainerLogs();
		restoreAutoRefresh();
	}
	if (pageId === "auth") loadAuth();
}

function dismissReloadWarning(doReload) {
	const modal = document.getElementById("reload-warning-modal");
	if (modal) modal.close();
	if (doReload) {
		reloadLiteLLM();
	}
	needsReload = false;
	const reloadBtn = document.getElementById("reload-litellm-btn");
	if (reloadBtn) reloadBtn.classList.remove("needs-reload");
	if (window._pendingPage) {
		const pageId = window._pendingPage;
		delete window._pendingPage;
		document.querySelectorAll(".page").forEach((p) => {
			p.classList.remove("active");
		});
		document.getElementById(`page-${pageId}`).classList.add("active");
		document.querySelectorAll(".nav a").forEach((a) => {
			a.classList.remove("active");
			if (a.getAttribute("onclick") === `showPage('${pageId}')`) {
				a.classList.add("active");
			}
		});
		if (pageId === "dashboard") loadDashboard();
		if (pageId === "security") loadKeyStatus();
		if (pageId === "backup") loadExportStats();
		if (pageId === "providers") loadProvidersPage();
		if (pageId === "playground") loadPlayground();
		if (pageId === "tags") loadTagsPage();
		if (pageId === "logs") {
			loadLogs();
			loadDebugLogs();
			loadContainerLogs();
			restoreAutoRefresh();
		}
		if (pageId === "auth") loadAuth();
	}
}

function showPage2(pageId) {
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

function hideLoadingOverlay() {
	const overlay = document.getElementById("loading-overlay");
	if (overlay) overlay.remove();
}
