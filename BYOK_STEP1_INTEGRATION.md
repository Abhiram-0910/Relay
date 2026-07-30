# BYOK Step 1 — integration notes (Session 3, 2026-07-27)

## What's real vs. what's not yet

**Actually run and passing, in this sandbox, this session:**
- `worker-byok.test.js` — 9/9 tests pass (vitest, fake KV) — includes the structural
  "key can never enter the dispatch inputs" proof.
- `test_ci_runner_byok.py` — 7/7 tests pass (pytest, mocked `requests.get`).

**Not yet true, and don't claim it is until it actually happens:**
- Merged into the real `worker/src/index.js` and `worker/src/index.test.js` (or
  wherever the real router/tests live).
- Merged into the real `backend/app/ci_runner.py` and its test file.
- The `.github/workflows/generate.yml` change below applied.
- Run against real Cloudflare KV / a real GitHub Action / a real (throwaway) key.

This session (claude.ai chat, no repo access) could only verify the logic in isolation.
Same rule as every other claim in this project: nothing above counts as done until it
has a live receipt against real infrastructure — Task 8/8.5/9/13 all earned that the
hard way, this shouldn't get a pass.

## Files delivered
| File | Goes to (in the real repo) |
|---|---|
| `worker-byok.js` | `worker/src/byok.js` (new file) |
| `worker-byok.test.js` | `worker/test/byok.test.js` (new file) |
| `ci_runner_byok.py` | `backend/app/byok.py` (new file) |
| `test_ci_runner_byok.py` | `backend/tests/test_byok.py` (new file; update the import at the top from `ci_runner_byok` to `app.byok` to match the real package layout) |

## Wiring into the existing router (worker/src/index.js)
Two edits to the existing `POST /api/runs` handler:

1. After `runId` is minted, before the dispatch call:
   ```js
   import { storeByokKey, buildDispatchInputs, handleByokKeyFetch } from "./byok.js";
   // ...
   const hasByok = await storeByokKey(env, runId, body.apiKey, body.provider);
   const inputs = buildDispatchInputs(runId, body.specUrl, callbackUrl, hasByok);
   ```
   **Check this specifically:** if the current dispatch code builds `inputs` by
   spreading the parsed request body (e.g. `inputs: { ...body, run_id, ... }`), that
   would leak `apiKey` even with this file added, since `body.apiKey` would ride along.
   Replace whatever currently builds `inputs` with `buildDispatchInputs()` — it has no
   `apiKey` parameter, so there's structurally nothing for it to leak.

2. Add the new route (adjust to match however the real router dispatches on path):
   ```js
   if (request.method === "GET" && pathname === `/api/runs/${runId}/byok-key`) {
     return handleByokKeyFetch(request, env, runId);
   }
   ```

3. Confirm the KV binding name. This module assumes `env.RELAY_KV`; rename the two
   references in `byok.js` if the real binding is named differently (ARCHITECTURE.md
   doesn't give the literal binding name, only "Cloudflare Worker KV (hosted)").

## Wiring into ci_runner.py
See the integration comment block at the top of `ci_runner_byok.py` — short version:
add a `RELAY_HAS_BYOK` env var (sourced from the new `has_byok` workflow_dispatch
input), call `run_with_optional_byok(...)` around the Task 9 provider call, and give
`self_correct` an optional `api_key` param that overrides `GEMINI_API_KEY` when set.

## Workflow YAML change (.github/workflows/generate.yml)
Add `has_byok` to the `workflow_dispatch` input schema and pass it through as an env var
to the job step that runs `ci_runner.py`:
```yaml
on:
  workflow_dispatch:
    inputs:
      run_id: { required: true, type: string }
      spec_url: { required: true, type: string }
      callback_url: { required: true, type: string }
      has_byok: { required: false, type: string, default: "false"}   # NEW

jobs:
  generate:
    steps:
      # ...
      - name: Run pipeline
        env:
          RELAY_RUN_ID: ${{ inputs.run_id }}
          RELAY_SPEC_URL: ${{ inputs.spec_url }}
          RELAY_CALLBACK_URL: ${{ inputs.callback_url }}
          RELAY_HAS_BYOK: ${{ inputs.has_byok }}      # NEW
          # RELAY_CALLBACK_SECRET / GEMINI_API_KEY already come from secrets
        run: python backend/app/ci_runner.py
```
`has_byok` is a plain `"true"`/`"false"` string — this is the *only* BYOK-related value
that ever touches `workflow_dispatch`, and it carries no information about the key
itself, just whether one exists.

## Frontend piece (not built this session — small, do alongside Step 1 closing)
The existing run-polling response already gains `byokReceivedAt` / `byokDeletedAt`
once a key is delivered (no new endpoint). Frontend: when both are present on the
polled run object, render something like:
> Your key was received at `{byokReceivedAt}`, used once, and deleted at `{byokDeletedAt}`.

This is real, not copy — both timestamps are read straight off Worker KV state.

## What "Step 1 done" requires before moving to Step 2
1. Merge the four files above into the real repo per the notes.
2. Run the real test suites (backend + worker) and get them green for real, not just
   in this sandbox.
3. One live end-to-end test with a throwaway/test API key: trigger a run with a BYOK
   key, confirm `byokDeletedAt` appears in the polled run, confirm the key does not
   appear anywhere in the Action's public run log (this is checkable directly — pull
   the real log and grep it), confirm a second manual call to the `byok-key` endpoint
   after the run returns 404.
4. Only then start Step 2 (multi-provider), per the sequencing already agreed — Step 2
   should build on a key-handling layer that's actually been proven, not just designed.
