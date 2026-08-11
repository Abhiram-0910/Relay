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
| Public API / edge | Cloudflare Worker | Stateless front door: trigger, KV state, per-IP rate limit. Free tier, no server to run. |
| Compute | GitHub Actions (workflow_dispatch, ubuntu-latest) | Runs the pipeline incl. Docker sandbox; public repo = free unlimited minutes |
| Job/progress + results | Cloudflare Worker KV (hosted); in-memory + SSE (local FastAPI dev) | No DB; run state namespaced by run id. Local dev keeps the SSE path. |
| Rate limiting | KV daily counter (`rl:{ip}:{day}`) in the Worker | Per-IP free-generation cap; no SQLite/DB (superseded the earlier SQLite plan) |
| Mock fallback | Stoplight Prism | For endpoints unsafe to call live (destructive/paid/rate-limited) |
| Deployment | Worker: `wrangler deploy` (Cloudflare). Compute: GitHub Actions. Frontend (Task 13): Vercel | Cost: $0 |
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

## Hosting architecture (Cloudflare Worker + GitHub Actions + KV)
No always-on server. A stateless Cloudflare Worker is the public front door; a GitHub Action is the
compute (GitHub-hosted runners ship Docker, so the Task 8/8.5 sandbox runs there unchanged; public
repo = free unlimited Actions minutes).

```
Browser ─POST /api/runs {specUrl}─► Worker
                                     1. validate URL
                                     2. per-IP daily rate-limit (KV) ─► 429  ⟵ BEFORE dispatch
                                     3. runId=randomUUID; KV put run:{runId}=queued
                                     4. GitHub workflow_dispatch (Bearer GH_PAT) inputs{run_id,spec_url,callback_url}
                                     5. 202 {runId,statusUrl}
GitHub Action runs backend/app/ci_runner.py (parse→generate→sandbox→self-correct)
     └─ checkpoints ─► POST {callback_url}/api/runs/{runId}/progress (Bearer CALLBACK_SECRET)
                        Worker verifies secret, throttles ≤1 write/sec/key ─► KV put run:{runId}
Browser ─GET /api/runs/{runId} (poll)─► Worker ─► KV get ─► snapshot
```

Three credentials, three jobs: `GH_PAT` (Worker→GitHub, dispatch only), `CALLBACK_SECRET` (Action
→Worker, shared secret authenticating callbacks), `GEMINI_API_KEY` (Action→Gemini). The Action's
built-in `GITHUB_TOKEN` is used only for checkout — never to call the Worker. Run state is
namespaced by the Worker-minted `runId`, so concurrent visitors never cross. Rate limit is a KV
daily counter (`rl:{ip}:{YYYYMMDD}`, end-of-day TTL, default 3/IP/day, over-limit costs 0 writes).
Progress is coalesced to a handful of KV writes per run (KV Free = 1,000 writes/day, 1 write/sec
per key); the Worker enforces the throttle so a buggy reporter can't blow the budget. Provisioning:
`worker/DEPLOY.md`. Verify a deployment: `scripts/verify_deployed.sh <worker-url>`.

---

## BYOK (bring-your-own-key) security design — SCOPED 2026-07-26, NOT YET BUILT
Lets a user supply their own LLM provider key so their runs use their quota instead of the shared
free-tier `GEMINI_API_KEY`. **Hard constraint driving this whole design:** the repo is public, so a
key must never be passed as a `workflow_dispatch` input — those are visible, unmasked, in public
Action run logs. GitHub's log masking only redacts values registered ahead of time as real repo
secrets; it can't know an arbitrary runtime input is sensitive. The key must never enter GitHub's
system at all.

**Flow (extends the existing trigger/callback pattern, doesn't replace it):**
```
Browser ─POST /api/runs {specUrl, apiKey?, provider?}─► Worker
                                     1–3. same as today (validate, rate-limit, mint runId)
                                     3a. if apiKey present: KV put byok:{runId}={apiKey,provider,
                                         storedAt}, expirationTtl=240s — BEFORE dispatch
                                     4. workflow_dispatch inputs{run_id, spec_url, callback_url,
                                        has_byok: "true"|"false"}  ← apiKey NEVER enters this object
                                     5. 202 {runId, statusUrl}
GitHub Action: if has_byok=="true", ci_runner calls
     GET {callback_url}/api/runs/{run_id}/byok-key  (Bearer CALLBACK_SECRET, same auth as progress)
Worker: KV get byok:{runId} → if present, KV delete byok:{runId} (delivery-once, not just TTL) →
     stamp run:{runId} with byokReceivedAt (=storedAt) and byokDeletedAt (=now) → 200 {apiKey,
     provider}. Second call (or call after TTL expiry) → 404 — key is already gone either way.
ci_runner: holds apiKey in a local variable only for the scope of the one LLM call, never logs it,
     never writes it to disk; goes out of scope after. (Documented residual: Python can't guarantee
     the string is zeroed in memory — not solvable without a native extension, not worth it here;
     the delivery-once + short-TTL + never-on-disk design closes the vectors that matter — leaking
     in a public log or a repo secret store.)
Browser: already polls GET /api/runs/{runId} — once byokDeletedAt appears in the snapshot, render
     the honest proof: "key received {byokReceivedAt}, used once, deleted {byokDeletedAt}." This is
     a real fact read off the Worker's own KV state, not copy.
```

**KV schema addition:**
| Key | Value | TTL |
|-----|-------|-----|
| `byok:{runId}` | `{apiKey, provider, storedAt}` | 240s, deleted on first read regardless of TTL |

`run:{runId}` gains two optional fields once delivered: `byokReceivedAt`, `byokDeletedAt`. The
`apiKey` value itself is never written into `run:{runId}` or any other longer-lived key — the only
place it ever exists in KV is the short-TTL `byok:{runId}` entry, and only until first read.

**New Worker endpoints:** none, strictly — `apiKey`/`provider` are optional fields on the existing
`POST /api/runs` body, so the byok write happens inside the existing run-creation handler, before
dispatch. One genuinely new endpoint: `GET /api/runs/:runId/byok-key` (Bearer `CALLBACK_SECRET`,
same credential/pattern as `/api/runs/:id/progress`) — Action-only, delivery-once.

**Test obligation before Step 2 (multi-provider) touches any of this:** a Worker test that mocks
the GitHub dispatch `fetch` call and asserts `JSON.stringify(dispatchRequestBody)` does not contain
the literal key value — i.e., proves structurally, not just by design intent, that the key can't
end up in a public Action log. Plus: delivery-once (second `byok-key` GET returns 404), wrong/missing
bearer on `byok-key` returns 401 and leaves the KV entry untouched, and `run:{runId}` never gains an
`apiKey` field at any point in the flow.

**Env vars (later, Step 2):** provider keys are never Worker/Action secrets under BYOK — they come
from the user, per run. No new long-lived secrets are needed for Step 1 itself.

---

## Step 2 — Multi-provider LLM support — SCOPED 2026-07-30, NOT YET BUILT
Extends BYOK from Gemini-only to five providers: **Gemini, OpenAI, Anthropic, Grok (xAI),
OpenRouter**. Each is a real integration against its own live API — never a cosmetic label over one
backend — but the honest wire-protocol count is **three, not five**: Grok and OpenRouter deliberately
implement OpenAI's exact REST API, so OpenAI/Grok/OpenRouter share one adapter parameterized by
`base_url`. Distinctness is preserved where it actually exists (Gemini vs. OpenAI-family vs.
Anthropic — three genuinely different request/response/structured-output shapes).

**Zero new dependencies.** OpenAI-family and Anthropic are plain JSON POSTs done with the
already-present `requests`; Gemini keeps the already-installed `google-genai` SDK. No `openai`/
`anthropic` SDKs added (project rule: no deps without asking — resolved: raw `requests`).

**Three adapters** (all in `correct.py`, behind a `CORRECTORS[provider]` dispatch; each returns the
existing `{models_py, client_py}` Patch dict, so `self_correct`'s ladder/resandbox loop is untouched):

| Adapter | Providers | Auth | Request / response shape | Structured output |
|---------|-----------|------|--------------------------|-------------------|
| `gemini` | Gemini | `?key=` (SDK) | `contents`+`config`; `candidates[].content.parts[]` | native Pydantic `response_schema` + `response_mime_type=application/json` → `response.parsed` (unchanged from Step 1) |
| `openai_compatible(base_url)` | OpenAI (`api.openai.com/v1`), Grok (`api.x.ai/v1`), OpenRouter (`openrouter.ai/api/v1`) | `Authorization: Bearer` | `messages[]` → `choices[0].message.content` (JSON string, `json.loads`) | `response_format={"type":"json_schema","json_schema":{name,schema,strict:true}}` |
| `anthropic` | Anthropic | `x-api-key` + `anthropic-version: 2023-06-01` | `messages[]` + top-level `system` → `content[]` blocks | **tool-use**: one tool whose `input_schema` is the patch schema, `tool_choice` forces it, patch read from the `tool_use` block's `.input` (portable across all Claude models/versions — chosen over the newer per-model-gated native `structured_outputs`) |

**No cross-model escalation under BYOK.** The Step 1 ladder `(flash, flash, pro)` is a Gemini
*free-tier* escalation and stays that way only for the shared-key path. On BYOK it's the user's own
account/billing, so a run uses the user's **one chosen model**, retried up to the cap — never
silently escalated to a different (costlier) model on their key.

**Model & provider plumbing (mostly already present):**
- `byok:{runId}` KV record gains a `model` field (`{apiKey, provider, model, storedAt}`);
  `storeByokKey` validates it. `provider` was already stored in Step 1.
- `provider` is currently *fetched and discarded* (`run_with_optional_byok` unpacks `_provider`).
  Step 2 stops discarding it and threads `(api_key, provider, model)` through
  `provider_call` → `_live_validate` → `self_correct` → `CORRECTORS[provider]`.
- `apiKey`/`provider`/`model` still never touch `workflow_dispatch` inputs — they ride the existing
  delivery-once `byok-key` endpoint, so the public-Action-log guarantee from Step 1 is unchanged.

**Live model-list fetch (never hardcoded lists):**
- New Worker endpoint `POST /api/models {provider, apiKey}` → Worker calls that provider's own
  list-models API with the user's key → returns a filtered list. **Worker-side, not browser-direct**:
  Anthropic/OpenAI don't serve permissive CORS to browser origins, and the key already crosses the
  browser→Worker HTTPS boundary for BYOK, so we reuse it rather than open a second one.
- Key is **transient** here — used for the outbound fetch, never written to a `byok:{runId}` entry
  (those are minted only at run-create). A 401 doubles as up-front key validation (feeds Step 3's
  error taxonomy: reject a bad key *before* a run is spent).
- List-models endpoints per provider:
  - Gemini: `GET generativelanguage.googleapis.com/v1beta/models?key=` → `models[]`, filter to
    those whose `supportedGenerationMethods` includes `generateContent`.
  - OpenAI: `GET api.openai.com/v1/models` (Bearer) → `data[]{id}` — **no capability fields**, filter
    chat-capable models by id heuristic / small curated allowlist.
  - Anthropic: `GET api.anthropic.com/v1/models` (`x-api-key` + `anthropic-version`) → rich
    `data[]{id, display_name, capabilities.structured_outputs, …}`.
  - Grok: `GET api.x.ai/v1/models` (Bearer) → OpenAI-shaped `data[]`.
  - OpenRouter: `GET openrouter.ai/api/v1/models` (Bearer; with the user's key reflects their
    enabled/credit models) → `data[]{id, name, pricing, context_length}` (~400+, must filter).
- **Cache** `models:{provider}` in KV, TTL ~1h (a model list is a per-provider public fact — cache
  the provider dimension, not the key). Per-key variance (OpenRouter enabled-models, OpenAI tier) is
  a documented ceiling: a listed-but-unusable model surfaces as a clean provider 4xx at run time,
  which Step 3 handles anyway. List-models calls consume no token quota on any provider, so the
  cache — not a rate limiter — is the throttle (the per-IP KV limiter can optionally gate the
  endpoint as belt-and-suspenders).
- The id-prefix/allowlist filter (OpenAI/Grok/OpenRouter only) is the one place a hardcoded model
  reference remains — it *filters* a live-fetched list, it does not *replace* it. Do not let it drift
  into a hardcoded model list, which the brief forbids.

**Residual (documented, not over-fixed): `gpt-4o-mini` is not reliable for large-payload corrections.**
Live-verified 2026-08-08 (`test_openai_fixes_broken_open_meteo_client`, `backend/tests/test_correct.py`):
against Open-Meteo's forecast endpoint (`models.py` ~40KB / ~10K tokens — the corrector must return
the full file verbatim under strict `json_schema`, plus one surgical fix), `gpt-4o-mini` exhausted
all 3 BYOK ladder attempts without fixing the injected bug — it edited the wrong file on attempt 1,
then returned the broken file byte-identical (no change at all) on attempt 2. `gpt-4o` fixed it
within the same ladder, same prompt, same adapter code — so this is a model capability gap on
"verbatim-copy-plus-edit at this payload size," not a prompt or wiring bug (confirmed: `_build_prompt`/
`_SYSTEM_INSTRUCTION` are identical across every provider). Not fixed in code — BYOK is the user's own
model choice; this is a known ceiling to know about before re-diagnosing it from scratch. (Separately,
first attempt also surfaced a real infra issue, since fixed: `_CORRECTOR_TIMEOUT` was 90s, too tight
for this payload size regardless of model — bumped to 300s, proven on the passing run.)

**Residual (documented, not over-fixed): Anthropic/Grok/OpenRouter adapters have never been live-verified.**
All three are hermetically tested (Step 2: request/response shape, structured-output parsing) and
Anthropic's exception classification is additionally verified against real `requests.HTTPError`
behavior (Step 3) — but none has ever made one real call to its actual provider API. Only Gemini
(Task 9's original proof) and OpenAI (Step 2's Part B, re-confirmed Step 4) have live receipts. Not a
blocker — BYOK means whichever provider the user picks, and the two proven providers already exercise
the shared adapter code paths end to end (`openai_compatible` serves OpenAI/Grok/OpenRouter
identically; only `base_url`/key differ). Live-verify Anthropic/Grok/OpenRouter specifically whenever
a throwaway key for one becomes available — same deliberately-broken-client technique as the existing
live tests, no new code needed.

**Frontend (Step 1's BYOK input was never built — `createRun` still sends only `{specUrl}`):** the
provider dropdown, key field, and a **live-populated** model dropdown (from `POST /api/models`) all
land here, together with the deferred Step 1 `byokReceivedAt`/`byokDeletedAt` receipt.

**Cost boundary (see AGENTS.md):** the shared `GEMINI_API_KEY` stays free-tier-only forever; BYOK
running on a user's own paid OpenAI/Anthropic/Grok account is not a violation of the zero-cost rule —
it is the entire point of BYOK. Spend is the user's, on the user's key.

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
| `backend/app/ci_runner.py` | End-to-end pipeline runner for CI: parse→generate→sandbox→self-correct, posts checkpoints to the Worker; local mode prints |
| `backend/tests/test_ci_runner.py` | Hermetic tests: base-URL resolution, required-param synthesis, reporter local mode |
| `worker/src/index.js` | Cloudflare Worker: `/api/runs` (trigger+rate-limit), `/api/runs/:id` (poll), `/api/runs/:id/progress` (auth callback) |
| `worker/test/worker.test.js` | Worker unit tests (fake KV+fetch): rate-limit-before-dispatch, run-id isolation, callback auth, write throttle |
| `worker/wrangler.jsonc`, `worker/DEPLOY.md` | Worker config + one-time provisioning (KV id, secrets, GitHub Actions secrets) |
| `.github/workflows/generate.yml` | `workflow_dispatch` job: build sandbox images, run `ci_runner`, report infra failure back |
| `scripts/verify_deployed.sh` | curl E2E against a deployed Worker: trigger, poll checkpoints, assert verified_pass, rate-limit, isolation |
| `Makefile` | `make sandbox-build` (builds both reused images), `test`, `test-live` |

---

## Environment Variables Required
```bash
# Backend / CI runner
GEMINI_API_KEY=          # Google Gemini API key — FREE TIER ONLY, never attach billing
RELAY_RUN_ID=            # (CI) Worker-minted run id
RELAY_SPEC_URL=          # (CI) spec URL to process
RELAY_CALLBACK_URL=      # (CI) Worker base URL for progress callbacks
RELAY_CALLBACK_SECRET=   # (CI) shared secret for callback auth

# Worker secrets (wrangler secret put) / GitHub Actions secrets — see worker/DEPLOY.md
GH_PAT=                  # Worker: fine-grained PAT, Actions read/write — triggers workflow_dispatch
CALLBACK_SECRET=         # Worker + GitHub Actions (same value) — authenticates callbacks
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
- [x] Rate limiting done via KV daily counter in the Worker (not SQLite — no DB under Worker hosting). Best-effort atomic: a rare concurrent race from one IP could allow ~1 extra past the cap. Exact enforcement would need a Durable Object; acceptable for a free-generation abuse guard.
- [x] **Spec fetch + `$ref` resolution SSRF — closed.** `ci_runner` calls `sandbox.resolve_and_validate_host(spec_url)` BEFORE fetching (rejects private/metadata spec URLs), and wraps `ResolvingParser` in `guarded_prance_resolve()`, which gates prance's single fetch choke point (`prance.util.url.fetch_url_text` — every remote/`file`/`python` ref fetch routes through it, transitively included): http(s) hosts run through the same SSRF check, `file://`/`python://` schemes are rejected outright (no runner file reads / package imports). The wrapper asserts the choke point still exists so a prance restructure fails loudly. Covered by 3 default-suite tests (private spec_url rejected before fetch; malicious `$ref` rejected during resolution and proven not-fetched; scheme/host guard units).
  **Redirect-following closed (Step 6 security review, F2, 2026-08-11):** the original check validated the URL once and then let `requests.get` follow redirects with its default `allow_redirects=True` — zero host validation on the redirect target, a much easier and more deterministic bypass than DNS rebinding (no timing games needed: any public host that passes the check can just redirect to `169.254.169.254`). `_redirect_safe_get` (shared by the spec fetch and the `$ref` fetch, both) now re-runs `resolve_and_validate_host` on every hop, bounded at 5 redirects. 4 new tests, plus a real-world check against the live demo spec URL to confirm the legitimate no-redirect case is unaffected.
  **Residual (documented, not over-fixed, re-confirmed during the same review — the original tradeoff still stands):** DNS rebinding remains open — `_redirect_safe_get` still calls plain `requests.get` for the actual connection on each hop, which re-resolves DNS itself; a host's DNS record changing in the window between `resolve_and_validate_host`'s check and that connect is not caught. Pinning the connection to the validated IP would break TLS SNI and isn't worth it for spec fetching — same reasoning as before, just now scoped to a narrower, harder-to-exploit remainder than when this note was first written (that version also covered the now-closed redirect vector).
- [ ] Live-validation currently covers only idempotent GET endpoints whose required params can be synthesized from spec example/default, plus a hardcoded demo-param hook for Open-Meteo `/v1/forecast` (`_DEMO_TARGETS` in `ci_runner.py`). General param synthesis + safe handling of non-GET endpoints is a later task; non-synthesizable endpoints are honestly reported `generated_only`.
- [ ] No frontend yet — deferred to Task 13 (Next.js + Tailwind on Vercel), to be built once against the proven Worker API.
- [ ] Local dev venv created via pip virtualenv workaround (sudo had no TTY for `apt install python3.14-venv`) — install `python3.14-venv` properly on the Oracle VM at deploy time for clean, reproducible provisioning.
