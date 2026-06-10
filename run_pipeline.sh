#!/bin/zsh
# Full registered bench pipeline (runner -> judge -> analyze), pinned to
# loopgain==0.4.0. Launched detached via detach_pipeline.py so it survives
# Claude Code session suspends (RUNNING_BENCHMARKS.md §1).
#
# KEY HANDLING: the ambient shell sets ANTHROPIC_API_KEY to an EMPTY string and
# omits OPENAI_API_KEY entirely; bench.judge / bench.llm do NOT load .env (only
# bench.runner does, with override=False, so the empty ambient var would win).
# Pull the real keys from .env into the environment here so runner AND judge
# both authenticate. cut -f2- preserves any '=' inside the value; keys are set
# via command substitution so they never appear in argv / ps output.
set -e
cd "$(dirname "$0")" || exit 1

export ANTHROPIC_API_KEY="$(grep '^ANTHROPIC_API_KEY=' .env | cut -d'=' -f2-)"
export OPENAI_API_KEY="$(grep '^OPENAI_API_KEY=' .env | cut -d'=' -f2-)"

PY=.venv/bin/python

echo "=== PIPELINE START $(date '+%Y-%m-%d %H:%M:%S') | loopgain==$($PY -c 'import importlib.metadata as m;print(m.version(\"loopgain\"))') ==="
$PY -m bench.runner --all-cells --n 200 --tag registered --cells-parallel 2 --skip-existing
echo "=== BENCH DONE; starting JUDGE $(date '+%H:%M:%S') ==="
$PY -m bench.judge --input data/raw/ --tag registered
echo "=== JUDGE DONE; starting ANALYZE $(date '+%H:%M:%S') ==="
$PY -m analysis.run --input data/raw/ --output data/results/ --tag registered
echo "=== PIPELINE COMPLETE $(date '+%Y-%m-%d %H:%M:%S') ==="
