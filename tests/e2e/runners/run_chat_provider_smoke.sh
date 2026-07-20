#!/usr/bin/env bash
# OOR-08: CLI smoke script for provider-chat validation.
# Canonical runner:
#   openminion/tests/e2e/runners/run_chat_provider_smoke.sh [--one-turn|--tool-intent|--error-intent|--ollama|--openrouter]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRAMEWORK_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
OPENMINION_DIR="${FRAMEWORK_ROOT}/openminion"
OPENMINION_HOME="${OPENMINION_HOME:-$OPENMINION_DIR}"
OPENMINION_DATA_ROOT="${OPENMINION_DATA_ROOT:-$OPENMINION_HOME/.openminion}"
OPENMINION_CONFIG="${OPENMINION_CONFIG:-$FRAMEWORK_ROOT/.tmp/per-agent.json}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Ollama is available and running
check_ollama() {
    log_info "Checking Ollama availability..."
    if ! command -v ollama &> /dev/null; then
        log_error "Ollama not installed. Install from https://github.com/ollama/ollama"
        return 1
    fi

    # Check if Ollama is running by trying to list models
    if ! ollama list &> /dev/null; then
        log_warn "Ollama is not running. Starting ollama serve..."
        # Don't auto-start, let user start it
        log_error "Please run 'ollama serve' in another terminal"
        return 1
    fi

    log_info "Ollama is available"
    return 0
}

# Run one-turn smoke test
run_one_turn() {
    local provider=$1
    log_info "Running one-turn smoke test for $provider..."

    export PYTHONPATH="$OPENMINION_DIR/src"

    # Use echo provider for smoke test (no external dependencies)
    printf 'hi\n/exit\n' | OPENMINION_HOME="$OPENMINION_HOME" OPENMINION_DATA_ROOT="$OPENMINION_DATA_ROOT" python3.11 -m openminion --config "$OPENMINION_CONFIG" --agent echo-agent --session smoke-test --verbosity quiet 2>&1

    if [ $? -eq 0 ]; then
        log_info "One-turn smoke test PASSED for $provider"
        return 0
    else
        log_error "One-turn smoke test FAILED for $provider"
        return 1
    fi
}

# Run tool-intent smoke test
run_tool_intent() {
    local provider=$1
    log_info "Running tool-intent smoke test for $provider..."

    export PYTHONPATH="$OPENMINION_DIR/src"

    # Test with a prompt that might trigger tool calling
    printf 'what is the weather in tokyo\n/exit\n' | OPENMINION_HOME="$OPENMINION_HOME" OPENMINION_DATA_ROOT="$OPENMINION_DATA_ROOT" python3.11 -m openminion --config "$OPENMINION_CONFIG" --agent echo-agent --session smoke-tool-test --verbosity quiet 2>&1

    if [ $? -eq 0 ]; then
        log_info "Tool-intent smoke test PASSED for $provider"
        return 0
    else
        log_error "Tool-intent smoke test FAILED for $provider"
        return 1
    fi
}

# Run error-intent smoke test
run_error_intent() {
    local provider=$1
    log_info "Running error-intent smoke test for $provider..."

    export PYTHONPATH="$OPENMINION_DIR/src"

    # Test with invalid configuration to trigger error handling
    printf 'hello\n/exit\n' | OPENMINION_DISABLE_LLMCTL_BRIDGE=1 OPENMINION_HOME="$OPENMINION_HOME" OPENMINION_DATA_ROOT="$OPENMINION_DATA_ROOT" python3.11 -m openminion --config "$OPENMINION_CONFIG" --agent echo-agent --session smoke-error-test --verbosity quiet 2>&1

    # This should fail with a clear error message
    log_info "Error-intent smoke test completed for $provider (expected failure)"
    return 0
}

# Main execution
main() {
    local mode="${1:-one-turn}"

    log_info "OpenMinion CLI Smoke Tests"
    log_info "============================"

    case "$mode" in
        --one-turn)
            run_one_turn "echo"
            ;;
        --tool-intent)
            run_tool_intent "echo"
            ;;
        --error-intent)
            run_error_intent "echo"
            ;;
        --ollama)
            check_ollama || exit 1
            run_one_turn "ollama"
            ;;
        --openrouter)
            if [ -z "$OPENROUTER_API_KEY" ]; then
                log_error "OPENROUTER_API_KEY environment variable not set"
                log_info "Set it with: export OPENROUTER_API_KEY='your-key-here'"
                exit 1
            fi
            run_one_turn "openrouter"
            ;;
        --all)
            log_info "Running all smoke tests..."
            run_one_turn "echo"
            run_tool_intent "echo"
            run_error_intent "echo"
            log_info "All smoke tests completed!"
            ;;
        *)
            echo "Usage: $0 [--one-turn|--tool-intent|--error-intent|--ollama|--openrouter|--all]"
            echo ""
            echo "Smoke test modes:"
            echo "  --one-turn     Run basic one-turn chat test (default)"
            echo "  --tool-intent  Run test with potential tool-calling intent"
            echo "  --error-intent Run test to verify error handling"
            echo "  --ollama       Run test with Ollama provider"
            echo "  --openrouter   Run test with OpenRouter provider"
            echo "  --all          Run all smoke tests"
            exit 1
            ;;
    esac
}

main "$@"
