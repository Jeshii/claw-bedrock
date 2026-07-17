loadDashboard();
loadAuth();
loadModels();
loadEncryptionStatus();
setTimeout(hideLoadingOverlay, 8000);
setTimeout(loadDashboard, 5000);
setInterval(loadAuth, 10000);
loadPlayground();
loadRouterSettings();

const prefixToggle = document.getElementById("use-prefix-toggle");
if (prefixToggle) {
	prefixToggle.checked = window.USE_PREFIX !== false;
}

(() => {
	const mgmtUrl = window.location.origin;
	const apiHost = window.location.hostname;
	const apiUrl = `${window.location.protocol}//${apiHost}:4000`;
	const apiUrlV1 = `${apiUrl}/v1`;
	document.getElementById("mgmt-url").textContent = mgmtUrl;
	document.getElementById("litellm-url").textContent = apiUrl;
	document.getElementById("models-curl").textContent = `curl ${apiUrl}/models`;
	document.getElementById("opencode-url").textContent = apiUrlV1;
	document.getElementById("clawcode-url").textContent = apiUrlV1;
	document.getElementById("help-curl-base").textContent = apiUrlV1;
	document.getElementById("help-py-base").textContent = apiUrlV1;
})();

// Fallback backdrop click dismiss for dialogs (e.g. Safari)
if (!("closedBy" in HTMLDialogElement.prototype)) {
	document.querySelectorAll("dialog").forEach((dialog) => {
		dialog.addEventListener("click", (event) => {
			if (event.target !== dialog) return;
			const rect = dialog.getBoundingClientRect();
			const isInside =
				rect.top <= event.clientY &&
				event.clientY <= rect.top + rect.height &&
				rect.left <= event.clientX &&
				event.clientX <= rect.left + rect.width;
			if (!isInside) {
				dialog.close();
			}
		});
	});
}

/* ── Mobile sidebar ── */
const mqMobile = window.matchMedia("(max-width: 768px)");
const sidebarEl = document.getElementById("sidebar");
const toggleBtn = document.getElementById("sidebar-toggle");
const backdrop = document.getElementById("sidebar-backdrop");

function closeSidebar() {
	sidebarEl?.classList.remove("sidebar-open");
	backdrop?.classList.remove("active");
	toggleBtn?.setAttribute("aria-expanded", "false");
}

toggleBtn?.addEventListener("click", () => {
	const opening = sidebarEl?.classList.toggle("sidebar-open");
	backdrop?.classList.toggle("active", !!opening);
	toggleBtn?.setAttribute("aria-expanded", String(!!opening));
});

backdrop?.addEventListener("click", closeSidebar);

document.querySelectorAll(".nav a").forEach((link) => {
	link.addEventListener("click", () => {
		if (mqMobile.matches) closeSidebar();
	});
});
