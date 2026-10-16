#!/usr/bin/env bash
# Cross-track parity: run every self-contained example on both runtimes and diff
# the rows (sorted; tie order is engine-defined). Query paths are absolute, so
# the commands may be relative. Usage: scripts/parity.sh "<ts command>" "<python command>" [python flags]
#   CI:    scripts/parity.sh "node compiler/dist/main.js" "python -m unsequel" "--engine duckdb"
#   local: scripts/parity.sh "unsequel-run/unsequel" "unsequel-linux-x64"
set -u
TS=$1; PY=$2; PYFLAGS=${3:-}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SPEC="$ROOT/examples/spec"
SPEC_DATA=""
for f in "$SPEC"/*.csv; do SPEC_DATA="$SPEC_DATA --data $(basename "$f" .csv)=$f"; done
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
fail=0; count=0
check() {  # name, then the shared run arguments
  local name=$1; shift
  count=$((count + 1))
  $TS run "$@" --format csv < /dev/null 2>&1 | grep -v '^note:' | tail -n +2 | sort > "$work/ts.csv"
  $PY run "$@" --format csv $PYFLAGS < /dev/null 2>&1 | grep -v '^note:' | tail -n +2 | sort > "$work/py.csv"
  if ! diff -q "$work/ts.csv" "$work/py.csv" > /dev/null; then
    fail=$((fail + 1)); echo "DIFF $name"; diff "$work/ts.csv" "$work/py.csv" | head -8 | sed 's/^/    /'
  fi
}
for q in "$ROOT"/compiler/examples/real/*.unsequel; do
  check "real/$(basename "$q")" "$q"
done
for q in "$ROOT"/compiler/examples/edge/*.unsequel; do
  # The memory engine has no DECIMAL/HUGEINT semantics; those files are
  # DuckDB-only fixtures (they pin number spellings, not engine agreement).
  case "$PYFLAGS" in *memory*) grep -qE 'CAST|DECIMAL|1e' "$q" && continue ;; esac
  check "edge/$(basename "$q")" "$q"
done
for q in "$ROOT"/compiler/examples/reference/*.unsequel; do
  # The memory engine refuses the sql hatch by design; compare it only against DuckDB.
  case "$PYFLAGS" in *memory*) grep -q '^sql\b\|| sql ' "$q" && continue ;; esac
  check "reference/$(basename "$q")" "$q" --schema "$SPEC/schema.json" $SPEC_DATA
done
echo "parity: $((count - fail))/$count identical"
[ "$fail" -eq 0 ]
