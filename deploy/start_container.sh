#!/usr/bin/env bash
set -euo pipefail

# Support CONFIG_DIR environment variable (default: /app)
CONFIG_DIR="${CONFIG_DIR:-/app}"
CONFIG_PATH="${CONFIG_DIR}/config.yaml"
export CONFIG_PATH  # Make available to child processes
BEDROCK_MODELS="${CONFIG_DIR}/bedrock_models.json"
DB_PATH="${CONFIG_DIR}/models.db.json"
PID_FILE="/tmp/litellm.pid"

# Initialize default configs if they don't exist
init_configs() {
    # Copy bedrock models catalog if it doesn't exist
    if [[ ! -f "${BEDROCK_MODELS}" ]] && [[ -f "/app/bedrock_models.json" ]]; then
        echo "Copying bedrock models catalog to ${BEDROCK_MODELS}..."
        cp "/app/bedrock_models.json" "${BEDROCK_MODELS}"
    fi

    # Always copy token_refresher so it stays in sync with the image
    if [[ -f "/app/token_refresher.py" ]]; then
        cp "/app/token_refresher.py" "${CONFIG_DIR}/token_refresher.py"
    fi
}

# Handle shutdown signals
cleanup() {
    echo "Shutting down..."
    if [[ -n "${MGMT_PID:-}" ]]; then
        kill "${MGMT_PID}" 2>/dev/null || true
    fi
    if [[ -f "${PID_FILE}" ]]; then
        LITELLM_PID=$(cat "${PID_FILE}")
        kill "${LITELLM_PID}" 2>/dev/null || true
        rm -f "${PID_FILE}"
    fi
    exit 0
}

trap cleanup SIGTERM SIGINT

# Duplicate all stdout/stderr to container.log (podman logs -f still works)
exec > >(tee -a "${CONFIG_DIR}/container.log") 2>&1

# Initialize configs
init_configs

# Start Management UI in background (so the web interface is available immediately)
echo "Starting Management UI on port 8282..."
export CONFIG_DIR="${CONFIG_DIR}"
uvicorn management_app:app --host 0.0.0.0 --port 8282 &
MGMT_PID=$!
echo "Management UI started with PID ${MGMT_PID}"

# Start LiteLLM proxy in background
echo "Starting LiteLLM proxy on port 4000..."
litellm --config "${CONFIG_PATH}" --port 4000 --host 0.0.0.0 > "${CONFIG_DIR}/litellm.log" 2>&1 &
LITELLM_PID=$!
echo "${LITELLM_PID}" > "${PID_FILE}"
echo "LiteLLM started with PID ${LITELLM_PID}"

# Wait for LiteLLM to be healthy (up to 60s)
echo "Waiting for LiteLLM to become healthy..."
for i in $(seq 1 60); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:4000/health', timeout=2)" 2>/dev/null; then
        echo "LiteLLM is healthy after ${i}s"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "Warning: LiteLLM did not become healthy within 60s, continuing anyway"
    fi
    sleep 1
done

# Bring Management UI to foreground to keep the container alive
wait ${MGMT_PID}
