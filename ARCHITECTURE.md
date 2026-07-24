# ARCHITECTURE.md — relay

Claude reads this file at the start of every session to orient itself in the codebase.
Keep this updated as the project evolves.

---

## Stack
| Layer | Technology | Why chosen |
|-------|-----------|------------|
| Frontend | Next.js 14 + Tailwind | Free-tier Vercel deploy, SSE-friendly |
| Backend | FastAPI (Python) | Async, native SSE via StreamingResponse, typed with Pydantic |
| Spec parsing | prance + openapi-spec-validator | Deterministic, no LLM in the parse/validate path |
| Type/client generation | Jinja2 + datamodel-code-generator (Python) + openapi-typescript (TS) | Deterministic templating, not LLM-generated code |
| LLM | Anthropic API — Claude Haiku 4.5 default, escalate to Sonnet 5 on repeated validation failure | Cost control; only pay for a bigger model when the cheap one can't self-correct |
| Validation sandbox | Docker, one `--rm` container per run, network-restricted, resource/time capped | Proves generated code actually runs against the real API, not just "compiles" |
| Mock fallback | Stoplight Prism | For endpoints unsafe to call live (destructive/paid/rate-limited) |
| Job/progress | In-memory async store + Server-Sent Events | No DB needed for ephemeral job state |
| Persistence | SQLite (WAL mode) | Per-IP daily free-generation rate limiting only — nothing else |
| Reverse proxy | Caddy | Automatic HTTPS on the Oracle Cloud VM |
| Deployment | Backend: Oracle Cloud Always Free VM (Ubuntu 24.04, Docker). Frontend: Vercel free tier | Cost: $0 |
| Auth handling | Detected from spec, generates env-var placeholders only | Never stores real credentials |

---

## Folder Structure
```
relay/
├── backend/
│   ├── app/
│   │   ├── main.py        # FastAPI app, routes
│   │   └── ...             # generation, sandbox, jobs modules land here as built
│   ├── tests/
│   ├── requirements.txt
│   └── venv/               # gitignored
├── frontend/                # not started yet
├── CLAUDE.md
├── AGENTS.md
├── ARCHITECTURE.md
└── TODO.md
```

---

## Pipeline (target end state)
```
User pastes OpenAPI/Swagger URL
  → fetch + parse (prance) + validate (openapi-spec-validator)
  → generate types (datamodel-code-generator / openapi-typescript) + client (Jinja2 templates)
  → run generated client in a sandboxed Docker container against the live API (or Prism mock
    if unsafe to call live)
  → on failure: feed error back to Claude Haiku for a self-correction patch (capped retries),
    escalate to Sonnet only after repeated Haiku failures
  → stream progress the whole way via SSE
  → return validated client + a pass/fail report
```

## Current implementation status
Only the first pipeline stage exists: `POST /api/parse-spec` fetches a spec URL, parses it with
prance, validates it with openapi-spec-validator, and streams progress/result over SSE. No
generation, no sandbox, no LLM calls yet.

---

## Key Files
| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI app; `/api/parse-spec` SSE endpoint |
| `backend/requirements.txt` | Plain pip deps, no uv/poetry |
| `backend/tests/test_main.py` | Smoke test for the parse endpoint |

---

## Environment Variables Required
```bash
ANTHROPIC_API_KEY=       # not yet used by any code
```

---

## Known Technical Debt
- [ ] No generation step yet (types/client templates).
- [ ] No sandbox runner yet.
- [ ] No rate-limiting SQLite store yet.
- [ ] No frontend yet.
- [ ] Local dev venv created via pip virtualenv workaround (sudo had no TTY for `apt install python3.14-venv`) — install `python3.14-venv` properly on the Oracle VM at deploy time for clean, reproducible provisioning.
