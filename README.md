# Relay

Point Relay at an OpenAPI/Swagger spec URL. It generates a typed Python client (Pydantic v2
models + `requests`-based methods), runs that client live against the real target API inside a
network-isolated Docker sandbox, and — if the live call fails — feeds the failure back to an LLM
for a self-correcting patch, re-validated in the sandbox on every attempt. The output is a client
that's been proven to actually work against the live API, not just checked for syntax.

**Live app:** `https://relay-cyan-rho.vercel.app`
**API:** `https://relay-worker.abhiram-dev.workers.dev` (Cloudflare Worker; no public root route,
only `POST /api/runs` and `POST /api/models`)

---

## How it works

```
Browser (Next.js, Vercel)
  → POST /api/runs {specUrl, byok?}          Cloudflare Worker
  → rate limit (KV, per-IP/day) → workflow_dispatch    GitHub Actions
      → fetch spec (SSRF-guarded) → parse (prance) → validate (openapi-spec-validator)
      → generate Pydantic models + client methods (Jinja2, deterministic — no LLM here)
      → run the generated client in a sandboxed Docker container against the real target API
      → on failure: self-correct via LLM (capped ladder: 2 free-tier attempts, then 1 escalated
        attempt, then hard fail), re-validated in the sandbox after every patch
      → progress + result posted back to the Worker (KV)
  ← Browser polls GET /api/runs/:id, renders live status + a downloadable code viewer on success
```

The backend (`backend/app/`) isn't a running web server — it's a Python package invoked once per
run as `python -m app.ci_runner` inside the GitHub Action, driven entirely by env vars the Worker
supplies through `workflow_dispatch`. There's no database anywhere in the hosted path: run state,
progress, and per-IP rate limiting all live in Cloudflare Worker KV, namespaced by run id.

### Sandbox isolation
The generated client runs inside a `--rm`, resource-capped Docker container on its own `--internal`
Docker network, reachable to the outside world only through a pinned `socat` sidecar — so even a
maliciously crafted spec can't reach anything but the one target host it was invoked against.

### Self-correction (BYOK-aware)
Default: Google Gemini, permanent free tier, zero paid calls ever (Flash → Pro escalation, capped).
Bring-your-own-key: OpenAI, Anthropic, xAI Grok, or OpenRouter — the key travels browser → Worker
over HTTPS only, is stored as a single-use KV entry with a short TTL, and is deleted the instant
the Action's authenticated callback reads it. It's never written to `workflow_dispatch` inputs
(which would leak it into this repo's public Action logs), never logged, never written to disk.
The run record honestly surfaces `byokReceivedAt` / `byokDeletedAt` as proof, not just a claim.

---

## Repo layout

```
backend/    Python: spec parsing/validation, deterministic generation, Docker sandbox runner,
            LLM self-correction adapters (Gemini/OpenAI/Anthropic/Grok/OpenRouter), pipeline
            entry point (ci_runner.py) — invoked by the GitHub Action, not served as an API
worker/     Cloudflare Worker (JS): run triggering, KV state, rate limiting, BYOK key handoff,
            live model-list proxy
frontend/   Next.js 14 + Tailwind, deployed on Vercel
.github/workflows/generate.yml   The Action the Worker dispatches per run
```

---

## Running it locally

**Backend**
```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
make sandbox-build          # builds the relay-sandbox + relay-sidecar Docker images (one-time)
pytest                      # hermetic suite (network-off tests only)
pytest -m live               # add tests that hit real network APIs (Petstore, Open-Meteo)
```
CI runs this against Python 3.12; local dev has also been run against 3.14 without issue.

To run the full pipeline directly against a spec (bypassing the Worker/Action):
```bash
# omit RELAY_CALLBACK_URL/RELAY_CALLBACK_SECRET entirely for local-mode (prints progress instead
# of posting it — Reporter.live is False without both set)
RELAY_RUN_ID=local RELAY_SPEC_URL=<spec-url> GEMINI_API_KEY=<your-key> python -m app.ci_runner
```

**Worker**
```bash
cd worker
npm install
npm test          # vitest, hermetic (fake KV/fetch)
npm run dev        # wrangler dev, needs GH_PAT/CALLBACK_SECRET set via `wrangler secret put` or .dev.vars
```

**Frontend**
```bash
cd frontend
npm install
npm test            # vitest
npm run dev          # needs NEXT_PUBLIC_WORKER_URL — see .env.local.example
```

Secrets (`GH_PAT`, `CALLBACK_SECRET`, `GEMINI_API_KEY`) are never committed — set via
`wrangler secret put` (Worker) and GitHub Actions repo secrets (Action). BYOK keys never touch
either secret store; they're runtime-only, per-run KV entries with a short TTL.

---

## Honest limitations

This section exists because the project's own discipline (since Session 1) has been: nothing is
claimed done without a live receipt, and nothing gets swept under a "future work" list without
being named specifically. These are the real, current gaps — each one was deliberately accepted,
not overlooked.

- **Anthropic, xAI Grok, and OpenRouter self-correction have never made one real live call.**
  Every adapter has full hermetic test coverage and Grok/OpenRouter share OpenAI's already-proven
  wire protocol (a `base_url` swap), but no API key has ever been available to actually exercise
  them end to end. Only Google Gemini and OpenAI have live receipts. Anthropic's tool-use protocol
  is structurally different from the other four, so it's the one adapter genuinely unproven in a
  way that isn't just "same code path, different key."
- **DNS-rebinding window on spec/redirect fetches.** The SSRF guard validates the target host
  before every fetch and redirect hop, but the actual TCP connection re-resolves DNS itself; a
  host's DNS record changing in the narrow window between validation and connect isn't caught.
  Pinning the connection to the validated IP would break TLS SNI and wasn't judged worth it for a
  spec-fetching feature that already blocks private/internal targets outright.
- **The per-IP rate limiter has a non-atomic read-then-write race.** A handful of concurrent
  requests from one IP could all observe the same stale count and pass the gate together. This is
  a cost-abuse guard (CI minutes, shared free-tier API quota) — nothing sensitive is gated behind
  it, so an exact fix (a Durable Object or Cloudflare's Rate Limiting binding) hasn't been built
  without evidence of real abuse.
- **The self-correction LLM's returned patch is trusted structurally, not validated as safe Python
  before it's written and executed.** The *inputs* to the correction prompt are fenced and length
  capped against injection; the *output* isn't AST-validated first. The blast radius is bounded —
  it only ever executes inside the `--rm`, network-isolated sandbox, never on a user's machine —
  but it's a real, tracked gap, not a closed one.
- **`gpt-4o-mini` reliably fails large-payload self-correction** (3/3 in testing) — it tends to
  either edit the wrong file or return the input unchanged. `gpt-4o` succeeds on the identical
  case. This is a model capability ceiling, not a Relay bug; BYOK users on OpenAI should expect
  better results from `gpt-4o`-class models than the `-mini` tier for anything beyond a trivial spec.
- **Zero frontend component/rendering tests.** The frontend's test suite is entirely pure-function
  (formatting, status mapping, API client logic) — no `@testing-library`-style rendering tests
  exist. This was a deliberate choice made early and revisited (not defaulted into), but it's the
  reason at least one real rendering bug (a BYOK hint that could never actually render) shipped
  unnoticed for a full step before a live re-verification pass caught it.
- **Generated clients only cover idempotent GET endpoints** whose required parameters can be
  synthesized from the spec's own examples/defaults, plus one hardcoded demo-param hook for the
  Open-Meteo forecast endpoint used as this project's default target. Non-GET endpoints and
  endpoints needing real (non-example) parameter values are honestly reported as generated but not
  live-validated (`generated_only`), never silently skipped or claimed as passing.
