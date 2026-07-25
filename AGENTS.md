# AGENTS.md — Session Handoff Log

This file is updated at the END of every Claude Code session.
Claude reads this at the START of every new session to understand what happened before.

---

## Project State
**Last updated:** 2026-07-25
**Current branch:** master
**Overall status:** Backend pipeline through deterministic generation AND sandboxed live validation is working and live-verified (parse → validate → generate → run-in-sandbox). Open-Meteo `/v1/forecast` returns `verified_pass`. No LLM self-correction (Task 9) and no frontend yet.

---

## Session Log

### Session 1 — 2026-07-24
**Goal:** Stand up the project from an empty template and build the MVP pipeline as far as deterministic client generation, before touching the Docker sandbox.

**Completed:**
- [x] Filled in `CLAUDE.md`, `ARCHITECTURE.md`, `TODO.md` from unfilled templates with the real project brief (OpenAPI → validated typed client generator); `git init` + `.gitignore`.
- [x] `backend/app/main.py` — `POST /api/parse-spec`: Pydantic-validated URL in, SSE stream out. Fetches the spec, parses with prance, validates with openapi-spec-validator.
- [x] `backend/app/generate.py` — deterministic generation, no LLM: for every operation with a JSON response, generates a standalone Pydantic response model (+ request model if the operation has a JSON request body) via datamodel-code-generator, and a client method via Jinja2. Handles path params, query params (with None-filtering so unset optional filters aren't sent as literal `"None"`), and JSON request bodies. Operations without a JSON response are reported as skipped with a reason, not silently dropped.
- [x] SSE stream extended with a `generating` stage reporting per-endpoint progress (`current`/`total`/`endpoint`); result event carries both `generated` and `skipped` lists.
- [x] Test suite: 8 default tests (parse/validate paths, generation for path-param/query-param/request-body/skip cases) + 1 `@pytest.mark.live` test (excluded by default via `pytest.ini`, run with `-m live`) that hits the real Swagger Petstore spec end-to-end and compile-checks every generated file.
- [x] Live-verified against real Swagger Petstore spec: 19/19 operations accounted for, 14 generated, 5 correctly skipped (no JSON response), every generated `models.py`/`client.py` pair passed `compile()`.
- [x] Docker installed natively in WSL and confirmed working (`docker run hello-world`).
- [x] Three commits: docs+skeleton, single-endpoint generation slice, full-spec generation scale-up.

**Decisions made:**
- Native Docker in WSL, not Docker Desktop's WSL integration. Reason: the deploy target (Oracle Cloud VM) runs native Docker with no Docker Desktop involved — matching dev to prod now avoids a "works on my machine, fails on the VM" surprise when Task 8 lands.
- Each endpoint generates its own independent models.py/client.py pair rather than being merged into one client package. Reason: avoids inventing schema-dedup/merge logic before there's a real deliverable file tree to justify it (YAGNI); flagged as debt to revisit once sandbox execution needs an actual assembled package.
- Response/request model names are always `{OperationId}Response` / `{OperationId}Request`, not the original OpenAPI component name (e.g. not `Pet`). Reason: component names are lost once prance's `ResolvingParser` fully dereferences `$ref`s, and operation-based naming is guaranteed collision-free across endpoints without extra bookkeeping.

**Problems encountered:**
- `python3 -m venv` failed (`ensurepip` unavailable, needs `apt install python3.14-venv`); `sudo` had no TTY through the Claude Code session so the apt install couldn't run from here. Worked around with `pip install --user --break-system-packages virtualenv`. Debt: install `python3.14-venv` properly on the Oracle VM at deploy time.
- Docker was completely absent from the WSL distro at first check (not just misconfigured) — user installed it natively in parallel while generation work continued; confirmed working before end of session.
- Real bug: concatenating two independently-generated Python modules (response models + request models) placed a second `from __future__ import annotations` mid-file, which is a `SyntaxError` (future imports must be the first statement). Fixed with an import-hoisting merge (`_merge_python_modules` in `generate.py`) that dedupes all top-level imports to the top of the file. Caught by the test suite, not by inspection — reinforces running the generated-code compile check on every endpoint, not just the first.

**Left incomplete:**
- [ ] Docker sandbox runner (Task 8) — not started.
- [ ] Claude Haiku self-correction loop on sandbox failure, escalate to Sonnet — not started, depends on Task 8.
- [ ] SQLite (WAL) per-IP rate limiting — not started.
- [ ] Frontend (Next.js) — not started.
- [ ] TypeScript client generation (openapi-typescript) — not started.
- [ ] Prism mock fallback for unsafe-to-call-live endpoints — not started.
- [ ] Merging per-endpoint client files into one cohesive client package — deferred (see decisions above).

**Next session should start with:**
- Task 8: build the Docker sandbox runner. One `--rm` container per run, network-restricted, resource/time capped, that executes a generated client against the live target API (Open-Meteo as the default demo target) and reports pass/fail. Docker is confirmed available — verify with `docker run hello-world` first thing if picking this up in a new environment.

---

### Session 2 — 2026-07-25
**Goal:** Task 8 — Docker sandbox runner. Smallest useful slice: run ONE already-generated client method (Open-Meteo's simplest GET) inside a locked-down container and get back a real pass/fail. No job orchestration, no LLM (that's Task 9).

**Completed:**
- [x] `backend/app/sandbox.py` — host-side runner. `resolve_and_validate_host` (SSRF pre-flight: `socket.getaddrinfo` + `ipaddress.is_global`, rejects private/loopback/link-local/reserved/CGNAT/multicast, all-families-must-be-public); `run_in_sandbox` runs one `--rm` container (read-only rootfs, `--cap-drop ALL`, `--security-opt no-new-privileges`, `--memory`/`--cpus`/`--pids-limit`, wall-clock timeout with guaranteed `docker rm -f` kill, `--add-host` pinning the validated IP against DNS rebinding).
- [x] `backend/sandbox/runner.py` — in-container entrypoint. Dynamically imports the generated client, makes one call, emits one `RELAY_RESULT:{...}` line. Distinguishes `verified_pass` / `verified_live_validation_failed` (pydantic ValidationError after a real call) / `call_failed` (requests.RequestException incl. HTTP/timeout/non-JSON, or crash).
- [x] `backend/sandbox/Dockerfile` — minimal `python:3.12-slim` + requests + pydantic, non-root, built ONCE as `relay-sandbox`.
- [x] `Makefile` — `sandbox-build` / `test` / `test-live`.
- [x] `backend/tests/test_sandbox.py` — 8 pure SSRF-guard/image-missing tests (default suite) + 1 live end-to-end (`-m live`, skips without docker+image).
- [x] Report is honest: only `verified_pass` sets `passed: true`; `verified_live` true only when a real HTTP call completed. Nothing claims a call it didn't make.
- [x] **Live-verified:** Open-Meteo `/v1/forecast` client (generated fresh from the real spec) → `status: verified_pass` against the real API. Metadata endpoint `169.254.169.254` → `ssrf_blocked` before any container starts. No leaked containers after runs.
- [x] Full suite green: 19 passed hermetic, 1 passed live.

**Decisions made:**
- **SSRF control is pre-flight DNS validation, not a per-host egress firewall.** Docker has no native "allow exactly one host" without host iptables/nftables surgery. While the code we run is our own deterministic template (only ever calls the spec's target), the real vector is a malicious `servers:`/base_url, which pre-flight closes. Flagged as `ponytail:` debt in `sandbox.py` + ARCHITECTURE debt: real egress lockdown is required in Task 9 when untrusted LLM code runs. Confirmed with the user.
- **`is_global` (not the individual is_* flags) is the SSRF discriminator** — it's the only one that catches CGNAT 100.64.0.0/10; multicast is the one global-scope range excluded on top. Caught by the parametrized test (the naive is_private/is_loopback/... version let 100.64.0.1 through).
- **No auto-build of the sandbox image** — `run_in_sandbox` fails fast with the exact `docker build`/`make sandbox-build` command if `relay-sandbox` is missing (user's call). Build must also run once on the Oracle VM at deploy (Task 15) — noted in TODO/ARCHITECTURE.
- **Structured status, not flat pass/fail** (user's call): only `verified_pass` counts as a pass in the report; `verified_live_validation_failed` and `call_failed` both fail but keep distinct status + detail so Task 9 knows whether to fix request-building or the response model.
- No new dependencies — SSRF guard and runner are pure stdlib (`socket`, `ipaddress`, `subprocess`).

**Problems encountered:**
- First SSRF implementation used `is_private or is_loopback or ...` and let CGNAT (100.64.0.0/10) through — `ipaddress` marks it private=False, global=False. Switched to `is_global and not is_multicast`. The parametrized test caught it, not inspection.
- `make` output is piped through an `rtk` wrapper that errored in this environment; ran `docker build` directly instead. The Makefile target itself is correct.

**Left incomplete (unchanged from Session 1 unless noted):**
- [ ] Task 9: Claude Haiku→Sonnet self-correction loop on sandbox failure — depends on the structured status this session produced. **Add real egress firewalling before untrusted LLM code runs in the sandbox.**
- [ ] Wire parse→generate→sandbox into one end-to-end SSE job.
- [ ] Merge per-endpoint client files into one client package.
- [ ] TS generation, frontend, SQLite rate limiting, Prism mock fallback — not started.

**Next session should start with:**
- Task 9. Consume `sandbox.run_in_sandbox`'s report; feed `call_failed`/`verified_live_validation_failed` detail back to Haiku for a capped self-correction loop, escalate to Sonnet on repeated failure. First, decide the egress-firewall upgrade (see ARCHITECTURE debt) since Task 9 introduces untrusted generated code into the container.

---

<!-- Copy the block above for each new session -->
