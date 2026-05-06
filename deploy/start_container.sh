#!/usr/bin/env bash
set -euo pipefail

# Support CONFIG_DIR environment variable (default: /app)
CONFIG_DIR="${CONFIG_DIR:-/app}"
CONFIG_PATH="${CONFIG_DIR}/config.yaml"
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
    if [[ -f "${PID_FILE}" ]]; then
        LITELLM_PID=$(cat "${PID_FILE}")
        kill "${LITELLM_PID}" 2>/dev/null || true
        rm -f "${PID_FILE}"
    fi
    exit 0
}

trap cleanup SIGTERM SIGINT

# Initialize configs
init_configs

# Start LiteLLM proxy in background
echo "Starting LiteLLM proxy on port 4000..."
litellm --config "${CONFIG_PATH}" --port 4000 --host 0.0.0.0 > "${CONFIG_DIR}/litellm.log" 2>&1 &
LITELLM_PID=$!
echo "${LITELLM_PID}" > "${PID_FILE}"
echo "LiteLLM started with PID ${LITELLM_PID}"

# Wait for LiteLLM to be healthy (up to 60s)
echo "Waiting for LiteLLM to become healthy..."
for i in $(seq 1 60); do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:4000/health --max-time 2 | grep -q "200"; then
        echo "LiteLLM is healthy after ${i}s"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "Warning: LiteLLM did not become healthy within 60s, continuing anyway"
    fi
    sleep 1
done

# Start Management UI (foreground)
echo "Starting Management UI on port 8282..."
export CONFIG_DIR="${CONFIG_DIR}"
uvicorn management_app:app --host 0.0.0.0 --port 8282
