# relay — dev tasks

# Build both sandbox images once; reused for every validation run (never pip-installed per run).
# Must be run once in local dev before sandbox tests, and once on the Oracle VM at deploy (Task 15).
#   relay-sandbox  — runs the generated client (no external route of its own)
#   relay-sidecar  — the per-run pinned egress relay (socat)
.PHONY: sandbox-build
sandbox-build:
	cd backend && docker build -t relay-sandbox -f sandbox/Dockerfile . \
	           && docker build -t relay-sidecar -f sandbox/sidecar.Dockerfile .

.PHONY: test
test:
	cd backend && ./venv/bin/pytest

.PHONY: test-live
test-live:
	cd backend && ./venv/bin/pytest -m live
