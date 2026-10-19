"""Probe which OpenAI model ids accept the corrector's request body.

P1 sends the exact _openai_chat_payload body (no temperature) to every id in
worker/test/fixtures/openai-model-ids.json. P2 re-sends it with temperature=0.0, only to the ids
that returned 200 in P1. Results go to worker/test/fixtures/openai-probe.json.

The key is read once via getpass and lives only in a local variable and the Authorization header.
It is never printed, logged or written, and the output is scrubbed and checked for it before
anything is written.

Usage (from the repo root): backend/venv/bin/python scripts/probe_openai_models.py
"""
from __future__ import annotations

import getpass
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.correct import _openai_chat_payload  # noqa: E402

URL = "https://api.openai.com/v1/chat/completions"
IDS_PATH = ROOT / "worker" / "test" / "fixtures" / "openai-model-ids.json"
OUT_PATH = ROOT / "worker" / "test" / "fixtures" / "openai-probe.json"
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
PAUSE = 0.4
TIMEOUT = 120
MAX_429_RETRIES = 3
_KEYLIKE = re.compile(r"sk-[A-Za-z0-9_\-\*]{4,}")


class AuthAbort(Exception):
    """The very first response was 401/403: the key is wrong, so probing further is pointless."""


def body(model: str, temperature: float | None = None) -> dict:
    payload = _openai_chat_payload(model, "Reply with JSON.", 'Return {"ok": true}.', SCHEMA,
                                   temperature=temperature)
    payload["max_completion_tokens"] = 64
    return payload


def classify(resp) -> dict:
    """status, error.code, error.type, error.message[:200], and finish_reason on 200."""
    rec = {"status": resp.status_code, "code": None, "type": None, "message": None,
           "finish_reason": None}
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code == 200:
        try:
            rec["finish_reason"] = data["choices"][0]["finish_reason"]
        except (KeyError, IndexError, TypeError):
            pass
        return rec
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        rec["code"], rec["type"] = err.get("code"), err.get("type")
        rec["message"] = str(err.get("message") or "")[:200]
    else:
        rec["message"] = resp.text[:200]
    return rec


def call(key: str, payload: dict, *, post=requests.post, sleep=time.sleep) -> dict:
    """One probe call, retrying 429s up to MAX_429_RETRIES times with exponential backoff."""
    for attempt in range(MAX_429_RETRIES + 1):
        resp = post(URL, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
        if resp.status_code != 429 or attempt == MAX_429_RETRIES:
            return classify(resp)
        sleep(2 ** (attempt + 1))


def _safe_call(key: str, payload: dict, post, sleep) -> dict:
    try:
        return call(key, payload, post=post, sleep=sleep)
    except Exception as exc:  # record and move on; one bad id must not end the run
        return {"status": None, "exception": f"{type(exc).__name__}: {exc}"[:200]}


def probe(key: str, ids: list[str], *, post=requests.post, sleep=time.sleep) -> dict:
    results: dict[str, dict] = {}
    seen_response = False
    for i, mid in enumerate(ids, 1):
        rec = _safe_call(key, body(mid), post, sleep)
        if not seen_response and rec["status"] is not None:
            seen_response = True
            if rec["status"] in (401, 403):
                raise AuthAbort(f"first response was HTTP {rec['status']}")
        results[mid] = {"p1": rec}
        print(f"P1 [{i}/{len(ids)}] {mid}: {rec['status']}", flush=True)
        sleep(PAUSE)
    passed = [mid for mid, r in results.items() if r["p1"]["status"] == 200]
    for i, mid in enumerate(passed, 1):
        rec = _safe_call(key, body(mid, temperature=0.0), post, sleep)
        results[mid]["p2"] = rec
        print(f"P2 [{i}/{len(passed)}] {mid}: {rec['status']}", flush=True)
        sleep(PAUSE)
    return results


def scrub_and_dump(results: dict, key: str) -> str:
    """Serialize, mask anything key-shaped, and refuse if the full key survives anyway."""
    text = _KEYLIKE.sub("sk-***", json.dumps(results, indent=2, sort_keys=True))
    if not key or key in text:
        raise RuntimeError("API key found in probe output after scrubbing; nothing written")
    return text + "\n"


def _failure_code(rec: dict) -> str:
    if rec.get("exception"):
        return rec["exception"].split(":")[0]
    return str(rec.get("code") or f"http_{rec['status']}")


def summary(results: dict) -> str:
    p1 = Counter(r["p1"]["status"] if r["p1"]["status"] in (200, 400, 404) else "other"
                 for r in results.values())
    p2_fail = Counter(_failure_code(r["p2"])
                      for r in results.values() if "p2" in r and r["p2"]["status"] != 200)
    lines = [f"P1: 200={p1[200]} 400={p1[400]} 404={p1[404]} other={p1['other']}",
             f"P2 (temperature=0.0) failures: {sum(p2_fail.values())}"]
    lines += [f"  {code}: {n}" for code, n in p2_fail.most_common()]
    return "\n".join(lines)


def main() -> int:
    ids = json.loads(IDS_PATH.read_text())
    key = getpass.getpass("OpenAI API key (input hidden): ").strip()
    if not key:
        print("no key entered; nothing written", file=sys.stderr)
        return 1
    try:
        results = probe(key, ids)
    except AuthAbort as exc:
        print(f"aborted, nothing written: {exc}", file=sys.stderr)
        return 1
    OUT_PATH.write_text(scrub_and_dump(results, key))
    print(f"wrote {OUT_PATH.relative_to(ROOT)}")
    print(summary(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
