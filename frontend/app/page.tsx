"use client";

import { useCallback, useEffect, useState } from "react";

import { Banner } from "@/components/Banner";
import { ByokFields } from "@/components/ByokFields";
import { CodeViewer } from "@/components/CodeViewer";
import { RunProgress } from "@/components/RunProgress";
import { StatusBadge } from "@/components/StatusBadge";
import { ValidationReport } from "@/components/ValidationReport";
import { createRun, getRun, RateLimitedError } from "@/lib/api";
import { byokReceiptSummary, EMPTY_BYOK, type ByokState } from "@/lib/byok";
import { errorMessage, suggestsByok } from "@/lib/errors";
import type { RateLimitInfo, RunSnapshot } from "@/lib/types";

type Phase = "idle" | "creating" | "polling" | "done" | "error";

const POLL_MS = 1500;
const MAX_POLL_MS = 10 * 60 * 1000; // runs include a Docker image build; give them room
const DEMO_SPEC = "https://raw.githubusercontent.com/open-meteo/open-meteo/main/openapi/forecast.yml";

export default function Home() {
  const [specUrl, setSpecUrl] = useState(DEMO_SPEC);
  const [phase, setPhase] = useState<Phase>("idle");
  const [runId, setRunId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [rateLimit, setRateLimit] = useState<RateLimitInfo | null>(null);
  const [byok, setByok] = useState<ByokState>(EMPTY_BYOK);

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const url = specUrl.trim();
      if (!url) return;
      setPhase("creating");
      setErrorMsg(null);
      setRateLimit(null);
      setSnapshot(null);
      setRunId(null);
      try {
        const { runId } = await createRun(url, byok);
        setRunId(runId);
        setPhase("polling");
      } catch (err) {
        if (err instanceof RateLimitedError) setRateLimit(err.info);
        else setErrorMsg(err instanceof Error ? err.message : "Something went wrong");
        setPhase("error");
      }
    },
    [specUrl, byok],
  );

  useEffect(() => {
    if (phase !== "polling" || !runId) return;
    let cancelled = false;
    const started = Date.now();

    const tick = async () => {
      if (cancelled) return;
      try {
        const snap = await getRun(runId);
        if (cancelled) return;
        if (!snap) {
          setErrorMsg("Run not found — it may have expired.");
          setPhase("error");
          return;
        }
        setSnapshot(snap);
        if (snap.status === "succeeded" || snap.status === "failed") {
          setPhase("done");
          return;
        }
        if (Date.now() - started > MAX_POLL_MS) {
          setErrorMsg("Timed out waiting for the run to finish.");
          setPhase("error");
          return;
        }
        setTimeout(tick, POLL_MS);
      } catch (err) {
        if (cancelled) return;
        setErrorMsg(err instanceof Error ? err.message : "Polling failed");
        setPhase("error");
      }
    };
    tick();
    return () => {
      cancelled = true;
    };
  }, [phase, runId]);

  const busy = phase === "creating" || phase === "polling";
  const byokReceipt = snapshot ? byokReceiptSummary(snapshot) : null;

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-8 px-5 py-16 sm:py-24">
      <header className="space-y-3">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Relay</h1>
        <p className="text-pretty text-muted">
          Point it at an OpenAPI spec URL. Relay generates a typed client and{" "}
          <span className="text-foreground">proves it works by running it live in a sandbox</span> —
          not just checking that it compiles.
        </p>
      </header>

      <form onSubmit={submit} className="space-y-3">
        <label htmlFor="specUrl" className="block text-sm font-medium text-slate-300">
          OpenAPI / Swagger spec URL
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            id="specUrl"
            type="url"
            required
            value={specUrl}
            onChange={(e) => setSpecUrl(e.target.value)}
            disabled={busy}
            placeholder="https://…/openapi.yaml"
            className="min-h-11 flex-1 rounded-lg border border-line bg-white/5 px-3 font-mono text-sm text-foreground outline-none transition focus:border-emerald-500/50 focus:ring-2 focus:ring-emerald-500/20 disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={busy}
            className="min-h-11 cursor-pointer rounded-lg bg-emerald-500 px-5 text-sm font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? "Working…" : "Generate & validate"}
          </button>
        </div>
        <ByokFields value={byok} onChange={setByok} disabled={busy} />
        <p className="text-xs text-muted">Free tier: 3 generations per day. Default is the Open-Meteo demo.</p>
      </form>

      {rateLimit && (
        <Banner status="rate_limited">
          You&apos;ve used all {rateLimit.limit} free generations for today. This is a cap to keep the
          free tier alive — nothing broke. Try again in about{" "}
          {Math.max(1, Math.round(rateLimit.retryAfter / 3600))} hour
          {Math.round(rateLimit.retryAfter / 3600) === 1 ? "" : "s"}.
          {suggestsByok(rateLimit.error) && " Or bring your own key to skip this limit."}
        </Banner>
      )}

      {errorMsg && <Banner status="error">{errorMsg}</Banner>}

      {snapshot && (phase === "polling" || phase === "done") && (
        <section className="space-y-5 rounded-xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-muted">Run</h2>
            <StatusBadge status={snapshot.status} pulse={phase === "polling"} />
          </div>

          {byokReceipt && (
            <p
              className="flex items-start gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/[0.04] p-3 text-xs text-emerald-200"
              title={`Received ${byokReceipt.receivedAt} · Deleted ${byokReceipt.deletedAt}`}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
                   className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true">
                <rect x="4.5" y="10.5" width="15" height="10" rx="2" />
                <path d="M8 10.5V7a4 4 0 0 1 8 0v3.5" />
              </svg>
              <span>{byokReceipt.text}</span>
            </p>
          )}

          {phase === "polling" && <RunProgress snapshot={snapshot} />}

          {snapshot.status === "failed" && snapshot.error && (
            <Banner status={snapshot.error}>
              {errorMessage(snapshot.error)}
              {suggestsByok(snapshot.error) && " You can also bring your own key to avoid this limit."}
            </Banner>
          )}

          {phase === "done" && snapshot.result && (
            <>
              <ValidationReport result={snapshot.result} />
              {snapshot.status === "succeeded" && <CodeViewer runId={snapshot.runId} />}
            </>
          )}
        </section>
      )}
    </main>
  );
}
