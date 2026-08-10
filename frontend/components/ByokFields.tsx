"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchModels } from "@/lib/api";
import {
  canFetchModels,
  MODEL_FETCH_DEBOUNCE_MS,
  modelErrorMessage,
  PROVIDER_OPTIONS,
  type ByokState,
  type Provider,
} from "@/lib/byok";
import { debounce } from "@/lib/debounce";
import type { ModelOption } from "@/lib/types";

const INPUT_CLASS =
  "min-h-11 w-full rounded-lg border border-line bg-white/5 px-3 text-sm text-foreground outline-none transition focus:border-emerald-500/50 focus:ring-2 focus:ring-emerald-500/20 disabled:opacity-60";

interface Props {
  value: ByokState;
  onChange: (next: ByokState) => void;
  disabled?: boolean;
}

/** Optional BYOK section: provider → key → live model list. The key is held in component state
 * only for the run submission — never logged, never written to localStorage/sessionStorage. */
export function ByokFields({ value, onChange, disabled }: Props) {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showKey, setShowKey] = useState(false);

  // Only touches stable setters + its args, so one debounced copy for the component's lifetime.
  const loadModels = useCallback(async (provider: string, apiKey: string) => {
    setLoading(true);
    setError(null);
    try {
      const list = await fetchModels(provider, apiKey);
      setModels(list);
      if (list.length === 0) setError("No compatible models found for this key.");
    } catch (err) {
      setModels([]);
      setError(modelErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const debouncedLoad = useRef(debounce(loadModels, MODEL_FETCH_DEBOUNCE_MS)).current;

  useEffect(() => {
    // Provider/key changed: drop the stale list, then (only if the input is complete) debounce a
    // fresh fetch. The debounce + this cleanup are why we don't hit the provider on every keystroke.
    setModels([]);
    setError(null);
    if (!canFetchModels(value.provider, value.apiKey)) {
      debouncedLoad.cancel();
      return;
    }
    debouncedLoad(value.provider, value.apiKey);
    return () => debouncedLoad.cancel();
  }, [value.provider, value.apiKey, debouncedLoad]);

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
            onChange={(e) => onChange({ provider: e.target.value as Provider | "", apiKey: value.apiKey, model: "" })}
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
              onChange={(e) => onChange({ ...value, apiKey: e.target.value, model: "" })}
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

        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <label htmlFor="byok-model" className="block text-xs font-medium text-slate-400">
              Model
            </label>
            {loading && (
              <svg viewBox="0 0 24 24" fill="none" className="h-3 w-3 animate-spin text-muted" aria-hidden="true">
                <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" strokeOpacity="0.25" />
                <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            )}
          </div>
          <select
            id="byok-model"
            value={value.model}
            disabled={disabled || models.length === 0}
            onChange={(e) => onChange({ ...value, model: e.target.value })}
            className={INPUT_CLASS}
          >
            <option value="">
              {loading ? "Loading models…" : models.length === 0 ? "Enter a valid key first" : "Select a model…"}
            </option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
          {error && <p className="text-xs text-red-300">{error}</p>}
        </div>
      </div>
    </details>
  );
}
