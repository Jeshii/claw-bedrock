#!/usr/bin/env bash
# Single source of truth for the lint checks. Run this before committing and
# from CI so the two cannot drift.
#
# Deliberately no git hook: this project installs nothing into .git/hooks, so
# feedback comes from running this directly (or from your editor's language
# servers). See AGENTS.md.
#
# Requires ruff, djlint and biome on PATH:
#   pip install -r requirements-dev.txt   # ruff, djlint
#   npm install -g @biomejs/biome@2.5.14  # biome
set -uo pipefail
cd "$(dirname "$0")/.."

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
