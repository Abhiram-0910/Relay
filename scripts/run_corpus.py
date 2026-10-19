"""Generation-fidelity harness over the pinned corpus (piece 4f). No live API calls, no LLM.

For each spec in backend/corpus/manifest.json, in its own subprocess (180 s timeout): verify the
cached bytes' sha256, run ci_runner.load_spec_text (version gate, $ref resolution with remote
fetches refused, validation), generate_all_endpoints, then per generated operation:
  - compile both files;
  - assert_generated_source_inert on both (a failure is a finding; that operation is never run);
  - an execution check in the relay-sandbox image (--network none, --read-only, --cap-drop ALL,
    pids/memory limits, files mounted read-only) by scripts/corpus_check.py.
Writes backend/corpus/RESULTS.json and RESULTS.md. Specs are read from the gitignored cache only.

Usage (from the repo root): backend/venv/bin/python scripts/run_corpus.py
"""
from __future__ import annotations

import collections
import contextlib
import datetime
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app import ci_runner  # noqa: E402
from app.generate import HTTP_METHODS, generate_all_endpoints  # noqa: E402
from app.params import merged_parameters  # noqa: E402
from tests.source_inert import assert_generated_source_inert  # noqa: E402

CORPUS = ROOT / "backend" / "corpus"
CACHE = CORPUS / ".cache"
MANIFEST = CORPUS / "manifest.json"
CHECKER = ROOT / "scripts" / "corpus_check.py"
SPEC_TIMEOUT = 180
DOCKER_TIMEOUT = 150
CHECK_BASE_URL = "https://corpus.invalid"
IMAGE = "relay-sandbox"
PACKAGES = ("datamodel-code-generator", "pydantic", "prance", "jinja2", "requests")
_NO_JSON = "has no JSON response schema to generate a model from"


@contextlib.contextmanager
def offline_refs():
    """Refuse every remote $ref fetch: a corpus result must depend only on the pinned bytes."""
    def refuse(url, *a, **k):
        raise ci_runner.PranceResolutionError("remote $ref refused in the offline corpus run")

    original = ci_runner._safe_ref_fetch_url_text
    ci_runner._safe_ref_fetch_url_text = refuse
    try:
        yield
    finally:
        ci_runner._safe_ref_fetch_url_text = original


def skip_code(reason: str) -> str:
    """Our skip reasons -> a stable code (paths are spec text and are dropped)."""
    if reason.startswith("generator_error:"):
        return reason.replace(" ", "")
    if reason.endswith(_NO_JSON):
        return "no_json_response"
    return reason.rsplit(" skipped: ", 1)[-1] if " skipped: " in reason else "other"


def spec_features(spec: dict) -> dict:
    """Which parameter/server/response constructs a resolved spec uses (selection statistics)."""
    feats = dict.fromkeys(("header_params", "array_query_params", "path_item_parameters",
                           "operation_or_path_servers", "non_json_responses"), False)
    for item in (spec.get("paths") or {}).values():
        if not isinstance(item, dict):
            continue
        feats["path_item_parameters"] |= bool(item.get("parameters"))
        feats["operation_or_path_servers"] |= bool(item.get("servers"))
        for method, op in item.items():
            if method not in HTTP_METHODS or not isinstance(op, dict):
                continue
            feats["operation_or_path_servers"] |= bool(op.get("servers"))
            for p in merged_parameters(item, op):
                if not isinstance(p, dict):
                    continue
                schema = p.get("schema") or {}
                types = schema.get("type") if isinstance(schema.get("type"), list) else [schema.get("type")]
                feats["header_params"] |= p.get("in") == "header"
                feats["array_query_params"] |= p.get("in") == "query" and "array" in types
            for status, resp in (op.get("responses") or {}).items():
                if str(status).startswith("2") and isinstance(resp, dict):
                    feats["non_json_responses"] |= any(ct != "application/json" for ct in (resp.get("content") or {}))
    return feats


def _docker_check(gen_root: Path, ops: list[dict]) -> dict[str, dict]:
    job = gen_root / "ops.json"
    job.write_text(json.dumps({"root": "/corpus", "base_url": CHECK_BASE_URL, "ops": ops}))
    for path in [gen_root, *gen_root.rglob("*")]:  # the image runs as uid 10001
        path.chmod(0o755 if path.is_dir() else 0o644)
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "512m",
           "--memory-swap", "512m", "--tmpfs", "/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
           "-v", f"{gen_root}:/corpus:ro", "-v", f"{CHECKER}:/check.py:ro",
           "--entrypoint", "python", IMAGE, "/check.py", "/corpus/ops.json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DOCKER_TIMEOUT)
    results = {}
    for line in proc.stdout.splitlines():
        if line.startswith("CORPUS:"):
            row = json.loads(line[len("CORPUS:"):])
            results[row.pop("op_id")] = row
    for op in ops:  # a container crash must not read as a pass
        results.setdefault(op["op_id"], {"ok": False, "error": "NoCheckerResult",
                                         "detail": repr(proc.stderr[-200:])})
    return results


def run_one(entry: dict) -> dict:
    started = time.monotonic()
    rec = {"id": entry["id"], "openapi": entry["openapi"], "status": "ok", "spec_version": None,
           "ops_total": entry["operations"], "generated": 0, "skipped": {}, "lossy": {}, "ignored": {},
           "compile_failures": [], "inert_failures": [], "exec_ok": 0, "exec_skipped_required_body": 0,
           "exec_failures": [], "features": {}}
    cached = CACHE / entry["sha256"]
    raw = cached.read_bytes() if cached.exists() else b""
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        rec["status"] = "hash_mismatch"
        return rec
    try:
        with offline_refs():
            spec, _, rec["spec_version"] = ci_runner.load_spec_text(raw.decode("utf-8"), entry["url"])
    except Exception as exc:
        rec["status"] = f"load_failed:{ci_runner._classify_pipeline_error(exc)}"
        rec["load_error"] = type(exc).__name__
        rec["seconds"] = round(time.monotonic() - started, 1)
        return rec
    rec["features"] = spec_features(spec)

    with tempfile.TemporaryDirectory() as tmp:
        gen_root = Path(tmp) / "corpus"
        gen_root.mkdir()
        items = list(generate_all_endpoints(spec, Path(tmp) / "work"))
        rec["ops_total"] = len(items)
        to_check = []
        for n, item in enumerate(items):
            op_id = f"{item['endpoint']['method']} {item['endpoint']['path']}"
            if item["status"] != "generated":
                code = skip_code(item["reason"])
                rec["skipped"][code] = rec["skipped"].get(code, 0) + 1
                continue
            rec["generated"] += 1
            for entry_ in item["lossy"]:
                rec["lossy"][entry_["reason"]] = rec["lossy"].get(entry_["reason"], 0) + 1
            for entry_ in item["ignored"]:
                rec["ignored"][entry_["reason"]] = rec["ignored"].get(entry_["reason"], 0) + 1
            try:
                for name in ("models_code", "client_code"):
                    compile(item[name], name, "exec")
            except SyntaxError as exc:
                rec["compile_failures"].append({"op": op_id, "error": type(exc).__name__})
                continue
            try:
                for name in ("models_code", "client_code"):
                    assert_generated_source_inert(item[name])
            except AssertionError as exc:
                rec["inert_failures"].append({"op": op_id, "file": name, "detail": repr(str(exc))[:200]})
                continue
            pkg = gen_root / f"op{n}"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("")
            (pkg / "models.py").write_text(item["models_code"])
            (pkg / "client.py").write_text(item["client_code"])
            to_check.append({"op_id": op_id, "package": pkg.name, "class_name": item["class_name"],
                             "method_name": item["method_name"], "params": item["params"]})
        if to_check:
            for op_id, result in _docker_check(gen_root, to_check).items():
                if result.get("ok") is True:
                    rec["exec_ok"] += 1
                elif result.get("skipped") == "required_body":
                    rec["exec_skipped_required_body"] += 1
                else:
                    rec["exec_failures"].append({"op": op_id, "error": result["error"], "detail": result["detail"]})
    rec["seconds"] = round(time.monotonic() - started, 1)
    return rec


# --- parent: run every spec in a subprocess, then write the results -------------------------------

def _versions() -> dict:
    venv = {"python": platform.python_version(), **{p: version(p) for p in PACKAGES}}
    probe = ("import sys, importlib.metadata as m; print(sys.version.split()[0]); "
             "[print(m.version(p)) for p in ('pydantic', 'requests')]")
    out = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", IMAGE,
                          "-c", probe], capture_output=True, text=True, timeout=60).stdout.split()
    image = dict(zip(("python", "pydantic", "requests"), out))
    return {"venv": venv, "sandbox_image": image}


def _git() -> dict:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "backend/app", "scripts"], cwd=ROOT,
                           capture_output=True, text=True).stdout.split("\n")
    return {"sha": sha, "uncommitted": sorted(line[3:] for line in dirty if line.strip())}


def run_all(manifest: dict) -> list[dict]:
    records = []
    for entry in manifest["specs"]:
        try:
            proc = subprocess.run([sys.executable, __file__, "--one", entry["id"]], capture_output=True,
                                  text=True, timeout=SPEC_TIMEOUT, cwd=ROOT)
            lines = [line for line in proc.stdout.splitlines() if line.startswith("RECORD:")]
            rec = json.loads(lines[-1][len("RECORD:"):]) if lines else {
                "id": entry["id"], "openapi": entry["openapi"], "status": f"crashed (exit {proc.returncode})"}
        except subprocess.TimeoutExpired:
            rec = {"id": entry["id"], "openapi": entry["openapi"], "status": "timeout", "seconds": SPEC_TIMEOUT}
        print(f"{rec['id']}: {rec['status']} generated={rec.get('generated', '-')} "
              f"exec_failures={len(rec.get('exec_failures', []))}", flush=True)
        records.append(rec)
    return records


def gates(records: list[dict]) -> list[tuple[str, int]]:
    total = lambda key: sum(len(r.get(key, [])) for r in records)  # noqa: E731
    gen_errors = sum(n for r in records for code, n in r.get("skipped", {}).items() if code.startswith("generator_error"))
    syntax = sum(n for r in records for code, n in r.get("skipped", {}).items() if code == "generator_error:SyntaxError")
    bad_status = sum(1 for r in records if r["status"] in ("hash_mismatch", "timeout"))
    return [("G1 zero compile failures", total("compile_failures") + syntax),
            ("G2 zero inert-check failures", total("inert_failures")),
            ("G3 zero generator_error skips", gen_errors),
            ("G4 zero execution-check failures", total("exec_failures")),
            ("G5 zero hash_mismatch and zero timeouts", bad_status)]


def _md(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _counts(d: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "-"


def write_results(manifest: dict, records: list[dict], meta: dict) -> None:
    (CORPUS / "RESULTS.json").write_text(json.dumps({"meta": meta, "gates": gates(records), "specs": records},
                                                    indent=2) + "\n")
    ok = [r for r in records if r["status"] == "ok"]
    minors = collections.Counter(r["openapi"][:3] for r in records)
    feature_counts = collections.Counter(k for r in ok for k, v in r.get("features", {}).items() if v)
    lines = [
        "# Corpus results — generation fidelity",
        "",
        "Generated by `scripts/run_corpus.py`; do not edit by hand.",
        "",
        "**Generation fidelity only: no operation in this table was called live.**",
        "",
        f"- Relay commit: `{meta['git']['sha']}`"
        + (f" plus uncommitted changes in {', '.join(f'`{p}`' for p in meta['git']['uncommitted'])}"
           if meta["git"]["uncommitted"] else ""),
        f"- Date: {meta['date']}",
        f"- Host venv: " + ", ".join(f"{k} {v}" for k, v in meta["versions"]["venv"].items()),
        f"- Sandbox image (`{IMAGE}`): " + ", ".join(f"{k} {v}" for k, v in meta["versions"]["sandbox_image"].items()),
        "",
        "## Source",
        "",
        f"{len(records)} specs: {sum(1 for e in manifest['specs'] if e['source'] == 'apis.guru')} from the "
        "APIs.guru directory (natively-authored OpenAPI 3.x only, one per provider, 5-100 operations, first in "
        "sorted-id order) plus Open-Meteo and Petstore v3. The APIs.guru directory is stale (newest spec update "
        "2023-04-21); its pinned copies are used (sha256 in `manifest.json`), and each original `x-origin` URL is "
        "recorded as provenance only, never fetched. Sorted-id order means the APIs.guru picks are all providers "
        "whose names start with a digit, `a` or `b`.",
        "",
        f"OpenAPI versions: {', '.join(f'{k}.x {v}' for k, v in sorted(minors.items()))}. "
        f"**3.1 coverage is limited to those {minors.get('3.1', 0)} specs.**",
        "",
        "Specs are not redistributed: they are cached under `backend/corpus/.cache/`, which is gitignored. "
        "Licences, where a spec declares one, are recorded in `manifest.json`.",
        "",
        "## Selection statistics (specs that loaded)",
        "",
        *[f"- {k.replace('_', ' ')}: {feature_counts.get(k, 0)} of {len(ok)}"
          for k in ("header_params", "array_query_params", "path_item_parameters",
                    "operation_or_path_servers", "non_json_responses")],
        "",
        "## Per spec",
        "",
        "| Spec | OpenAPI | Status | Ops | Generated | Skipped (by reason) | Lossy | Ignored | Inert fail | Exec ok | Body-skip | Exec fail | s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append("| " + " | ".join(_md(x) for x in (
            r["id"], r["openapi"], r["status"], r.get("ops_total", "-"), r.get("generated", "-"),
            _counts(r.get("skipped", {})), _counts(r.get("lossy", {})), _counts(r.get("ignored", {})),
            len(r.get("inert_failures", [])), r.get("exec_ok", "-"), r.get("exec_skipped_required_body", "-"),
            len(r.get("exec_failures", [])), r.get("seconds", "-"))) + " |")
    sums = {k: sum(r.get(k, 0) or 0 for r in records if isinstance(r.get(k, 0), int))
            for k in ("ops_total", "generated", "exec_ok", "exec_skipped_required_body")}
    skipped_total = collections.Counter()
    for r in records:
        skipped_total.update(r.get("skipped", {}))
    lines += ["", "## Totals", "",
              f"- Specs: {len(records)} ({len(ok)} loaded; "
              f"{_counts(collections.Counter(r['status'] for r in records if r['status'] != 'ok'))} not loaded)",
              f"- Operations in the manifest: {sum(e['operations'] for e in manifest['specs'])}; in the "
              f"{len(ok)} loaded specs: {sum(r['ops_total'] for r in ok)}, of which generated {sums['generated']} "
              f"and skipped {sum(skipped_total.values())} ({_counts(skipped_total)})",
              f"- Execution check on generated operations: {sums['exec_ok']} ok, "
              f"{sums['exec_skipped_required_body']} skipped (required body), "
              f"{sum(len(r.get('exec_failures', [])) for r in records)} failed, "
              f"{sum(len(r.get('inert_failures', [])) for r in records)} not run (failed the inert check)",
              "", "## Gates", ""]
    lines += [f"- {name}: **{'PASS' if n == 0 else 'FAIL'}** ({n})" for name, n in gates(records)]
    lines += ["", "## Findings", ""]
    findings = []
    for r in records:
        if r["status"] != "ok":
            findings.append((r["id"], "(whole spec)", r["status"], r.get("load_error", "")))
        for code, n in r.get("skipped", {}).items():
            if code.startswith("generator_error"):
                findings.append((r["id"], f"{n} operation(s)", code, ""))
        for f in r.get("compile_failures", []):
            findings.append((r["id"], f["op"], "compile_failure", f["error"]))
        for f in r.get("inert_failures", []):
            findings.append((r["id"], f["op"], "inert_check_failure", f["detail"]))
        for f in r.get("exec_failures", []):
            findings.append((r["id"], f["op"], f"exec_check:{f['error']}", f["detail"]))
    lines += [f"{i}. `{_md(spec_id)}` — `{_md(op)}` — **{_md(cls)}**" + (f" — {_md(detail)}" if detail else "")
              for i, (spec_id, op, cls, detail) in enumerate(findings, 1)] or ["None."]
    (CORPUS / "RESULTS.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str]) -> int:
    manifest = json.loads(MANIFEST.read_text())
    if len(argv) == 2 and argv[0] == "--one":
        entry = next(e for e in manifest["specs"] if e["id"] == argv[1])
        print("RECORD:" + json.dumps(run_one(entry)), flush=True)
        return 0
    records = run_all(manifest)
    meta = {"git": _git(), "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
            "versions": _versions()}
    write_results(manifest, records, meta)
    for name, n in gates(records):
        print(f"{name}: {'PASS' if n == 0 else 'FAIL'} ({n})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
