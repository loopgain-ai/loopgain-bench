.PHONY: install install-dev test mock dry-run bench analyze judge judge-dry-run clean

install:
	pip install -e .

install-dev:
	pip install -e ".[dev,all]"

# Mock-mode end-to-end smoke (no API calls, deterministic, fast).
# Verifies the harness pipes correctly; not a results-producing run.
mock:
	BENCH_MOCK=1 python -m bench.runner --workload w5_adversarial --n 5

# Dry-run at n=10 per cell (real API). PER BENCH_PROTOCOL.md, dry-run data
# is NOT included in the registered analysis — its job is to surface adapter
# bugs and methodology holes cheap before the full N>=200 run.
dry-run:
	@echo "Real-API dry-run, n=10 per cell. Estimated spend: <\$$5. Confirm BENCH_PROTOCOL.md is REGISTERED."
	python -m bench.runner --all-cells --n 10 --tag dry-run

# Full registered run, n>=200 per cell. ~$30-60 spend.
#
# Concurrency model: each trial's 4 conditions always run in parallel (~4x);
# trials within a cell run with --trials-parallel 8 (~8x on top); cells run
# with --cells-parallel 2 to overlap Anthropic-bucket and OpenAI-bucket
# cells (~2x on top). --skip-existing preserves any cell JSONL that's
# already complete (e.g. a prior interrupted run).
#
# Methodology lockdowns under concurrency: Lockdown #4 (same seeds across
# conditions) holds — each condition's LLM client is fresh per trial; per
# Lockdown #7 ("same wall-clock environment"), running the 4 conditions
# concurrently within a trial strengthens temporal locality (all four are
# in the same instant), it doesn't weaken it.
bench:
	@echo "Full run, n=200 per cell. Estimated spend: \$$30-60."
	@echo "BENCH_PROTOCOL.md MUST be committed REGISTERED before this runs."
	python -m bench.runner --all-cells --n 200 --tag registered \
		--trials-parallel 8 --cells-parallel 2 --skip-existing

analyze:
	python -m analysis.run --input data/raw/ --output data/results/

# Run pairwise LLM judge on trial JSONLs. Cross-vendor enforced — Anthropic
# loops are judged by OpenAI gpt-4.1-mini; OpenAI loops are judged by
# Anthropic claude-haiku-4-5. RAG cells (iterative_retrieval) are skipped —
# their quality is programmatic (retrieval@k).
judge-dry-run:
	python -m bench.judge --input data/raw/ --tag dry-run

judge:
	python -m bench.judge --input data/raw/ --tag registered

clean:
	rm -rf data/raw/*.jsonl data/results/*.json data/results/*.csv data/results/*.html
	rm -rf __pycache__ */__pycache__ */*/__pycache__
