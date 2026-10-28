let playgroundAbortController = null;
let messages = [];
let activeModel = null;
let isStreaming = false;

let currentConversationId = null;
let conversations = [];
let conversationSearchQuery = "";
let conversationSearchTimer = null;

function escapeHtml(str) {
	return String(str ?? "")
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#39;");
}

function escapeAttr(str) {
	return String(str ?? "")
		.replace(/&/g, "&amp;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#39;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;");
}

function formatConversationDate(value) {
	if (!value) return "";
	try {
		const d = new Date(value);
		if (Number.isNaN(d.getTime())) return value;
		return d.toLocaleString(undefined, {
			month: "short",
			day: "numeric",
			hour: "2-digit",
			minute: "2-digit",
		});
	} catch {
		return value;
	}
}

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
		} else if (activeModel && models.includes(activeModel)) {
			modelSelect.value = activeModel;
		}

		modelSelect.removeEventListener("change", handleModelChange);
		modelSelect.addEventListener("change", handleModelChange);
	} catch (e) {
		if (placeholder)
			placeholder.innerHTML = `<p>Could not load models: ${e.message} <button type="button" onclick="loadPlayground()">Retry</button></p>`;
	}
}

function handleModelChange() {
	const select = document.getElementById("playground-model");
	if (!select) return;
	const newModel = select.value;

	if (
		messages.length > 0 &&
		!confirm("Switching model will reset the conversation. Continue?")
	) {
		select.value = activeModel || "";
		return;
	}
	resetConversation();
	activeModel = newModel || null;
}

function toggleSystemPrompt() {
	const section = document.getElementById("playground-system-section");
	const btn = document.getElementById("sysprompt-toggle");
	if (!section || !btn) return;
	const hidden = section.classList.toggle("hidden");
	btn.classList.toggle("active", !hidden);
}

function clearChatDisplay() {
	const container = document.getElementById("playground-messages");
	if (!container) return;
	container.innerHTML = `
		<div class="playground-placeholder">
			<p>Select a model and send a message to start.</p>
		</div>
	`;
}

function renderMessages() {
	const container = document.getElementById("playground-messages");
	if (!container) return;
	container.innerHTML = "";
	if (messages.length === 0) {
		clearChatDisplay();
		return;
	}
	for (const m of messages) {
		if (m.role === "system") continue;
		addMessageBubble(m.role, m.content);
	}
}

function newConversation() {
	if (isStreaming) {
		stopPlaygroundStream();
	}
	currentConversationId = null;
	messages = [];
	const sysPrompt = document
		.getElementById("playground-system-prompt")
		?.value.trim();
	if (sysPrompt) {
		messages.push({ role: "system", content: sysPrompt });
	}
	activeModel = document.getElementById("playground-model")?.value || null;
	isStreaming = false;
	clearChatDisplay();
	const sendBtn = document.getElementById("playground-send-btn");
	const stopBtn = document.getElementById("playground-stop-btn");
	const input = document.getElementById("playground-input");
	if (sendBtn) sendBtn.classList.remove("hidden");
	if (stopBtn) stopBtn.classList.add("hidden");
	if (input) input.disabled = false;
	playgroundAbortController = null;
	if (window.PlaygroundAudio) window.PlaygroundAudio.disarm();
	renderConversationList();
}

function resetConversation() {
	newConversation();
}

function appendUserMessage(content) {
	messages.push({ role: "user", content });
	addMessageBubble("user", content);
}

function commitAssistantMessage(content) {
	messages.push({ role: "assistant", content });
}

function getRequestMessages() {
	const sysPrompt = document
		.getElementById("playground-system-prompt")
		?.value.trim();
	const base = messages.map((m) => ({ ...m }));
	if (sysPrompt && !base.some((m) => m.role === "system")) {
		base.unshift({ role: "system", content: sysPrompt });
	}
	return base;
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

function hideStreamingIndicator() {
	const el = document.querySelector(".streaming-indicator");
	if (el) el.remove();
}

// ---------- Conversation sidebar ----------

function handleConversationSearchInput() {
	const input = document.getElementById("conversation-search");
	conversationSearchQuery = input?.value || "";
	syncConversationSearchClear();
	if (conversationSearchTimer) clearTimeout(conversationSearchTimer);
	conversationSearchTimer = setTimeout(() => {
		loadConversations(conversationSearchQuery);
	}, 250);
}

function clearConversationSearch() {
	const input = document.getElementById("conversation-search");
	if (input) {
		input.value = "";
		input.focus();
	}
	conversationSearchQuery = "";
	syncConversationSearchClear();
	if (conversationSearchTimer) clearTimeout(conversationSearchTimer);
	loadConversations("");
}

function syncConversationSearchClear() {
	const input = document.getElementById("conversation-search");
	const btn = document.getElementById("conversation-search-clear");
	if (!btn) return;
	const hasText = !!input?.value?.trim();
	btn.classList.toggle("hidden", !hasText);
}

async function loadConversations(search) {
	const q = search === undefined ? conversationSearchQuery : search;
	conversationSearchQuery = q;
	const list = document.getElementById("conversation-list");
	if (list) list.innerHTML = '<p class="muted">Loading…</p>';
	try {
		const resp = await fetch(
			`/api/conversations?search=${encodeURIComponent(q)}&per_page=100`,
		);
		if (!resp.ok) throw new Error(`Error ${resp.status}`);
		const data = await resp.json();
		conversations = data.conversations || [];
		renderConversationList();
	} catch (e) {
		if (list)
			list.innerHTML = `
			<div class="conversation-list-error">
				<div class="conversation-list-empty-title">Could not load conversations</div>
				<div class="conversation-list-empty-hint">${escapeHtml(e.message)}</div>
			</div>`;
	}
}

function renderConversationList() {
	const list = document.getElementById("conversation-list");
	if (!list) return;
	list.onclick = handleConversationListClick;
	if (!conversations.length) {
		const emptyMsg = conversationSearchQuery.trim()
			? "No conversations match your search."
			: "No conversations yet.";
		const hint = conversationSearchQuery.trim()
			? "Try a different search term."
			: "Start one with “+ New”.";
		list.innerHTML = `
			<div class="conversation-list-empty">
				<div class="conversation-list-empty-title">${escapeHtml(emptyMsg)}</div>
				<div class="conversation-list-empty-hint">${escapeHtml(hint)}</div>
			</div>`;
		return;
	}
	list.innerHTML = conversations
		.map((c) => {
			const active = c.id === currentConversationId ? "active" : "";
			return `
			<div class="conversation-item ${active}" data-id="${escapeAttr(c.id)}">
				<div class="conversation-title">${escapeHtml(c.title || "Untitled")}</div>
				<div class="conversation-meta">${escapeHtml(c.model || "")} · ${escapeHtml(formatConversationDate(c.updated_at))}</div>
				<div class="conversation-actions">
					<button type="button" data-action="rename" title="Rename">✎</button>
					<button type="button" data-action="delete" title="Delete">✕</button>
				</div>
			</div>`;
		})
		.join("");
}

function handleConversationListClick(e) {
	const item = e.target.closest(".conversation-item");
	if (!item) return;
	const id = item.dataset.id;
	if (e.target.closest('[data-action="rename"]')) {
		startRenameConversation(id, item);
		return;
	}
	if (e.target.closest('[data-action="delete"]')) {
		deleteConversation(id);
		return;
	}
	if (!e.target.closest("input")) {
		selectConversation(id);
	}
}

async function selectConversation(id) {
	if (isStreaming) return;
	try {
		const resp = await fetch(`/api/conversations/${id}`);
		if (!resp.ok) throw new Error(`Error ${resp.status}`);
		const data = await resp.json();
		currentConversationId = id;
		activeModel = data.model || null;
		messages = (data.messages || []).map((m) => ({
			role: m.role,
			content: m.content,
		}));
		const select = document.getElementById("playground-model");
		if (select && activeModel) select.value = activeModel;
		renderMessages();
		renderConversationList();
	} catch (e) {
		showToast(`Could not open conversation: ${e.message}`, "error");
	}
}

function startRenameConversation(id, item) {
	const conv = conversations.find((c) => c.id === id);
	const titleEl = item.querySelector(".conversation-title");
	if (!titleEl) return;
	const input = document.createElement("input");
	input.type = "text";
	input.value = conv?.title || "";
	input.className = "conversation-rename-input";
	titleEl.replaceWith(input);
	input.focus();
	input.select();

	let finished = false;
	const finish = async (save) => {
		if (finished) return;
		finished = true;
		const newTitle = input.value.trim();
		if (save && newTitle && newTitle !== (conv?.title || "")) {
			try {
				await fetch(`/api/conversations/${id}`, {
					method: "PUT",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ title: newTitle }),
				});
				if (conv) conv.title = newTitle;
			} catch (e) {
				showToast(`Rename failed: ${e.message}`, "error");
			}
		}
		loadConversations(conversationSearchQuery);
	};

	input.addEventListener("keydown", (e) => {
		if (e.key === "Enter") {
			e.preventDefault();
			finish(true);
		} else if (e.key === "Escape") {
			finish(false);
		}
	});
	input.addEventListener("blur", () => finish(true));
}

async function deleteConversation(id) {
	if (!confirm("Delete this conversation? This cannot be undone.")) return;
	try {
		const resp = await fetch(`/api/conversations/${id}`, { method: "DELETE" });
		if (!resp.ok) throw new Error(`Error ${resp.status}`);
		if (currentConversationId === id) {
			newConversation();
		}
		loadConversations(conversationSearchQuery);
	} catch (e) {
		showToast(`Delete failed: ${e.message}`, "error");
	}
}

// ---------- Send flow ----------

async function sendPlaygroundMessage() {
	const modelSelect = document.getElementById("playground-model");
	const input = document.getElementById("playground-input");
	const sendBtn = document.getElementById("playground-send-btn");
	const stopBtn = document.getElementById("playground-stop-btn");

	const model = modelSelect?.value;
	const text = input?.value.trim();

	if (isStreaming) return;

	if (!model) {
		showToast("Please select a model first", "warning");
		return;
	}
	if (!text) {
		showToast("Please enter a message", "warning");
		return;
	}

	activeModel = model;
	isStreaming = true;
	appendUserMessage(text);
	input.value = "";
	sendBtn.classList.add("hidden");
	stopBtn.classList.remove("hidden");

	playgroundAbortController = new AbortController();

	if (window.PlaygroundAudio) window.PlaygroundAudio.onSend();

	// Create the conversation lazily on the first send.
	if (!currentConversationId) {
		try {
			const titleSource = text.replace(/\s+/g, " ").trim();
			const title =
				titleSource.length > 60
					? `${titleSource.slice(0, 57)}…`
					: titleSource || "New conversation";
			const resp = await fetch("/api/conversations", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ title, model }),
			});
			if (!resp.ok) {
				let errMsg = `Request failed (${resp.status})`;
				try {
					const err = await resp.json();
					errMsg = err.detail || errMsg;
				} catch {}
				throw new Error(errMsg);
			}
			const conv = await resp.json();
			currentConversationId = conv.id;
			renderConversationList();
		} catch (e) {
			hideStreamingIndicator();
			addMessageBubble("assistant", `Error: ${e.message}`);
			isStreaming = false;
			sendBtn.classList.remove("hidden");
			stopBtn.classList.add("hidden");
			playgroundAbortController = null;
			if (window.PlaygroundAudio) window.PlaygroundAudio.onStreamEnd();
			return;
		}
	}

	try {
		const resp = await fetch(
			`/api/conversations/${currentConversationId}/messages`,
			{
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ model, messages: getRequestMessages() }),
				signal: playgroundAbortController.signal,
			},
		);

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
		let sawDone = false;

		while (true) {
			const { done, value } = await reader.read();
			if (done) break;

			buffer += decoder.decode(value, { stream: true });
			const lines = buffer.split("\n");
			buffer = lines.pop() || "";

			for (const line of lines) {
				if (!line.startsWith("data: ")) continue;
				const payload = line.slice(6).trim();
				if (payload === "[DONE]") {
					sawDone = true;
					continue;
				}
				let parsed;
				try {
					parsed = JSON.parse(payload);
				} catch {
					if (!sawDone) throw new Error("Stream parse failure");
					continue;
				}
				const choices = parsed.choices;
				if (!choices || choices.length === 0) continue;
				const delta = choices[0].delta;
				if (!delta) continue;
				const content = delta.content ?? "";
				const reasoningDelta = delta.reasoning_content ?? "";

				// Render streamed tool calls inline, if any.
				if (delta.tool_calls?.length) {
					if (!messageTextEl) {
						hideStreamingIndicator();
						messageTextEl = addMessageBubble("assistant", "");
					}
					const bubble = messageTextEl.parentNode;
					for (const tc of delta.tool_calls) {
						const name = tc.function?.name || tc.function_name || "tool";
						const args = tc.function?.arguments || "";
						let div = bubble.querySelector(".tool-call:last-of-type");
						if (!div || div.dataset.done === "1") {
							div = document.createElement("div");
							div.className = "tool-call";
							bubble.appendChild(div);
						}
						div.textContent = `🔧 ${name}(${args})`;
						div.dataset.done = "0";
					}
				}

				if (!content && !reasoningDelta && !delta.tool_calls?.length) continue;

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
					const reasoningContentEl = document.createElement("div");
					reasoningContentEl.className = "reasoning-content";
					reasoningDetailsEl.append(summary, reasoningContentEl);
					reasoningDetailsEl.open = true;
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
				if (window.PlaygroundAudio) window.PlaygroundAudio.onDelta(fullContent);
				const container = document.getElementById("playground-messages");
				if (container) container.scrollTop = container.scrollHeight;
			}
		}

		if (messageTextEl && fullContent && window.MarkdownRenderer) {
			MarkdownRenderer.finalizeStreamingMessage(messageTextEl, fullContent);
		}

		if (reasoningDetailsEl && reasoning && window.MarkdownRenderer) {
			const rc = reasoningDetailsEl.querySelector(".reasoning-content");
			MarkdownRenderer.finalizeStreamingMessage(rc, reasoning);
		}

		if (!fullContent && !reasoning) {
			hideStreamingIndicator();
			addMessageBubble("assistant", "(empty response)");
		} else if (fullContent) {
			commitAssistantMessage(fullContent);
		}

		if (window.PlaygroundAudio)
			window.PlaygroundAudio.onStreamComplete(fullContent);
	} catch (e) {
		if (e.name === "AbortError") {
			const container = document.getElementById("playground-messages");
			if (!container || container.querySelector(".playground-placeholder"))
				return;
			hideStreamingIndicator();
			addMessageBubble("assistant", "(stopped)");
		} else {
			hideStreamingIndicator();
			addMessageBubble("assistant", `Error: ${e.message}`);
		}
	} finally {
		playgroundAbortController = null;
		isStreaming = false;
		sendBtn.classList.remove("hidden");
		stopBtn.classList.add("hidden");
		if (window.PlaygroundAudio) window.PlaygroundAudio.onStreamEnd();
		loadConversations(conversationSearchQuery);
	}
}

function stopPlaygroundStream() {
	if (playgroundAbortController) {
		playgroundAbortController.abort();
		playgroundAbortController = null;
	}
	if (window.PlaygroundAudio) window.PlaygroundAudio.cancelSpeech();
}

// Init sidebar on load.
setTimeout(() => {
	syncConversationSearchClear();
	loadConversations();
}, 0);
