// worker/src/rateLimit.js
//
// Shared per-IP daily KV counter. Extracted out of index.js (Step 6 security review, F6) so
// POST /api/models can use the SAME mechanism as POST /api/runs — a bespoke, separately-designed
// limiter for /api/models was explicitly ruled out in favor of reusing this one. Each caller picks
// its own `bucket` (a plain string namespace, e.g. "runs" / "models") so the two endpoints don't
// share a single budget — hitting the models limit shouldn't cost you a run, and vice versa.

function secondsToMidnightUTC(now = new Date()) {
  const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
  return Math.max(1, Math.ceil((midnight - now.getTime()) / 1000));
}

/** Per-IP daily counter in KV, namespaced by `bucket`. Over-limit costs ZERO writes (read-only).
 * Best-effort atomic — see ARCHITECTURE.md's documented residual (F4, Step 6 security review):
 * this is a cost-abuse guard, not a security boundary, and a KV read-then-write race is accepted. */
export async function checkAndBumpRateLimit(env, bucket, ip) {
  const limit = parseInt(env.RATE_LIMIT ?? "3", 10);
  const day = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const key = `rl:${bucket}:${ip}:${day}`;
  const count = parseInt((await env.RELAY_KV.get(key)) ?? "0", 10);
  if (count >= limit) {
    return { allowed: false, limit, remaining: 0, retryAfter: secondsToMidnightUTC() };
  }
  await env.RELAY_KV.put(key, String(count + 1), { expirationTtl: secondsToMidnightUTC() });
  return { allowed: true, limit, remaining: limit - count - 1, key, previous: count };
}

export async function refundRateLimit(env, rl) {
  if (rl?.key) await env.RELAY_KV.put(rl.key, String(rl.previous), { expirationTtl: secondsToMidnightUTC() });
}
