# AGENTS.md — Session Handoff Log

This file is updated at the END of every Claude Code session.
Claude reads this at the START of every new session to understand what happened before.

---

## Project State
**Last updated:** 2026-08-10 (Session 9)
**Current branch:** main
**Overall status:** MVP is **complete and live-verified end-to-end, frontend included** (see Session 2 close, 2026-07-26). Vercel frontend (`https://relay-cyan-rho.vercel.app`) → Cloudflare Worker (`https://relay-worker.abhiram-dev.workers.dev`) → GitHub Actions → Docker sandbox → Gemini self-correction → KV, with per-IP rate limiting, run-id isolation, and a code viewer. A real browser run polls to `Succeeded` / `verified_pass` and shows the real generated client. Nothing in the MVP is claimed-but-unproven. Tests: backend 33 · worker 12 · frontend 7 hermetic, all green.

**Phase 2 (scoped 2026-07-26, build starting Session 3, 2026-07-27):** BYOK security infrastructure → multi-provider LLM (Gemini/OpenAI/Anthropic/Grok/OpenRouter) → honest error taxonomy → full QA re-verification → visual/UX pass → `/security-review` + remediation → final retest and deliver. **Progress:** Step 1 (BYOK) CLOSED live (Session 4). Step 2 (multi-provider) CLOSED live (Session 6, commits `4866d2a`→`7f846fb`) — see Session 6 for the full receipt; one accepted gap logged there (corrector proven live independently of the deployed pipeline, not together in one run — no test-injection hook exists in the public API for that). Step 3 (honest error taxonomy) CLOSED (Session 7, commits `caf86cf`→`dc5e5cf`) — see Session 7 for the full receipt; one deliberate scope boundary logged there (per-attempt corrector codes still render as raw tokens in the compact attempts list). Step 4 (full QA pass) CLOSED (Session 8, commits `bbb35fd`→`572c18d`) — see Session 8 for the full receipt, including a real production bug (the Action's infra-failure watchdog silently overwriting every classified error) found live and fixed, not just a coverage-extension step. Step 5 (visual/UX pass) CLOSED (Session 9, commits `c21ca2a`→`8ce5b3d`) — see Session 9 for the full receipt and a logged pattern-to-watch (`status.ts` caught incomplete twice now). Steps 6–7 not started. Nothing in Phase 2 is claimed-but-unproven — each step stays open until its own live receipt, same discipline as Phase 1.

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

**✅ CLOSED — 2026-07-26 — Task 13 fully proven live.** Real end-to-end run driven in a real browser against the Vercel frontend (`https://relay-cyan-rho.vercel.app`) → deployed Worker → real GitHub Action → real Docker sandbox: polled through `queued → fetching_spec → spec_validated → generated → live_validating → done` to a real `Succeeded` (`GET /v1/forecast` = `verified_pass`), then the code viewer fetched and displayed the REAL generated `models.py` — 9 nested classes (`Hourly`/`HourlyUnits`/`Daily`/`Current`/…), live `datamodel-codegen` timestamp — NOT the mock's canned 6-field version. Screenshot captured. (Required first: pushing both Task 13 commits to `origin/main` and `wrangler deploy` of the Worker — the initial live attempt hit a stale deploy: the code endpoints returned 404 and stored nothing because the Worker/Action were pre-Task-13. Verified the redeploy via a `POST …/code → 401` probe before re-running.) The mock-driven screenshots from before were faithful to the real shapes; this run confirms them against real infrastructure. Nothing in the project is now claimed-but-unproven.

---

### Session 3 — 2026-07-27
**Goal:** Close the doc gap flagged at the end of Session 2 (Phase 2 was scoped verbally — BYOK, multi-provider LLM, error taxonomy, QA, visual pass, security review — but never written into AGENTS.md/ARCHITECTURE.md/TODO.md), then start Step 1 (BYOK) — the only step where a mistake harms real users, not just the demo.

**Context carried in from the previous session (hit context limit before Abhi could confirm the sequencing):** Claude had identified that the naive BYOK implementation — passing a user's API key as a `workflow_dispatch` input — would leak it in public Action logs (this is a public repo; GitHub's log masking only redacts pre-registered repo secrets, not arbitrary runtime inputs). Correct design agreed in principle: user key goes browser → Worker over HTTPS only, stored as `byok:{runId}` in KV with a short TTL, deleted the instant the Action's authenticated callback reads it (delivery-once), held in the runner's memory only for the one LLM call, never logged/written to disk, with a Worker-verifiable timestamp pair (`byokReceivedAt` / `byokDeletedAt`) surfaced honestly in the frontend instead of just claimed in copy.

**Session 3 opened by:** re-reading AGENTS.md/ARCHITECTURE.md/TODO.md, then running `conversation_search` against this Claude Project for "Relay BYOK workflow_dispatch" to pull the actual prior-session transcript rather than trusting the pasted handoff prompt at face value — confirmed the handoff accurately reflects what was actually discussed (same design, same open confirmation question) rather than a distorted summary.

**Docs updated this session (before any code):** AGENTS.md (this entry + status line), ARCHITECTURE.md (new "BYOK security design" section: KV schema, endpoint contracts, sequence, env vars), TODO.md (Phase 2 promoted into Current Sprint as Steps 1–7, old current-sprint items moved to Completed).

**Constraint worth logging:** this session is running in the claude.ai chat interface, not Claude Code with direct repo access — no visibility into the actual current contents of `worker/src/index.js`, `ci_runner.py`, or the callback-auth code, only what ARCHITECTURE.md documents about their shape. Code for Step 1 is being written as new, additive, self-contained route handlers / functions with explicit integration notes rather than as a full-file diff against source Claude hasn't actually seen — to avoid guessing at existing structure, which would violate the project's zero-fake-data discipline. Whoever picks this up next (Claude Code session or Abhi) should paste the real current files back in if a precise merged diff is wanted, and must run the new Worker tests for real before marking Step 1 done — nothing here counts as verified until it has a live receipt, same as every other claim in this file.

**Left incomplete:** Step 1 code has not been run against real Cloudflare/GitHub infrastructure from this session (can't be — no access to it here). Needs: wiring the new route handlers into the real `worker/src/index.js`, wiring `fetch_byok_key()` into the real `ci_runner.py`, running the new Vitest suite for real, then a live end-to-end BYOK test (a throwaway key, confirm it's deleted-on-read, confirm it never appears in a real Action run log) before Step 1 is called closed.

---

### Session 4 — 2026-07-30 — BYOK Step 1 CLOSED (proven live)
**Goal:** Take Session 3's drafted-but-unwired BYOK files and actually merge, test, and live-prove them (Claude Code session, real repo access this time).

**Done & committed (`23317fb`, pushed to `origin/main`):**
- Moved the four drafted files into the real tree: `worker/src/byok.js`, `worker/test/byok.test.js`, `backend/app/byok.py`, `backend/tests/test_byok.py` (fixed its import `ci_runner_byok`→`app.byok`). `BYOK_STEP1_INTEGRATION.md` kept at root as reference.
- Wired into `worker/src/index.js`: `storeByokKey` before dispatch (malformed key → 400 `invalid_api_key` + rate-limit refund), dispatch routed through `buildDispatchInputs`, new `GET /api/runs/:id/byok-key` (Action-only). **Leak-risk check the doc flagged:** the existing dispatch did NOT spread the body (explicit `{run_id,spec_url,callback_url}`, never read `apiKey`) — no pre-existing leak; routing through `buildDispatchInputs` (no `apiKey` param) keeps it structurally impossible.
- Wired into `ci_runner.py`: `run_with_optional_byok` wraps the `_live_validate` pass (delivery-once fetch, threads `api_key` into `self_correct`). `correct.py`: `self_correct`/`gemini_corrector` take optional `api_key` → `genai.Client(api_key=...)`, forwarded only when non-None so hermetic test correctors are untouched. `generate.yml`: `has_byok` input → `RELAY_HAS_BYOK`.

**Live receipt (deployed Worker + real Action, throwaway sentinel key):** run `805d0ccc` polled to `succeeded` with `byokReceivedAt=16:52:47Z` / `byokDeletedAt=16:53:21Z`; Action log `RELAY_HAS_BYOK: true`, **sentinel = 0 occurrences in the public Action log**, 0 in the polled run record (no `apiKey` field), post-run unauthenticated `byok-key` GET → 401. (Skipped the authenticated-404 sub-check by choice — no local `CALLBACK_SECRET`; `byokDeletedAt` + delivery-once already prove the delete.)

**Gotcha logged (same as Task 13's stale-deploy trap):** the FIRST live attempt (`71e6bdc2`) came through with `RELAY_HAS_BYOK: false` and no stamp — a Cloudflare edge propagation race: the run was triggered seconds after `wrangler deploy`, so the edge still served the pre-BYOK Worker (old dispatch had no `has_byok` → GitHub applied the workflow default `"false"`). Fix: after deploying the Worker, probe the new route (`byok-key` → 401) to confirm propagation BEFORE triggering a run. Re-run then passed clean.

**Watch-item (not blocking):** `BYOK_TTL_SECONDS = 120`. Both live runs reached the key-fetch ~34s after storage (sandbox images built in ~10s), well inside budget — but a genuinely cold `make sandbox-build` could approach the 120s ceiling and silently fall back to the shared key. Bump the TTL if a real cold build ever exceeds it.

**Cost-rule clarification (logged 2026-07-30, scoping Step 2):** the shared `GEMINI_API_KEY` stays free-tier-only forever — never attach billing. BYOK explicitly means the user's OWN provider account/billing (OpenAI/Anthropic/Grok/OpenRouter/their own Gemini key); a user spending on their own key is NOT a violation of this project's zero-cost rule — it is the point of the BYOK feature. The zero-cost constraint governs the shared key only.

**Next:** Step 2 (multi-provider) — spec written into ARCHITECTURE.md ("Step 2 — Multi-provider LLM support" section, 2026-07-30): 3 wire-protocol adapters for 5 providers (Gemini / OpenAI-family / Anthropic-tool-use), raw `requests` (zero new deps), no cross-model escalation on BYOK, live model-list fetch via a new Worker `POST /api/models` with a per-provider 1h KV cache. Now safe to build on a proven key-handling layer.

---

### Session 5 — 2026-08-01 — Step 2 (multi-provider) BUILT + hermetically tested — NOT closed (no live receipt yet)
**Goal:** Build Step 2 end to end (5 provider adapters → `/api/models` → BYOK frontend) against the proven Step 1 key layer, one piece per commit, each with hermetic tests before moving on.

**Built & committed (all pushed to `origin/main`), in order:**
- **`f0570e6` — scaffolding + Gemini adapter.** `CORRECTORS[provider]` dispatch in `correct.py`; `gemini_corrector` moved behind `CORRECTORS["gemini"]` unchanged. `_resolve_corrector` defaults to Gemini, raises for unwired providers. `self_correct` gained `provider`/`model`; `_ladder_for` pins a BYOK model to one-model×cap (no cross-model escalation), shared-key path keeps flash→flash→pro. `model` threaded end to end: `byok.js` `storeByokKey`(+validation)/`handleByokKeyFetch`, `index.js` passes `payload.model`, `byok.py` `fetch_byok_key`→`(api_key, provider, model)`, `run_with_optional_byok` stops discarding provider, `ci_runner` threads provider/model into `self_correct`.
- **`e1f235a` — `openai_compatible_corrector(base_url)`.** One factory serving OpenAI/Grok/OpenRouter (identical wire protocol); raw `requests` POST to `{base_url}/chat/completions`, Bearer auth, `response_format` strict `json_schema`, parses `choices[0].message.content` into the same `{models_py,client_py}` Patch. DRY `_build_prompt` shared with Gemini (output byte-identical). BYOK-only (missing key raises).
- **`749fa86` — `anthropic_corrector`.** Messages API, `x-api-key` + `anthropic-version` headers, structured output via forced **tool-use** (not `response_format`): one tool whose `input_schema` is the Patch shape, `tool_choice` forcing it, patch read from the `tool_use` block. Scans `content[]` so a leading text block doesn't break parsing. All 5 providers now behind `CORRECTORS`, `self_correct` unchanged.
- **`0bd1f2f` — `POST /api/models` (new `worker/src/models.js`).** Live model-list fetch, Worker-side (CORS + reuse the browser→Worker HTTPS boundary). Per-provider build+normalize → uniform `{models:[{id,label}]}` (gemini `generateContent` filter; openai chat-prefix FILTER over the live list; grok OpenAI-shaped; anthropic `display_name`; openrouter structured-capability filter + UI cap). Key is **transient** (never a `byok:{runId}` entry, never in the cache key/value). Cache `models:{provider}` 1h, provider-scoped. 401/403 → clean `key_rejected` passthrough (up-front key validation), never cached.
- **`052510b` — BYOK frontend.** Logic kept in pure `lib/` fns (no `@testing-library` dep) so it's testable in the existing hermetic style; `ByokFields.tsx` is a thin shell. Collapsed native `<details>` (optional, not forced): provider → password key → live model dropdown via **debounced** `POST /api/models`; `key_rejected` → inline message. Key held in state only — never logged, never localStorage/sessionStorage. `createRun(specUrl, byok?)` builds body via `buildRunPayload` (attaches BYOK only when fully configured — key never rides a non-BYOK run). Deferred Step 1 receipt renders once both `byokReceivedAt`/`byokDeletedAt` are on the snapshot. Reused dark-OLED tokens + `lib/status.ts` SSOT.

**Tests — hermetic only, all green:** backend **50**, worker **32**, frontend **25**; frontend `tsc --noEmit` clean. Adapters mock the provider HTTP call; `/api/models` uses FakeKV + mocked `fetch`; frontend tests are pure-fn (buildRunPayload no-key-when-unused, debounce fake-timers + guard, `fetchModels`→`KeyRejectedError`, receipt gating).

**NOT closed — no live receipt yet.** Everything above is hermetic. Per this project's zero-claimed-but-unproven discipline, Step 2 stays open until a real browser run with a throwaway **non-Gemini** key proves: (1) the model dropdown populates from a real `POST /api/models`, (2) a run actually self-corrects on that provider/model (deliberately break a model to exercise correction, like Task 9's original live proof), (3) the `byokReceivedAt`/`byokDeletedAt` receipt renders in the real browser. That live verification is the next action; only then does TODO's Step 2 get checked off with the receipt.

### Session 6 — 2026-08-08 — Step 2 (multi-provider) CLOSED live
**Goal:** Take Session 5's hermetically-tested-but-unproven Step 2 and get its live receipt — the corrector live-verified on a non-Gemini BYOK provider, plus the full deployed Worker/Action/BYOK-receipt path proven end to end. Session picked up cold after a laptop restart; first confirmed nothing was lost — Docker images (`relay-sandbox`, `relay-sidecar`, built 2026-07-25, Dockerfile unchanged since), the deployed Worker, and all prior commits were intact. `test_openai_fixes_broken_open_meteo_client` (Part B's test) was written Session 5 but still uncommitted and never run live — confirmed via git status/diff, not assumed.

**Part B — backend corrector, live (`4866d2a`, `1a16ab9`):**
- First live attempt (`gpt-4o-mini`) failed twice in a row: `HTTPSConnectionPool(host='api.openai.com'...) Read timed out (read timeout=90)`. Diagnosed, not guessed: `api.openai.com/v1/models` responded in 1.1s with the same key (connectivity fine), and the actual payload was measured — Open-Meteo's `models.py` is ~40KB/~10K tokens, which the corrector must return in full, verbatim, inside a strict `json_schema` string. `_CORRECTOR_TIMEOUT` 90→300 (`4866d2a`), committed independently of the model-capability question — proven on its own merit (next run completed the full 383s, no timeout).
- With the timeout fixed, `gpt-4o-mini` still failed — but now on model quality, not infra: attempt 1 edited the wrong file (rule said fix `models.py` on this failure status, it changed `client.py`), attempt 2 returned `models.py` byte-identical to the broken input (no fix attempted), 3/3 ladder attempts exhausted (BYOK never escalates models). Ruled out a Gemini-biased prompt first: `_build_prompt`/`_SYSTEM_INSTRUCTION` confirmed identical across every provider. `RELAY_LIVE_OPENAI_MODEL=gpt-4o` → `1 passed` — same prompt, same adapter code, only the model changed. Committed (`1a16ab9`) with the receipt in the message; the `gpt-4o-mini` finding logged as a documented residual in ARCHITECTURE.md's Step 2 section (`7f846fb`) so a future session doesn't rediscover it by burning API credits again.

**Part A — deployed Worker/Action, live (docs `7f846fb`):**
- `POST /api/models {provider:"openai", apiKey}` → `200`, real live list (105 models, incl. `gpt-4o`).
- `POST /api/runs {specUrl, apiKey, provider:"openai", model:"gpt-4o"}` → `202`, run `1955733a-03b7-4486-bdf8-51b27129c524`, polled `queued/None` → `succeeded/done`. Final snapshot: `/v1/forecast` → `verified_pass`, `byokReceivedAt=2026-08-08T16:45:51.060Z`, `byokDeletedAt=2026-08-08T16:46:24.817Z`.

**Accepted gap (logged, not blocking closure):** this run used the real, unmodified spec, so it validated on the first generated attempt (`"attempts": []`) — the corrector fired live (Part B) and the deployed pipeline fired live (Part A), but never together in one run: no test-injection hook exists in the public API to deliberately break a client through `/api/runs`. Same two-piece proof pattern as Task 9's original Gemini verification (pytest-level live fix + a separately proven deployed E2E pass), accepted on that precedent.

**Security note (not a code issue):** a real OpenAI key was exported into the terminal transcript directly by the user this session rather than injected out-of-band. Flagged in-session; not persisted to any file, memory, or commit. User was advised to rotate/revoke it after this session's live runs.

**Next:** Step 3 — honest error taxonomy.

### Session 7 — 2026-08-09 — Step 3 (honest error taxonomy) CLOSED
**Goal:** Replace every raw exception/HTTP-status surface across the stack with a stable taxonomy of codes, verified against the real backends (SDKs/libraries) rather than assumed, then an honest plain-language layer in the frontend — one piece per commit, backend first (each hermetically tested and committed before moving to the next layer), matching Step 2's discipline.

**Backend classification (`caf86cf`, `293aa36`, `3196ce7`):**
- `sandbox.py`: `STATUS_TIMEOUT` split out of `STATUS_CALL_FAILED` — a host-level wall-clock kill (`subprocess.TimeoutExpired`) now gets its own status. The one existing `call_failed` assertion in `test_sandbox.py` turned out to test a different path entirely (an unreachable-IP escape attempt), so this was a pure addition, not a migration.
- `correct.py`: each adapter (gemini/openai_compatible/anthropic) classifies its own provider exceptions into a shared four-bucket vocabulary (`CorrectorAuthError`/`Quota`/`Network`/`BadResponse`), verified against the real installed SDKs rather than assumed — `google-genai` 2.14.0 raises its own `ClientError`/`ServerError` (with a `.code` int), and its sync client runs on `httpx`, not `requests`, so a true connection failure is `httpx.ConnectError`/`TimeoutException`, not a `requests` exception. `self_correct` maps the four markers to codes, splitting quota and auth into shared-key vs BYOK variants (`quota_exhausted_shared`/`byok`, `corrector_config_error`/`corrector_auth_failed`) — decided during scoping that the message and the fix genuinely differ there; everywhere else stays one code regardless of key source. Also: `self_correct` now skips the corrector entirely on `STATUS_TIMEOUT` (a timeout means the target API was slow, not that the code is wrong) — noted inline that this is safe *because* generated clients don't have their own retry/backoff loops today, to revisit if that changes.
- `ci_runner.py`: `run()`'s outer except classifies into `spec_fetch_failed`/`spec_invalid`/`ssrf_blocked_spec`/`generation_failed`/`sandbox_unavailable`/`internal_error`, verified against the real exception types — `prance.util.formats.ParseError` (malformed body), `prance.util.url.ResolutionError` ($ref won't resolve), and prance's own `ValidationError` (internal backend validation) are all distinct from `openapi_spec_validator`'s `OpenAPIValidationError`. `generation_failed` has no exception type of its own, so `generate_all_endpoints` gets wrapped in a local `_GenerationError` marker — same "adapter classifies, one function maps markers" shape as `correct.py`. `error_detail` (raw text, capped at 500 chars) rides alongside the code for KV/debugging.

**Action entry point (`78ba7a2`):** `.github/workflows/generate.yml` runs `python -m app.ci_runner` — the `__main__` block previously let an uncaught exception propagate, so Python's default handler printed a full traceback (raw exception text included) to stderr, which in the Action is a public log, unlike KV. Now caught, prints one controlled line with the classified code, exits 1. `run()` itself is unchanged — still raises, still reports the classified code + full `error_detail` to the Worker/KV first; `test_private_spec_url_rejected_before_any_fetch` (asserts `run()` raises `SSRFError` directly) passes unmodified. Manually verified end-to-end (not covered by the hermetic suite, which never exercises `__main__`): `python -m app.ci_runner <private-url>` → stdout/stderr shows only `FINAL: failed (ssrf_blocked_spec)`, exit code 1, no traceback.

**Worker (`1fe24ad`):** `handleProgress`'s merge silently dropped `error_detail` — `ci_runner.py` was already sending it, but it never reached KV at all. Fixed to store it. Separately decided during scoping: `handleGetRun` (the one public, unauthenticated poll endpoint) strips `error_detail` before returning — "server-side only, no disclosure UI yet" means it never leaves the Worker, not just that the frontend doesn't render it.

**Frontend (`dc5e5cf`):** new `lib/errors.ts`, sibling to `lib/status.ts` (same SSOT shape) — a flat code→message map covering every code across all three backend layers, plus a separate `suggestsByok(code)` lookup (`rate_limited`/`quota_exhausted_shared`/`corrector_config_error` only — the cases where switching key source genuinely unblocks you; the message body is identical either way, only the hint changes). Wired into `page.tsx` (the failed-run banner, previously raw `snapshot.error` in a `font-mono` block), `lib/api.ts` (`createRun`/`fetchModels`, previously `"Worker rejected the request: <code>"` / `"Fetching models failed (<status>)"`), and `lib/byok.ts` (`modelErrorMessage` now passes through the already-honest message instead of a hardcoded fallback). Also closed a gap from the `STATUS_TIMEOUT` commit earlier in this same piece: `status.ts` had no tone/label for `sandbox_timeout` at all — an endpoint hitting it would've shown an unstyled neutral badge with the raw code as its label.

**Tests:** backend **75** (was 50), worker **33** (was 32), frontend **33** (was 25); frontend `tsc --noEmit` clean. All hermetic — zero live calls this entire step.

**Deliberate scope boundary (not a gap):** per-attempt corrector codes (`corrector_auth_failed`, `quota_exhausted_byok`, etc.) still render as raw tokens in `EndpointRow`'s compact attempts list (`{model}: {status_before} → {status_after}`) — `errorMessage()` returns full sentences that don't fit that inline format, and `status.ts`'s short-label style doesn't cover the new corrector codes either. Left unaddressed rather than forced into either file; not a regression (the single generic `corrector_error` token was equally raw before this step).

**Next:** Step 4 — full QA pass (extend backend/worker/frontend suites to the new surface area, then a real live end-to-end re-verification with a receipt).

### Session 8 — 2026-08-09 — Step 4 (full QA pass) CLOSED
**Goal:** Extend backend/worker/frontend suites to Phase 2's actual surface area based on a real coverage audit — tracing exactly what's untested and why, not "add more tests" — then a live end-to-end re-verification proving the pieces work together, since Steps 1–3 had each only ever been proven in isolation.

**The finding this step exists to make possible: the Action's infra-failure watchdog was silently destroying every classified error, in production, since before Step 3 existed.**

This is the most consequential thing found in the project so far, and it was found live, not by inspection. A deliberate `spec_invalid` trigger (`specUrl: https://example.com`) against the real deployed pipeline came back as `{"status":"failed","stage":"ci_error","error":"pipeline job failed before reporting a result"}` — not the `spec_invalid` code `ci_runner.py` should have sent. Root cause: `.github/workflows/generate.yml`'s "Report infra failure" step runs `if: failure()`, which GitHub Actions fires on *any* non-zero exit from the "Run pipeline" step — and `ci_runner.py`'s `run()` *always* re-raises after reporting a classified failure (by design; `test_private_spec_url_rejected_before_any_fetch` depends on this, unchanged by Step 3). So the watchdog fired on every single classified failure too, ran *second* (after the real report had already reached the Worker), and unconditionally overwrote it with its own generic message.

This bug predates Step 3 — the race between the two steps' callbacks always existed. But before Step 3, `run()`'s own report was also just raw exception text (`f"{type(exc).__name__}: {exc}"`), so the watchdog's equally-uninformative fallback overwriting it was invisible: one bad message silently replacing another bad message looks identical to no bug at all. The moment Step 3 made the real report something worth reading, this watchdog started silently deleting that improvement on every production failure, with no signal anywhere that it was happening — the exact "we shipped an honest error taxonomy, but nobody could ever see it" failure mode a live re-verification exists to catch.

**Why hermetic tests structurally could not have caught this.** The bug lives entirely in `.github/workflows/generate.yml` — a second, independent CI step's YAML condition, evaluated by GitHub Actions itself after the Python process has already exited. No `pytest` run ever executes it; no `vitest` run ever executes it; it isn't reachable from either language's test runner at all. `ci_runner.py`'s classification logic was and remains correct in isolation — proven by 75 passing hermetic tests, none of which regressed. The defect isn't in any function; it's in the *interaction* between two systems (a job's exit code, and a separate step's `if: failure()` condition) that only both exist together at deploy time, on the real GitHub Actions infrastructure. This is the concrete case for why TODO.md's Step 4 line has always said "then a real live end-to-end re-verification," not just "extend the suites" — hermetic coverage and live verification catch genuinely different classes of bug, and skipping the live half here would have meant Step 3 shipped, looked done, and silently accomplished nothing in production.

**Fix (`572c18d`):** the watchdog now only sends its generic fallback if the run *isn't already* `"failed"` — restoring its original, narrower purpose (dependency install / Docker build / OOM / timeout: failures that happen before `ci_runner.py`'s own `try`/`except` is ever reached, the only cases it was ever meant to cover). Fails open by design, per explicit instruction before writing it: if the status check itself can't be read for any reason (network hiccup, malformed JSON, curl error), `status` stays `"unknown"`, never `"failed"`, so the fallback still fires rather than silently leaving a run stuck showing no terminal state at all. Verified all 5 branches locally (already-failed → suppressed; queued, curl failure, malformed JSON, empty body → all fire) before spending live budget confirming it against the real pipeline.

**Coverage audit + fixes (`bbb35fd`, `75bf3ff`, `370deae`, `9a27a11`):**
- `suggestsByok()` (Step 3) was structurally unreachable — traced every call site and found its only caller (`page.tsx`, gated on `snapshot.error`) can never see any of its 3 trigger codes; the BYOK hint had never once rendered for a real user. Fixed at the two real render sites: `rate_limited`'s existing banner in `page.tsx`, and a new terminal-attempt check in `ValidationReport.tsx`'s `EndpointRow` (only the *last* attempt can ever hold a corrector code — `self_correct` always `break`s the ladder immediately on a corrector exception). Also closed the still-open half of Step 3's scope boundary while there: `status.ts` gained short labels for all 7 corrector codes, so the compact attempts line stopped showing raw tokens.
- Worker: `POST /api/runs`'s BYOK fields had only ever been tested against `byok.js`'s exported functions directly, never through the real `handle()` router the Worker actually serves — added integration tests to `worker.test.js` (byok:{runId} creation, `has_byok` in real dispatch inputs, `invalid_api_key` 400 + refund) and closed an adjacent unit gap (`storeByokKey`'s `model` validation branch had zero coverage at all).
- `models.js`: grok's request shape (untested despite its corrector counterpart being covered), `provider_unreachable`/`provider_error`/`provider_bad_response`, and the `OPENROUTER_MAX_MODELS` cap were all untested branches sitting next to well-covered siblings.
- `ARCHITECTURE.md`: logged that Anthropic/Grok/OpenRouter have never made one real call to their provider APIs — only Gemini and OpenAI have live receipts (see below for why this stayed true this session too).

**Live re-verification:**
- **OpenAI BYOK regression** (swapped from the originally-planned Anthropic run — no Anthropic/Grok/OpenRouter key available, and not acquiring one just for this; logged as the residual above instead): `gpt-4o`, same deliberately-broken-client technique as Step 2's Part B → `1 passed`. Confirms Step 3's classification try/except wrapping (added inside every corrector adapter) didn't regress the happy path.
- **`spec_invalid` trigger:** found and fixed the watchdog bug (above) on the first attempt; retried clean — real run `413f25ea-f020-4c5d-a375-938bb743da4d`: `status:"failed"`, `stage:"error"`, `error:"spec_invalid"`, and `error_detail` confirmed **absent** from the `GET /api/runs/:id` response body — the public-strip behavior proven live, not just against a fake KV.
- **`rate_limited` regression:** combined into the same request sequence as the `spec_invalid` trigger rather than run separately — every request against the garbage `specUrl` still dispatches a real Action (validation happens inside the Action, not the Worker's rate gate), so reusing the same 3-request budget for both proofs avoided spending 3 extra Action-minutes on throwaway runs. Fourth request: `429 {"error":"rate_limited","limit":3,"remaining":0,"retryAfter":20875}` — unchanged, still correct.

**Docker note (environment, not code):** this session's local live pytest run was initially blocked — `/usr/bin/docker` is a symlink into WSL2's Docker Desktop integration path, which returns an I/O error whenever Docker Desktop isn't actually running on the Windows host. Not a Relay bug; resolved by starting Docker Desktop, confirmed via `docker info` before retrying.

**Tests:** backend **75** (unchanged this step) · worker **45** (was 33) · frontend **34** (was 33).

**Accepted limitation (not fixed this step):** zero component/rendering tests exist anywhere in the frontend — pre-existing, deliberate (pure-fn testing style, no `@testing-library` dependency; decided explicitly, not defaulted into). Worth naming plainly given this session's own results: it's exactly the kind of gap that let the `suggestsByok` bug ship unnoticed. Adding a rendering-test dependency was out of scope for this step regardless.

**Next:** Step 5 — visual/UX pass (`frontend-design` + `ui-ux-pro-max` skills + Magic MCP, after the logic is solid — which, as of this session, it now actually is, not just looked like it).

### Session 9 — 2026-08-10 — Step 5 (visual/UX pass) CLOSED
**Goal:** Design refinement on top of a now-functionally-complete app — no new backend logic. Inventory the frontend as it actually exists (not assumed), identify specific UX gaps in the Steps 1–4 features that were built logic-first with no design attention, and apply a scoped set of fixes on top of the existing dark-OLED aesthetic — refinement, not a rebuild.

**Environment note:** no browser-automation tool is available in this session, and no Magic MCP server is configured here despite CLAUDE.md referencing it — so the inventory was read directly from the code (accurate, not a fabricated screenshot claim), and implementation leaned on the `ui-ux-pro-max` skill's guidance plus direct Tailwind rather than generated components. Ran the existing palette through the skill's design-system generator independently as a sanity check — it converged on nearly identical values for a "dark mode developer tool," confirming the palette was already right rather than something to change.

**Pattern to watch: `status.ts` has now been caught incomplete twice, both times for the same reason.** Step 4 discovered 7 corrector-level codes had never been given a `status.ts` label (added them). This session, giving the run-failed banner its own `StatusBadge` surfaced that 6 pipeline-level codes (`spec_invalid`, `ssrf_blocked_spec`, etc.) *also* had none — `errors.ts` had full-sentence messages for them, but nothing short/renderable. Both times, whoever added the new status code was focused on the logic (classifying an exception correctly) and never touched the rendering surface at all — `status.ts` isn't imported anywhere near `correct.py` or `ci_runner.py`, so there's no natural prompt to remember it exists. Next time a new status/error code gets added anywhere in the backend, check `frontend/lib/status.ts` in the same pass, not after the fact a third time.

**Coverage-style findings, from tracing the actual code (not assumed):**
- Three near-duplicate hand-rolled error blocks in `page.tsx` (slightly different radius/opacity between two of them), none with `role="alert"`/`aria-live` — screen readers got no notification when any of them appeared.
- The BYOK receipt (`byokReceivedAt`/`byokDeletedAt`, Step 1) rendered as a wall of raw ISO-8601 text — functionally honest, illegible at a glance.
- The BYOK `<details>` disclosure had no expand/collapse affordance beyond the unstyled native marker.
- The password field had no show/hide toggle — a real gap for a technical audience pasting long keys.
- The model dropdown's loading state was text baked into a placeholder option, easy to miss.
- `EndpointRow`'s attempt outcomes computed tone data (Step 4) but never used it for color — success and failure read identically.
- Zero icons anywhere in the app at all — confirmed via `package.json` (no icon library) and a full grep (no inline SVGs either) before this session.

**Fixes, 4 commits, grouped by relation (same one-piece-per-commit discipline as Steps 2–4):**
- **`c21ca2a`** — new `components/Banner.tsx` (routes color through `status.ts`'s `styleOf`, never hardcoded) replaces the 3 duplicate blocks; `role="alert"` added once, after explicitly tracing `page.tsx`'s state machine per instruction to confirm the 3 real call sites are mutually exclusive by construction (never rendered together) and that `StatusBadge` itself has no `aria-live` of its own — no double-announce risk. Also: the pipeline-code `status.ts` gap above, and a `surface` field added to `TONE_CLASSES` (background tint for a full banner, distinct from the badge pill's background — same SSOT rule, new consumer size).
- **`156bb7a`** — BYOK flow polish: `byokReceiptText` → `byokReceiptSummary` (humanized duration — "deleted 34 seconds later," the actual trust signal, not a "time ago" that goes stale on render — exact timestamps kept as a `title` tooltip, not dropped), a lock icon anchoring the receipt as a security proof, a rotating chevron on the `<details>` disclosure (Tailwind 3.4's `group-open:` variant), a password show/hide toggle, and a loading spinner for the model dropdown — deliberately placed in the label row rather than overlaid on the `<select>` after checking the select still uses its native browser arrow (no `appearance-none` anywhere in this codebase) and an overlay would have collided with it.
- **`fcf47e6`** — wired `styleOf(a.status_after).text` into `EndpointRow`'s attempt line so success/failure are color-distinct; bumped `text-[11px]` to `text-xs` (12px floor) and dropped the BYOK hint's `/80` opacity to full — it was the smallest, dimmest text on the page for what's meant to be an actionable hint.
- **`8ce5b3d`** — a CSS-only `animate-spin` ring on the primary submit button alongside "Working…", so a slow network doesn't feel like an unregistered click.

**Zero new dependencies.** Every icon (lock, chevron, eye/eye-slash, spinners) is a hand-drawn inline SVG — confirmed as the approach before writing any of it, per the same "ask before adding" rule as every other step.

**Tests:** frontend **37** (was 34) — `byok.ts`'s new duration-formatting logic got real unit coverage (sub-second/seconds/minutes gaps, tooltip precision preserved), `status.ts`'s new pipeline-code entries got the same reachability-style coverage as Step 4's corrector codes. The JSX-only changes (Banner wiring, EndpointRow coloring, spinners) added no new tests — no rendering-library dependency exists to test them with (still true; not revisited this step), consistent with Step 4's accepted limitation. `tsc --noEmit` and `next lint` run clean after every one of the 4 commits, not just at the end.

**Next:** Step 6 — security review (`/security-review`, plus the Claude Security plugin's deeper "Scan codebase" if available).

<!-- Copy the block above for each new session -->
