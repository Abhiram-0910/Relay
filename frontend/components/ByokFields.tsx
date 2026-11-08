"use client";

import { useRef, useState } from "react";

import { fetchModels } from "@/lib/api";
import { canFetchModels, modelErrorMessage, PROVIDER_OPTIONS, type ByokState, type Provider } from "@/lib/byok";
import {
  canGoHigher,
  canGoLower,
  confirm,
  confirmedModel,
  currentRung,
  higher,
  IDLE_PICK,
  lower,
  proposeFromLadder,
  TIER_LABELS,
  type ModelPick,
} from "@/lib/modelPick";

const INPUT_CLASS =
  "min-h-11 w-full rounded-lg border border-line bg-white/5 px-3 text-sm text-foreground outline-none transition focus:border-emerald-500/50 focus:ring-2 focus:ring-emerald-500/20 disabled:opacity-60";
const BUTTON_CLASS =
  "min-h-11 cursor-pointer rounded-lg border border-line px-3 text-sm text-slate-200 transition hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-50";

interface Props {
  value: ByokState;
  onChange: (next: ByokState) => void;
  disabled?: boolean;
}

/** Optional BYOK section: provider → key → "Find a model" → one proposed model to confirm (or move
 * one step higher/lower). The key is held in component state only for the run submission — never
 * logged, never written to localStorage/sessionStorage. value.model is set only on Confirm. */
export function ByokFields({ value, onChange, disabled }: Props) {
  const [pick, setPick] = useState<ModelPick>(IDLE_PICK);
  const [showKey, setShowKey] = useState(false);
  // Bumped on every provider/key edit, so a slow ladder response for the old key is dropped.
  const requestSeq = useRef(0);

  const editInput = (next: Pick<ByokState, "provider" | "apiKey">) => {
    requestSeq.current += 1;
    setPick(IDLE_PICK);
    onChange({ ...next, model: "" });
  };

  const findModel = async () => {
    const seq = ++requestSeq.current;
    setPick({ status: "loading" });
    let next: ModelPick;
    try {
      const { ladder, default_index } = await fetchModels(value.provider, value.apiKey);
      next = proposeFromLadder(ladder, default_index);
    } catch (err) {
      next = { status: "error", message: modelErrorMessage(err) };
    }
    if (seq === requestSeq.current) setPick(next);
  };

  // Any move re-proposes an unconfirmed model, so the run is blocked again until Confirm.
  const step = (next: ModelPick) => {
    setPick(next);
    onChange({ ...value, model: confirmedModel(next) });
  };

  const rung = currentRung(pick);
  const confirmed = pick.status === "confirmed";

  return (
    <details className="group rounded-lg border border-line bg-white/[0.02]">
      <summary className="flex cursor-pointer select-none items-center justify-between gap-2 px-3 py-2.5 text-sm text-slate-300 marker:hidden [&::-webkit-details-marker]:hidden">
        <span>
          Use your own API key{" "}
          <span className="text-muted">(optional — otherwise the shared free tier is used)</span>
        </span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"
             className="h-4 w-4 shrink-0 text-muted transition-transform duration-200 group-open:rotate-90"
             aria-hidden="true">
          <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </summary>

      <div className="space-y-3 border-t border-line p-3">
        <div className="space-y-1.5">
          <label htmlFor="byok-provider" className="block text-xs font-medium text-slate-400">
            Provider
          </label>
          <select
            id="byok-provider"
            value={value.provider}
            disabled={disabled}
            onChange={(e) => editInput({ provider: e.target.value as Provider | "", apiKey: value.apiKey })}
            className={INPUT_CLASS}
          >
            <option value="">Select a provider…</option>
            {PROVIDER_OPTIONS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1.5">
          <label htmlFor="byok-key" className="block text-xs font-medium text-slate-400">
            API key
          </label>
          <div className="relative">
            <input
              id="byok-key"
              type={showKey ? "text" : "password"}
              autoComplete="off"
              spellCheck={false}
              value={value.apiKey}
              disabled={disabled || !value.provider}
              onChange={(e) => editInput({ provider: value.provider, apiKey: e.target.value })}
              placeholder="Your provider API key"
              className={`${INPUT_CLASS} pr-10 font-mono`}
            />
            <button
              type="button"
              onClick={() => setShowKey((s) => !s)}
              disabled={disabled || !value.provider}
              aria-label={showKey ? "Hide API key" : "Show API key"}
              aria-pressed={showKey}
              className="absolute inset-y-0 right-0 flex w-10 cursor-pointer items-center justify-center text-muted transition hover:text-slate-300 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {showKey ? (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-4 w-4" aria-hidden="true">
                  <path d="M3 3l18 18" strokeLinecap="round" />
                  <path d="M10.6 5.1A9.5 9.5 0 0 1 12 5c6 0 9.5 7 9.5 7a14.6 14.6 0 0 1-3.3 4.2M6.5 6.5C4 8.3 2.5 12 2.5 12S6 19 12 19a9.6 9.6 0 0 0 3-.5"
                        strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M9.5 9.9a3 3 0 0 0 4.2 4.2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-4 w-4" aria-hidden="true">
                  <path d="M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7S2.5 12 2.5 12Z" strokeLinecap="round" strokeLinejoin="round" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
              )}
            </button>
          </div>
          <p className="text-xs text-muted">
            Used once for this run, then deleted — never stored. Sent only to your chosen provider.
          </p>
        </div>

        <button
          type="button"
          onClick={findModel}
          disabled={disabled || pick.status === "loading" || !canFetchModels(value.provider, value.apiKey)}
          className={BUTTON_CLASS}
        >
          {pick.status === "loading" ? "Finding a model…" : "Find a model"}
        </button>
        {pick.status === "error" && <p className="text-xs text-red-300">{pick.message}</p>}

        {rung && (
          <div className="space-y-3 rounded-lg border border-line bg-white/[0.03] p-3" aria-live="polite">
            <div className="space-y-0.5">
              <p className="text-xs text-muted">{confirmed ? "Confirmed model" : "Proposed model"}</p>
              <p className="break-all font-mono text-sm text-foreground">{rung.id}</p>
              <p className="text-xs text-slate-300">{TIER_LABELS[rung.tier]}</p>
            </div>
            <p className="text-xs text-muted">Higher models usually cost more on your own account.</p>
            <div className="grid gap-2 sm:grid-cols-3">
              <button type="button" onClick={() => step(confirm(pick))} disabled={disabled || confirmed}
                      className={`${BUTTON_CLASS} border-emerald-500/40 text-emerald-200`}>
                {confirmed ? "Confirmed" : "Confirm"}
              </button>
              <div className="space-y-1">
                <button type="button" onClick={() => step(higher(pick))} disabled={disabled || !canGoHigher(pick)}
                        className={`${BUTTON_CLASS} w-full`}>
                  Use higher model
                </button>
                {!canGoHigher(pick) && <p className="text-xs text-muted">No higher model available</p>}
              </div>
              <div className="space-y-1">
                <button type="button" onClick={() => step(lower(pick))} disabled={disabled || !canGoLower(pick)}
                        className={`${BUTTON_CLASS} w-full`}>
                  Use lower model
                </button>
                {!canGoLower(pick) && <p className="text-xs text-muted">No lower model available</p>}
              </div>
            </div>
          </div>
        )}
      </div>
    </details>
  );
}
