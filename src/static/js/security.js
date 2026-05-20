async function loadKeyStatus() {
	const res = await fetch("/api/security/key");
	const data = await res.json();
	const statusEl = document.getElementById("key-status");
	const revokeBtn = document.getElementById("btn-revoke-key");

	if (data.enabled) {
		statusEl.innerHTML =
			'<span style="display:inline-block;padding:4px 10px;border-radius:4px;background:#d4edda;color:#155724;font-weight:600;margin-bottom:4px;">Active</span><br><code style="font-size:13px;word-break:break-all;">' +
			data.masked_key +
			"</code>";
		revokeBtn.style.display = "";
	} else {
		statusEl.innerHTML =
			'<span style="display:inline-block;padding:4px 10px;border-radius:4px;background:#e2e3e5;color:#383d41;font-weight:600;">Not configured</span>';
		revokeBtn.style.display = "none";
	}
}

async function _generateKey() {
	if (!confirm("Generate a new key? Any existing key will be invalidated."))
		return;
	const res = await fetch("/api/security/key/generate", { method: "POST" });
	const data = await res.json();
	document.getElementById("revealed-key").textContent = data.key;
	document.getElementById("key-reveal-modal").showModal();
	loadKeyStatus();
}

async function _revokeKey() {
	if (
		!confirm("Revoke the current key? The proxy will become unauthenticated.")
	)
		return;
	await fetch("/api/security/key", { method: "DELETE" });
	loadKeyStatus();
}
