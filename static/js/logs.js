async function loadLogs() {
	const lines = document.getElementById("log-lines").value;
	const res = await fetch(`/api/logs?lines=${lines}`);
	const data = await res.json();
	const logsDiv = document.getElementById("logs-output");
	const simplified = simplifyErrorMessage(data.logs);
	logsDiv.innerHTML = ansiToHtml(simplified);
	logsDiv.scrollTop = logsDiv.scrollHeight;
}

async function copyLogs() {
	const logsText = document.getElementById("logs-output").innerText;
	if (!logsText) return showToast("No logs to copy", "warning");
	try {
		await navigator.clipboard.writeText(logsText);
		showToast("Logs copied to clipboard");
	} catch (err) {
		showToast("Failed to copy logs", "error");
	}
}

async function loadDebugLogs() {
	const lines = document.getElementById("debug-log-lines").value;
	const res = await fetch(`/api/logs/debug?lines=${lines}`);
	const data = await res.json();
	const logsDiv = document.getElementById("debug-logs-output");
	const simplified = simplifyErrorMessage(data.logs);
	logsDiv.innerHTML = ansiToHtml(simplified);
	logsDiv.scrollTop = logsDiv.scrollHeight;
}

async function copyDebugLogs() {
	const logsText = document.getElementById("debug-logs-output").innerText;
	if (!logsText) return showToast("No debug logs to copy", "warning");
	try {
		await navigator.clipboard.writeText(logsText);
		showToast("Debug logs copied to clipboard");
	} catch (err) {
		showToast("Failed to copy debug logs", "error");
	}
}

async function loadContainerLogs() {
	const lines = document.getElementById("container-log-lines").value;
	const res = await fetch(`/api/logs/container?lines=${lines}`);
	const data = await res.json();
	const logsDiv = document.getElementById("container-logs-output");
	logsDiv.innerHTML = ansiToHtml(data.logs);
	logsDiv.scrollTop = logsDiv.scrollHeight;
}

async function copyContainerLogs() {
	const logsText = document.getElementById("container-logs-output").innerText;
	if (!logsText) return showToast("No container logs to copy", "warning");
	try {
		await navigator.clipboard.writeText(logsText);
		showToast("Container logs copied to clipboard");
	} catch (err) {
		showToast("Failed to copy container logs", "error");
	}
}
