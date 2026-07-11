REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
OPENMINION_HOME ?= $(REPO_ROOT)
OPENMINION_DATA_ROOT ?= $(OPENMINION_HOME)/.openminion
OPENMINION_EVAL_ROOT ?= $(REPO_ROOT)/.deps/openminion-eval
VENV := $(REPO_ROOT)/.venv
DEV_STAMP := $(VENV)/.baseline-tools-installed
PYTHON := $(VENV)/bin/python3.11
PIP := $(PYTHON) -m pip
PRE_COMMIT := $(PYTHON) -m pre_commit
PYTEST := $(PYTHON) -m pytest
RUFF := $(PYTHON) -m ruff

# I-17 (2026-06-02): parallel `validate-patterns` job count. Defaults to the
# host CPU count (capped at 8 to keep output readable on big servers).
# Override via `make JOBS=N lint`. Single-process behavior is preserved with
# `make JOBS=1 lint`.
JOBS ?= $(shell n=$$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4); if [ "$$n" -gt 8 ]; then echo 8; else echo "$$n"; fi)

# I-17: pure-script validators that take no special arguments. Each becomes
# a `_vp-<name>` phony target so `make -j N validate-patterns` runs them
# concurrently. Validators with non-standard CLI args (e.g.
# `direct_env_calls --fail-on-violation`) get their own explicit
# target below.
VALIDATE_PATTERN_MODULES := \
	validate.env_config_centralization \
	validate.import_boundaries \
	validate.config_manager_usage \
	validate.config_shape \
	validate.data_root_defaults \
	validate.logging_control_plane \
	validate.contract_literals \
	validate.helper_duplicates \
	validate.config_constants \
	validate.max_file_loc \
	validate.telemetry_event_catalog \
	validate.module_typed_raises \
	validate.tool_selection_scoring_contract \
	validate.self_improvement_contract \
	validate.recovery_pipeline_contract \
	validate.runner_delegates \
	validate.pydantic_extra_allow_audit \
	validate.termination_reason_vocabulary \
	validate.runtime_step_ownership \
	validate.no_source_e2e_artifact_refs \
	validate.services_layout \
	validate.memory_boundary \
	validate.base_charter \
	validate.openminion_root_layout \
	validate.modules_shape \
	validate.module_cycles \
	validate.public_surface \
	validate.api_layout \
	validate.tools_layout \
	validate.cli_layout \
	validate.openminion_eval_layout \
	validate.focus_layout \
	validate.chat_import_boundaries \
	validate.no_raw_control_value_strings \
	validate.asyncio_run_boundary \
	validate.artifact_locations \
	validate.path_structure_hygiene \
	validate.prompt_literals \
	validate.method_loc \
	validate.broad_exception \
	validate.filename_underscore_hygiene \
	validate.type_ignore_hygiene

# Compatibility mirror for tests and simple file-existence audits. Keep these
# entries aligned with VALIDATE_PATTERN_MODULES, but expressed as repo-relative
# script paths under scripts/.
VALIDATE_PATTERN_SCRIPTS := \
	validate/env_config_centralization \
	validate/import_boundaries \
	validate/config_manager_usage \
	validate/config_shape \
	validate/data_root_defaults \
	validate/logging_control_plane \
	validate/contract_literals \
	validate/helper_duplicates \
	validate/config_constants \
	validate/max_file_loc \
	validate/telemetry_event_catalog \
	validate/module_typed_raises \
	validate/tool_selection_scoring_contract \
	validate/self_improvement_contract \
	validate/recovery_pipeline_contract \
	validate/runner_delegates \
	validate/pydantic_extra_allow_audit \
	validate/termination_reason_vocabulary \
	validate/runtime_step_ownership \
	validate/no_source_e2e_artifact_refs \
	validate/services_layout \
	validate/memory_boundary \
	validate/base_charter \
	validate/openminion_root_layout \
	validate/modules_shape \
	validate/module_cycles \
	validate/public_surface \
	validate/api_layout \
	validate/tools_layout \
	validate/cli_layout \
	validate/openminion_eval_layout \
	validate/focus_layout \
	validate/chat_import_boundaries \
	validate/no_raw_control_value_strings \
	validate/asyncio_run_boundary \
	validate/artifact_locations \
	validate/path_structure_hygiene \
	validate/prompt_literals \
	validate/method_loc \
	validate/broad_exception \
	validate/filename_underscore_hygiene \
	validate/type_ignore_hygiene

_VP_TARGETS := $(addprefix _vp-, $(VALIDATE_PATTERN_MODULES)) _vp-validate.direct_env_calls _vp-direct-env-calls

.PHONY: help venv dev-install hooks-install hooks-run fix format format-check lint lint-advisory validate-patterns typecheck typecheck-strict test bench check eval $(_VP_TARGETS)

help:
	@printf '%s\n' \
		'Targets:' \
		'  make dev-install   Create/update .venv and install OpenMinion with dev extras' \
		'  make hooks-install Install pre-commit and commit-msg hooks into .git/hooks' \
		'  make hooks-run     Run pre-commit across the OpenMinion repo' \
		'  make fix           Apply local Ruff formatting and autofixes' \
		'  make format        Run Ruff formatter' \
		'  make format-check  Check formatting without changing files' \
		'  make lint          Run Ruff plus blocking repo validation scripts (incl. typecheck)' \
		'                     - I-17: validate-patterns runs in parallel via JOBS=$(JOBS).' \
		'                       Override with `make JOBS=N lint` or force serial with JOBS=1.' \
		'  make lint-advisory Run the current warn-only control-value validator' \
		'  make typecheck     Run mypy over the configured scope (see pyproject [tool.mypy])' \
		'  make typecheck-strict Run typecheck + reject any new bare `# type: ignore`' \
		'  make eval          G-06: run the 5 starter EvalCases via openminion-eval' \
		'                     Override category: make eval ARGS="--category coding"' \
		'  make test          Run the OpenMinion pytest suite' \
		'  make bench         Run storage benchmark regression harness' \
		'  make check         Run format-check, lint, and test'

venv:
	@test -x "$(PYTHON)" || python3.11 -m venv "$(VENV)"

$(DEV_STAMP): pyproject.toml | venv
	$(PIP) install --upgrade pip setuptools wheel
	cd "$(REPO_ROOT)" && $(PIP) install -e ".[dev]"
	@touch "$(DEV_STAMP)"

dev-install: $(DEV_STAMP)

hooks-install: $(DEV_STAMP)
	$(PRE_COMMIT) install --install-hooks --hook-type pre-commit --hook-type commit-msg

hooks-run: $(DEV_STAMP)
	$(PRE_COMMIT) run --all-files

fix: $(DEV_STAMP)
	$(RUFF) format "$(REPO_ROOT)"
	$(RUFF) check --fix "$(REPO_ROOT)"

format: $(DEV_STAMP)
	$(RUFF) format "$(REPO_ROOT)"

format-check: $(DEV_STAMP)
	$(RUFF) format --check "$(REPO_ROOT)"

lint: $(DEV_STAMP)
	$(RUFF) check "$(REPO_ROOT)"
	$(MAKE) -j $(JOBS) validate-patterns
	$(MAKE) typecheck

typecheck: $(DEV_STAMP)
	cd "$(REPO_ROOT)" && $(PYTHON) -m scripts.validate.mypy_error_budget

# G-06 (2026-06-06): proxy to the openminion-eval `make eval` target. Override
# `OPENMINION_EVAL_ROOT` when the eval package lives outside the package-local
# `.deps/openminion-eval` checkout path.
eval:
	@eval_root="$(OPENMINION_EVAL_ROOT)"; \
	if [ ! -d "$$eval_root" ]; then \
		fallback_root="$$(cd "$(REPO_ROOT)/.." && pwd)/openminion-eval"; \
		if [ -d "$$fallback_root" ]; then \
			eval_root="$$fallback_root"; \
		fi; \
	fi; \
	test -d "$$eval_root" || { \
		echo "error: openminion-eval checkout not found: $(OPENMINION_EVAL_ROOT)"; \
		echo "set OPENMINION_EVAL_ROOT=/path/to/openminion-eval"; \
		exit 1; \
	}; \
	$(MAKE) -C "$$eval_root" eval ARGS="$(ARGS)"

typecheck-strict: typecheck
	cd "$(REPO_ROOT)" && $(PYTHON) -m scripts.validate.type_ignore_hygiene --strict

lint-advisory: $(DEV_STAMP)
	cd "$(REPO_ROOT)" && $(PYTHON) -m scripts.validate.control_value_constants
	cd "$(REPO_ROOT)" && $(PYTHON) -m scripts.validate.passthrough
	cd "$(REPO_ROOT)" && $(PYTHON) -m scripts.validate.type_ignore_hygiene --baseline

# I-17 (2026-06-02): each validator runs as its own phony target so
# `make -j N validate-patterns` parallelizes execution.
$(addprefix _vp-, $(VALIDATE_PATTERN_MODULES)): _vp-%: $(DEV_STAMP)
	@cd "$(REPO_ROOT)" && $(PYTHON) -m scripts.$*

_vp-validate.direct_env_calls: $(DEV_STAMP)
	@cd "$(REPO_ROOT)" && $(PYTHON) -m scripts.validate.direct_env_calls --fail-on-violation

_vp-direct-env-calls: _vp-validate.direct_env_calls

validate-patterns: $(_VP_TARGETS)

test: $(DEV_STAMP)
	PYTHONPATH="$(REPO_ROOT)/src" \
	OPENMINION_HOME="$(OPENMINION_HOME)" \
	OPENMINION_DATA_ROOT="$(OPENMINION_DATA_ROOT)" \
	$(PYTEST) -q "$(REPO_ROOT)/tests"

bench: $(DEV_STAMP)
	PYTHONPATH="$(REPO_ROOT)/src" \
	OPENMINION_HOME="$(OPENMINION_HOME)" \
	OPENMINION_DATA_ROOT="$(OPENMINION_DATA_ROOT)" \
	$(PYTEST) -q "$(REPO_ROOT)/tests/storage/benchmarks" -m benchmark -s

check: format-check lint test
