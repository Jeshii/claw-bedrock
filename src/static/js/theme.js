function toggleHljsTheme(isDark) {
	const link = document.querySelector(".hljs-dark-theme");
	if (link) link.disabled = !isDark;
}

function toggleTheme() {
	const root = document.documentElement;
	const btn = document.querySelector(".theme-toggle");
	root.classList.toggle("dark");
	const isDark = root.classList.contains("dark");
	btn.innerHTML = isDark ? `${SUN_SVG}Light` : `${MOON_SVG}Dark`;
	localStorage.setItem("theme", isDark ? "dark" : "light");
	toggleHljsTheme(isDark);
}

function initTheme() {
	const btn = document.querySelector(".theme-toggle");
	const pinnedTheme = localStorage.getItem("theme");
	const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
	const isDark = pinnedTheme === "dark" || (!pinnedTheme && prefersDark);

	if (isDark) {
		document.documentElement.classList.add("dark");
	} else {
		document.documentElement.classList.remove("dark");
	}
	toggleHljsTheme(isDark);
	if (btn) {
		btn.innerHTML = isDark ? `${SUN_SVG}Light` : `${MOON_SVG}Dark`;
	}

	window
		.matchMedia("(prefers-color-scheme: dark)")
		.addEventListener("change", (e) => {
			if (!localStorage.getItem("theme")) {
				const shouldBeDark = e.matches;
				if (shouldBeDark) {
					document.documentElement.classList.add("dark");
				} else {
					document.documentElement.classList.remove("dark");
				}
				toggleHljsTheme(shouldBeDark);
				if (btn) {
					btn.innerHTML = shouldBeDark ? `${SUN_SVG}Light` : `${MOON_SVG}Dark`;
				}
			}
		});
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", initTheme);
} else {
	initTheme();
}

if (window.AUTH_REQUIRED) {
	const sidebarFooter = document.querySelector(".sidebar-footer");
	const logoutBtn = document.createElement("button");
	logoutBtn.className = "logout-btn";
	logoutBtn.title = "Logout";
	logoutBtn.innerHTML =
		'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;margin-right:4px" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></svg> Logout';
	logoutBtn.addEventListener("click", logout);
	sidebarFooter.appendChild(logoutBtn);
}
