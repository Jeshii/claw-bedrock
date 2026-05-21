loadDashboard();
loadAuth();
loadModels();
loadEncryptionStatus();
setTimeout(hideLoadingOverlay, 8000);
setTimeout(loadDashboard, 5000);
setInterval(loadAuth, 10000);

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
