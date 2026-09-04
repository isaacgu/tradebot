PYTHON ?= python

.PHONY: bootstrap demo evidence-hashes lint typecheck test security check

bootstrap:
	uv lock --check
	uv sync --locked --extra dev

demo:
	$(PYTHON) -m tradebot.demo --output build/gate0/demo-manifest.json

evidence-hashes:
	$(PYTHON) scripts/hash_evidence.py

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -W error -m pytest
	$(PYTHON) -m coverage report --include="src/tradebot/core/*" --fail-under=90
	$(PYTHON) -m coverage report --omit="src/tradebot/core/*" --fail-under=80

security:
	$(PYTHON) -m bandit -q -r src
	$(PYTHON) -m pip_audit

check: lint typecheck test security demo
