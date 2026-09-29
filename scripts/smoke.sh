#!/usr/bin/env bash
# Boots the image and asserts it actually serves. Single source of truth for
# the smoke test, called by .github/workflows/build-container.yml before the
# image is pushed, and runnable locally the same way.
#
# This exists because `docker build` never runs ENTRYPOINT. An image can build
# clean, pass every unit test, and still be unbootable: a module missing from
# the image only surfaces as an ImportError in the container log, after the
# image has been published and someone has restarted their service onto it.
#
# Usage:
#   ./scripts/smoke.sh                      build and test
#   IMAGE=ghcr.io/jeshii/claw-bedrock:develop SKIP_BUILD=1 ./scripts/smoke.sh
#                                           test an existing image
#
# Requires: podman or docker, curl, python3
set -uo pipefail
cd "$(dirname "$0")/.."

ENGINE="${CONTAINER_ENGINE:-}"
if [ -z "$ENGINE" ]; then
    if command -v podman > /dev/null 2>&1; then
        ENGINE=podman
    else
        ENGINE=docker
    fi
fi

IMAGE="${IMAGE:-claw-bedrock:smoke}"
NAME="${NAME:-claw-bedrock-smoke}"
# High ports so this never collides with a real instance on 8282/4000.
MGMT_PORT="${MGMT_PORT:-18282}"
LITELLM_PORT="${LITELLM_PORT:-14000}"
# management_app.py budgets up to 60s for a slow LiteLLM start, and LiteLLM's
# first launch races the config write and is retried by the watchdog, so allow
# comfortably more than either. Flaky timeouts here get muted, and a muted
# smoke test is the gap this script was added to close.
TIMEOUT="${TIMEOUT:-180}"

status=0

cleanup() {
    "$ENGINE" rm -f "$NAME" > /dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
    echo "FAIL: $*" >&2
    status=1
}

# Dump enough to tell "slow to start" apart from "genuinely broken". The
# listing is what names a missing module outright.
diagnose() {
    echo "--- container logs ---" >&2
    "$ENGINE" logs "$NAME" 2>&1 | tail -60 >&2
    echo "--- /app contents ---" >&2
    "$ENGINE" exec "$NAME" ls /app 2>&1 >&2
}

if [ -z "${SKIP_BUILD:-}" ]; then
    echo "== building $IMAGE with $ENGINE"
    "$ENGINE" build -f .github/Containerfile -t "$IMAGE" . || exit 1
fi

echo "== starting $NAME"
"$ENGINE" rm -f "$NAME" > /dev/null 2>&1
"$ENGINE" run -d --name "$NAME" \
    -p "${MGMT_PORT}:8282" -p "${LITELLM_PORT}:4000" \
    "$IMAGE" > /dev/null || exit 1

# The app serves templates/ and /static/ from disk, so a successful response
# also proves those directories were copied.
echo "== waiting for the management UI on :$MGMT_PORT"
ready=0
for _ in $(seq "$TIMEOUT"); do
    if curl -fsS "http://127.0.0.1:${MGMT_PORT}/api/version" > /dev/null 2>&1; then
        ready=1
        break
    fi
    # The entrypoint is `wait ${MGMT_PID}`, so an app that dies during import
    # takes the container down with it. Without this the loop would burn the
    # whole timeout against a container that is already gone.
    if ! "$ENGINE" inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -q true; then
        fail "container exited during startup"
        diagnose
        exit 1
    fi
    sleep 1
done

if [ "$ready" -ne 1 ]; then
    fail "management UI did not answer within ${TIMEOUT}s"
    diagnose
    exit 1
fi

# Asserts the image contains what the app imports. A module omitted from the
# Containerfile dies at import, so this is the assertion that would have caught
# the missing settings_resolver.py.
echo "== checking /api/version"
version=$(curl -fsS "http://127.0.0.1:${MGMT_PORT}/api/version" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' 2>/dev/null)

if [ -z "$version" ] || [ "$version" = "unknown" ]; then
    fail "no version reported; /app/VERSION was not baked in"
    diagnose
    exit 1
fi
echo "   version: $version"

echo "== waiting for LiteLLM on :$LITELLM_PORT"
# Asserts the *liveliness* endpoint, not /health.
#
# /api/health/litellm proxies LiteLLM's /health, which is an active check: it
# makes a real call to every model in model_list unless
# general_settings.background_health_checks is set, which this config does not
# set. It is therefore coupled to provider credentials, and with no models
# configured it passes vacuously — which is exactly what it did in CI, where the
# assertion proved nothing about serving requests.
#
# /health/liveliness only reports whether the proxy process is up, so it cannot
# go red for reasons unrelated to "did this image boot". That is the question
# this script exists to answer. Whether Bedrock can actually serve is checked on
# the dashboard, where a red /health means what it says.
#
# The upstream status code is asserted too: management_app.py returns
# status: ok whenever the probe does not *raise*, whatever code it got back.
litellm_ok=0
for _ in $(seq "$TIMEOUT"); do
    body=$(curl -fsS "http://127.0.0.1:${MGMT_PORT}/api/health/litellm/liveliness" 2>/dev/null)
    if [ -n "$body" ] && echo "$body" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)
sys.exit(0 if d.get("status") == "ok" and d.get("litellm_status") == 200 else 1)
' 2> /dev/null; then
        litellm_ok=1
        break
    fi
    sleep 1
done

if [ "$litellm_ok" -ne 1 ]; then
    fail "LiteLLM did not become live within ${TIMEOUT}s"
    diagnose
    exit 1
fi

if [ "$status" -eq 0 ]; then
    printf '\nsmoke test passed\n'
fi
exit "$status"
