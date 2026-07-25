# TODO.md — relay

Claude updates this at the end of every session.
Read this at the start of every session to know what to work on next.

---

## 🔴 Current Sprint (Do This Now)
- [ ] **Next up:** Backend: Docker sandbox runner — one `--rm`, network-restricted, resource/time-capped container that executes a generated client against the live API. Docker confirmed working natively in WSL (`docker run hello-world` succeeded) — unblocked.
- [ ] Backend: Claude Haiku self-correction loop on sandbox failure, capped retries, escalate to Sonnet after repeated failures
- [ ] Wire the above into one end-to-end job, tested against Open-Meteo
- [ ] Merge per-endpoint client files into one cohesive client package (currently each endpoint is standalone) — natural to do alongside sandbox execution

---

## 🟡 Up Next (After Current Sprint)
- [ ] TypeScript client generation (openapi-typescript)
- [ ] Frontend: Next.js page to paste a URL and watch SSE progress
- [ ] SQLite (WAL) per-IP daily rate limiting
- [ ] Prism mock fallback for endpoints unsafe to call live

---

## 🟢 Backlog (Future)
- [ ] MCP-server-compliant tool wrapper output (stretch goal)
- [ ] Caddy reverse proxy + Oracle Cloud VM deploy
- [ ] Vercel deploy for frontend

---

## ✅ Completed
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
