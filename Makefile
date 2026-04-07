.PHONY: format check local-ci

PYTHON ?= python

# Format all Python sources (see pyproject.toml excludes)
format:
	$(PYTHON) -m ruff format .

# Lint with flake8 (pycodestyle + pyflakes), see .flake8
check:
	$(PYTHON) -m flake8 .

# Local CI: format then flake8
local-ci: format
	$(PYTHON) -m flake8 .