// worker/src/byok.js
//
// New, additive module for BYOK (bring-your-own-key) support — Step 1 of the
// Phase 2 plan in TODO.md / ARCHITECTURE.md. This file does NOT assume or
// replace the existing router in worker/src/index.js (this session has no
// view of that file's real contents) — wire the exports below into it.
//
// Integration points:
//   1. Inside the existing POST /api/runs handler, AFTER runId is minted and
//      BEFORE the workflow_dispatch call is built:
//         const hasByok = await storeByokKey(env, runId, body.apiKey, body.provider);
//         const inputs = buildDispatchInputs(runId, body.specUrl, callbackUrl, hasByok);
//      If the existing dispatch code builds `inputs` by spreading the parsed
//      request body (e.g. `{...body, run_id, ...}`), that would leak apiKey
//      even with this file added — replace it with buildDispatchInputs()
//      below, which structurally cannot include apiKey since apiKey isn't
//      one of its parameters.
//   2. Add a new route: GET /api/runs/:runId/byok-key -> handleByokKeyFetch.
//      Same Bearer CALLBACK_SECRET auth as /api/runs/:id/progress today.
//   3. KV binding is assumed to be `env.RELAY_KV` (per ARCHITECTURE.md's
//      "no DB, run state namespaced by run id" description) — rename to match
//      the real binding name if it differs.

const BYOK_TTL_SECONDS = 240; // KV minimum is 60s. Was 120s, but a genuinely
                               // cold `make sandbox-build` in the Action can
                               // push key-fetch past that ceiling, silently
                               // expiring the ticket and falling back to the
                               // shared key. 240s keeps headroom while staying
                               // short — this is a single-use ticket (deleted
                               // on first read), not a stored secret.

/**
 * Shared Bearer-CALLBACK_SECRET check for every Action-only callback route (this file's
 * handleByokKeyFetch, and index.js's handleProgress/handleStoreCode). Fails CLOSED on a missing
 * secret: `!env.CALLBACK_SECRET` is checked explicitly rather than relying on the string
 * comparison alone, because an unset secret makes `` `Bearer ${env.CALLBACK_SECRET}` `` the
 * literal "Bearer undefined" — a value any caller can send. Step 6 security review (F3): this
 * file's own handleByokKeyFetch — the one route that returns a user's plaintext BYOK key — had
 * diverged from its two siblings and skipped this check. One shared helper so a future handler
 * can't repeat that divergence.
 */
export function isAuthorizedCallback(request, env) {
  const auth = request.headers.get("Authorization") || "";
  return Boolean(env.CALLBACK_SECRET) && auth === `Bearer ${env.CALLBACK_SECRET}`;
}

/**
 * Call from the POST /api/runs handler, after runId is minted, before
 * building workflow_dispatch inputs. No-ops (returns false) if no apiKey was
 * supplied on this run — most runs won't have one, and that's fine.
 *
 * @returns {Promise<boolean>} true if a key was stored — caller should pass
 *   this into buildDispatchInputs() as `hasByok`.
 */
export async function storeByokKey(env, runId, apiKey, provider, model) {
  if (!apiKey) return false;
  if (typeof apiKey !== "string" || apiKey.length < 8 || apiKey.length > 512) {
    // Fail closed on malformed input rather than storing garbage a later
    // step would have to guess about.
    throw new Error("invalid apiKey");
  }
  if (model != null && (typeof model !== "string" || model.length < 1 || model.length > 256)) {
    // Model id is optional (shared-key/Gemini-default runs omit it), but if
    // present it must be a sane string — same fail-closed stance as apiKey.
    throw new Error("invalid model");
  }
  const record = {
    apiKey,
    provider: provider || "unknown",
    model: model || null,
    storedAt: new Date().toISOString(),
  };
  await env.RELAY_KV.put(`byok:${runId}`, JSON.stringify(record), {
    expirationTtl: BYOK_TTL_SECONDS,
  });
  return true;
}

/**
 * Builds the workflow_dispatch `inputs` object. apiKey is deliberately not a
 * parameter here — that's the actual guarantee, not just a coding
 * convention: there is no apiKey value in scope for this function to leak.
 */
export function buildDispatchInputs(runId, specUrl, callbackUrl, hasByok) {
  return {
    run_id: runId,
    spec_url: specUrl,
    callback_url: callbackUrl,
    has_byok: hasByok ? "true" : "false",
  };
}

/**
 * Route handler for GET /api/runs/:runId/byok-key.
 * Action-only. Delivery-once: the KV entry is deleted on the read that
 * succeeds, not left to expire via TTL alone. A second call, or a call after
 * natural TTL expiry, both return 404 — from the caller's side those are
 * meant to be indistinguishable, since both mean "the key is gone."
 */
export async function handleByokKeyFetch(request, env, runId) {
  if (!isAuthorizedCallback(request, env)) {
    return new Response(JSON.stringify({ error: "unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  const raw = await env.RELAY_KV.get(`byok:${runId}`);
  if (!raw) {
    return new Response(JSON.stringify({ found: false }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Delete BEFORE responding — if the response is lost in transit, the key
  // is gone rather than deliverable twice. ci_runner treats a failed fetch
  // as "no BYOK key, use the shared free tier," not as something to retry.
  await env.RELAY_KV.delete(`byok:${runId}`);

  const { apiKey, provider, model, storedAt } = JSON.parse(raw);
  const deliveredAt = new Date().toISOString();

  // Stamp the existing run record with the honest proof pair. This reuses
  // the polling endpoint the frontend already calls — no new frontend
  // request needed to show the receipt.
  const runRaw = await env.RELAY_KV.get(`run:${runId}`);
  if (runRaw) {
    const run = JSON.parse(runRaw);
    run.byokReceivedAt = storedAt;
    run.byokDeletedAt = deliveredAt;
    // apiKey is deliberately never written onto the run record.
    await env.RELAY_KV.put(`run:${runId}`, JSON.stringify(run));
  }

  return new Response(JSON.stringify({ apiKey, provider, model: model ?? null, deliveredAt }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
