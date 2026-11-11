PYTHON ?= python3

.PHONY: help check
help:
	@echo "make check - offline source and device-guard checks; no ADB"
check:
	$(PYTHON) scripts/check_repo.py
	$(PYTHON) -m unittest discover -s scripts/qa -p 'test_*.py'
