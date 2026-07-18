(() => {
	if (!window.marked || !window.DOMPurify) {
		console.error(
			"MarkdownRenderer: missing dependencies (marked or DOMPurify)",
		);
		return;
	}

	const ALLOWED_TAGS = [
		"p",
		"br",
		"strong",
		"em",
		"s",
		"code",
		"pre",
		"blockquote",
		"ul",
		"ol",
		"li",
		"h1",
		"h2",
		"h3",
		"h4",
		"h5",
		"h6",
		"table",
		"thead",
		"tbody",
		"tr",
		"th",
		"td",
		"a",
		"hr",
		"img",
		"span",
		"div",
		"dl",
		"dt",
		"dd",
	];
	const ALLOWED_ATTR = [
		"href",
		"title",
		"src",
		"alt",
		"class",
		"id",
		"target",
		"rel",
		"align",
	];

	const hljsInstance = window.hljs;
	const escapeHtml = (s) =>
		s
			.replaceAll("&", "&amp;")
			.replaceAll("<", "&lt;")
			.replaceAll(">", "&gt;")
			.replaceAll('"', "&quot;")
			.replaceAll("'", "&#39;");

	const renderer = new marked.Renderer();
	renderer.code = ({ text, lang }) => {
		const language = lang && hljsInstance?.getLanguage(lang) ? lang : null;
		const code = language
			? hljsInstance.highlight(text, { language }).value
			: escapeHtml(text);
		return `<pre><code class="hljs${language ? ` language-${language}` : ""}">${code}</code></pre>`;
	};

	marked.use({ breaks: true, gfm: true, renderer });

	function sanitize(html) {
		return DOMPurify.sanitize(html, {
			ALLOWED_TAGS,
			ALLOWED_ATTR,
			FORBID_TAGS: [
				"script",
				"style",
				"iframe",
				"object",
				"embed",
				"form",
				"input",
			],
			FORBID_ATTR: [
				"onerror",
				"onload",
				"onclick",
				"onmouseover",
				"onmouseout",
				"style",
			],
			ALLOW_DATA_ATTR: false,
		});
	}

	function injectCopyButtons(containerEl) {
		containerEl.querySelectorAll("pre").forEach((pre) => {
			if (pre.querySelector(".code-copy-btn")) return;
			const code = pre.querySelector("code");
			if (!code) return;
			const btn = document.createElement("button");
			btn.className = "code-copy-btn";
			btn.textContent = "Copy";
			btn.addEventListener("click", () => {
				navigator.clipboard.writeText(code.innerText).then(() => {
					btn.textContent = "Copied!";
					setTimeout(() => {
						btn.textContent = "Copy";
					}, 2000);
				});
			});
			pre.appendChild(btn);
		});
	}

	function renderAssistantMessage(fullContent) {
		return sanitize(marked.parse(fullContent));
	}

	function finalizeStreamingMessage(messageTextEl, fullContent) {
		const html = renderAssistantMessage(fullContent);
		messageTextEl.innerHTML = html;
		messageTextEl.dataset.raw = fullContent;
		messageTextEl.classList.add("markdown-rendered");
		injectCopyButtons(messageTextEl);
	}

	window.MarkdownRenderer = {
		renderAssistantMessage,
		finalizeStreamingMessage,
	};
})();
