/**
 * Unit tests for src/static/js/playground_audio.js.
 *
 * Audio APIs cannot run headlessly, so this covers what carries the feature
 * without a microphone: the sentence chunker, the silence timer and its grace
 * window, the restart gate, voice pick, and transcript accumulation. Everything
 * else is a manual matrix — Chrome and Safari, denied permission, no microphone,
 * light and dark.
 *
 * Run with `node --test tests/`, which scripts/lint.sh does on every run.
 *
 * The source is a classic script with top-level declarations, not a module, so
 * it is loaded by evaluating the file. `new Function` needs its DOM and Web
 * Speech globals supplied as parameters; the pure functions are then pulled off
 * the result. `document` is passed as undefined on purpose, which is what the
 * file's own `typeof document` guard keys off to skip initialisation.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
	join(here, "..", "src", "static", "js", "playground_audio.js"),
	"utf8",
);

/**
 * Deliberately undefined, not an empty object. The source guards its own
 * initialisation on `typeof document !== "undefined"`, so handing it a stub
 * object would make it think it is in a browser and run the DOM wiring.
 */
const noDom = undefined;

/**
 * Evaluate the source against a given `window`, and hand back both the pure
 * functions and that window — the tests need to inspect what the file put on it.
 */
function evaluate(fakeWindow) {
	return new Function(
		"window",
		"document",
		"SpeechSynthesisUtterance",
		"setTimeout",
		"clearTimeout",
		"setInterval",
		"clearInterval",
		`${source}
		return {
			nextSpeechChunk,
			createSilenceTimer,
			shouldRestartListening,
			resolveVoice,
			findSpeechBoundary,
			stripMarkdown,
			joinTranscript,
			accumulateResult,
			SILENCE_MS,
			GRACE_MS,
			SPEECH_MAX_CHARS,
			RESTART_DELAY_MS,
		};`,
	)(
		fakeWindow,
		noDom,
		function SpeechSynthesisUtteranceStub() {},
		setTimeout,
		clearTimeout,
		setInterval,
		clearInterval,
	);
}

function load() {
	// A window object is needed even for the pure tests, because the file
	// publishes the adapter onto it at load time. `document` stays undefined so
	// the DOM wiring does not run.
	return evaluate({});
}

const { nextSpeechChunk, stripMarkdown } = load();

/** A clock the tests drive by hand, so nothing depends on wall time. */
function fakeClock() {
	let now = 0;
	let seq = 0;
	const pending = new Map();

	return {
		setTimeout(fn, delay) {
			const id = ++seq;
			pending.set(id, { at: now + delay, fn });
			return id;
		},
		clearTimeout(id) {
			pending.delete(id);
		},
		/** Advance time, firing everything due, in scheduled order. */
		advance(ms) {
			now += ms;
			for (;;) {
				const due = [...pending.entries()]
					.filter(([, t]) => t.at <= now)
					.sort((a, b) => a[1].at - b[1].at);
				if (!due.length) return;
				const [id, task] = due[0];
				pending.delete(id);
				task.fn();
			}
		},
	};
}

// --- sentence chunker ------------------------------------------------------

test("chunker waits for a complete sentence", () => {
	const partial = nextSpeechChunk("The answer is", 0);
	assert.equal(partial.text, null, "must not speak an unfinished sentence");
	assert.equal(partial.consumed, 0, "must not advance past unsaid text");
});

test("chunker emits a sentence and reports how far it consumed", () => {
	const result = nextSpeechChunk("Hello there. How are", 0);
	assert.equal(result.text, "Hello there.");
	// Up to and including the terminator. The space after it belongs to the
	// next chunk, which is what lets a boundary be re-found after it arrives.
	assert.equal(result.consumed, 12);
});

test("chunker advances across successive streaming updates", () => {
	// The same call the delta hook makes, once per frame. fullText is cumulative
	// — playground.js hands over the whole assistant text so far, not the delta.
	let consumed = 0;
	let full = "";
	const spoken = [];
	const deltas = ["One. ", "Two. ", "Three st", "ill in progress"];
	for (const delta of deltas) {
		full += delta;
		const result = nextSpeechChunk(full, consumed);
		consumed = result.consumed;
		if (result.text) spoken.push(result.text);
	}
	assert.deepEqual(spoken, ["One.", "Two."]);
	assert.equal(consumed, 9, "the incomplete third sentence is left unconsumed");
});

test("chunker handles several sentences arriving in one frame", () => {
	// One sentence per call, not a batch: the queue is what serialises them.
	const first = nextSpeechChunk("First one. Second one. Third", 0);
	assert.equal(first.text, "First one.");
	assert.equal(first.consumed, 10);
	const second = nextSpeechChunk(
		"First one. Second one. Third",
		first.consumed,
	);
	assert.equal(second.text, "Second one.");
});

test("chunker does not split on a decimal point", () => {
	const result = nextSpeechChunk("It costs 3. Next", 0);
	assert.equal(result.text, "It costs 3.");
	assert.equal(result.consumed, 11);
});

test("chunker does not split on a common abbreviation", () => {
	const result = nextSpeechChunk("Use e.g. this value. Done", 0);
	assert.equal(result.text, "Use e.g. this value.");
});

test("chunker does not split on an initial", () => {
	const result = nextSpeechChunk("Written by J. R. Tolkien. Yes", 0);
	assert.equal(result.text, "Written by J. R. Tolkien.");
});

test("chunker treats a blank line as a boundary", () => {
	const result = nextSpeechChunk("A paragraph\n\nAnd another", 0);
	assert.equal(result.text, "A paragraph");
});

test("chunker treats a list item start as a boundary", () => {
	const result = nextSpeechChunk("Here are three:\n- first item\n- second", 0);
	assert.equal(result.text, "Here are three:");
});

test("chunker flushes a run-on with no terminator at the cap", () => {
	const long = "word ".repeat(200);
	const result = nextSpeechChunk(long, 0);
	assert.ok(
		result.text.length > 0,
		"must not stall forever on punctuation-free text",
	);
	assert.ok(
		result.text.length <= 220,
		`chunk of ${result.text.length} exceeds the cap that dodges Chrome's long-utterance cutoff`,
	);
	assert.ok(!result.text.endsWith("wor"), "should break at a word boundary");
});

test("chunker strips markdown rather than reading it aloud", () => {
	const cases = [
		["This is **bold** text.", "This is bold text."],
		["This is *emphasis* here.", "This is emphasis here."],
		["Use `npm run` to go.", "Use npm run to go."],
		["See [the docs](https://example.com).", "See the docs."],
		["## A heading", "A heading"],
		["> a quotation", "a quotation"],
		["1. a numbered item", "a numbered item"],
		["- a bullet item", "a bullet item"],
		["This is ~~struck~~ out.", "This is struck out."],
	];
	for (const [input, expected] of cases) {
		assert.equal(stripMarkdown(input), expected, `stripMarkdown(${input})`);
	}
});

test("chunker skips a fenced code block instead of reading code", () => {
	const result = nextSpeechChunk(
		"Here is code:\n```js\nconst x = 1;\n```\nDone.",
		0,
	);
	assert.equal(result.text, "Here is code:");
	assert.ok(!result.text.includes("const"), "a code fence must not be spoken");
});

test("chunker holds back text after an unterminated code fence", () => {
	// The closing fence may still be coming, so everything past the opener is
	// unsafe to speak.
	const result = nextSpeechChunk("Here is code:\n```js\nconst x = 1;\n", 0);
	assert.equal(result.text, "Here is code:");
	assert.equal(result.consumed, 14, "must not consume into the open fence");
});

test("chunker resumes after a code block closes", () => {
	const full = "Intro line.\n```\ncode\n```\nAfterwards. And more";
	const first = nextSpeechChunk(full, 0);
	assert.equal(first.text, "Intro line.");
	const second = nextSpeechChunk(full, first.consumed);
	assert.equal(second.text, "Afterwards.");
	assert.ok(
		second.consumed > first.consumed,
		"must move past the skipped block",
	);
});

test("chunker does not stall on a reply that is only markup", () => {
	// Empty fences, a horizontal rule and a bold marker all strip down to
	// nothing. The guard loop has to step over them and still reach the prose.
	const result = nextSpeechChunk("```\n```\n---\n***\nHello there. Bye", 0);
	assert.equal(
		result.text,
		"Hello there.",
		"must skip past empty chunks and still speak",
	);
	assert.ok(result.consumed > 8, "must have advanced past the skipped markup");
});

test("chunker is a no-op on empty and whitespace input", () => {
	assert.deepEqual(nextSpeechChunk("", 0), { text: null, consumed: 0 });
	assert.deepEqual(nextSpeechChunk("   \n  ", 0), { text: null, consumed: 0 });
	assert.equal(nextSpeechChunk(undefined, 0).text, null);
});

// --- public surface --------------------------------------------------------

test("the adapter is published on window, not just as a lexical binding", () => {
	// The one that nearly shipped: a top-level `const` in a classic script is a
	// global lexical binding, NOT a property of window. Declaring the adapter
	// that way left `window.PlaygroundAudio` undefined, so all eight
	// `if (window.PlaygroundAudio)` call sites in playground.js and
	// navigation.js skipped silently — no mic shutdown on send, no speech, no
	// disarm on page leave. The guards are what made it invisible.
	const fakeWindow = {
		SpeechRecognition: function FakeRecognition() {},
		speechSynthesis: { getVoices: () => [] },
	};
	evaluate(fakeWindow);

	assert.ok(
		fakeWindow.PlaygroundAudio,
		"window.PlaygroundAudio must be set, or every guarded call site is dead code",
	);
	for (const method of [
		"arm",
		"disarm",
		"toggle",
		"onSend",
		"onDelta",
		"onStreamComplete",
		"onStreamEnd",
		"cancelSpeech",
		"isSupported",
	]) {
		assert.equal(
			typeof fakeWindow.PlaygroundAudio[method],
			"function",
			`window.PlaygroundAudio.${method} is called by name from playground.js`,
		);
	}
});

test("an unsupported browser reports false rather than throwing", () => {
	// Firefox has no SpeechRecognition. isSupported() gates the mic button, and
	// arm() has to be safe to call either way.
	const fakeWindow = { speechSynthesis: { getVoices: () => [] } };
	evaluate(fakeWindow);
	assert.equal(fakeWindow.PlaygroundAudio.isSupported(), false);
});

// --- transcript assembly ---------------------------------------------------

test("final and interim text join with a space", () => {
	const { joinTranscript } = load();
	assert.equal(
		joinTranscript("What is two plus", " two"),
		"What is two plus two",
	);
	assert.equal(joinTranscript("Complete sentence.", ""), "Complete sentence.");
	assert.equal(joinTranscript("", "interim only"), "interim only");
	assert.equal(joinTranscript("", ""), "");
});

/** The session shape the adapter keeps, fresh for one test. */
function newSession() {
	return { finalTranscript: "", interimTranscript: "", finalIndex: 0 };
}

/** A browser's cumulative results list, with the given [text, isFinal] pairs. */
function resultList(...entries) {
	return entries.map(([transcript, isFinal]) => ({
		0: { transcript },
		isFinal,
	}));
}

test("a result that finalises after being interim still reaches the transcript", () => {
	// The regression. A browser delivers a result as interim first and then
	// flips the *same entry* to isFinal. If the append pointer advances past an
	// entry merely because it was read, the completed text is never stored, the
	// send goes out empty, and audio mode silently does nothing.
	const { accumulateResult } = load();
	const session = newSession();
	const results = resultList(["what is", false]);

	// First event: interim only. Nothing can be committed yet.
	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).text,
		"what is",
		"the interim text is shown while it is still provisional",
	);
	assert.equal(
		session.finalTranscript,
		"",
		"provisional text must not be committed",
	);

	// The same entry, now final. This is the event that carries the answer.
	results[0].isFinal = true;
	results[0][0].transcript = "what is two plus two";
	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).text,
		"what is two plus two",
	);
	assert.equal(
		session.finalTranscript,
		"what is two plus two",
		"the finalised text must be committed, or there is nothing to send",
	);
});

test("a re-fired result event does not double the transcript", () => {
	// recognition.results is cumulative and resultIndex marks the first changed
	// entry, so a browser may deliver an event whose window overlaps one already
	// read. The append pointer is what stops "hello" becoming "hellohello".
	const { accumulateResult } = load();
	const session = newSession();
	const results = resultList(["hello", true]);

	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).text,
		"hello",
	);
	// The same event again — must not append.
	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).text,
		"hello",
	);
	// A genuinely new result appends, spaced even though the browser omitted it.
	results.push(resultList(["world", true])[0]);
	assert.equal(
		accumulateResult(session, { results, resultIndex: 1 }).text,
		"hello world",
	);
	// A session restart resets the pointer, so the same text is heard again.
	session.finalIndex = 0;
	session.finalTranscript = "";
	session.interimTranscript = "";
	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).text,
		"hello world",
	);
});

test("two results finalising together are appended in order, once each", () => {
	const { accumulateResult } = load();
	const session = newSession();
	const results = resultList(["First part. ", false]);

	accumulateResult(session, { results, resultIndex: 0 });
	assert.equal(
		session.finalTranscript,
		"",
		"still provisional, nothing committed",
	);

	// The browser finalises it, and a second result finalises in the same event.
	// Browsers finalise in utterance order, so both are contiguous and commit
	// together — in order, which is why the pointer stops at the first entry
	// that is not final rather than skipping ahead to any later one.
	results[0].isFinal = true;
	results.push(resultList(["Second part.", true])[0]);
	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).text,
		"First part. Second part.",
	);
	assert.equal(session.finalIndex, 2, "both entries accounted for");

	// Re-firing must not duplicate either.
	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).text,
		"First part. Second part.",
	);
});

test("adjacent final results are spaced even when the browser omits whitespace", () => {
	const { accumulateResult } = load();
	const session = newSession();
	const results = resultList(
		["What is", true],
		["two plus", true],
		["two.", true],
	);

	accumulateResult(session, { results, resultIndex: 0 });
	accumulateResult(session, { results, resultIndex: 1 });
	accumulateResult(session, { results, resultIndex: 2 });
	assert.equal(
		session.finalTranscript,
		"What is two plus two.",
		"one commit, one space between — not 'What istwo plustwo.'",
	);
});

// --- what counts as new speech ----------------------------------------------

test("a re-delivered result reports no change, so it is not treated as speech", () => {
	// The contract that fixes the countdown restart. Once an entry has
	// finalised, `session.finalTranscript` is non-empty forever, so "is there
	// text to show" cannot answer "is the user still talking". A browser in
	// continuous mode re-delivers the same settled event repeatedly, and
	// re-arming the silence timer on those is what made the grace countdown
	// restart (3, 2, 3, 2, 1) and delayed the send indefinitely.
	const { accumulateResult } = load();
	const session = newSession();
	const results = resultList(["hello world", true]);

	const first = accumulateResult(session, { results, resultIndex: 0 });
	assert.equal(first.text, "hello world");
	assert.equal(first.changed, true, "a newly finalised entry is real speech");

	for (let i = 0; i < 3; i++) {
		const again = accumulateResult(session, { results, resultIndex: 0 });
		assert.equal(again.text, "hello world", "the transcript is still shown");
		assert.equal(
			again.changed,
			false,
			`re-delivery ${i + 1} carried no new text and must not count as activity`,
		);
	}
});

test("each interim update reports change while the utterance is still growing", () => {
	const { accumulateResult } = load();
	const session = newSession();
	const results = resultList(["what is", false]);

	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).changed,
		true,
		"first interim text",
	);

	results[0][0].transcript = "what is two";
	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).changed,
		true,
		"a growing interim transcript is real speech and must keep pushing the deadline out",
	);

	// Finalising commits the text, which is a change in its own right.
	results[0].isFinal = true;
	results[0][0].transcript = "what is two plus two";
	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).changed,
		true,
	);
	assert.equal(session.finalTranscript, "what is two plus two");

	assert.equal(
		accumulateResult(session, { results, resultIndex: 0 }).changed,
		false,
		"and once final, re-delivery is no longer activity",
	);
});

test("re-fired results do not restart the grace countdown", () => {
	// The end-to-end consequence, wired the way handleResult wires it. (It
	// mirrors that function rather than calling it: handleResult reaches module
	// state set by arm() and the DOM, neither of which exist here. The
	// accumulateResult contract above is what actually pins the source.)
	const { accumulateResult, createSilenceTimer } = load();
	const session = newSession();
	const results = resultList(["hello world", true]);
	const clock = fakeClock();
	const events = [];

	const timer = createSilenceTimer({
		silenceMs: 100,
		graceMs: 1000,
		onPending: () => events.push("pending"),
		onSend: () => events.push("send"),
		setTimeout: clock.setTimeout,
		clearTimeout: clock.clearTimeout,
	});

	const deliver = (event) => {
		const { text, changed } = accumulateResult(session, event);
		if (!text) return;
		if (changed) timer.noteActivity();
	};

	// The utterance is recognised and finalises.
	deliver({ results, resultIndex: 0 });
	assert.equal(session.finalTranscript, "hello world");
	assert.equal(timer.state, "waiting");

	clock.advance(100);
	assert.deepEqual(events, ["pending"], "silence elapsed, grace is open");

	// Now the browser re-delivers that same settled event over and over, with
	// nobody speaking at all.
	for (let i = 0; i < 6; i++) {
		clock.advance(100);
		deliver({ results, resultIndex: 0 });
	}

	assert.equal(
		events.filter((e) => e === "pending").length,
		1,
		"a re-delivered result must not open a second grace window",
	);
	assert.equal(
		events.includes("send"),
		false,
		"the first grace is still counting",
	);

	clock.advance(1000);
	assert.deepEqual(
		events,
		["pending", "send"],
		"the send lands on the original grace, not on a later one",
	);
});

// --- silence timer ---------------------------------------------------------

test("silence timer sends after silence with no new text", () => {
	const { createSilenceTimer } = load();
	const clock = fakeClock();
	const events = [];

	const timer = createSilenceTimer({
		silenceMs: 100,
		graceMs: 50,
		onPending: () => events.push("pending"),
		onSend: () => events.push("send"),
		setTimeout: clock.setTimeout,
		clearTimeout: clock.clearTimeout,
	});

	timer.noteActivity();
	clock.advance(99);
	assert.deepEqual(events, [], "must not fire early");

	clock.advance(1);
	assert.deepEqual(events, ["pending"], "grace opens at the silence boundary");
	assert.equal(timer.state, "pending");

	clock.advance(50);
	assert.deepEqual(events, ["pending", "send"]);
	assert.equal(timer.state, "idle");
});

test("silence timer resets on every new result", () => {
	const { createSilenceTimer } = load();
	const clock = fakeClock();
	let sends = 0;

	const timer = createSilenceTimer({
		silenceMs: 100,
		graceMs: 50,
		onSend: () => {
			sends += 1;
		},
		setTimeout: clock.setTimeout,
		clearTimeout: clock.clearTimeout,
	});

	// Someone speaking in bursts: each result pushes the deadline out.
	for (let i = 0; i < 5; i++) {
		clock.advance(60);
		timer.noteActivity();
	}
	assert.equal(sends, 0, "a continuous speaker must never trip the timer");

	clock.advance(100);
	clock.advance(50);
	assert.equal(sends, 1);
});

test("speaking during the grace window abandons the send", () => {
	const { createSilenceTimer } = load();
	const clock = fakeClock();
	const events = [];

	// silenceMs is long here so the only thing under test is the grace window
	// being abandoned, not the timer re-arming.
	const timer = createSilenceTimer({
		silenceMs: 10000,
		graceMs: 100,
		onPending: () => events.push("pending"),
		onSend: () => events.push("send"),
		setTimeout: clock.setTimeout,
		clearTimeout: clock.clearTimeout,
	});

	timer.noteActivity();
	clock.advance(10000);
	assert.deepEqual(events, ["pending"]);

	// The user notices the mishearing and carries on talking.
	timer.noteActivity();
	clock.advance(1000);
	assert.deepEqual(
		events,
		["pending"],
		"the abandoned send must not fire at the end of the original grace",
	);
	assert.equal(timer.state, "waiting", "it is back to waiting for silence");

	// And it still sends, once the user really does stop. Note the second
	// "pending": abandoning the send re-arms silence, so a fresh grace opens
	// rather than the abandoned one resuming.
	clock.advance(10000);
	assert.deepEqual(events, ["pending", "pending"]);
	clock.advance(100);
	assert.deepEqual(events, ["pending", "pending", "send"]);
	assert.equal(timer.state, "idle");
});

test("cancel drops a pending send", () => {
	const { createSilenceTimer } = load();
	const clock = fakeClock();
	let sends = 0;

	const timer = createSilenceTimer({
		silenceMs: 100,
		graceMs: 50,
		onSend: () => {
			sends += 1;
		},
		setTimeout: clock.setTimeout,
		clearTimeout: clock.clearTimeout,
	});

	timer.noteActivity();
	clock.advance(100);
	timer.cancel();
	assert.equal(timer.state, "idle");
	clock.advance(1000);
	assert.equal(sends, 0);
});

test("silence timer defaults match the shipped constants", () => {
	const { createSilenceTimer, SILENCE_MS, GRACE_MS, RESTART_DELAY_MS } = load();
	assert.equal(SILENCE_MS, 1300);
	assert.equal(
		GRACE_MS,
		2600,
		"the grace window is what makes a misheard word cheap",
	);
	assert.ok(
		RESTART_DELAY_MS > 0 && RESTART_DELAY_MS <= 250,
		`restart delay is ${RESTART_DELAY_MS}ms; it is the half of the mic-reopen gap we control, and raising it clips the first word back`,
	);

	// Constructed with no timing options at all, the defaults must still apply.
	const timer = createSilenceTimer({});
	assert.equal(timer.state, "idle");
	timer.noteActivity();
	assert.equal(timer.state, "waiting");
	timer.cancel();
});

// --- restart gate ----------------------------------------------------------

test("mic reopens only when the stream and the speech are both done", () => {
	const { shouldRestartListening } = load();

	assert.equal(
		shouldRestartListening({ armed: true, streamDone: true, speechDone: true }),
		true,
	);
	assert.equal(
		shouldRestartListening({
			armed: true,
			streamDone: true,
			speechDone: false,
		}),
		false,
		"the last sentence is usually still being read when the last delta lands",
	);
	assert.equal(
		shouldRestartListening({
			armed: true,
			streamDone: false,
			speechDone: true,
		}),
		false,
	);
	assert.equal(
		shouldRestartListening({
			armed: true,
			streamDone: false,
			speechDone: false,
		}),
		false,
	);
});

test("mic never reopens once disarmed", () => {
	const { shouldRestartListening } = load();
	assert.equal(
		shouldRestartListening({
			armed: false,
			streamDone: true,
			speechDone: true,
		}),
		false,
	);
	assert.equal(shouldRestartListening(null), false);
});

// --- voice pick ------------------------------------------------------------

test("voice pick prefers a local voice for the page language", () => {
	const { resolveVoice } = load();
	const voices = [
		{ name: "Daniel", lang: "en-GB", localService: false },
		{ name: "Alex", lang: "en-US", localService: true },
	];
	assert.equal(resolveVoice(voices, "en-US").name, "Alex");
});

test("voice pick matches a regional variant of the language", () => {
	const { resolveVoice } = load();
	const voices = [
		{ name: "Kyoko", lang: "ja-JP", localService: true },
		{ name: "Moira", lang: "en-IE", localService: true },
	];
	assert.equal(resolveVoice(voices, "en-US").name, "Moira");
});

test("voice pick falls back rather than returning nothing", () => {
	const { resolveVoice } = load();
	assert.equal(resolveVoice([], "en-US"), null, "no voices loaded yet");
	assert.equal(resolveVoice(null, "en-US"), null);
	assert.equal(
		resolveVoice(
			[{ name: "Zosia", lang: "pl-PL", localService: true }],
			"en-US",
		).name,
		"Zosia",
		"a wrong-language voice beats speaking in no voice at all",
	);
	assert.equal(
		resolveVoice(
			[{ name: "Remote", lang: "en-US", localService: false }],
			"en-US",
		).name,
		"Remote",
	);
});
