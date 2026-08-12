#!/usr/bin/env bash

isolate_openminion_test_roots() {
  local prefix="${1:-openminion-test}"
  local temp_root
  temp_root="$(mktemp -d "${TMPDIR:-/tmp}/${prefix}.XXXXXX")"
  OPENMINION_HOME="$(cd "$temp_root" && pwd -P)"
  OPENMINION_DATA_ROOT="${OPENMINION_HOME}/.openminion"
  OPENMINION_GENERATED_ROOT="${OPENMINION_DATA_ROOT}/runtime"
  export OPENMINION_HOME OPENMINION_DATA_ROOT OPENMINION_GENERATED_ROOT
}
