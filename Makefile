PYTHON ?= python

.PHONY: format format-check lint typecheck test security smoke check

format:
	$(PYTHON) -m ruff format src tests scripts

format-check:
	$(PYTHON) -m ruff format --check src tests scripts

lint:
	$(PYTHON) -m ruff check src tests scripts

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

security:
	$(PYTHON) scripts/scan_secrets.py

smoke:
	$(PYTHON) scripts/smoke.py

check: format-check lint typecheck test security smoke
