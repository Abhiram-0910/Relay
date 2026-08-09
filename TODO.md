# TODO.md — relay

Claude updates this at the end of every session.
Read this at the start of every session to know what to work on next.

---

## 🔴 Current Sprint (Do This Now) — Phase 2: BYOK → multi-provider → hardening
MVP (everything below in ✅ Completed) is done and live-verified. This sprint is the phase scoped
verbally at the end of Session 2 (2026-07-26), now written up properly — see AGENTS.md Session 3
and ARCHITECTURE.md's "BYOK security design" section for the full spec. Nothing here is built yet;
each step gets its own live receipt before it's checked off, same discipline as Phase 1.

- [x] **Step 1 — BYOK security infrastructure (CLOSED, Session 4, `23317fb`).** Ephemeral `byok:{runId}` KV entry, delivery-once via authenticated callback, key never enters `workflow_dispatch` inputs (structurally — `buildDispatchInputs` has no `apiKey` param), honest `byokReceivedAt`/`byokDeletedAt` surfaced on the run record. Wired into real `worker/src/index.js` + `ci_runner.py`/`correct.py`; suites green (worker 21, backend 40). Live-proven with a throwaway sentinel key: `byokDeletedAt` stamped, **sentinel absent from the public Action log**, unauth `byok-key` → 401. Frontend still needs to render the `byokReceivedAt`/`byokDeletedAt` receipt (data is live on the run object — small, do at Step 2 UI pass). Watch-item: 120s KV TTL headroom vs. a cold sandbox build.
- [x] **Step 2 — Multi-provider LLM support (CLOSED, Session 6, commits `4866d2a`→`7f846fb`).** Gemini, OpenAI, Anthropic, Grok, OpenRouter — 3 wire-protocol adapters behind `CORRECTORS` (Gemini native schema / OpenAI-family shared `base_url` / Anthropic tool-use), `model` plumbed end to end, no cross-model escalation on BYOK. Live model lists via `POST /api/models` (per-provider 1h KV cache); BYOK frontend built Session 5. **Live receipt — Part A (deployed Worker+Action):** `POST /api/models {provider:"openai"}` → real 105-model list; `POST /api/runs` with a real BYOK key/provider/model → run `1955733a…` polled `queued`→`succeeded`/`done`, `/v1/forecast` `verified_pass`, `byokReceivedAt`/`byokDeletedAt` stamped (docs `7f846fb`). **Part B (backend corrector):** `self_correct(provider="openai")` live-verified — `gpt-4o` fixes a deliberately-broken Open-Meteo client within the BYOK ladder (`1a16ab9`); `_CORRECTOR_TIMEOUT` 90→300 bumped and proven independently first (`4866d2a`). **Accepted gap:** the corrector was proven live independently of the deployed pipeline, not exercised together in one deployed run — no test-injection hook exists in the public API to deliberately break a client through `/api/runs`. Same two-piece proof pattern as Task 9's original Gemini verification. **Documented residual (not blocking):** `gpt-4o-mini` fails this correction 3/3 (large-payload verbatim-copy-plus-edit); `gpt-4o` succeeds — model capability ceiling, see ARCHITECTURE.md Step 2 section.
- [ ] **Step 3 — Honest error taxonomy**: plain-language mapping for every real failure mode (rate limit w/ exact reset time, provider auth failure, sandbox timeout, SSRF block, quota exhausted, network error), each consistently offering the "use your own key" path, never a raw technical error surfaced to the end user.
- [ ] **Step 4 — Full QA pass**: extend backend/worker/frontend suites to the new surface area, then a real live end-to-end re-verification with a receipt — same standard as every prior claim in this project.
- [ ] **Step 5 — Visual/UX pass**: `frontend-design` + `ui-ux-pro-max` skills + Magic MCP, after the logic is solid, not before.
- [ ] **Step 6 — Security review**: `/security-review`, plus the Claude Security plugin's deeper "Scan codebase" if available; fix everything found.
- [ ] **Step 7 — Final full retest, then push and deliver.**
- [ ] Merge per-endpoint client files into one cohesive client package (currently each endpoint is standalone) — deferred from Phase 1, not urgent.

---

## 🟡 Up Next (After Current Sprint)
- [ ] TypeScript client generation (openapi-typescript)
- [ ] General required-param synthesis (retire the Open-Meteo demo-param hook)
- [ ] Prism mock fallback for endpoints unsafe to call live

---

## 🟢 Backlog (Future)
- [ ] MCP-server-compliant tool wrapper output (stretch goal)
- [ ] ~~Caddy + Oracle Cloud VM deploy~~ — dropped in favor of the Cloudflare Worker + GitHub Actions hosting pivot (no always-on VM; the Action builds the sandbox images per run)
- [ ] Vercel deploy for frontend (Task 13)

---

## ✅ Completed
- [x] Task 13 fully proven live: real browser run, Vercel frontend → deployed Worker → real Action/Docker/sandbox, polled to real `Succeeded`/`verified_pass`, code viewer showed the real generated `models.py`, not a mock. No claimed-but-unproven work remained in Phase 1 as of this — Session 2 close, 2026-07-26
- [x] Task 13 frontend (Next.js 14 + Tailwind): trigger/poll/report loop + on-demand code viewer (syntax-highlighted, download, honest truncation banner), status→color SSOT. Worker gained `code:{runId}` store + endpoints. Tests: frontend 7, worker 12, backend 33 — Session 2, 2026-07-25/26
- [x] Provision + deploy, live-verified: Worker at `https://relay-worker.abhiram-dev.workers.dev`; `scripts/verify_deployed.sh` → ALL CHECKS PASSED (two concurrent isolated runs, both `verified_pass`, rate limiter 429'd request #4) — Session 2, 2026-07-25
- [x] Spec-fetch/`$ref` SSRF guard in `ci_runner`: pre-fetch host validation + `guarded_prance_resolve` gating prance's fetch choke point, blocks private hosts + `file`/`python` schemes; 3 tests — Session 2, 2026-07-25
- [x] Hosting pivot: Cloudflare Worker (trigger + KV state + KV per-IP rate limit) + GitHub Actions invoking `ci_runner`; 9 worker unit tests + ci_runner live-verified — Session 2, 2026-07-25
- [x] End-to-end pipeline runner (`ci_runner.py`): parse→generate→sandbox→self-correct in one job, live `succeeded`/`verified_pass` against Open-Meteo — Session 2, 2026-07-25
- [x] Gemini self-correction loop (Task 9): capped Flash→Pro ladder, sandbox re-run every attempt; live-verified fix of a broken Open-Meteo model → `verified_pass` — Session 2, 2026-07-25
- [x] Dropped Anthropic entirely; LLM layer is Google Gemini free tier only (never enable billing) — Session 2, 2026-07-25
- [x] Sandbox network isolation (Task 8.5): per-run `--internal` net + pinned socat sidecar; other-public-IP-unreachable proven live — Session 2, 2026-07-25
- [x] Docker sandbox runner: `--rm`, read-only, capped, SSRF pre-flight guard, structured report; live `verified_pass` on Open-Meteo `/v1/forecast` — Session 2, 2026-07-25
- [x] Filled in CLAUDE.md, ARCHITECTURE.md, TODO.md, git init — Session 1, 2026-07-24
- [x] `/api/parse-spec` SSE endpoint: fetch + parse (prance) + validate (openapi-spec-validator) — Session 1, 2026-07-24
- [x] Deterministic generation slice: one GET endpoint's Pydantic model + client method, live-verified against real Petstore spec, compile-checked — Session 1, 2026-07-24
- [x] Scaled generation to every endpoint: query params, JSON request bodies, per-endpoint SSE progress, full-spec live test (14/19 generated, 5/19 correctly skipped, all compile-checked) — Session 1, 2026-07-24

---

## 🐛 Known Bugs
(none yet)

---

## 💡 Ideas / Notes
- Docker was unavailable earlier in session 1 (`docker: command not found`), then installed natively in WSL by the user in parallel — `docker run hello-world` now succeeds. Task 8 (sandbox runner) is unblocked.
