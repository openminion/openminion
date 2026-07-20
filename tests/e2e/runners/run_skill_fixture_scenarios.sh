#!/bin/bash
# SEFV-08: Helper script to run all skill fixture scenarios non-interactively
# and emit pass/fail summary.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRAMEWORK_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
OPENMINION_DIR="${FRAMEWORK_ROOT}/openminion"
FIXTURES_DIR="${FRAMEWORK_ROOT}/openminion/examples/skills/cli-chat-smoke"
INVALID_FIXTURES_DIR="${FRAMEWORK_ROOT}/openminion/examples/skills/cli-chat-smoke-invalid"
export OPENMINION_HOME="${OPENMINION_HOME:-$OPENMINION_DIR}"
export OPENMINION_DATA_ROOT="${OPENMINION_DATA_ROOT:-$OPENMINION_HOME/.openminion}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Ensure Python environment
PY="${OPENMINION_DIR}/.venv/bin/python3.11"
if [ ! -x "$PY" ]; then
    echo "Creating virtual environment..."
    cd "$OPENMINION_DIR"
    python3.11 -m venv .venv
    PY="${OPENMINION_DIR}/.venv/bin/python3.11"
fi

# Results tracking
PASSED=0
FAILED=0
TOTAL=0

# Function to run a scenario
run_scenario() {
    local name="$1"
    local commands="$2"
    local session="$3"
    local expect_fail="${4:-false}"

    TOTAL=$((TOTAL + 1))

    echo -n "Running $name... "

    output=$(cd "$OPENMINION_DIR" && OPENMINION_HOME="$OPENMINION_HOME" OPENMINION_DATA_ROOT="$OPENMINION_DATA_ROOT" PYTHONPATH=src "$PY" -m openminion \
        --config test-configs/per-agent.json \
        --agent test-agent --session "$session" --verbosity quiet 2>&1 <<< "$commands" || true)

    if [ "$expect_fail" = "true" ]; then
        # For negative tests, we expect an error message
        if echo "$output" | grep -qiE "(error|failed|parse)"; then
            echo -e "${GREEN}PASS${NC} (expected failure)"
            PASSED=$((PASSED + 1))
        else
            echo -e "${RED}FAIL${NC} (expected error but got success)"
            FAILED=$((FAILED + 1))
            echo "  Output: $output"
        fi
    else
        # For positive tests, we expect success
        if echo "$output" | grep -q "Successfully ingested skill"; then
            echo -e "${GREEN}PASS${NC}"
            PASSED=$((PASSED + 1))
        else
            echo -e "${RED}FAIL${NC}"
            FAILED=$((FAILED + 1))
            echo "  Output: $output"
        fi
    fi
}

# Header
echo "======================================"
echo "SEFV: Skill Fixture Scenario Runner"
echo "======================================"
echo ""

# Check fixtures exist
echo "Checking fixtures..."
if [ ! -d "$FIXTURES_DIR" ]; then
    echo -e "${RED}ERROR: Valid fixtures directory not found: $FIXTURES_DIR${NC}"
    exit 1
fi

if [ ! -d "$INVALID_FIXTURES_DIR" ]; then
    echo -e "${RED}ERROR: Invalid fixtures directory not found: $INVALID_FIXTURES_DIR${NC}"
    exit 1
fi

echo -e "${GREEN}Fixtures found${NC}"
echo ""

# Run positive scenarios
echo "--- Positive Scenarios (Valid Fixtures) ---"
run_scenario "SEFV-E2E-01: Plan skill ingest" \
    "/skill ingest ${FIXTURES_DIR}/plan/SKILL.md
/skill list
/exit" \
    "sefv-e2e-01"

run_scenario "SEFV-E2E-02: Debug skill ingest" \
    "/skill ingest ${FIXTURES_DIR}/debug/SKILL.md
/skill list
/exit" \
    "sefv-e2e-02"

run_scenario "SEFV-E2E-03: Web-research skill ingest" \
    "/skill ingest ${FIXTURES_DIR}/web-research/SKILL.md
/skill list
/exit" \
    "sefv-e2e-03"

run_scenario "SEFV-E2E-04: API-post skill ingest" \
    "/skill ingest ${FIXTURES_DIR}/api-post/SKILL.md
/skill list
/exit" \
    "sefv-e2e-04"

echo ""
echo "--- Negative Scenarios (Invalid Fixtures) ---"
run_scenario "SEFV-E2E-05: Missing sections (should fail gracefully)" \
    "/skill ingest ${INVALID_FIXTURES_DIR}/missing-sections/SKILL.md
/exit" \
    "sefv-negative-01" \
    "true"

run_scenario "SEFV-E2E-06: Malformed headings (should fail gracefully)" \
    "/skill ingest ${INVALID_FIXTURES_DIR}/malformed-headings/SKILL.md
/exit" \
    "sefv-negative-02" \
    "true"

run_scenario "SEFV-E2E-07: Invalid tools (should fail gracefully)" \
    "/skill ingest ${INVALID_FIXTURES_DIR}/invalid-tools/SKILL.md
/exit" \
    "sefv-negative-03" \
    "true"

# Summary
echo ""
echo "======================================"
echo "Summary"
echo "======================================"
echo -e "Total:  $TOTAL"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All scenarios passed!${NC}"
    exit 0
else
    echo -e "${RED}Some scenarios failed. Check output above.${NC}"
    exit 1
fi
