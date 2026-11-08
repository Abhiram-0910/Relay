// worker/src/modelRank.js
//
// rankModels(provider, models) -> { ladder: [{id, tier}], default_index }
//
// Pure and deterministic. Input is the already-filtered chat-capable list from models.js
// ([{id, created?}]); this never widens it. Ranking is a NAMING-SCHEME HEURISTIC: each provider has
// a small rule table mapping an id to {family, tier, version}, derived from real lists (the
// committed 133-id OpenAI capture and OpenRouter's public /models, 2026-11-05). An id that matches
// no rule is EXCLUDED, never guessed — so a brand-new family name stays invisible until a rule is
// added here (documented residual).
//
// Default = the balanced tier. The corrector patches a small piece of generated code; that needs a
// competent model, not the most expensive one, and the user pays on their own account. So the
// default is the strongest mid-tier model; small/flagship are one click away.

export const TIERS = ["small", "balanced", "flagship"];
export const MODEL_ID_MAX_LEN = 128;
// Shared with storeByokKey (POST /api/runs) so a run's model id obeys the same shape as a ladder id.
export const isValidModelId = (id) =>
  typeof id === "string" && id.length >= 1 && id.length <= MODEL_ID_MAX_LEN && /^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(id);

const V = String.raw`(\d+(?:[.-]\d{1,2})?)`; // "5", "5.4", "4-5" (anthropic native). Never 4+ digits.
const OPENAI_DATE = String.raw`(?:-(\d{4}-\d{2}-\d{2}|\d{4}))?`; // -2025-08-07 or legacy -0613
const ANTHROPIC_DATE = String.raw`(?:-(\d{8}))?`; // -20250929
const GEMINI_SNAPSHOT = String.raw`(?:-(\d{3}))?`; // -001
const GROK_DATE = String.raw`(?:-(\d{4}))?`; // -0709

// Each rule: re (anchored; groups: version, variant, date), tier per variant, grade per variant.
// grade only breaks ties between variants that share a tier AND a version (e.g. gpt-6.1-sol vs
// gpt-6.1-terra, both balanced: sol is priced/positioned above terra, so it is the "strongest").
const OPENAI = [
  // Classic: gpt-5.4-nano / gpt-5.4-mini / gpt-5.5 (+ dated twins). -pro, -codex, -turbo, 4o,
  // -search-api, audio/live and the o-series match nothing here, so they are excluded from auto-pick.
  { re: new RegExp(`^gpt-${V}(?:-(nano|mini))?${OPENAI_DATE}$`),
    tier: { nano: "small", mini: "balanced", "": "flagship" }, grade: {} },
  // Celestial (5.6+): luna < terra < sol < astra by OpenRouter's descriptions and prices. sol was the
  // 5.6 flagship but sits below astra from gpt-6 on, so it is balanced, ranked above terra.
  { re: new RegExp(`^gpt-${V}-(luna|terra|sol|astra)${OPENAI_DATE}$`),
    tier: { luna: "small", terra: "balanced", sol: "balanced", astra: "flagship" }, grade: { sol: 1 } },
];
const ANTHROPIC = [
  // claude-sonnet-4-5-20250929 / claude-opus-4-6 / OpenRouter's claude-opus-5.5. fable (priced above
  // opus) has no rule: it is a premium line above the flagship tier, like OpenAI's -pro.
  { re: new RegExp(`^claude-(haiku|sonnet|opus)-${V}${ANTHROPIC_DATE}$`), variantFirst: true,
    tier: { haiku: "small", sonnet: "balanced", opus: "flagship" }, grade: {} },
  // Pre-4 scheme: claude-3-5-haiku-20241022.
  { re: new RegExp(`^claude-${V}-(haiku|sonnet|opus)${ANTHROPIC_DATE}$`),
    tier: { haiku: "small", sonnet: "balanced", opus: "flagship" }, grade: {} },
];
const GEMINI = [
  // gemini-3.5-flash-lite / gemini-3.8-flash / gemini-2.5-pro / gemini-4-argon (Google's gemini-4
  // flagship). -preview/-exp/-image/-tts/-latest and gemma-* match nothing.
  { re: new RegExp(`^gemini-${V}-(flash-lite|flash|pro|argon)${GEMINI_SNAPSHOT}$`),
    tier: { "flash-lite": "small", flash: "balanced", pro: "flagship", argon: "flagship" }, grade: {} },
];
const GROK = [
  // grok-3-mini / grok-4.7 / grok-4-0709. -fast-*, -code-*, -build-*, -vision, -multi-agent: no rule.
  // Unverified against xAI's own list (no key); shapes come from OpenRouter's x-ai/ ids.
  { re: new RegExp(`^grok-${V}(?:-(mini))?${GROK_DATE}$`), tier: { mini: "small", "": "flagship" }, grade: {} },
];
// grok-4.20 shipped before 4.3 (OpenRouter created: 2026-03); a numeric compare would rank it above
// 4.7. Exact id, excluded rather than renumbered.
const MISNUMBERED = new Set(["grok-4.20"]);

const TABLES = { openai: OPENAI, anthropic: ANTHROPIC, gemini: GEMINI, grok: GROK };
// OpenRouter: vendor/model, ranked with the vendor's native table. Everything else is excluded:
// other vendors, and ~moving aliases / :batch / :free variants (isValidModelId rejects '~' and ':').
const OPENROUTER_VENDORS = { openai: OPENAI, anthropic: ANTHROPIC, google: GEMINI, "x-ai": GROK };

const UNSTABLE = /(preview|experimental|deprecated|-exp\b|-latest\b)/;

const parseVersion = (s) => s.split(/[.-]/).map((n) => parseInt(n, 10));
export function compareVersions(a, b) {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}

function classify(id, table) {
  if (UNSTABLE.test(id) || MISNUMBERED.has(id)) return null;
  for (const rule of table) {
    const m = id.match(rule.re);
    if (!m) continue;
    const [version, variant = ""] = rule.variantFirst ? [m[2], m[1]] : [m[1], m[2]];
    const tier = rule.tier[variant];
    if (!tier) return null;
    const date = m[3] ?? null;
    // base = the undated alias this id would be a snapshot of.
    const base = date ? id.slice(0, id.length - date.length - 1) : id;
    return { tier, version: parseVersion(version), grade: rule.grade[variant] ?? 0, date, base };
  }
  return null;
}

function classifyFor(provider, id) {
  if (provider === "openrouter") {
    const slash = id.indexOf("/");
    const table = OPENROUTER_VENDORS[id.slice(0, slash)];
    if (slash < 1 || !table) return null;
    const c = classify(id.slice(slash + 1), table);
    return c && { ...c, family: id.slice(0, slash), base: `${id.slice(0, slash)}/${c.base}` };
  }
  const table = TABLES[provider];
  const c = table && classify(id, table);
  return c && { ...c, family: provider };
}

// Within one family: highest version, then grade, then latest snapshot date, then created.
const withinFamily = (a, b) =>
  compareVersions(b.version, a.version) || b.grade - a.grade ||
  String(b.date ?? "").localeCompare(String(a.date ?? "")) || (b.created ?? 0) - (a.created ?? 0) ||
  (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
// Across families (OpenRouter only: versions of different vendors don't compare) the newest
// release wins; created is the only shared ordering the list provides.
const acrossFamilies = (a, b) => b.created - a.created || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

export function rankModels(provider, models) {
  const candidates = [];
  for (const m of models ?? []) {
    const id = m?.id;
    if (!isValidModelId(id)) continue;
    const c = classifyFor(provider, id);
    if (c) candidates.push({ id, created: Number(m.created) || 0, ...c });
  }
  // A dated snapshot is dropped when its undated alias is also listed.
  const aliases = new Set(candidates.filter((c) => !c.date).map((c) => c.id));
  const live = candidates.filter((c) => !c.date || !aliases.has(c.base));

  const ladder = [];
  for (const tier of TIERS) {
    const inTier = live.filter((c) => c.tier === tier);
    if (inTier.length === 0) continue;
    const bestPerFamily = [...new Set(inTier.map((c) => c.family))]
      .map((f) => inTier.filter((c) => c.family === f).sort(withinFamily)[0]);
    ladder.push({ id: bestPerFamily.sort(acrossFamilies)[0].id, tier });
  }
  return { ladder, default_index: defaultIndex(ladder) };
}

// Balanced if present; otherwise the nearest existing tier, preferring the cheaper one on a tie.
function defaultIndex(ladder) {
  if (ladder.length === 0) return -1;
  const target = TIERS.indexOf("balanced");
  let best = 0;
  ladder.forEach((rung, i) => {
    const d = Math.abs(TIERS.indexOf(rung.tier) - target);
    if (d < Math.abs(TIERS.indexOf(ladder[best].tier) - target)) best = i;
  });
  return best;
}
