let playgroundAbortController = null;

async function loadPlayground() {
	const modelSelect = document.getElementById("playground-model");
	const placeholder = document.querySelector(".playground-placeholder");
	if (!modelSelect) return;
	const idx = modelSelect.selectedIndex;
	const prevVal = modelSelect.options[idx]?.value || "";
	modelSelect.disabled = true;

	try {
		const resp = await fetch("/api/chat/models");
		if (!resp.ok) {
			const errText =
				resp.status === 502 ? "LiteLLM not reachable" : `Error ${resp.status}`;
			if (placeholder)
				placeholder.innerHTML = `<p>${errText}. Make sure LiteLLM is running. <button type="button" onclick="loadPlayground()">Retry</button></p>`;
			return;
		}
		const data = await resp.json();
		const models = data.models || [];
		modelSelect.disabled = false;
		modelSelect.innerHTML = '<option value="">— Select a model —</option>';
		if (models.length === 0) {
			modelSelect.innerHTML =
				'<option value="">— No models configured —</option>';
			if (placeholder)
				placeholder.innerHTML = `<p>No models found. Add models on the Models page first. <button type="button" onclick="loadPlayground()">Retry</button></p>`;
			return;
		}
		for (const id of models) {
			const opt = document.createElement("option");
			opt.value = id;
			opt.textContent = id;
			modelSelect.appendChild(opt);
		}
		if (prevVal && models.includes(prevVal)) {
			modelSelect.value = prevVal;
		}
	} catch (e) {
		if (placeholder)
			placeholder.innerHTML = `<p>Could not load models: ${e.message} <button type="button" onclick="loadPlayground()">Retry</button></p>`;
	}
}

function toggleSystemPrompt() {
	const section = document.getElementById("playground-system-section");
	const btn = document.getElementById("sysprompt-toggle");
	if (!section || !btn) return;
	const hidden = section.classList.toggle("hidden");
	btn.classList.toggle("active", !hidden);
}

function clearChat() {
	const container = document.getElementById("playground-messages");
	if (!container) return;
	container.innerHTML = `
		<div class="playground-placeholder">
			<p>Select a model and send a message to start.</p>
		</div>
	`;
}

function handlePlaygroundKeydown(e) {
	if (e.key === "Enter" && !e.shiftKey) {
		e.preventDefault();
		sendPlaygroundMessage();
	}
}

function addMessageBubble(role, content) {
	const container = document.getElementById("playground-messages");
	if (!container) return;

	const placeholder = container.querySelector(".playground-placeholder");
	if (placeholder) placeholder.remove();

	const bubble = document.createElement("div");
	bubble.className = `message-bubble message-${role}`;
	const label = document.createElement("div");
	label.className = "message-label";
	label.textContent = role === "user" ? "You" : "Assistant";
	bubble.appendChild(label);

	const text = document.createElement("div");
	text.className = "message-text";
	text.textContent = content;
	bubble.appendChild(text);

	container.appendChild(bubble);
	container.scrollTop = container.scrollHeight;
	return text;
}

function showStreamingIndicator() {
	const container = document.getElementById("playground-messages");
	if (!container) return;
	const placeholder = container.querySelector(".playground-placeholder");
	if (placeholder) placeholder.remove();

	let indicator = container.querySelector(".streaming-indicator");
	if (!indicator) {
		indicator = document.createElement("div");
		indicator.className = "streaming-indicator";
		indicator.innerHTML = "<span></span><span></span><span></span>";
		container.appendChild(indicator);
		container.scrollTop = container.scrollHeight;
	}
}

function hideStreamingIndicator() {
	const el = document.querySelector(".streaming-indicator");
	if (el) el.remove();
}

async function sendPlaygroundMessage() {
	const modelSelect = document.getElementById("playground-model");
	const input = document.getElementById("playground-input");
	const sendBtn = document.getElementById("playground-send-btn");
	const stopBtn = document.getElementById("playground-stop-btn");

	const model = modelSelect?.value;
	const text = input?.value.trim();

	if (!model) {
		showToast("Please select a model first", "warning");
		return;
	}
	if (!text) {
		showToast("Please enter a message", "warning");
		return;
	}

	input.value = "";
	addMessageBubble("user", text);
	showStreamingIndicator();
	sendBtn.classList.add("hidden");
	stopBtn.classList.remove("hidden");

	const messages = [];
	const sysPrompt = document
		.getElementById("playground-system-prompt")
		?.value.trim();
	if (sysPrompt) {
		messages.push({ role: "system", content: sysPrompt });
	}
	messages.push({ role: "user", content: text });

	playgroundAbortController = new AbortController();

	try {
		const resp = await fetch("/api/chat/completions", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ model, messages }),
			signal: playgroundAbortController.signal,
		});

		if (!resp.ok) {
			let errMsg = `Request failed (${resp.status})`;
			try {
				const err = await resp.json();
				errMsg = err.detail || errMsg;
			} catch {}
			hideStreamingIndicator();
			addMessageBubble("assistant", `Error: ${errMsg}`);
			return;
		}

		const reader = resp.body.getReader();
		const decoder = new TextDecoder();
		let buffer = "";
		let fullContent = "";
		let reasoning = "";
		let messageTextEl = null;
		let reasoningDetailsEl = null;

		while (true) {
			const { done, value } = await reader.read();
			if (done) break;

			buffer += decoder.decode(value, { stream: true });
			const lines = buffer.split("\n");
			buffer = lines.pop() || "";

			for (const line of lines) {
				if (!line.startsWith("data: ")) continue;
				const payload = line.slice(6).trim();
				if (payload === "[DONE]") continue;
				try {
					const parsed = JSON.parse(payload);
					const choices = parsed.choices;
					if (!choices || choices.length === 0) continue;
					const delta = choices[0].delta;
					if (!delta) continue;
					const content = delta.content ?? "";
					const reasoningDelta = delta.reasoning_content ?? "";

					if (!content && !reasoningDelta) continue;

					fullContent += content;
					reasoning += reasoningDelta;

					if (!messageTextEl) {
						hideStreamingIndicator();
						messageTextEl = addMessageBubble("assistant", "");
					}

					if (reasoningDelta && !reasoningDetailsEl) {
						reasoningDetailsEl = document.createElement("details");
						reasoningDetailsEl.className = "reasoning-details";
						const summary = document.createElement("summary");
						summary.textContent = "Show reasoning";
						const pre = document.createElement("pre");
						pre.className = "reasoning-content";
						reasoningDetailsEl.append(summary, pre);
						messageTextEl.parentNode.insertBefore(
							reasoningDetailsEl,
							messageTextEl,
						);
					}

					if (reasoningDetailsEl) {
						reasoningDetailsEl.querySelector(".reasoning-content").textContent =
							reasoning;
					}
					messageTextEl.textContent = fullContent;
					const container = document.getElementById("playground-messages");
					if (container) container.scrollTop = container.scrollHeight;
				} catch {}
			}
		}

		if (!fullContent && !reasoning && !messageTextEl) {
			hideStreamingIndicator();
			addMessageBubble("assistant", "(empty response)");
		}
	} catch (e) {
		if (e.name === "AbortError") {
			hideStreamingIndicator();
			addMessageBubble("assistant", "(stopped)");
		} else {
			hideStreamingIndicator();
			addMessageBubble("assistant", `Error: ${e.message}`);
		}
	} finally {
		playgroundAbortController = null;
		sendBtn.classList.remove("hidden");
		stopBtn.classList.add("hidden");
	}
}

function stopPlaygroundStream() {
	if (playgroundAbortController) {
		playgroundAbortController.abort();
		playgroundAbortController = null;
	}
}
