.PHONY: install install-dev test mock dry-run bench analyze clean

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

# Full registered run, n>=200 per cell. ~$30-60 spend. Hands-off.
bench:
	@echo "Full run, n=200 per cell. Estimated spend: \$$30-60."
	@echo "BENCH_PROTOCOL.md MUST be committed REGISTERED before this runs."
	python -m bench.runner --all-cells --n 200 --tag registered

analyze:
	python -m analysis.run --input data/raw/ --output data/results/

clean:
	rm -rf data/raw/*.jsonl data/results/*.json data/results/*.csv data/results/*.html
	rm -rf __pycache__ */__pycache__ */*/__pycache__
