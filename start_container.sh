#!/usr/bin/env bash
set -euo pipefail

# Support CONFIG_DIR environment variable (default: /app)
CONFIG_DIR="${CONFIG_DIR:-/app}"
CONFIG_PATH="${CONFIG_DIR}/config.yaml"
BEDROCK_CONFIG="${CONFIG_DIR}/config.bedrock.yaml"
LOCAL_CONFIG="${CONFIG_DIR}/config.local.yaml"
PID_FILE="/tmp/litellm.pid"

# Initialize default configs if they don't exist
init_configs() {
    # Create config.local.yaml if it doesn't exist
    if [[ ! -f "${LOCAL_CONFIG}" ]]; then
        echo "Initializing ${LOCAL_CONFIG}..."
        echo '{"model_list": []}' > "${LOCAL_CONFIG}"
    fi

    # Copy bedrock config template if it doesn't exist
    if [[ ! -f "${BEDROCK_CONFIG}" ]] && [[ -f "/app/config.bedrock.yaml" ]]; then
        echo "Copying bedrock config template to ${BEDROCK_CONFIG}..."
        cp "/app/config.bedrock.yaml" "${BEDROCK_CONFIG}"
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

# Start Management UI (foreground)
echo "Starting Management UI on port 8282..."
export CONFIG_DIR="${CONFIG_DIR}"
uvicorn management_app:app --host 0.0.0.0 --port 8282
