#!/usr/bin/env bash
# Starts the LiteLLM proxy server with AWS token refresh capability

# Exit on any error or undefined variable
set -euo pipefail

CONFIG_FILE="config.yaml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source environment if available
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    source "${SCRIPT_DIR}/.env"
fi

# Merge config.bedrock.yaml and config.local.yaml into config.yaml
merge_configs() {
    local bedrock_file="${SCRIPT_DIR}/config.bedrock.yaml"
    local local_file="${SCRIPT_DIR}/config.local.yaml"
    local output_file="${SCRIPT_DIR}/config.yaml"
    
    echo "🔧 Merging configuration files..."
    
    # Start with empty configuration
    echo "" > "${output_file}"
    
    # Add bedrock config if it exists
    if [[ -f "${bedrock_file}" ]]; then
        echo "   Adding base configuration from config.bedrock.yaml"
        cat "${bedrock_file}" >> "${output_file}"
        echo "" >> "${output_file}"  # Add newline between configs
    fi
    
    # Add/local override config if it exists (skip first line to avoid duplicate model_list header)
    if [[ -f "${local_file}" ]]; then
        echo "   Adding local overrides from config.local.yaml (skipping first line)"
        # Skip the first line of local file to avoid duplicate model_list header
        tail -n +2 "${local_file}" >> "${output_file}"
    fi
    
    if [[ ! -f "${bedrock_file}" && ! -f "${local_file}" ]]; then
        echo "⚠️  Warning: Neither config.bedrock.yaml nor config.local.yaml found"
        echo "   Using empty configuration"
    fi
    
    echo "   Merged configuration written to ${output_file}"
}

# Function to start the server
start_server() {
    echo "🚀 Starting LiteLLM proxy server..."
    echo "   Configuration: ${CONFIG_FILE}"
    echo "   AWS Profile: ${AWS_PROFILE:-default}"
    echo "   Region: ${AWS_REGION:-us-east-1}"
    
    # Export for Python access
    export AWS_PROFILE="${AWS_PROFILE:-default}"
    export AWS_REGION="${AWS_REGION:-us-east-1}"
    
    # Start the server and capture exit code
    pipenv run litellm --config "${CONFIG_FILE}"
    EXIT_CODE=$?
    
    # Exit code 42 means we need to re-authenticate
    if [[ ${EXIT_CODE} -eq 42 ]]; then
        echo -e "\n🔔 Authentication required"
        echo "   Your AWS session has expired. Please authenticate:"
        echo "   1. Open your browser"
        echo "   2. Run: ./start.sh (this will prompt for authentication)"
        echo "   3. Complete the AWS SSO login flow"
        
        # Wait a moment before exit to ensure user sees message
        sleep 2
        exit ${EXIT_CODE}
    elif [[ ${EXIT_CODE} -ne 0 ]]; then
        echo -e "\n❌ Server exited with error code: ${EXIT_CODE}"
        exit ${EXIT_CODE}
    fi
}

# Auto-restart on auth failure with exponential backoff
MAX_RETRIES=3
RETRY_COUNT=0

# Merge configs before starting
merge_configs

while [[ ${RETRY_COUNT} -le ${MAX_RETRIES} ]]; do
    start_server
    # If we get here, server exited with code 42
    RETRY_COUNT=$((RETRY_COUNT + 1))
    
    if [[ ${RETRY_COUNT} -le ${MAX_RETRIES} ]]; then
        echo -e "\n🔄 Attempt ${RETRY_COUNT}/${MAX_RETRIES}: Waiting for re-authentication..."
        sleep 10
    fi
done

echo -e "\n❌ Maximum retry attempts reached. Please run './start.sh' manually to re-authenticate."
exit 42
