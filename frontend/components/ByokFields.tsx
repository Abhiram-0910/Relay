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
    <details className="rounded-lg border border-line bg-white/[0.02]">
      <summary className="cursor-pointer select-none px-3 py-2.5 text-sm text-slate-300">
        Use your own API key{" "}
        <span className="text-muted">(optional — otherwise the shared free tier is used)</span>
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
          <input
            id="byok-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={value.apiKey}
            disabled={disabled || !value.provider}
            onChange={(e) => onChange({ ...value, apiKey: e.target.value, model: "" })}
            placeholder="Your provider API key"
            className={`${INPUT_CLASS} font-mono`}
          />
          <p className="text-xs text-muted">
            Used once for this run, then deleted — never stored. Sent only to your chosen provider.
          </p>
        </div>

        <div className="space-y-1.5">
          <label htmlFor="byok-model" className="block text-xs font-medium text-slate-400">
            Model
          </label>
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
