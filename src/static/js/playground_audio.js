/**
 * Playground Audio Mode, Phase A — browser-native speech.
 *
 * Two halves. The top of this file is pure and has no DOM or Web Speech
 * dependency at all: nextSpeechChunk, createSilenceTimer, shouldRestartListening
 * and resolveVoice are what tests/playground_audio.test.mjs exercises. The rest
 * is the adapter that drives SpeechRecognition and speechSynthesis, and it is
 * reachable only through window.PlaygroundAudio.
 *
 * The whole design turns on one constraint: speechSynthesis plays out of the
 * speakers, the microphone hears it, and the model transcribes its own reply and
 * answers itself, indefinitely. So recognition is stopped before the first
 * utterance and started again when the last one ends. Half-duplex by
 * construction; barge-in is deferred because there is no VAD in the Web Speech
 * API to build it on.
 *
 * Phase B swaps the browser provider for Bedrock speech models behind the same
 * interface. Nothing outside this file knows which provider is active.
 */

/** Fallback for a reply that never produces a sentence terminator. */
const SPEECH_MAX_CHARS = 220;

/** Trailing silence that marks the end of an utterance. */
const SILENCE_MS = 1300;

/**
 * Grace between the silence timer firing and the message going out. Long enough
 * to notice a misheard word and say something else, short enough not to feel
 * like a wait. Speaking during this window calls noteActivity() again and the
 * send is abandoned.
 */
const GRACE_MS = 2600;

/**
 * Beat between recognition ending and being asked to start again.
 *
 * The other term in that gap is however long the browser takes to fire `onend`,
 * which is not ours to control. Lowering this increases the chance of calling
 * start() while the previous session is still tearing down, which throws — see
 * startRecognition, where that is now retried rather than left to strand the
 * mic.
 */
const RESTART_DELAY_MS = 150;

/** Consecutive start() failures tolerated before giving up. See startRecognition. */
const START_RETRY_LIMIT = 3;

const SPEECH_LANG = "en-US";

/**
 * Dots that end a sentence look exactly like dots that do not. Without this,
 * "3. Next" splits mid-number and "e.g. this" splits mid-abbreviation.
 */
const ABBREVIATIONS = new Set([
	"mr",
	"mrs",
	"ms",
	"dr",
	"prof",
	"sr",
	"jr",
	"st",
	"vs",
	"etc",
	"e.g",
	"i.e",
	"fig",
	"no",
	"inc",
	"ltd",
	"co",
	"approx",
	"dept",
]);

// ---------------------------------------------------------------------------
// Pure
// ---------------------------------------------------------------------------

/**
 * True when the "." at `i` is a decimal point or an abbreviation rather than a
 * sentence end. Callers only reach here for a dot already followed by
 * whitespace, so this is the whole of the disambiguation.
 */
function isAbbrevDot(slice, i) {
	const prev = slice[i - 1] || "";
	const next = slice[i + 1] || "";
	if (/\d/.test(prev) && /\d/.test(next)) return true;

	const before = slice.slice(Math.max(0, i - 8), i);
	const m = before.match(/([A-Za-z.]+)$/);
	if (!m) return false;
	const word = m[1].toLowerCase().replace(/\.$/, "");
	if (ABBREVIATIONS.has(word)) return true;
	// A single capitalised letter is an initial: "J. R. R. Tolkien".
	return word.length === 1 && /[A-Z]/.test(m[1]);
}

/** A line that opens a new block, where a break is a better split than a dot. */
function isLineStartMarker(slice, i) {
	const ch = slice[i];
	if (ch === "#" || ch === ">" || ch === "|" || ch === ":") return true;
	if (ch === "-" || ch === "*" || ch === "+")
		return /\s/.test(slice[i + 1] || "");
	if (/\d/.test(ch)) return /[.)]/.test(slice[i + 1] || "");
	return false;
}

/**
 * Where a fenced code block opens, or -1. A single inline backtick pair is not a
 * fence and does not count — those are short enough to read out.
 */
function findFenceStart(slice) {
	const at = slice.indexOf("```");
	return at;
}

/** Last-whitespace cut, so a long run-on breaks at a word rather than mid-word. */
function cutAtWhitespace(slice, max) {
	const window = slice.slice(0, max);
	const at = window.lastIndexOf(" ");
	return at > 0 ? at + 1 : max;
}

/**
 * The next boundary to speak up to, as an exclusive index into `slice`, or -1
 * when there is not enough settled text yet.
 *
 * Fenced code is skipped whole rather than read out. If the closing fence has
 * not arrived we return -1, because everything after an open fence might turn
 * out to be inside it.
 */
function findSpeechBoundary(slice) {
	const fenceAt = findFenceStart(slice);

	if (fenceAt === 0) {
		const close = slice.indexOf("\n```", 3);
		if (close === -1) return -1;
		const end = slice.indexOf("\n", close + 1);
		return end === -1 ? -1 : end + 1;
	}

	const limit = fenceAt === -1 ? slice.length : fenceAt;

	for (let i = 0; i < limit; i++) {
		const ch = slice[i];
		if (ch === "\n") {
			if (i + 1 >= limit || slice[i + 1] === "\n") return i + 1;
			if (isLineStartMarker(slice, i + 1)) return i + 1;
			continue;
		}
		if (ch !== "." && ch !== "!" && ch !== "?") continue;
		if (i + 1 >= limit || !/\s/.test(slice[i + 1])) continue;
		if (ch === "." && isAbbrevDot(slice, i)) continue;
		return i + 1;
	}

	if (limit >= SPEECH_MAX_CHARS)
		return cutAtWhitespace(slice, SPEECH_MAX_CHARS);
	return -1;
}

/**
 * Markdown to prose. Applied to each extracted slice rather than to the whole
 * accumulated reply — re-cleaning the full text every tick would change its
 * length and break the consumed-offset arithmetic in nextSpeechChunk.
 */
function stripMarkdown(text) {
	let s = text;
	s = s.replace(/```[\s\S]*?```/g, " ");
	s = s.replace(/`([^`]+)`/g, "$1");
	s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
	s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
	s = s.replace(/^\s{0,3}#{1,6}\s+/gm, "");
	s = s.replace(/^\s{0,3}>\s?/gm, "");
	s = s.replace(/^\s*[-*+]\s+/gm, "");
	s = s.replace(/^\s*\d+[.)]\s+/gm, "");
	s = s.replace(/(\*\*|__)(.*?)\1/g, "$2");
	s = s.replace(/~~(.*?)~~/g, "$1");
	s = s.replace(/(\*|_)(?=\S)(.*?)(?<=\S)\1/g, "$2");
	s = s.replace(/^\s*([-*_]\s*){3,}$/gm, "");
	s = s.replace(/\|/g, " ");
	// A max-length cut can land mid-marker. Drop the orphans rather than let
	// the synthesiser read an asterisk as a word.
	s = s.replace(/[*_~`]+/g, "");
	return s.replace(/\s+/g, " ").trim();
}

/**
 * The next speakable sentence of a streaming reply.
 *
 * `fullText` is the assistant text so far and `consumed` is how much of it has
 * already been handed out, so the caller only needs to keep the latest value of
 * both. Returns `{ text: null, consumed }` when the text so far does not yet
 * contain a complete sentence — a fast first sentence is what makes the reply
 * start talking while the rest is still arriving.
 *
 * A chunk that strips down to nothing (a code fence, a bare rule) is skipped
 * internally, and the guard bounds the loop so a reply that is nothing but
 * markup cannot spin here.
 */
function nextSpeechChunk(fullText, consumed) {
	let pos = consumed || 0;
	const source = fullText || "";

	for (let guard = 0; guard < 8; guard++) {
		const slice = source.slice(pos);
		if (!slice.trim()) return { text: null, consumed: pos };

		const cut = findSpeechBoundary(slice);
		if (cut === -1) return { text: null, consumed: pos };

		const text = stripMarkdown(slice.slice(0, cut));
		pos += cut;
		if (text) return { text, consumed: pos };
	}

	return { text: null, consumed: pos };
}

/**
 * The send-on-silence mechanism, as a state machine over injected timers.
 *
 * There is no VAD in the Web Speech API — `onspeechend` is non-standard and
 * unreliable — so this trailing timer is the whole mechanism, which is why it
 * gets injected timers and a test rather than a manual matrix.
 *
 *   idle    -> noteActivity() -> waiting
 *   waiting -> silence elapses -> pending (grace)
 *   pending -> grace elapses   -> idle, onSend()
 *
 * noteActivity() during the grace window abandons the send and re-arms silence,
 * which is the escape hatch for a misheard transcript. cancel() drops it
 * outright, which is what typing in the input box does.
 */
function createSilenceTimer(opts) {
	const o = opts || {};
	const onPending = o.onPending || null;
	const onSend = o.onSend || null;
	const setT = o.setTimeout || setTimeout;
	const clearT = o.clearTimeout || clearTimeout;
	const silenceMs = o.silenceMs == null ? SILENCE_MS : o.silenceMs;
	const graceMs = o.graceMs == null ? GRACE_MS : o.graceMs;

	let handle = null;
	let state = "idle";

	function clear() {
		if (handle !== null) {
			clearT(handle);
			handle = null;
		}
	}

	function arm(delayMs, next) {
		clear();
		handle = setT(() => {
			handle = null;
			next();
		}, delayMs);
	}

	return {
		get state() {
			return state;
		},
		noteActivity() {
			state = "waiting";
			arm(silenceMs, () => {
				state = "pending";
				if (onPending) onPending();
				arm(graceMs, () => {
					state = "idle";
					if (onSend) onSend();
				});
			});
		},
		cancel() {
			state = "idle";
			clear();
		},
	};
}

/**
 * Whether the microphone may be opened again.
 *
 * Both flags, not either. The last sentence of a reply is usually still being
 * read out when the last delta lands, so restarting on stream end alone would
 * open the mic mid-utterance and start the echo loop this file is built to
 * avoid.
 */
function shouldRestartListening(s) {
	return Boolean(s?.armed && s.streamDone && s.speechDone);
}

/**
 * getVoices() returns [] until voiceschanged fires, so this has to be
 * re-resolvable rather than resolved once at arm time. A local voice for the
 * page's own language beats whatever the OS happened to default to.
 */
function resolveVoice(voices, lang) {
	if (!voices?.length) return null;
	const want = (lang || SPEECH_LANG).toLowerCase().replace(/_/g, "-");
	const base = want.split("-")[0];
	const isMatch = (v) => {
		const l = (v.lang || "").toLowerCase().replace(/_/g, "-");
		return l === want || l === base || l.startsWith(`${base}-`);
	};
	return (
		voices.find((v) => v.localService && isMatch(v)) ||
		voices.find((v) => isMatch(v)) ||
		voices.find((v) => v.localService) ||
		voices[0] ||
		null
	);
}

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

const state = {
	armed: false,
	streamDone: true,
	speechDone: true,
	recognitionActive: false,
};

let recognition = null;
let silenceTimer = null;
let speakQueue = [];
let spokenConsumed = 0;
/**
 * Mutable recognition state, held as one object so the accumulation helper can
 * be called and tested without reaching into module scope.
 */
const resultSession = {
	finalTranscript: "",
	interimTranscript: "",
	/**
	 * How many results have been committed as final text. Not a count of
	 * entries *read* — see accumulateResult for why that distinction is the
	 * whole ballgame.
	 */
	finalIndex: 0,
};
let cachedVoice = null;
let restartHandle = null;
let countdownHandle = null;
/** Consecutive start() failures, so the retry in startRecognition can give up. */
let startAttempts = 0;

function support() {
	return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function isSupported() {
	return Boolean(support());
}

/**
 * Repaint the status chip, the mic button and the audio bar.
 *
 * Every label is written here rather than carried in the markup, because the
 * unsupported case has to overwrite whatever state would otherwise be shown.
 */
function setState(next) {
	const bar = document.getElementById("playground-audio-bar");
	const chip = document.getElementById("playground-audio-status");
	const label = document.getElementById("playground-audio-status-text");
	const dot = document.getElementById("playground-audio-dot");
	const micBtn = document.getElementById("playground-mic-btn");

	if (micBtn) {
		micBtn.classList.toggle("active", state.armed);
		micBtn.setAttribute("aria-pressed", state.armed ? "true" : "false");
	}
	if (!bar || !chip || !label) return;

	// The bar is only worth the vertical space once audio is in play, and it
	// stays visible while armed so the state is legible at a glance.
	const show = state.armed || isSupported();
	bar.classList.toggle("hidden", !show);
	bar.dataset.state = next;

	chip.className = `status-chip audio-${next}`;
	let text;
	if (!isSupported()) {
		text = "Audio needs Chrome or Safari";
	} else if (next === "listening") {
		text = "Listening — stop talking to send";
	} else if (next === "pending") {
		text = "Sending…";
	} else if (next === "streaming") {
		text = "Thinking…";
	} else if (next === "speaking") {
		text = "Speaking…";
	} else {
		text = "Mic off";
	}
	label.textContent = text;
	if (dot) dot.className = "audio-dot";
}

function showInterim(text) {
	const el = document.getElementById("playground-audio-transcript");
	if (el) el.textContent = text;
}

function clearInterim() {
	showInterim("");
}

// --- speech ----------------------------------------------------------------

function primeVoices() {
	if (cachedVoice) return;
	const list = window.speechSynthesis.getVoices();
	if (!list?.length) return;
	cachedVoice = resolveVoice(list, SPEECH_LANG);
}

function speechNext() {
	if (!speakQueue.length) {
		state.speechDone = true;
		afterSpeechChange();
		return;
	}
	const text = speakQueue.shift();
	const utter = new SpeechSynthesisUtterance(text);
	primeVoices();
	if (cachedVoice) utter.voice = cachedVoice;
	utter.lang = SPEECH_LANG;
	utter.rate = 1.05;
	// Chrome silently stops synthesising after ~15s and the tail is lost with no
	// error event. resume() nudges it along; it is a no-op elsewhere.
	const keepAlive = window.setInterval(() => {
		if (!state.speechDone) {
			window.speechSynthesis.pause();
			window.speechSynthesis.resume();
		}
	}, 10000);
	const done = () => {
		window.clearInterval(keepAlive);
		speechNext();
	};
	utter.onend = done;
	utter.onerror = done;
	window.speechSynthesis.speak(utter);
}

function enqueue(text) {
	if (!text) return;
	speakQueue.push(text);
	if (state.speechDone) {
		state.speechDone = false;
		afterSpeechChange();
	}
	speechNext();
}

/**
 * Feed a cumulative update of the assistant text.
 *
 * One sentence per call, so speech keeps pace with the stream rather than
 * dumping a finished paragraph into the queue at the end.
 */
function feedSpeech(fullContent) {
	if (!state.armed) return;
	const result = nextSpeechChunk(fullContent, spokenConsumed);
	spokenConsumed = result.consumed;
	if (result.text) enqueue(result.text);
}

/**
 * Speak every complete sentence still outstanding.
 *
 * Needed because feedSpeech takes one sentence per delta. Tokens arrive faster
 * than sentences complete, so a reply routinely ends with one or two finished
 * sentences that no delta ever had room to hand over — the tail was silent
 * without this.
 */
function drainSpeech(fullContent) {
	if (!state.armed) return;
	for (;;) {
		const result = nextSpeechChunk(fullContent, spokenConsumed);
		spokenConsumed = result.consumed;
		if (!result.text) return;
		enqueue(result.text);
	}
}

function silenceSpeech() {
	speakQueue = [];
	window.speechSynthesis.cancel();
	state.speechDone = true;
	afterSpeechChange();
}

/**
 * Start the next queued sentence if one is waiting.
 *
 * The queue drains itself through each utterance's onend, so this is only for
 * the case where a turn produced queued text that nothing has begun speaking.
 */
function flushSpeech() {
	if (speakQueue.length) speechNext();
}

// --- recognition -----------------------------------------------------------

function maybeRestart() {
	if (restartHandle !== null) return;
	if (!shouldRestartListening(state) || state.recognitionActive) return;
	restartHandle = window.setTimeout(() => {
		restartHandle = null;
		if (shouldRestartListening(state) && !state.recognitionActive) {
			startRecognition();
		}
	}, RESTART_DELAY_MS);
}

/**
 * Repaint after the speak queue changes, and reopen the mic once the last
 * sentence has finished. The queue draining is the *second* half of the restart
 * condition — see shouldRestartListening.
 */
function afterSpeechChange() {
	if (!state.armed) return;
	if (state.speechDone) clearInterim();
	if (state.streamDone && !state.speechDone) {
		setState("speaking");
	} else if (state.streamDone && state.speechDone) {
		setState("listening");
		maybeRestart();
	}
}

/**
 * Read new text out of a recognition event.
 *
 * Split out from handleResult so the accumulation is testable without a
 * browser: it is the fiddly part, and the part that has been wrong.
 *
 * Reports whether anything actually *changed*, which is not the same question
 * as whether there is text to show. A browser in continuous mode re-fires the
 * same result event repeatedly, and once an entry has finalised the transcript
 * is non-empty forever after — so "is there text" is true on every re-delivery
 * and cannot be used to decide that the user is still talking. See handleResult.
 */
function accumulateResult(session, event) {
	// Interim text is provisional by design — it is meant to be re-read and
	// replaced on every event — so it is taken fresh from resultIndex each time
	// and needs no bookkeeping of its own.
	let interim = "";
	for (let i = event.resultIndex; i < event.results.length; i++) {
		if (!event.results[i].isFinal) interim += event.results[i][0].transcript;
	}

	// Finals are appended exactly once. The pointer advances only while entries
	// really are final — NOT merely because they have been read. A browser
	// delivers a result as interim first and then flips the same entry to
	// isFinal, so a count of read entries has already passed it by the time the
	// finalised text arrives, and the completed transcript is never stored. The
	// send then goes out empty and audio mode silently does nothing, which is
	// exactly the bug this ordering exists to prevent.
	//
	// Stopping at the first non-final rather than skipping ahead to any later
	// final keeps the transcript in utterance order.
	let changed = false;
	while (
		session.finalIndex < event.results.length &&
		event.results[session.finalIndex].isFinal
	) {
		appendFinal(session, event.results[session.finalIndex][0].transcript);
		session.finalIndex++;
		changed = true;
	}

	// A growing interim transcript is real speech. An identical one is the same
	// word counted twice, and treating it as activity is what made the grace
	// countdown restart over and over.
	if (interim !== session.interimTranscript) changed = true;
	session.interimTranscript = interim;

	return { text: joinTranscript(session.finalTranscript, interim), changed };
}

/**
 * Commit one finalised result, inserting the space the browser may have
 * omitted between two adjacent results. joinTranscript does the same job for
 * final-plus-interim; without both, a reply reads "What istwo plustwo."
 */
function appendFinal(session, text) {
	const chunk = (text || "").trim();
	if (!chunk) return;
	session.finalTranscript = session.finalTranscript
		? `${session.finalTranscript.trimEnd()} ${chunk}`
		: chunk;
}

function handleResult(event) {
	const { text, changed } = accumulateResult(resultSession, event);
	if (!text) return;

	showInterim(text);
	// Only genuinely new text is activity. Re-arm on every delivered event and
	// a continuous-mode browser's repeated re-firing of the same result resets
	// the grace window each time — the countdown visibly restarts (3, 2, 3, 2,
	// 1) and the send is delayed by as long as the engine keeps firing.
	//
	// silenceTimer is null once disarmed, and a result already in flight can
	// land after that, so this cannot assume it exists.
	if (changed && silenceTimer) silenceTimer.noteActivity();
}

/**
 * Final and interim text are separate results, and a final one does not always
 * end where the next begins. Without the join the transcript reads
 * "twominus one".
 */
function joinTranscript(final, interim) {
	const a = final.trim();
	const b = interim.trim();
	if (!a) return b;
	if (!b) return a;
	return `${a} ${b}`;
}

function handleError(event) {
	const err = event?.error || "";
	if (err === "not-allowed" || err === "service-not-allowed") {
		disarm();
		showToast(
			"Microphone permission denied. Allow it in the browser's site settings to use audio mode.",
			"error",
			6000,
		);
		return;
	}
	// no-speech, aborted and network are all transient here: maybeRestart() puts
	// the loop back together.
}

function startRecognition() {
	if (state.recognitionActive || !state.armed) return;
	if (!isSupported()) return;
	if (!recognition) {
		const Ctor = support();
		recognition = new Ctor();
		recognition.continuous = true;
		recognition.interimResults = true;
		recognition.lang = SPEECH_LANG;
		recognition.onresult = handleResult;
		recognition.onerror = handleError;
		recognition.onend = () => {
			state.recognitionActive = false;
			maybeRestart();
		};
	}
	// Results are indexed per session, and a fresh session starts them over.
	resultSession.finalIndex = 0;
	try {
		recognition.start();
		state.recognitionActive = true;
		startAttempts = 0;
		setState("listening");
	} catch {
		// start() throws if the previous session has not finished tearing down.
		// stopRecognition() clears recognitionActive optimistically, so this can
		// be reached while the old session is still shutting down — and because
		// nothing started, no onend is coming to put us back together. Retry
		// here or the mic stays shut until the next turn.
		state.recognitionActive = false;
		// Bounded, so a start() that keeps failing is not a hot loop.
		if (++startAttempts <= START_RETRY_LIMIT) maybeRestart();
	}
}

function stopRecognition() {
	if (recognition && state.recognitionActive) {
		try {
			recognition.stop();
		} catch {
			// Already stopped; onend still fires.
		}
	}
	state.recognitionActive = false;
}

function currentTranscript() {
	return joinTranscript(
		resultSession.finalTranscript,
		resultSession.interimTranscript,
	);
}

function resetTranscript() {
	resultSession.finalTranscript = "";
	resultSession.interimTranscript = "";
	resultSession.finalIndex = 0;
	clearInterim();
	// Each reply restarts the chunker, since fullContent begins again from the
	// assistant's first token. Left to run on from the previous reply, the
	// offset would be past the end of the new one and nothing would be spoken.
	spokenConsumed = 0;
}

function sendFromTranscript() {
	const text = currentTranscript();
	if (!text) {
		// Armed silence, not an utterance. Go back to waiting rather than
		// bouncing a "Please enter a message" toast off a hands-free flow.
		resetTranscript();
		// Repaint here rather than relying on maybeRestart. onSend() has not run,
		// so recognition was never stopped and maybeRestart bails on
		// state.recognitionActive — which would leave the countdown's last words,
		// "Sending in 1s", on screen indefinitely. The chip has to be corrected
		// whether or not anything else needs doing.
		setState("listening");
		maybeRestart();
		return;
	}
	const input = document.getElementById("playground-input");
	if (input) input.value = text;
	resetTranscript();
	// The same call the Send button makes. onSend() below closes the mic.
	sendPlaygroundMessage();
}

function arm() {
	if (!isSupported()) {
		showToast(
			"Audio mode needs SpeechRecognition, which Firefox does not implement. Use Chrome or Safari.",
			"warning",
			6000,
		);
		return;
	}
	state.armed = true;
	state.streamDone = true;
	state.speechDone = true;
	// Clear any exhausted start() retry budget left over from a previous arming,
	// so a teardown race on this turn gets its retries rather than none.
	startAttempts = 0;
	resetTranscript();

	silenceTimer = createSilenceTimer({
		silenceMs: SILENCE_MS,
		graceMs: GRACE_MS,
		onPending: () => {
			setState("pending");
			startCountdown();
		},
		onSend: () => {
			stopCountdown();
			sendFromTranscript();
		},
	});

	window.speechSynthesis.onvoiceschanged = () => {
		cachedVoice = null;
		primeVoices();
	};
	primeVoices();

	setState("listening");
	startRecognition();
}

function disarm() {
	state.armed = false;
	stopCountdown();
	stopRecognition();
	if (silenceTimer) silenceTimer.cancel();
	silenceTimer = null;
	window.speechSynthesis.cancel();
	speakQueue = [];
	state.speechDone = true;
	state.streamDone = true;
	resetTranscript();
	setState("off");
}

// --- grace countdown -------------------------------------------------------

function startCountdown() {
	stopCountdown();
	let remaining = Math.ceil(GRACE_MS / 1000);
	const label = document.getElementById("playground-audio-status-text");
	const paint = () => {
		if (!label) return;
		label.textContent = `Sending in ${remaining}s — speak to cancel`;
	};
	paint();
	countdownHandle = window.setInterval(() => {
		remaining -= 1;
		if (remaining <= 0) {
			stopCountdown();
			return;
		}
		paint();
	}, 1000);
}

function stopCountdown() {
	if (countdownHandle !== null) {
		window.clearInterval(countdownHandle);
		countdownHandle = null;
	}
}

// --- public surface -------------------------------------------------------

/**
 * Assigned to window explicitly, like MarkdownRenderer.
 *
 * A top-level `const` in a classic script creates a global *lexical* binding,
 * which is not a property of `window` — so `window.PlaygroundAudio` would be
 * undefined and every `if (window.PlaygroundAudio)` guard in playground.js and
 * navigation.js would quietly skip. Those guards are what made that failure
 * invisible: the feature armed, listened and counted down, while the mic never
 * closed on send, no reply was ever spoken, and leaving the page never disarmed.
 */
window.PlaygroundAudio = {
	isSupported,

	arm,
	disarm,

	toggle() {
		if (state.armed) disarm();
		else arm();
	},

	/** A send is starting, from audio or from the keyboard. Close the mic. */
	onSend() {
		state.streamDone = false;
		stopCountdown();
		stopRecognition();
		if (silenceTimer) silenceTimer.cancel();
		// The new reply starts from an empty string, so the chunker's offset
		// has to go back to zero with it.
		resetTranscript();
		if (!speakQueue.length) {
			window.speechSynthesis.cancel();
			state.speechDone = true;
		}
		setState("streaming");
	},

	/** Cumulative assistant text, called as it streams. */
	onDelta(fullContent) {
		feedSpeech(fullContent);
	},

	/**
	 * The last of the text, so speak whatever is still outstanding.
	 *
	 * Distinct from onStreamEnd because Stop aborts the fetch: the finally block
	 * runs, but the partial reply is never committed and must not be read out.
	 */
	onStreamComplete(fullContent) {
		drainSpeech(fullContent);
	},

	/** The stream finished, for any reason including Stop and error. */
	onStreamEnd() {
		state.streamDone = true;
		// A reply can end with text still queued rather than being spoken.
		flushSpeech();
		// speechDone is only true here if there was nothing to say, in which case
		// the mic never had to close and goes straight back.
		if (state.speechDone) {
			setState("listening");
			maybeRestart();
		} else {
			setState("speaking");
		}
	},

	/** Stop is a turn boundary, not a disarm — see the Stop button. */
	cancelSpeech() {
		stopCountdown();
		silenceSpeech();
		if (state.armed) {
			setState("listening");
			maybeRestart();
		}
	},

	getState() {
		return state;
	},
};

// --- wiring ----------------------------------------------------------------

function cancelPendingSend() {
	if (silenceTimer && silenceTimer.state === "pending") {
		stopCountdown();
		silenceTimer.noteActivity();
		setState("listening");
	}
}

function initPlaygroundAudio() {
	const micBtn = document.getElementById("playground-mic-btn");
	const input = document.getElementById("playground-input");

	if (micBtn) {
		micBtn.addEventListener("click", () => PlaygroundAudio.toggle());
		if (!isSupported()) {
			micBtn.disabled = true;
			micBtn.title =
				"Audio mode needs SpeechRecognition, which Firefox does not implement. Use Chrome or Safari.";
		} else {
			micBtn.title =
				"Hands-free: listen, then read the reply aloud (Esc to stop)";
		}
	}

	if (input) {
		input.addEventListener("input", () => cancelPendingSend());
	}

	document.addEventListener("keydown", (e) => {
		if (e.key === "Escape" && state.armed) {
			e.preventDefault();
			disarm();
			showToast("Mic off", "info", 2000);
		}
	});

	// Backgrounding the tab must not leave the microphone open, and Chrome
	// throttles timers in a hidden tab anyway, so the silence timer cannot be
	// trusted to fire. Disarm outright rather than try to resume.
	document.addEventListener("visibilitychange", () => {
		if (document.hidden && state.armed) disarm();
	});
	window.addEventListener("pagehide", () => {
		if (state.armed) disarm();
	});

	setState("off");
}

// Tests load this file with `new Function` and no document, so this must be
// the only statement that touches the DOM at load time.
if (typeof document !== "undefined") {
	initPlaygroundAudio();
}
