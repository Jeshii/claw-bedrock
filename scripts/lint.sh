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
step "node --test"   node --test tests/

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
