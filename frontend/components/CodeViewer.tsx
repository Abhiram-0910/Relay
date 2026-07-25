"use client";

import { useState } from "react";

import { getCode } from "@/lib/api";
import { TOKEN_CLASS, tokenizePython } from "@/lib/highlight";
import type { CodeBundle, CodeFile } from "@/lib/types";

function Highlighted({ code }: { code: string }) {
  const tokens = tokenizePython(code);
  return (
    <pre className="max-h-[28rem] overflow-auto rounded-lg border border-white/10 bg-black/30 p-4 text-xs leading-relaxed">
      <code className="font-mono">
        {tokens.map((tok, i) => (
          <span key={i} className={TOKEN_CLASS[tok.t]}>
            {tok.v}
          </span>
        ))}
      </code>
    </pre>
  );
}

function downloadFile(file: CodeFile) {
  const slug = file.endpoint
    ? file.endpoint.path.replace(/[^\w]+/g, "_").replace(/^_|_$/g, "")
    : "code";
  const blob = new Blob([file.content], { type: "text/x-python" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slug}_${file.name}`;
  a.click();
  URL.revokeObjectURL(url);
}

const btn =
  "min-h-9 cursor-pointer rounded-lg border border-line bg-white/5 px-3 text-sm text-slate-200 transition hover:bg-white/10 disabled:opacity-60";

export function CodeViewer({ runId }: { runId: string }) {
  const [bundle, setBundle] = useState<CodeBundle | null>(null);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setErr(null);
    try {
      const b = await getCode(runId);
      if (!b || b.files.length === 0) {
        setErr("No generated code was stored for this run.");
        return;
      }
      setBundle(b);
      setActive(0);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load code");
    } finally {
      setLoading(false);
    }
  };

  if (!bundle) {
    return (
      <div className="border-t border-white/10 pt-4">
        <button onClick={load} disabled={loading} className={btn}>
          {loading ? "Loading…" : "View generated code"}
        </button>
        {err && <p className="mt-2 text-sm text-red-300">{err}</p>}
      </div>
    );
  }

  const file = bundle.files[active];
  return (
    <div className="space-y-3 border-t border-white/10 pt-4">
      {bundle.truncated && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-300">
          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
          <span>
            Partial output — some generated code was truncated to fit storage limits
            {bundle.dropped > 0 ? `, and ${bundle.dropped} file(s) were dropped` : ""}. This is not
            the complete source.
          </span>
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        {bundle.files.map((f, i) => (
          <button
            key={i}
            onClick={() => setActive(i)}
            className={`rounded-md border px-2.5 py-1 font-mono text-xs transition ${
              i === active
                ? "border-white/20 bg-white/10 text-foreground"
                : "border-line bg-white/[0.03] text-muted hover:text-slate-300"
            }`}
          >
            {f.name}
            {f.endpoint ? ` · ${f.endpoint.path}` : ""}
            {f.truncated ? " (partial)" : ""}
          </button>
        ))}
      </div>

      {file.truncated && (
        <p className="text-xs text-amber-300">
          This file is truncated — the download contains the same partial content.
        </p>
      )}

      <Highlighted code={file.content} />

      <button onClick={() => downloadFile(file)} className={btn}>
        Download {file.name}
        {file.truncated ? " (partial)" : ""}
      </button>
    </div>
  );
}
