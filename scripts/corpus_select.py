"""One-time corpus selection for the generation-fidelity harness (piece 4f).

Source: the APIs.guru directory (https://api.apis.guru/v2/list.json). It is stale (newest spec
update 2023-04-21), which is acceptable here: the harness checks generation fidelity, not today's
APIs. Only entries whose x-origin is NATIVE OpenAPI 3.x are eligible (not converted from Swagger 2,
Google discovery or Postman). Each spec is the pinned APIs.guru copy (`url`, sha256); the x-origin
URL is recorded as provenance (`origin_url`) and never fetched. Open-Meteo and Petstore v3 are
always included from their own URLs.

Rules: OpenAPI 3.x, 5-100 operations (HTTP-method keys under `paths`), at most one spec per
provider (the id prefix before ':'), ids in sorted order, stop at 30.

Usage (from the repo root): backend/venv/bin/python scripts/corpus_select.py
Writes backend/corpus/manifest.json; spec bytes are cached under backend/corpus/.cache/ (gitignored,
never committed or redistributed).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from prance.util.formats import parse_spec  # noqa: E402

from app.generate import HTTP_METHODS  # noqa: E402

CORPUS = ROOT / "backend" / "corpus"
CACHE = CORPUS / ".cache"
MANIFEST = CORPUS / "manifest.json"
LIST_URL = "https://api.apis.guru/v2/list.json"
TARGET = 30
MIN_OPS, MAX_OPS = 5, 100
FIXED = [
    ("open-meteo.com:forecast", "https://raw.githubusercontent.com/open-meteo/open-meteo/main/openapi/forecast.yml"),
    ("swagger.io:petstore3", "https://petstore3.swagger.io/api/v3/openapi.json"),
]


def count_operations(document: dict) -> int:
    paths = document.get("paths") if isinstance(document, dict) else None
    return sum(1 for item in (paths or {}).values() if isinstance(item, dict)
               for key in item if key.lower() in HTTP_METHODS)


def native_openapi3(preferred: dict) -> bool:
    """True when the entry was authored as OpenAPI 3.x (its last x-origin), not converted."""
    origins = (preferred.get("info") or {}).get("x-origin") or []
    last = origins[-1] if origins else {}
    return (str(preferred.get("openapiVer", "")).startswith("3.") and last.get("format") == "openapi"
            and str(last.get("version", "")).startswith("3"))


def candidates(directory: dict) -> list[tuple[str, dict]]:
    out = []
    for api_id in sorted(directory):
        api = directory[api_id]
        preferred = api["versions"][api["preferred"]]
        if native_openapi3(preferred):
            out.append((api_id, preferred))
    return out


def _entry(api_id: str, source: str, url: str, origin_url: str | None, raw: bytes, document: dict,
           fetched: str) -> dict:
    info = document.get("info") or {}
    license_ = info.get("license") if isinstance(info.get("license"), dict) else {}
    return {"id": api_id, "source": source, "url": url, "origin_url": origin_url,
            "sha256": hashlib.sha256(raw).hexdigest(), "openapi": str(document.get("openapi")),
            "operations": count_operations(document), "license": license_.get("name"), "fetched": fetched}


def select(directory: dict, fetch: Callable[[str], bytes], fetched: str, target: int = TARGET) -> list[dict]:
    """Deterministic: same directory + same fetched bytes -> same selection, in sorted-id order."""
    chosen: list[dict] = []
    providers: set[str] = set()
    for api_id, preferred in candidates(directory):
        provider = api_id.split(":")[0]
        if provider in providers:
            continue
        raw = fetch(preferred["swaggerUrl"])
        document = parse_spec(raw.decode("utf-8"))
        if not MIN_OPS <= count_operations(document) <= MAX_OPS:
            continue
        origin = ((preferred.get("info") or {}).get("x-origin") or [{}])[-1].get("url")
        chosen.append(_entry(api_id, "apis.guru", preferred["swaggerUrl"], origin, raw, document, fetched))
        providers.add(provider)
        if len(chosen) == target:
            break
    return chosen


def _cached_fetch(url: str) -> bytes:
    import requests

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    raw = resp.content
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / hashlib.sha256(raw).hexdigest()).write_bytes(raw)
    return raw


def main() -> int:
    fetched = datetime.date.today().isoformat()
    directory = json.loads(_cached_fetch(LIST_URL))
    entries = [_entry(api_id, "fixed", url, None, raw, parse_spec(raw.decode("utf-8")), fetched)
               for api_id, url in FIXED for raw in [_cached_fetch(url)]]
    entries += select(directory, _cached_fetch, fetched)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({"source": LIST_URL, "rules": {
        "native_openapi3_only": True, "operations": [MIN_OPS, MAX_OPS], "one_per_provider": True,
        "order": "sorted id", "target": TARGET}, "specs": entries}, indent=2) + "\n")
    print(f"wrote {MANIFEST.relative_to(ROOT)}: {len(entries)} specs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
