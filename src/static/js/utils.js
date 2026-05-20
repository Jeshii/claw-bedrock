const _CHECK_SVG =
	'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><polyline points="22 4 12 14.01 9 11.01" /></svg>';
const _X_CIRCLE_SVG =
	'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle" aria-hidden="true"><circle cx="12" cy="12" r="10" /><line x1="15" y1="9" x2="9" y2="15" /><line x1="9" y1="9" x2="15" y2="15" /></svg>';
const _RELOAD_SVG =
	'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle" aria-hidden="true"><path d="M1 4v6h6M23 20v-6h-6" /><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15" /></svg>';
const _CHEVRON_RIGHT_SVG =
	'<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle" aria-hidden="true"><path d="M9 18l6-6-6-6" /></svg>';
const _CHEVRON_DOWN_SVG =
	'<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>';
const _CLOSE_SVG =
	'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>';
const _FREE_SVG =
	'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle" aria-hidden="true"><path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2z" /></svg>';
const _WARNING_SVG =
	'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" /></svg>';
const _MOON_SVG =
	'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;margin-right:4px" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" /></svg>';
const _SUN_SVG =
	'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;margin-right:4px" aria-hidden="true"><circle cx="12" cy="12" r="5" /><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" /></svg>';

const autoRefreshIntervals = { logs: null, debug: null, container: null };

function showToast(
	message,
	type = "success",
	duration = 3000,
	showSpinner = false,
) {
	const container = document.getElementById("toast-container");
	const toast = document.createElement("div");
	toast.className = `toast toast-${type}`;
	if (showSpinner) {
		toast.innerHTML = `<span class="toast-spinner"></span>${message}`;
	} else {
		toast.textContent = message;
	}
	container.appendChild(toast);
	if (duration > 0) {
		setTimeout(() => {
			toast.style.opacity = "0";
			toast.style.transition = "opacity 0.3s";
			setTimeout(() => toast.remove(), 300);
		}, duration);
	}
	return toast;
}

function updateToast(toast, message, type = null, removeSpinner = true) {
	if (type) toast.className = `toast toast-${type}`;
	if (removeSpinner) {
		toast.innerHTML = message;
	} else {
		toast.innerHTML = `<span class="toast-spinner"></span>${message}`;
	}
}

function _showReloadToast(toast, reloaded, pid) {
	if (reloaded) {
		updateToast(toast, `LiteLLM reloaded (PID ${pid || "unknown"})`, "info");
	} else {
		updateToast(
			toast,
			"LiteLLM reload failed - try restarting the container",
			"warning",
		);
	}
	setTimeout(() => {
		toast.style.opacity = "0";
		toast.style.transition = "opacity 0.3s";
		setTimeout(() => toast.remove(), 300);
	}, 3000);
}

function _base64urlEncode(str) {
	return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function _base64urlDecode(str) {
	str += "=".repeat((4 - (str.length % 4)) % 4);
	return atob(str.replace(/-/g, "+").replace(/_/g, "/"));
}

function _ansiToHtml(text) {
	const colorMap = {
		30: "black",
		31: "red",
		32: "green",
		33: "yellow",
		34: "blue",
		35: "magenta",
		36: "cyan",
		37: "white",
		90: "#808080",
		91: "#ff5555",
		92: "#55ff55",
		93: "#ffff55",
		94: "#5555ff",
		95: "#ff55ff",
		96: "#55ffff",
		97: "#ffffff",
	};
	let result = "";
	let openSpan = false;
	const ESC = String.fromCharCode(27);
	const regex = new RegExp(`${ESC}\\[([0-9;]*)m`, "g");
	let lastIndex = 0;
	let match;
	match = regex.exec(text);
	while (match !== null) {
		result += text
			.slice(lastIndex, match.index)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;");
		const codes = match[1].split(";");
		for (const code of codes) {
			if (code === "0" || code === "") {
				if (openSpan) {
					result += "</span>";
					openSpan = false;
				}
			} else if (colorMap[code]) {
				if (openSpan) {
					result += "</span>";
				}
				result += `<span style="color:${colorMap[code]}">`;
				openSpan = true;
			}
		}
		lastIndex = regex.lastIndex;
		match = regex.exec(text);
	}
	result += text
		.slice(lastIndex)
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;");
	if (openSpan) result += "</span>";
	return result.replace(/\n/g, "<br>");
}

function _simplifyErrorMessage(text) {
	const lines = text.split("\n");
	const simplified = [];
	let inTraceback = false;
	let lastError = null;

	for (const line of lines) {
		if (line.includes("Traceback (most recent call last):")) {
			inTraceback = true;
			continue;
		}
		if (inTraceback) {
			if (line.match(/^\w+Error:|^\w+Exception:/)) {
				lastError = line.trim();
				if (line.includes("RateLimitError") || line.includes("rate_limit")) {
					const modelMatch = line.match(/Model Group=([^\s]+)/);
					const model = modelMatch
						? modelMatch[1].replace("openrouter/", "")
						: "unknown";
					simplified.push(
						`⚠️ Rate limit exceeded for model ${model}. Try again later or switch models.`,
					);
					lastError = null;
				} else if (
					line.includes("AuthenticationError") ||
					line.includes("auth")
				) {
					simplified.push("⚠️ Authentication failed. Check your API keys.");
					lastError = null;
				} else if (
					line.includes("NotFoundError") ||
					line.includes("not found")
				) {
					simplified.push(
						"⚠️ Model not found. It may have been removed or renamed.",
					);
					lastError = null;
				}
			}
			if (line.trim().startsWith("File ") || line.trim().startsWith("  ")) {
				continue;
			}
			if (
				line.trim() &&
				!line.trim().startsWith("File ") &&
				!line.includes("await ") &&
				!line.includes("async ")
			) {
				inTraceback = false;
			}
			continue;
		}
		simplified.push(line);
	}

	if (lastError) {
		simplified.push(`⚠️ Error: ${lastError.substring(0, 100)}`);
	}

	return simplified.join("\n");
}

function _formatContextLength(ctx) {
	if (!ctx) return "";
	if (ctx >= 1048576) return `${ctx / 1048576}M ctx`;
	if (ctx >= 1024) return `${ctx / 1024}k ctx`;
	return `${ctx} ctx`;
}

function _toggleAutoRefresh(type) {
	const toggleId =
		type === "logs"
			? "auto-refresh-toggle"
			: type === "debug"
				? "auto-refresh-debug-toggle"
				: "auto-refresh-container-toggle";
	const intervalId =
		type === "logs"
			? "auto-refresh-interval"
			: type === "debug"
				? "auto-refresh-debug-interval"
				: "auto-refresh-container-interval";
	const toggleBtn = document.getElementById(toggleId);
	const intervalSelect = document.getElementById(intervalId);
	const storageKey = `autoRefresh_${type}`;

	if (autoRefreshIntervals[type]) {
		clearInterval(autoRefreshIntervals[type]);
		autoRefreshIntervals[type] = null;
		toggleBtn.style.background = "";
		toggleBtn.style.color = "";
		localStorage.setItem(storageKey, "off");
		showToast(`Auto-refresh ${type} disabled`, "info");
	} else {
		const interval = parseInt(intervalSelect.value, 10);
		const loadFunc = type === "logs" ? loadLogs : loadDebugLogs;
		autoRefreshIntervals[type] = setInterval(loadFunc, interval);
		toggleBtn.style.background = "#28a745";
		toggleBtn.style.color = "white";
		localStorage.setItem(storageKey, "on");
		localStorage.setItem(`${storageKey}_interval`, intervalSelect.value);
		showToast(`Auto-refresh ${type} enabled (${interval / 1000}s)`, "info");
	}
}

function _restoreAutoRefresh() {
	["logs", "debug", "container"].forEach((type) => {
		const storageKey = `autoRefresh_${type}`;
		const toggleId =
			type === "logs"
				? "auto-refresh-toggle"
				: type === "debug"
					? "auto-refresh-debug-toggle"
					: "auto-refresh-container-toggle";
		const intervalId =
			type === "logs"
				? "auto-refresh-interval"
				: type === "debug"
					? "auto-refresh-debug-interval"
					: "auto-refresh-container-interval";
		const toggleBtn = document.getElementById(toggleId);
		const intervalSelect = document.getElementById(intervalId);

		if (localStorage.getItem(storageKey) === "on") {
			const savedInterval = localStorage.getItem(`${storageKey}_interval`);
			if (savedInterval) intervalSelect.value = savedInterval;
			const interval = parseInt(intervalSelect.value, 10);
			const loadFunc =
				type === "logs"
					? loadLogs
					: type === "debug"
						? loadDebugLogs
						: loadContainerLogs;
			autoRefreshIntervals[type] = setInterval(loadFunc, interval);
			toggleBtn.style.background = "#28a745";
			toggleBtn.style.color = "white";
		}
	});
}

function _clearAutoRefresh() {
	["logs", "debug", "container"].forEach((type) => {
		if (autoRefreshIntervals[type]) {
			clearInterval(autoRefreshIntervals[type]);
			autoRefreshIntervals[type] = null;
		}
	});
}

function _toggleLog(id, toggleId) {
	const pre = document.getElementById(id);
	const toggle = document.getElementById(toggleId);
	if (pre.style.display === "none") {
		pre.style.display = "block";
		toggle.textContent = "Hide Logs";
	} else {
		pre.style.display = "none";
		toggle.textContent = "Show Logs";
	}
}

const _TAG_PALETTE = [
	"#4CAF50",
	"#2196F3",
	"#FF9800",
	"#9C27B0",
	"#F44336",
	"#00BCD4",
	"#8BC34A",
	"#795548",
	"#607D8B",
	"#E91E63",
	"#3F51B5",
	"#009688",
];
