ifeq ($(OS),Windows_NT)
PYTHON ?= .venv/Scripts/python.exe
else
PYTHON ?= .venv/bin/python
endif

.PHONY: install ask test build test-web eval api web dev smoke verify

install:
	$(PYTHON) -m pip install -e ".[dev]"
	npm --prefix web install

test:
	$(PYTHON) -m pytest -q

ask:
	$(PYTHON) scripts/ask.py

build:
	npm --prefix web run build

test-web:
	npm --prefix web test -- --run

eval:
	$(PYTHON) scripts/run_eval.py

api dev:
	$(PYTHON) -m scripts.run_api

web:
	npm --prefix web run dev

smoke:
	$(PYTHON) scripts/smoke_test.py

verify: test test-web build eval
