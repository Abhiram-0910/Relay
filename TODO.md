# TODO.md — relay

Claude updates this at the end of every session.
Read this at the start of every session to know what to work on next.

---

## 🔴 Current Sprint (Do This Now)
- [ ] Backend: parse + validate an OpenAPI URL over SSE (`/api/parse-spec`) — in progress
- [ ] Backend: generate Python types (datamodel-code-generator) + client (Jinja2) from the parsed spec
- [ ] Backend: Docker sandbox runner — one `--rm`, network-restricted, capped container that executes the generated client against the live API
- [ ] Backend: Claude Haiku self-correction loop on sandbox failure, capped retries, escalate to Sonnet after repeated failures
- [ ] Wire the above into one end-to-end job, tested against Open-Meteo

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

---

## 🐛 Known Bugs
(none yet)

---

## 💡 Ideas / Notes
- Confirm Docker is actually available in the dev/deploy environment before building the sandbox runner (flagged as a risk in session 1).
