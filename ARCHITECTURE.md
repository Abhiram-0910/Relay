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
`POST /api/parse-spec` fetches a spec URL, parses it with prance, validates it with
openapi-spec-validator, then deterministically generates a standalone typed Python model +
client method for every operation that has a JSON response (datamodel-code-generator + Jinja2),
supporting path params, query params, and JSON request bodies. Operations without a JSON
response are reported as skipped, not silently dropped. Progress streams per-endpoint over SSE
(`generating`, current/total). Live-verified against the real Swagger Petstore spec: 14/19
operations generated, 5/19 correctly skipped, every generated file compile-checked. Each
endpoint's client is still its own independent file (not yet merged into one client package),
no sandbox execution, no LLM calls yet.

---

## Key Files
| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI app; `/api/parse-spec` SSE endpoint |
| `backend/app/generate.py` | Deterministic model+client generation for every eligible operation |
| `backend/requirements.txt` | Plain pip deps, no uv/poetry |
| `backend/tests/test_main.py` | Parse/validate endpoint tests + full-spec live Petstore test (`-m live`) |
| `backend/tests/test_generate.py` | Generation compile-check tests (path params, query params, request bodies, skip path) |

---

## Environment Variables Required
```bash
ANTHROPIC_API_KEY=       # not yet used by any code
```

---

## Known Technical Debt
- [ ] Each endpoint generates its own standalone client class/file — not yet merged into one cohesive client package per job. Likely to happen alongside sandbox execution (Task 8), once there's a real file tree to hand the sandbox.
- [ ] No path-param name sanitization (assumes the OpenAPI param name is already a valid Python identifier — true for Petstore, not guaranteed generally).
- [ ] Query/path param types only cover JSON scalar types (string/integer/number/boolean); arrays/objects fall back to `str`.
- [ ] Nested sub-schemas (e.g. a "Category" object embedded in multiple endpoints' Pet schema) are regenerated independently per endpoint with no cross-endpoint dedup — harmless while each endpoint is its own file, would need addressing if/when client files get merged.
- [ ] datamodel-code-generator's own naming heuristic can produce odd class names for array/root responses (e.g. truncates a trailing "s" when wrapping a list) — cosmetic, still valid Python, not something our code controls.
- [x] Docker installed natively in WSL (not Docker Desktop integration, matching the Oracle VM's native-Docker deploy target) — confirmed working via `docker run hello-world`.
- [ ] No sandbox runner yet — Task 8, now unblocked.
- [ ] No rate-limiting SQLite store yet.
- [ ] No frontend yet.
- [ ] Local dev venv created via pip virtualenv workaround (sudo had no TTY for `apt install python3.14-venv`) — install `python3.14-venv` properly on the Oracle VM at deploy time for clean, reproducible provisioning.
