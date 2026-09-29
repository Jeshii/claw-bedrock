#!/usr/bin/env bash
# Single source of truth for the lint checks. Run this before committing and
# from CI so the two cannot drift.
#
# Deliberately no git hook: this project installs nothing into .git/hooks, so
# feedback comes from running this directly (or from your editor's language
# servers). See AGENTS.md.
#
# Toolchain (biome is not pip-installable, so it stays global):
#   .venv/bin/pip install -r requirements-dev.txt      # ruff, djlint, pytest
#   npm install -g @biomejs/biome@2.5.14               # biome
set -uo pipefail
cd "$(dirname "$0")/.."

# Prefer the pinned toolchain in .venv over whatever is on PATH. Both can be
# present, and a Homebrew djlint silently answering for the pinned one means a
# version drift shows up as a pass instead of a failure. CI has no .venv, so
# this is a no-op there and the PATH lookup stands.
if [ -d .venv/bin ]; then
    PATH=".venv/bin:$PATH"
    export PATH
fi

# Fail loudly and specifically if a tool is absent, rather than letting the
# shell report "command not found" from inside a step() several lines later.
# node is only needed for the Playground Audio Mode unit tests, which cover the
# two pure functions that carry a feature which otherwise cannot be exercised
# headlessly. It ships with the runners, so this costs CI nothing.
for tool in ruff djlint biome node; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "lint.sh: '$tool' not found." >&2
        case "$tool" in
            biome) echo "  npm install -g @biomejs/biome@2.5.14" >&2 ;;
            node)  echo "  brew install node  (or any Node 20+)" >&2 ;;
            *)     echo "  .venv/bin/pip install -r requirements-dev.txt" >&2 ;;
        esac
        exit 127
    fi
done

status=0

# Accumulate rather than fail fast: one local run should report everything that
# needs fixing, not just the first problem.
step() {
    printf '\n== %s\n' "$1"
    shift
    "$@" || status=1
}

step "ruff check"    ruff check
step "ruff format"   ruff format --check
# Summary reporter only: same exit codes, but it names the file and rule
# instead of printing every offending line.
step "biome"         biome ci . --reporter=summary
step "djlint lint"   djlint templates/
step "djlint format" djlint templates/ --check

# No test runner for JS otherwise, and no dependencies to install: node ships
# its own. These cover the sentence chunker and the silence timer, which are the
# parts of audio mode that can be tested without a microphone.
#
# The glob is deliberate and not shorthand. A bare directory argument is not
# portable across node majors: node 22 loads "tests" as a module path and dies
# with MODULE_NOT_FOUND, while node 26 searches it. That cost a red build once,
# because CI pins 22 and a developer's local node is usually newer. Shell
# expansion gives node an explicit file list, which behaves the same on every
# major.
#
# The no-match guard is load-bearing, and not belt-and-braces. An unmatched glob
# is *not* a loud failure: node reads the literal pattern, finds nothing, and
# exits 0 having run zero tests. So renaming or moving the test file would turn
# this step into a silent pass — the exact failure mode the exit-127 check above
# exists to prevent, one layer down. nullglob expands the pattern to zero words
# instead of a literal, which is what makes the count checkable.
shopt -s nullglob
js_tests=(tests/*.test.mjs)
shopt -u nullglob
if [ "${#js_tests[@]}" -eq 0 ]; then
    echo "no files matched tests/*.test.mjs -- refusing to report a pass" >&2
    status=1
else
    # The version in the step header is the local one, which is the point: when
    # it disagrees with the node-version in .github/workflows/lint.yml, that is
    # visible in the log instead of something to deduce from a stack trace.
    step "node --test ($(node --version))" node --test "${js_tests[@]}"
fi

# Guards the failure mode the build-deps skill documents: a hand-edited or
# badly-merged lock carries duplicate pins and only surfaces as
# ResolutionImpossible during the image build.
printf '\n== requirements.lock\n'
dupes=$(awk -F'==' '/^[A-Za-z0-9]/ {
    name = $1
    sub(/\[[^]]*\]/, "", name)
    sub(/[=<>!~ ]/, "", name)
    print tolower(name)
}' requirements.lock | sort | uniq -d)

if [ -n "$dupes" ]; then
    echo "duplicate pins in requirements.lock -- regenerate with pip-compile:"
    echo "$dupes"
    status=1
else
    echo "no duplicate pins"
fi

if [ "$status" -eq 0 ]; then
    printf '\nall checks passed\n'
fi
exit "$status"
