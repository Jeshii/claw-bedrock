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
