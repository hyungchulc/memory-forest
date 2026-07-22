PYTHON ?= python3

.PHONY: audit build check install-local test

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

audit:
	$(PYTHON) scripts/audit_public_release.py --root .

check: test audit

build:
	$(PYTHON) -m pip wheel --no-deps --wheel-dir dist .

install-local:
	$(PYTHON) -m pip install --force-reinstall .
