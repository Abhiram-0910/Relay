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
| LLM | Google Gemini API (official Google Gen AI Python SDK) — Gemini Flash default, escalate to Gemini Pro on repeated validation failure | Permanent free tier, no card/billing ever; Flash ~1,500 req/day, Pro ~50/day (scarce). Zero paid calls. |
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
  → on failure: feed error back to Gemini Flash for a self-correction patch (capped: 2 Flash
    attempts, then 1 Gemini Pro attempt, then hard fail), escalate to Pro only after Flash fails
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
endpoint's client is still its own independent file (not yet merged into one client package).

Sandbox execution (Task 8 + 8.5) works: `app/sandbox.py` runs one generated client method in a
single `--rm` container (read-only rootfs, `--cap-drop ALL`, no-new-privileges, memory/CPU/pids
caps, wall-clock timeout with guaranteed kill). Two composed network layers: (1) SSRF pre-flight —
`resolve_and_validate_host` refuses any target whose DNS resolves to a non-public IP (`is_global`,
which also catches CGNAT; plus multicast); (2) network isolation — the sandbox joins only a per-run
`--internal` network and reaches the outside solely through a socat sidecar pinned to that one
validated IP:port, so any other host/IP (even a legit public one) has no route. Reports
carry a structured status: `verified_pass` (real call + response validated — the only pass),
`verified_live_validation_failed`, `call_failed`, or `ssrf_blocked`, each with detail for Task 9.
Live-verified end to end: the Open-Meteo `/v1/forecast` client returns `verified_pass`. No LLM /
self-correction yet.

The sandbox base image (`relay-sandbox`) is built ONCE via `make sandbox-build` and reused per
run; `run_in_sandbox` fails fast with the exact build command if it's missing (never auto-builds).
This build must run once in local dev before Task 8 tests, and once on the Oracle VM at deploy
(Task 15).

Self-correction (Task 9) works: `app/correct.py::self_correct` consumes a failing sandbox report
and asks Google Gemini (free tier, via the official `google-genai` SDK) for corrected file
contents, re-running every patch through the FULL sandbox. Capped ladder: 2 `gemini-3.5-flash`
attempts → 1 `gemini-2.5-pro` attempt → hard fail with the full attempt log. `verified_live_
validation_failed` → patch models.py; `call_failed` → patch client.py. The sandbox runner and the
Gemini corrector are both dependency-injected so the loop is unit-tested without Docker or quota.
Live-verified: a deliberately-broken Open-Meteo response model was fixed by Flash on attempt #1 →
`verified_pass`. Key comes from `GEMINI_API_KEY` (local dev: gitignored `backend/.env`).
Note: `gemini-2.5-flash` was retired for new API keys ("no longer available to new users"), so
Flash is pinned to `gemini-3.5-flash`; model IDs are constants in `correct.py` for one-line swaps.

---

## Key Files
| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI app; `/api/parse-spec` SSE endpoint |
| `backend/app/generate.py` | Deterministic model+client generation for every eligible operation |
| `backend/requirements.txt` | Plain pip deps, no uv/poetry |
| `backend/tests/test_main.py` | Parse/validate endpoint tests + full-spec live Petstore test (`-m live`) |
| `backend/tests/test_generate.py` | Generation compile-check tests (path params, query params, request bodies, skip path) |
| `backend/app/sandbox.py` | Host-side Docker sandbox runner: SSRF pre-flight + `--internal` network + pinned socat sidecar + locked-down `--rm` container + structured report |
| `backend/sandbox/runner.py` | In-container entrypoint: imports the generated client, makes one call, emits a structured result line |
| `backend/sandbox/Dockerfile` | Minimal `python:3.12-slim` + requests + pydantic base image (`relay-sandbox`), built once |
| `backend/sandbox/sidecar.Dockerfile` | Alpine + socat egress sidecar (`relay-sidecar`); per-run pinned relay to the one validated IP:port |
| `backend/app/correct.py` | Gemini self-correction loop: capped Flash→Pro ladder, sandbox re-run per attempt, structured attempt log |
| `backend/tests/test_correct.py` | Hermetic loop-logic tests (fake sandbox+corrector) + live real-Gemini fix of a broken Open-Meteo client (`-m live`) |
| `backend/tests/test_sandbox.py` | SSRF-guard unit tests (default) + live tests (`-m live`): Open-Meteo E2E and other-public-IP-unreachable isolation proof |
| `Makefile` | `make sandbox-build` (builds both reused images), `test`, `test-live` |

---

## Environment Variables Required
```bash
GEMINI_API_KEY=          # Google Gemini API key — FREE TIER ONLY, never attach billing
```

---

## Known Technical Debt
- [ ] Each endpoint generates its own standalone client class/file — not yet merged into one cohesive client package per job. Likely to happen alongside sandbox execution (Task 8), once there's a real file tree to hand the sandbox.
- [ ] No path-param name sanitization (assumes the OpenAPI param name is already a valid Python identifier — true for Petstore, not guaranteed generally).
- [ ] Query/path param types only cover JSON scalar types (string/integer/number/boolean); arrays/objects fall back to `str`.
- [ ] Nested sub-schemas (e.g. a "Category" object embedded in multiple endpoints' Pet schema) are regenerated independently per endpoint with no cross-endpoint dedup — harmless while each endpoint is its own file, would need addressing if/when client files get merged.
- [ ] datamodel-code-generator's own naming heuristic can produce odd class names for array/root responses (e.g. truncates a trailing "s" when wrapping a list) — cosmetic, still valid Python, not something our code controls.
- [x] Docker installed natively in WSL (not Docker Desktop integration, matching the Oracle VM's native-Docker deploy target) — confirmed working via `docker run hello-world`.
- [x] Sandbox runner built (Task 8) — one `--rm` container per run, SSRF pre-flight, resource/time caps, structured report.
- [x] **Per-host egress isolation (Task 8.5) — closed.** The sandbox container joins ONLY a per-run `--internal` Docker network with no external route. A socat sidecar (`relay-sidecar`) joins both that internal network and bridge, forwarding exclusively to the single pre-validated `IP:port` — that allowlist is the socat argv, templated per run, never a shared/static config. The target hostname is pinned to the sidecar's internal IP via `--add-host`, so the unchanged generated client's normal `self.base_url` call is the ONLY reachable destination; TLS stays end-to-end (socat is a raw byte pipe — real cert, real SNI, no re-resolution). Composes with the SSRF pre-flight, which decides the one IP the sidecar may reach. Live-verified: Open-Meteo still returns `verified_pass` through this path, and a hardcoded request to a different but perfectly-public IP (1.1.1.1) fails `call_failed / Network is unreachable`. This is what makes it safe to run Task 9's untrusted LLM-generated code here.
- [ ] `--add-host` / sidecar pin a single IP (prefers IPv4); a dual-stack target still validates every resolved family, but only one IP is pinned/relayed. Fine for single-A-record hosts; revisit if a target needs multi-IP pinning.
- [ ] No rate-limiting SQLite store yet.
- [ ] No frontend yet.
- [ ] Local dev venv created via pip virtualenv workaround (sudo had no TTY for `apt install python3.14-venv`) — install `python3.14-venv` properly on the Oracle VM at deploy time for clean, reproducible provisioning.
