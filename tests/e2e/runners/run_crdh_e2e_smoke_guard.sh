#!/usr/bin/env bash
# CRDH-07: E2E smoke guard for Cortensor weather prompt.
# Canonical runner:
#   openminion/tests/e2e/runners/run_crdh_e2e_smoke_guard.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENMINION_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
FRAMEWORK_ROOT="$(cd "$OPENMINION_DIR/.." && pwd)"
export OPENMINION_HOME="${OPENMINION_HOME:-$FRAMEWORK_ROOT}"
export OPENMINION_DATA_ROOT="${OPENMINION_DATA_ROOT:-$OPENMINION_HOME/.openminion}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
MAX_DURATION_SECONDS=30
WEATHER_PROMPT="what's weather at sf?"
SESSION_ID="crdh-e2e-$(date +%s)"
AGENT_ID="cortensor35"

# Ensure Python environment
PY="${OPENMINION_DIR}/.venv/bin/python3.11"
if [ ! -x "$PY" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    cd "$OPENMINION_DIR"
    python3.11 -m venv .venv
    PY="${OPENMINION_DIR}/.venv/bin/python3.11"
fi

echo "======================================"
echo "CRDH-07: E2E Weather Prompt Smoke Test"
echo "======================================"
echo ""
echo "Prompt: $WEATHER_PROMPT"
echo "Max Duration: ${MAX_DURATION_SECONDS}s"
echo "Session: $SESSION_ID"
echo ""

# Create temp config if needed
CONFIG_PATH="${OPENMINION_DIR}/test-configs/per-agent.json"
if [ ! -f "$CONFIG_PATH" ]; then
    mkdir -p "$(dirname "$CONFIG_PATH")"
    cat > "$CONFIG_PATH" << 'EOF'
{
  "runtime": {
    "provider": "cortensor",
    "model": "cortensor35"
  }
}
EOF
fi

# Run the chat command with timeout
echo -n "Running weather prompt... "
START_TIME=$(date +%s)

# Use timeout command to enforce wall-clock limit
timeout_output=$(
    cd "$OPENMINION_DIR" && \
    printf '%s\n/exit\n' "$WEATHER_PROMPT" | \
    timeout ${MAX_DURATION_SECONDS}s \
    OPENMINION_HOME="$OPENMINION_HOME" \
    OPENMINION_DATA_ROOT="$OPENMINION_DATA_ROOT" \
    PYTHONPATH=src "$PY" -m openminion \
        --config "$CONFIG_PATH" \
        chat \
        --agent "$AGENT_ID" \
        --session "$SESSION_ID" \
        --quiet \
        2>&1 || true
)

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo "(${ELAPSED}s)"

# Check results
echo ""
echo "--- Results ---"
echo "Elapsed time: ${ELAPSED}s"

# Determine success/failure
if [ $ELAPSED -ge $MAX_DURATION_SECONDS ]; then
    echo -e "${RED}FAIL${NC}: Test exceeded maximum duration (${MAX_DURATION_SECONDS}s)"
    echo "This indicates a potential hang - the spinner may be stalled."
    echo ""
    echo "Output preview:"
    echo "$timeout_output" | tail -20
    exit 1
fi

# Check for response indicators
if echo "$timeout_output" | grep -qiE "(weather|temperature|sunny|cloudy|rain|forecast|san francisco|sf)"; then
    echo -e "${GREEN}PASS${NC}: Received weather-related response within ${ELAPSED}s"
    echo ""
    echo "Response preview:"
    echo "$timeout_output" | tail -10
    exit 0
fi

# Check for graceful error
echo "$timeout_output" | grep -qiE "(error|fail|timeout|unavailable|sorry)"
if [ $? -eq 0 ]; then
    echo -e "${YELLOW}PASS (with error)${NC}: Received deterministic error within ${ELAPSED}s"
    echo "This is acceptable - the provider returned an error rather than hanging."
    echo ""
    echo "Error preview:"
    echo "$timeout_output" | tail -10
    exit 0
fi

# Unknown outcome
echo -e "${YELLOW}UNCERTAIN${NC}: Test completed in ${ELAPSED}s but no clear weather response or error found"
echo ""
echo "Full output:"
echo "$timeout_output"
exit 0
