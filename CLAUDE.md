# CLAUDE.md — relay

## Project Overview
**What this project does:** Point it at an OpenAPI/Swagger spec URL, get back a working typed
API client (Python or TypeScript), validated by actually running the generated code against the
live or mocked target API in a sandboxed Docker container — not just checked for compiling.
Stretch goal: also emit an MCP-server-compliant tool wrapper from the same pipeline.
**Stack:** FastAPI (Python) + Next.js 14/Tailwind + Docker sandbox + Google Gemini API (Gemini Flash default, Gemini Pro escalation — permanent free tier, no billing ever)
**Current phase:** MVP
**Primary goal this sprint:** End-to-end pipeline — paste an OpenAPI URL, get a validated typed client back, tested live against Open-Meteo's public API as the default demo target.

---

## Read These First (Every Session)
- `ARCHITECTURE.md` — codebase map, key decisions, folder structure
- `AGENTS.md` — what was done in previous sessions
- `TODO.md` — what needs to be done next

Global rules from `~/.claude/CLAUDE.md` apply to this project unless overridden below.

---

## Project-Specific Rules

### Stack Constraints
- Do not add new dependencies without asking first.
- Backend deps go in `backend/requirements.txt`, plain pip — no uv/poetry.
- Spec parsing/validation must stay deterministic (prance + openapi-spec-validator) — no LLM in that path.
- Type/client generation must stay deterministic templating (Jinja2, datamodel-code-generator, openapi-typescript) — the LLM only touches self-correction on validation failure, never first-pass generation.
- All API routes must have Pydantic-typed request/response models and input validation.

### LLM Layer (Google Gemini — free tier only)
- Use the Google Gemini API via the official Google Gen AI Python SDK. No other LLM provider.
- Zero paid LLM calls anywhere — not capped, not budgeted, ZERO. The entire LLM layer runs on
  Gemini's permanent free tier (no card required).
- Default model: **Gemini Flash** (~1,500 req/day free). Escalate to **Gemini Pro** only after
  repeated Flash failures — Pro's free quota is ~50/day, so treat every Pro call as scarce.
- Self-correction is capped: 2 Flash attempts, then 1 Pro attempt, then hard fail with full
  history. Never an uncapped retry loop — the cap protects free-tier quota.
- API key comes from `GEMINI_API_KEY` (env var only, never hardcoded).

### Forbidden
- **Never enable billing on the Google Cloud project backing the Gemini API key.** Enabling
  billing deletes the free tier entirely and makes every call billable. This project must NEVER
  have a payment method attached — no billing account, no spend caps, no paid tier, nothing.
- Never create an Anthropic API key, billing account, or spend-cap config anywhere in this project.
- Never store real API credentials — auth handling only ever generates env-var placeholders.
- Never let sandbox containers run without `--rm`, network restrictions, and resource/time caps.
- Never use a DB for job/progress state. Hosted path: state + results live in Cloudflare Worker KV
  (namespaced by run id); per-IP daily rate limiting is a KV counter (`rl:{ip}:{day}`, no SQLite).
  Local FastAPI dev path streams job state over SSE (no DB either). (Supersedes the earlier
  SQLite-for-rate-limiting rule — there is no SQLite under Worker hosting.)
- Never hardcode API keys or secrets. Worker secrets via `wrangler secret put`; CI via GitHub
  Actions secrets. The GitHub PAT, `CALLBACK_SECRET`, and `GEMINI_API_KEY` never enter git or chat.
- Rate-limit check must run BEFORE triggering the GitHub Action (the expensive step).

### Preferred Patterns
- Local FastAPI dev: stream long-running work over SSE. Hosted: the CI runner posts coalesced
  checkpoints to the Worker (KV); the Worker enforces the ≤1-write/sec-per-key throttle itself.
- Escalate Gemini Flash → Gemini Pro only after repeated self-correction failures, never as the default.

---

## Workflow for This Project

### Starting a Session
1. Read AGENTS.md — understand what was done
2. Read TODO.md — pick the next task
3. Read ARCHITECTURE.md — orient yourself in the codebase
4. State your plan before writing any code
5. Confirm scope: touch only files needed for this task

### Ending a Session
1. Run all tests — fix failures before stopping
2. Run linter/type checker
3. Update AGENTS.md — what you did, what changed
4. Update TODO.md — cross off done, add new items discovered
5. Run `rtk gain` — report token savings

---

## Grill-Me Protocol
Before starting any new feature, ask these questions:
- What is the exact expected input and output?
- What are the edge cases?
- What files will be touched?
- What could break?
- Is there existing code that can be reused?

---

## Testing Requirements
- [ ] Unit tests for all business logic
- [ ] Integration tests for all API endpoints
- [ ] E2E tests for critical user flows (if applicable)
- Run: `cd backend && source venv/bin/activate && pytest`
