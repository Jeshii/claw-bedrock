async function loadDashboard() {
	try {
		const ctrl = new AbortController();
		const timer = setTimeout(() => ctrl.abort(), 5000);
		const res = await fetch("/api/dashboard", { signal: ctrl.signal });
		clearTimeout(timer);
		const data = await res.json();
		const serverStatus = document.getElementById("server-status");
		let html = `<p><strong>Version:</strong> ${data.version}</p>`;
		html += `<p><strong>Configured Models:</strong> ${data.model_count}</p>`;
		if (Object.keys(data.providers).length > 0) {
			html += "<p><strong>Providers:</strong></p><ul>";
			for (const [provider, count] of Object.entries(data.providers)) {
				html += `<li>${provider}: ${count} model(s)</li>`;
			}
			html += "</ul>";
		}
		serverStatus.innerHTML = html;
	} catch (e) {
		const serverStatus = document.getElementById("server-status");
		if (serverStatus) serverStatus.innerHTML = "<p>Server is starting up…</p>";
	} finally {
		hideLoadingOverlay();
	}
}

function updateDashboardBanner(data) {
	const banner = document.getElementById("dashboard-auth-banner");
	const bannerBody = document.getElementById("banner-body");
	if (!data.auth_needed) {
		banner.style.display = "none";
		return;
	}
	banner.style.display = "";
	let html = "";
	if (data.auth_error) {
		html += `<div class="banner-error" style="color: #dc3545; font-weight: 600;">Auth failed: ${data.auth_error}</div>`;
		html +=
			'<div style="margin-top:8px;"><button type="button" class="submit-code-btn" onclick="showPage2(\'auth\');retryLogin(true);">Retry Login</button></div>';
	} else if (!data.auth_url) {
		html +=
			'<div class="banner-url">Bedrock models require AWS authentication &mdash; go to the Authentication page to start the login flow.</div>';
	}
	bannerBody.innerHTML = html;
}

async function loadAuth() {
	const res = await fetch("/api/auth/status");
	const data = await res.json();
	const authDiv = document.getElementById("auth-status");
	const existingInput = document.getElementById("aws-code-input");
	const preservedValue =
		existingInput && existingInput.value ? existingInput.value : "";
	let html = "";

	updateDashboardBanner(data);

	if (data.auth_needed) {
		html +=
			'<div class="auth-needed"><p><strong>&#9888; AWS Authentication</strong></p>';
		if (data.auth_error) {
			html += `<p style="color: #dc3545; margin-top: 8px;"><strong>Error:</strong> ${data.auth_error}</p>`;
		}
		html += '<div class="auth-steps">';

		html += '<div class="auth-step">';
		html += '<div class="auth-step-num">1</div>';
		html += '<div class="auth-step-body">';
		if (data.auth_url) {
			html += "<p>Open this URL in your browser:</p>";
			html += `<div class="auth-url-row"><a class="auth-url-link" href="${data.auth_url}" target="_blank">${data.auth_url}</a></div>`;
		} else {
			html += "<p>Waiting for AWS login URL&hellip;</p>";
		}
		html += "</div></div>";

		html += '<div class="auth-step">';
		html += '<div class="auth-step-num">2</div>';
		html += '<div class="auth-step-body">';
		if (data.awaiting_code) {
			html +=
				"<p>After approving in your browser, paste the authorization code shown:</p>";
			html += '<div class="auth-code-input-row">';
			html +=
				'<input type="text" id="aws-code-input" class="auth-code-input" placeholder="Paste authorization code..." onkeydown="if(event.key===\'Enter\'){event.preventDefault();submitAWSCode();}" />';
			html +=
				'<button type="button" class="submit-code-btn" onclick="submitAWSCode()">Submit Code</button>';
			html += "</div>";
			html +=
				'<p class="auth-note">The code is displayed on the AWS authorization page after you approve access.</p>';
		} else if (data.auth_url) {
			html +=
				"<p>Awaiting authorization&hellip; (the code input will appear here once the login process is ready)</p>";
		} else {
			html += "<p>Awaiting authorization&hellip; </p>";
		}
		html += "</div></div>";

		html += '<div class="auth-step">';
		html += '<div class="auth-step-num">3</div>';
		html +=
			'<div class="auth-step-body"><p>This page will refresh automatically once authentication completes.</p></div>';
		html += "</div>";

		if (data.auth_error || !data.auth_url) {
			const isRetry = !!data.auth_error;
			const btnLabel = isRetry ? "Retry Login" : "Start Login Flow";
			html += `<div style="margin-top: 12px;"><button type="button" class="submit-code-btn" onclick="retryLogin(${isRetry})">${btnLabel}</button></div>`;
		}

		html += "</div></div>";
	} else {
		html += "<p>" + CHECK_SVG + " Bedrock model access configured.</p>";
	}

	if (data.openrouter.configured) {
		html += "<p>" + CHECK_SVG + " OpenRouter API key configured.</p>";
	} else {
		html +=
			"<p>" +
			X_CIRCLE_SVG +
			" OpenRouter not configured (set OPENROUTER_API_KEY).</p>";
	}
	if (data.ollama.configured) {
		html += `<p>` + CHECK_SVG + ` Ollama configured (${data.ollama.host}).</p>`;
	} else {
		html +=
			"<p>" +
			X_CIRCLE_SVG +
			" Ollama not configured (set OLLAMA_API_BASE).</p>";
	}
	authDiv.innerHTML = html;
	if (preservedValue && data.awaiting_code) {
		const input = document.getElementById("aws-code-input");
		if (input) input.value = preservedValue;
	}
}

function copyAuthCode(code) {
	navigator.clipboard
		.writeText(code)
		.then(() => {
			showToast("Code copied to clipboard");
		})
		.catch(() => {
			showToast("Failed to copy code", "error");
		});
}

async function submitAWSCode() {
	const input = document.getElementById("aws-code-input");
	const code = input.value.trim();
	if (!code) {
		showToast("Please enter a code", "error");
		return;
	}
	try {
		const res = await fetch("/api/auth/submit-code", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ code: code }),
		});
		if (res.ok) {
			showToast("Code submitted successfully");
			input.value = "";
			setTimeout(loadAuth, 1000);
		} else {
			const data = await res.json();
			showToast(`Error: ${data.detail || "Failed to submit code"}`, "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}

async function logout() {
	try {
		await fetch("/api/logout", { method: "POST" });
	} catch (e) {
		// ignore errors — just redirect
	}
	window.location.href = "/login";
}

async function retryLogin(isRetry) {
	try {
		const res = await fetch("/api/auth/retry", { method: "POST" });
		if (res.ok) {
			showToast(isRetry ? "Login retry initiated" : "Starting login flow");
			setTimeout(loadAuth, 1000);
		} else {
			showToast("Failed to start login", "error");
		}
	} catch (e) {
		showToast(`Error: ${e.message}`, "error");
	}
}
