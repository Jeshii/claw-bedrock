#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="/app/config.yaml"
BEDROCK_CONFIG="/app/config.bedrock.yaml"
LOCAL_CONFIG="/app/config.local.yaml"

# Merge configuration files
merge_configs() {
    echo "Merging configuration files..."
    > "${CONFIG_PATH}"
    
    if [[ -f "${BEDROCK_CONFIG}" ]]; then
        echo "Adding base Bedrock configuration..."
        cat "${BEDROCK_CONFIG}" > "${CONFIG_PATH}"
        echo "" >> "${CONFIG_PATH}"
    fi
    
    if [[ -f "${LOCAL_CONFIG}" ]]; then
        echo "Adding local configuration overrides..."
        tail -n +2 "${LOCAL_CONFIG}" >> "${CONFIG_PATH}"
    fi
    
    echo "Configuration merged to ${CONFIG_PATH}"
}

# Handle shutdown signals
cleanup() {
    echo "Shutting down..."
    if [[ -n "${LITELLM_PID:-}" ]]; then
        kill "${LITELLM_PID}" 2>/dev/null || true
    fi
    exit 0
}

trap cleanup SIGTERM SIGINT

# Merge configs
merge_configs

# Start LiteLLM proxy in background
echo "Starting LiteLLM proxy on port 4000..."
litellm --config "${CONFIG_PATH}" --port 4000 --host 0.0.0.0 > /app/litellm.log 2>&1 &
LITELLM_PID=$!

# Start Management UI (foreground)
echo "Starting Management UI on port 8080..."
uvicorn management_app:app --host 0.0.0.0 --port 8080
