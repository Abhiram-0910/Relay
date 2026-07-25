# TODO.md — relay

Claude updates this at the end of every session.
Read this at the start of every session to know what to work on next.

---

## 🔴 Current Sprint (Do This Now)
- [x] Backend: Docker sandbox runner — one `--rm`, resource/time-capped, SSRF-guarded container that executes a generated client against the live API. **Done Session 2**: `verified_pass` live against Open-Meteo `/v1/forecast`.
- [ ] **Next up (Task 9):** Backend: Claude Haiku self-correction loop on sandbox failure, capped retries, escalate to Sonnet after repeated failures. Consume `sandbox.run_in_sandbox`'s structured status — `verified_live_validation_failed` → fix the response model, `call_failed` → fix request-building. **Before untrusted LLM code runs here, add real egress firewalling** (see ARCHITECTURE debt + `sandbox.py` ponytail note).
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
