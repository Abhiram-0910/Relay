# relay — dev tasks

# Build the sandbox base image once; reused for every validation run (never pip-installed per run).
# Must be run once in local dev before Task 8 tests, and once on the Oracle VM at deploy (Task 15).
.PHONY: sandbox-build
sandbox-build:
	cd backend && docker build -t relay-sandbox -f sandbox/Dockerfile .

.PHONY: test
test:
	cd backend && ./venv/bin/pytest

.PHONY: test-live
test-live:
	cd backend && ./venv/bin/pytest -m live
