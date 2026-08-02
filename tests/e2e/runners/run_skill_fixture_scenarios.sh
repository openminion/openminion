#!/bin/bash
# SEFV-08: Helper script to run all skill fixture scenarios non-interactively
# and emit pass/fail summary.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRAMEWORK_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
OPENMINION_DIR="${FRAMEWORK_ROOT}/openminion"
FIXTURES_DIR="${FRAMEWORK_ROOT}/openminion/examples/skills/cli-chat-smoke"
INVALID_FIXTURES_DIR="${FRAMEWORK_ROOT}/openminion/examples/skills/cli-chat-smoke-invalid"
export OPENMINION_HOME="${OPENMINION_HOME:-$OPENMINION_DIR}"
export OPENMINION_DATA_ROOT="${OPENMINION_DATA_ROOT:-$OPENMINION_HOME/.openminion}"
SKILL_CONFIG_PATH="${OPENMINION_DATA_ROOT}/skill-fixture-config.json"

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

mkdir -p "$OPENMINION_DATA_ROOT"
cat > "$SKILL_CONFIG_PATH" <<JSON
{
  "sqlite_path": "runtime/state/skills.db",
  "wal": false,
  "known_tools": ["file", "run_command", "http_request"]
}
JSON

# Results tracking
PASSED=0
FAILED=0
TOTAL=0

# Function to run a positive fixture scenario
run_positive_scenario() {
    local name="$1"
    local skill_file="$2"
    local expected_skill_id="$3"

    TOTAL=$((TOTAL + 1))

    echo -n "Running $name... "

    ingest_output=$(cd "$OPENMINION_DIR" && OPENMINION_HOME="$OPENMINION_HOME" OPENMINION_DATA_ROOT="$OPENMINION_DATA_ROOT" PYTHONPATH=src "$PY" -m openminion \
        skill ingest --config "$SKILL_CONFIG_PATH" --file "$skill_file" 2>&1 || true)
    list_output=$(cd "$OPENMINION_DIR" && OPENMINION_HOME="$OPENMINION_HOME" OPENMINION_DATA_ROOT="$OPENMINION_DATA_ROOT" PYTHONPATH=src "$PY" -m openminion \
        skill list --config "$SKILL_CONFIG_PATH" --json 2>&1 || true)

    if echo "$ingest_output" | grep -q '"ok": true' \
        && echo "$ingest_output" | grep -q "\"skill_id\": \"$expected_skill_id\"" \
        && echo "$list_output" | grep -q "\"skill_id\": \"$expected_skill_id\""; then
        echo -e "${GREEN}PASS${NC}"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAIL${NC}"
        FAILED=$((FAILED + 1))
        echo "  Ingest output: $ingest_output"
        echo "  List output: $list_output"
    fi
}

# Function to run a negative fixture scenario
run_negative_scenario() {
    local name="$1"
    local skill_file="$2"

    TOTAL=$((TOTAL + 1))

    echo -n "Running $name... "

    output=$(cd "$OPENMINION_DIR" && OPENMINION_HOME="$OPENMINION_HOME" OPENMINION_DATA_ROOT="$OPENMINION_DATA_ROOT" PYTHONPATH=src "$PY" -m openminion \
        skill ingest --config "$SKILL_CONFIG_PATH" --file "$skill_file" 2>&1 || true)

    if echo "$output" | grep -q '"ok": false'; then
        echo -e "${GREEN}PASS${NC} (expected failure)"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAIL${NC} (expected error but got success)"
        FAILED=$((FAILED + 1))
        echo "  Output: $output"
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
run_positive_scenario "SEFV-E2E-01: Plan skill ingest" \
    "${FIXTURES_DIR}/plan/SKILL.md" \
    "cli-chat-smoke-plan"

run_positive_scenario "SEFV-E2E-02: Debug skill ingest" \
    "${FIXTURES_DIR}/debug/SKILL.md" \
    "cli-chat-smoke-debug"

run_positive_scenario "SEFV-E2E-03: Web-research skill ingest" \
    "${FIXTURES_DIR}/web-research/SKILL.md" \
    "cli-chat-smoke-web-research"

run_positive_scenario "SEFV-E2E-04: API-post skill ingest" \
    "${FIXTURES_DIR}/api-post/SKILL.md" \
    "cli-chat-smoke-api-post"

echo ""
echo "--- Negative Scenarios (Invalid Fixtures) ---"
run_negative_scenario "SEFV-E2E-05: Missing sections (should fail gracefully)" \
    "${INVALID_FIXTURES_DIR}/missing-sections/SKILL.md"

run_negative_scenario "SEFV-E2E-06: Malformed headings (should fail gracefully)" \
    "${INVALID_FIXTURES_DIR}/malformed-headings/SKILL.md"

run_negative_scenario "SEFV-E2E-07: Invalid tools (should fail gracefully)" \
    "${INVALID_FIXTURES_DIR}/invalid-tools/SKILL.md"

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
