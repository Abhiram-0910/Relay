# AGENTS.md — Session Handoff Log

This file is updated at the END of every Claude Code session.
Claude reads this at the START of every new session to understand what happened before.

---

## Project State
**Last updated:** 2026-07-25
**Current branch:** main
**Overall status:** Full pipeline is **deployed and live-verified end-to-end** — public Cloudflare Worker (`https://relay-worker.abhiram-dev.workers.dev`) → GitHub Actions → sandbox → Gemini self-correction → KV, with per-IP rate limiting and run-id isolation. `scripts/verify_deployed.sh` passes all checks (concurrent isolation, live Open-Meteo `verified_pass`, 429 at the cap). Only the frontend (Task 13) remains before the MVP is user-facing.

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
- [ ] Gemini Flash self-correction loop on sandbox failure, escalate to Gemini Pro — not started, depends on Task 8. (Originally planned on Anthropic Claude; switched to Gemini free tier — see Session 2 correction.)
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
- [ ] Task 9: Gemini Flash→Pro self-correction loop on sandbox failure — depends on the structured status this session produced. (Anthropic dropped — Gemini free tier only, never enable billing.)
- [ ] Wire parse→generate→sandbox into one end-to-end SSE job.
- [ ] Merge per-endpoint client files into one client package.
- [ ] TS generation, frontend, SQLite rate limiting, Prism mock fallback — not started.

**Next session should start with:**
- Task 9. Consume `sandbox.run_in_sandbox`'s report; feed `call_failed`/`verified_live_validation_failed` detail back to Gemini Flash for a capped self-correction loop (2 Flash → 1 Pro → hard fail), via the official Google Gen AI Python SDK. Free tier only — never enable billing. Egress isolation (Task 8.5) is already done, so untrusted patched code is safe to run.

---

### Session 2 (cont'd) — 2026-07-25 — LLM stack correction + Task 9
**Goal:** Drop Anthropic entirely (zero paid LLM calls, ever); switch the self-correction layer to Google Gemini's permanent free tier; build Task 9.

**Completed:**
- [x] Removed all Anthropic/Claude/Haiku/Sonnet LLM references from CLAUDE.md, ARCHITECTURE.md, TODO.md, and forward-looking AGENTS.md pointers. Only remaining "Anthropic" mentions are the new *prohibitions* (never create an Anthropic key/billing; the historical "switched away" notes).
- [x] CLAUDE.md: new non-negotiable rule — **never enable billing on the Google Cloud project backing the Gemini key** (enabling billing deletes the free tier). New LLM-layer section: Gemini Flash default, Pro escalation only, capped 2 Flash → 1 Pro → hard fail, key from `GEMINI_API_KEY`.
- [x] `backend/app/correct.py` — `self_correct`: capped Flash→Pro ladder, every patch re-run through the full sandbox (SSRF + internal net + pinned sidecar), full attempt log, `corrector_error` short-circuits the ladder (protects quota). Gemini corrector uses the official `google-genai` SDK with structured JSON output (`response_schema`); returns full corrected file contents (not a diff). Sandbox runner + corrector both dependency-injected.
- [x] `google-genai` added to requirements (authorized — user directed the SDK). `backend/tests/test_correct.py` — 4 hermetic loop tests (early-stop, exact Flash/Flash/Pro cap, corrector-error stop, refuse-passing-run) + 1 live real-Gemini test. Full hermetic suite: 23 passed.
- [x] **Live-verified end to end** on a deliberately-broken Open-Meteo response model: BEFORE = `verified_live_validation_failed` (real live call), Gemini `gemini-3.5-flash` attempt #1 removed the bogus required field → AFTER = `verified_pass`. Never escalated to Pro. Demo/probe scripts deleted after confirmation; `.env` stays gitignored/uncommitted.

**Decisions made:**
- Looked up the current Google Gen AI Python SDK before wiring (Context7 + PyPI): package `google-genai`, `from google import genai`, `genai.Client()` auto-reads `GEMINI_API_KEY`, `client.models.generate_content(model=, contents=, config=types.GenerateContentConfig(...))`, structured output via `response_schema`. Did not assume from memory.
- Model IDs pinned (not `-latest`), per user. **But `gemini-2.5-flash` is now retired for new API keys** (API returns 404 "no longer available to new users") — discovered live. Re-probed the key's actual models and pinned Flash to `gemini-3.5-flash` (verified working). Pro pinned at `gemini-2.5-pro` (reachable but free-tier Pro quota was 429-exhausted at pin time — consistent with the ~50/day scarcity the ladder assumes). IDs are constants in `correct.py` for one-line swaps.
- Corrector returns full file contents, not diffs — robust to apply; loop writes back only files that actually changed and logs which.
- Key handling: never entered into the chat. Local dev reads a gitignored `backend/.env`; the demo loaded it internally and was never `cat`'d.

**Problems encountered:**
- Interactive `read -s` inside `!` session commands doesn't get a TTY, and `!` stdout isn't surfaced to the assistant — so the key-prompt approach failed silently. Resolved by having the user create `backend/.env` out-of-band and driving the run from the assistant's own tool shell (which loads `.env` internally).
- First live run failed fast with a 404 on `gemini-2.5-flash` — caught cleanly by the `corrector_error` path (ladder stopped, no wasted attempts), which is how the stale-model-id problem surfaced.

**Next session should start with:**
- Wire the full pipeline into one end-to-end SSE job: parse → generate → sandbox → self-correct, streamed, tested against Open-Meteo. Then merge per-endpoint client files into one package.

---

### Session 2 (cont'd) — 2026-07-25 — Hosting pivot (Cloudflare Worker + GitHub Actions + KV)
**Goal:** Host the pipeline with no always-on server: a Cloudflare Worker as the public front door (trigger + KV state + per-IP rate limit) and a GitHub Action as the compute (runs the Docker sandbox). Plan was confirmed point-by-point before any code.

**Completed:**
- [x] `backend/app/ci_runner.py` — end-to-end pipeline runner (also the deferred "wire into one job" task): fetch→parse→validate→generate-all→live-validate GET endpoints in the sandbox→self-correct. Posts coalesced checkpoints (fetching_spec → spec_validated → generated → live_validating → done) to the Worker via authenticated callback; local mode prints. **Live-verified**: Open-Meteo → `succeeded`, forecast `verified_pass`.
- [x] `worker/src/index.js` — Cloudflare Worker: `POST /api/runs` (validate → rate-limit → mint runId → KV queued → workflow_dispatch → 202), `GET /api/runs/:id` (poll KV), `POST /api/runs/:id/progress` (Bearer CALLBACK_SECRET, Worker-enforced ≤1-write/sec-per-key throttle). 9 unit tests pass (fake KV+fetch): rate-limit-fires-before-dispatch, refund-on-dispatch-failure, run-id isolation, callback auth, throttle coalescing + terminal-always-writes.
- [x] `.github/workflows/generate.yml` — `workflow_dispatch` (inputs run_id/spec_url/callback_url), builds sandbox images, runs `ci_runner`, and an `if: failure()` step that reports infra failure back so a dead job shows `failed` not eternal `queued`.
- [x] `worker/DEPLOY.md` (provisioning), `scripts/verify_deployed.sh` (curl E2E against a deployed Worker), `worker/wrangler.jsonc`, `worker/package.json` + vitest.
- [x] Docs: CLAUDE.md rate-limit/DB rules amended for the pivot; ARCHITECTURE.md hosting section + stack + key files + env + debt; TODO/backlog updated (Oracle VM deploy dropped). Backend suite 29 hermetic pass; worker 9 pass.

**Decisions made:**
- **Confirmed the plan's 5 points before coding** (endpoints, GitHub auth, three-credential model, run-id isolation, rate-limit-before-dispatch). Credentials: `GH_PAT` (Worker→GitHub dispatch), `CALLBACK_SECRET` (Action→Worker, shared), `GEMINI_API_KEY` (Action→Gemini). The Action's `GITHUB_TOKEN` is checkout-only, never calls the Worker.
- **Native Rate Limiting binding rejected** (verified via docs): its `period` is 10s or 60s only — can't express a daily quota. Fell back to KV daily counter (`rl:{ip}:{YYYYMMDD}`, EOD TTL, LIMIT=3, over-limit = 0 writes). Dropped the native binding entirely — with a 3/day cap, a 60s burst limiter adds nothing.
- **KV Free = 1,000 writes/day + 1 write/sec per key** (verified) → progress must be coalesced; the **Worker** enforces the throttle (not the reporter) so a buggy/retrying reporter can't blow the budget. A status *transition* or terminal result always writes; rapid same-status updates coalesce.
- **Worker mints its own runId** (workflow_dispatch returns no reliable sync run id) → independent of GitHub, KV keys namespaced by runId → no cross-contamination.
- Live-validation scoped to idempotent GETs with synthesizable required params + a clearly-marked Open-Meteo demo-param hook (`_DEMO_TARGETS`); everything else honestly `generated_only`.

**Problems encountered:**
- Worker test caught a real throttle bug: the `queued` entry set `updatedAt=now`, so the FIRST progress callback (queued→running, <1s later) was being coalesced/dropped. Fixed: coalesce only rapid SAME-status non-terminal updates; a status transition or terminal always writes.
- Interactive `read -s` in `!` still unusable; kept the `.env`-out-of-band pattern. Root `.env` (PAT) and `backend/.env` (Gemini) both gitignored/untracked — never read.

**Spec-fetch SSRF — closed (before deploy, as required).** `ci_runner` now (1) calls `sandbox.resolve_and_validate_host(spec_url)` before fetching, and (2) wraps `ResolvingParser` in `guarded_prance_resolve()`, which gates prance's single fetch choke point `prance.util.url.fetch_url_text` — chosen after reading prance's source (every remote/`file`/`python` ref fetch, transitively included, routes through it; a pre-scan of raw spec text would miss transitive refs). http(s) hosts run the SSRF check; `file://`/`python://` rejected (they'd read runner files / import packages — a vector broader than SSRF, found during investigation). Wrapper asserts the choke point exists so a prance restructure fails loudly. 3 hermetic default-suite tests (private spec_url rejected pre-fetch; malicious `$ref` blocked + proven not-fetched; guard units). Open-Meteo still `verified_pass` through the guard. Documented residual: spec_url `requests.get` DNS-rebind window (literal-private-URL closed; pinning would break TLS SNI, not worth it). 32 hermetic backend tests pass.

**Next session should start with:**
- Deploy is out-of-band (needs the user): follow `worker/DEPLOY.md`, then run `scripts/verify_deployed.sh <worker-url>` for the live integration check (trigger → poll checkpoints → verified_pass → rate-limit → isolation). After it's green: Task 13 frontend, then the spec-fetch SSRF guard.

---

### Session 2 — CLOSE — 2026-07-25 — Hosting pivot DEPLOYED & live-verified
**Status:** The Cloudflare Worker + GitHub Actions + KV pipeline is not just built — it is **deployed and passing end-to-end** at `https://relay-worker.abhiram-dev.workers.dev`.

**Live verification (`scripts/verify_deployed.sh`): ALL CHECKS PASSED**
- Two concurrent runs triggered, kept correctly isolated (distinct run ids, independent polling — no cross-contamination).
- Both reached `verified_pass` on live Open-Meteo `/v1/forecast` through the full path (Worker → workflow_dispatch → Action builds sandbox images → ci_runner parse→generate→sandbox→self-correct → KV → poll).
- Rate limiter correctly `429`'d request #4 (per-IP daily cap = 3), blocking before dispatch.

**Deploy-time issues found and fixed (all resolved):**
- **Default branch was `master`, not `main`.** `workflow_dispatch` (and the Worker's `GH_REF: main`) target `main`; renamed the branch to `main` so the trigger resolves. (Repo/config now consistently on `main`.)
- **Git remote was never added.** The local repo had no `origin` — the workflow file wasn't on GitHub yet. Added the remote and pushed so `.github/workflows/generate.yml` exists on the default branch (required for `workflow_dispatch` to find it).
- **`gh`/PAT lacked the `workflow` scope.** Pushing `.github/workflows/*` was rejected until the token had the `workflow` scope; re-scoped, then the push (and dispatch) succeeded.
- KV namespace created; `wrangler.jsonc` now carries the real namespace id (`ccabebcb…`, an account-scoped identifier, not a secret). Worker secrets (`GH_PAT`, `CALLBACK_SECRET`) and GitHub Actions secrets (`CALLBACK_SECRET`, `GEMINI_API_KEY`) set out-of-band per DEPLOY.md.

**Session 2 overall — shipped and live-verified:** Task 8 (Docker sandbox), Task 8.5 (network isolation), Task 9 (Gemini free-tier self-correction, Anthropic dropped), end-to-end `ci_runner`, spec-fetch SSRF guard, and the full hosting pivot (Worker + Actions + KV), now deployed. Backend 32 hermetic tests + worker 9 tests all green.

**Next session — Task 13: frontend.**
- Build the Next.js 14 + Tailwind frontend on Vercel, against the **proven, deployed** Worker API (`https://relay-worker.abhiram-dev.workers.dev`): paste an OpenAPI URL → `POST /api/runs` → poll `GET /api/runs/:id` and render the checkpoint progression (queued → running/stages → succeeded/failed) and the final per-endpoint `verified_pass` result. The API is stable and CORS-open; build the UI once against it, don't re-spec the backend.
- After the frontend: retire the Open-Meteo demo-param hook (general required-param synthesis), then TypeScript client generation.

---

### Task 13 — IN PROGRESS — 2026-07-25 — Frontend
**Slice 1 (loop) — done & committed (`411f624`):** Next.js 14 + Tailwind dark OLED frontend. Paste URL → `POST /api/runs` → poll `GET /api/runs/:id` every 1.5s → checkpoint timeline + per-endpoint validation report. Worker URL in `NEXT_PUBLIC_WORKER_URL` (never hardcoded). Status→color is a single source of truth (`lib/status.ts`, 5 hermetic tests) enforcing the honesty rule mechanically: emerald=verified_pass only, amber=generated_only/validation-fail/in-progress, red=failed/blocked/rate-limited. Honest 429 + failed-run rendering. Build/lint/types clean.

**⚠️ OPEN — NOT closed:** The **429 rate-limit** state was verified LIVE against the deployed Worker, but the **polling timeline and succeeded-report** states were verified only against a **local mock of the real data shapes** — not a real live-running Action — because today's per-IP rate-limit quota (3/day) is spent. **Real live poll-loop-to-completion not yet re-verified in the frontend; do this once quota resets (~10h) before calling Task 13 fully proven.**

**Slice 2 (code viewer) — done:** `ci_runner` POSTs per-endpoint source (`_code_files`: models.py + client.py each) to a new authenticated Worker callback `POST /api/runs/:id/code`; Worker stores it as a separate `code:{runId}` KV entry (size-guarded — per-file 256 KB / total 2 MB caps, truncation FLAGGED not silent) with TTL, served by public `GET /api/runs/:id/code`. Frontend: on-demand "View generated code" → tabs (models.py/client.py per endpoint), dependency-free Python syntax highlighter (`lib/highlight.ts`, cool violet/sky palette deliberately distinct from status emerald/amber/red), download button. Honesty extended to code: a truncated bundle shows an amber "Partial output" banner + `(partial)` tab marker + a note that the download is also partial — verified via mock-drive screenshot. Tests: worker 12 (incl. store-auth + size-guard-flags-truncation), frontend 7 (incl. highlighter round-trip: tokens must reconstruct input exactly, so displayed code can't be silently corrupted). Backend 33 (incl. `_code_files`).

**⚠️ STILL OPEN (unchanged):** Real live poll-loop-to-completion — and now the live code-fetch path — not yet re-verified in the frontend against a real running Action (today's 3/day quota is spent). The code viewer's normal + truncated states were verified against a mock of the real data shapes, same as the poll loop. Do a real live run once quota resets (~10h) before calling Task 13 fully proven.

---

<!-- Copy the block above for each new session -->
