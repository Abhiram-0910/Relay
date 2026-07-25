# TODO.md — relay

Claude updates this at the end of every session.
Read this at the start of every session to know what to work on next.

---

## 🔴 Current Sprint (Do This Now)
- [x] Backend: Docker sandbox runner — one `--rm`, resource/time-capped, SSRF-guarded container that executes a generated client against the live API. **Done Session 2**: `verified_pass` live against Open-Meteo `/v1/forecast`.
- [x] **Task 8.5: network isolation** — sandbox on a per-run `--internal` net, egress only via a socat sidecar pinned to the single validated IP:port. **Done Session 2**: live-verified — Open-Meteo still `verified_pass`, a hardcoded call to a different public IP (1.1.1.1) fails unreachable. Untrusted LLM code is now safe to run here.
- [x] **Task 9: Gemini Flash self-correction loop** — capped 2 Flash → 1 Pro → hard fail, every attempt re-run through the full sandbox. **Done Session 2**: live-verified — a deliberately-broken Open-Meteo response model (`verified_live_validation_failed`) fixed by `gemini-3.5-flash` on attempt #1 → `verified_pass`.
- [x] **End-to-end pipeline runner** (`ci_runner.py`): parse→generate→sandbox→self-correct in one job. **Done Session 2**: live `succeeded` / forecast `verified_pass` against Open-Meteo.
- [x] **Hosting pivot**: Cloudflare Worker (trigger + KV state + KV per-IP rate limit) + GitHub Actions workflow invoking `ci_runner`. **Done Session 2**: 9 worker unit tests (rate-limit-before-dispatch, run-id isolation, callback auth, write throttle) + ci_runner live-verified.
- [x] SSRF guard on the spec-fetch/`$ref` step in `ci_runner` — **Done Session 2**: pre-fetch host validation + `guarded_prance_resolve` gating prance's fetch choke point (blocks private hosts + `file`/`python` schemes); 3 default-suite tests. Open-Meteo still `verified_pass` through the guard.
- [x] **Provision + deploy** — **Done Session 2, LIVE-VERIFIED.** Worker at `https://relay-worker.abhiram-dev.workers.dev`; `scripts/verify_deployed.sh` → **ALL CHECKS PASSED**: two concurrent runs isolated + both `verified_pass` on live Open-Meteo, rate limiter `429`'d request #4. Deploy fixes: default branch master→main, added git remote + pushed, re-scoped token with `workflow` scope.
- [ ] **NEXT — Task 13: frontend.** Next.js 14 + Tailwind on Vercel against the deployed Worker API: paste URL → `POST /api/runs` → poll `GET /api/runs/:id`, render checkpoint progression + final per-endpoint `verified_pass` result. API is stable and CORS-open.
- [ ] Merge per-endpoint client files into one cohesive client package (currently each endpoint is standalone).

---

## 🟡 Up Next (After Current Sprint)
- [ ] TypeScript client generation (openapi-typescript)
- [ ] Task 13 — Frontend: Next.js + Tailwind on Vercel, built against the deployed Worker API (paste URL → poll progress)
- [ ] General required-param synthesis (retire the Open-Meteo demo-param hook)
- [ ] Prism mock fallback for endpoints unsafe to call live

---

## 🟢 Backlog (Future)
- [ ] MCP-server-compliant tool wrapper output (stretch goal)
- [ ] ~~Caddy + Oracle Cloud VM deploy~~ — dropped in favor of the Cloudflare Worker + GitHub Actions hosting pivot (no always-on VM; the Action builds the sandbox images per run)
- [ ] Vercel deploy for frontend (Task 13)

---

## ✅ Completed
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
