# CLAUDE.md — relay

## Project Overview
**What this project does:** Point it at an OpenAPI/Swagger spec URL, get back a working typed
API client (Python or TypeScript), validated by actually running the generated code against the
live or mocked target API in a sandboxed Docker container — not just checked for compiling.
Stretch goal: also emit an MCP-server-compliant tool wrapper from the same pipeline.
**Stack:** FastAPI (Python) + Next.js 14/Tailwind + Docker sandbox + Anthropic Claude (Haiku 4.5 default, Sonnet 5 escalation)
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

### Forbidden
- Never store real API credentials — auth handling only ever generates env-var placeholders.
- Never let sandbox containers run without `--rm`, network restrictions, and resource/time caps.
- Never use a DB for job/progress state — in-memory + SSE only. SQLite is for per-IP rate limiting only.
- Never hardcode API keys or secrets.

### Preferred Patterns
- Stream all long-running work (parse, generate, validate) to the client over SSE.
- Escalate Haiku → Sonnet only after repeated self-correction failures, never as the default.

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
