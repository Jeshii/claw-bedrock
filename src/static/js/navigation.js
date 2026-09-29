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
	activatePage(pageId);
}

/**
 * Show a page, highlight its nav link, and run that page's loader.
 * Shared by showPage() and dismissReloadWarning() so the dispatch list
 * only has to be maintained in one place.
 */
function activatePage(pageId) {
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
	if (pageId === "dashboard") loadDashboard();
	if (pageId === "security") loadKeyStatus();
	if (pageId === "models") loadModels();
	if (pageId === "backup") loadExportStats();
	if (pageId === "providers") loadProvidersPage();
	if (pageId === "playground") loadPlayground();
	if (pageId === "groups") loadGroups();
	if (pageId === "tags") loadTagsPage();
	if (pageId === "logs") {
		loadLogs();
		loadDebugLogs();
		loadContainerLogs();
		restoreAutoRefresh();
	}
	if (pageId === "auth") loadAuth();
	// Nothing unmounts when you navigate, so an armed mic would stay open on
	// another page. This is here rather than in showPage() because that can
	// return early via the reload-warning modal and only reach activatePage()
	// later, from dismissReloadWarning().
	if (pageId !== "playground" && window.PlaygroundAudio) {
		window.PlaygroundAudio.disarm();
	}
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
		activatePage(pageId);
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
