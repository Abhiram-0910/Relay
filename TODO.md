# TODO.md — relay

Claude updates this at the end of every session.
Read this at the start of every session to know what to work on next.

---

## 🔴 Current Sprint (Do This Now)
- [x] Backend: Docker sandbox runner — one `--rm`, resource/time-capped, SSRF-guarded container that executes a generated client against the live API. **Done Session 2**: `verified_pass` live against Open-Meteo `/v1/forecast`.
- [x] **Task 8.5: network isolation** — sandbox on a per-run `--internal` net, egress only via a socat sidecar pinned to the single validated IP:port. **Done Session 2**: live-verified — Open-Meteo still `verified_pass`, a hardcoded call to a different public IP (1.1.1.1) fails unreachable. Untrusted LLM code is now safe to run here.
- [x] **Task 9: Gemini Flash self-correction loop** — capped 2 Flash → 1 Pro → hard fail, every attempt re-run through the full sandbox. **Done Session 2**: live-verified — a deliberately-broken Open-Meteo response model (`verified_live_validation_failed`) fixed by `gemini-3.5-flash` on attempt #1 → `verified_pass`.
- [ ] **Next up:** Wire parse→generate→sandbox→self-correct into one end-to-end SSE job, tested against Open-Meteo.
- [ ] Wire parse→generate→sandbox into one end-to-end job over SSE, tested against Open-Meteo
- [ ] Merge per-endpoint client files into one cohesive client package (currently each endpoint is standalone) — natural to do alongside the end-to-end job

---

## 🟡 Up Next (After Current Sprint)
- [ ] TypeScript client generation (openapi-typescript)
- [ ] Frontend: Next.js page to paste a URL and watch SSE progress
- [ ] SQLite (WAL) per-IP daily rate limiting
- [ ] Prism mock fallback for endpoints unsafe to call live

---

## 🟢 Backlog (Future)
- [ ] MCP-server-compliant tool wrapper output (stretch goal)
- [ ] Caddy reverse proxy + Oracle Cloud VM deploy — **must run `make sandbox-build` once on the VM** (the sandbox runner fails fast without the `relay-sandbox` image)
- [ ] Vercel deploy for frontend

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
