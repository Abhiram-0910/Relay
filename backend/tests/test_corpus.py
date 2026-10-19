"""Piece 4f: the corpus manifest, the selection rules and the execution checker. Hermetic: no
network, no Docker (the checker runs in-process here; the harness runs it in the sandbox image)."""
from __future__ import annotations

import importlib.util
import json
import re
import typing
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

import pytest

from app.generate import generate_endpoint_client
from tests.generated_client import BASE_URL, load_client

ROOT = Path(__file__).resolve().parents[2]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus_select = _script("corpus_select")
corpus_check = _script("corpus_check")
run_corpus = _script("run_corpus")

MANIFEST = json.loads((ROOT / "backend/corpus/manifest.json").read_text())
_SECRET_KEYS = {"key", "apikey", "api_key", "token", "access_token", "secret", "password", "sig", "signature"}


# --- the committed manifest ----------------------------------------------------------------------

def test_manifest_ids_unique_and_hashes_well_formed() -> None:
    ids = [e["id"] for e in MANIFEST["specs"]]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[0-9a-f]{64}", e["sha256"]) for e in MANIFEST["specs"])
    assert {e["id"] for e in MANIFEST["specs"] if e["source"] == "fixed"} == {
        "open-meteo.com:forecast", "swagger.io:petstore3"}


@pytest.mark.parametrize("entry", MANIFEST["specs"], ids=[e["id"] for e in MANIFEST["specs"]])
def test_manifest_urls_carry_no_credentials(entry) -> None:
    url = urlsplit(entry["url"])
    assert url.scheme in ("http", "https") and url.hostname
    # origin_url is APIs.guru's x-origin as recorded (provenance only, never fetched); some are bare
    # filenames, so only the credential checks apply to it.
    for url in filter(None, (entry["url"], entry.get("origin_url"))):
        parts = urlsplit(url)
        assert parts.username is None and parts.password is None
        assert not {k.lower() for k in parse_qs(parts.query)} & _SECRET_KEYS


def test_manifest_follows_the_selection_rules() -> None:
    picked = [e for e in MANIFEST["specs"] if e["source"] == "apis.guru"]
    assert all(corpus_select.MIN_OPS <= e["operations"] <= corpus_select.MAX_OPS for e in picked)
    assert all(e["openapi"].startswith("3.") for e in MANIFEST["specs"])
    providers = [e["id"].split(":")[0] for e in picked]
    assert len(providers) == len(set(providers)) and len(picked) <= corpus_select.TARGET
    assert [e["id"] for e in picked] == sorted(e["id"] for e in picked)
    assert all(e["url"].startswith("https://api.apis.guru/") for e in picked)


# --- selection, against a stubbed directory ------------------------------------------------------

def _api(origin_format="openapi", origin_version="3.0", openapi_ver="3.0.0", url="u"):
    return {"preferred": "1", "versions": {"1": {
        "openapiVer": openapi_ver, "swaggerUrl": url,
        "info": {"x-origin": [{"format": origin_format, "version": origin_version, "url": f"https://origin/{url}"}]}}}}


def _doc(n_ops: int) -> bytes:
    return json.dumps({"openapi": "3.0.0", "info": {"title": "t", "license": {"name": "MIT"}},
                       "paths": {f"/p{i}": {"get": {}} for i in range(n_ops)}}).encode()


DIRECTORY = {
    "zeta.com": _api(url="zeta"),
    "alpha.com:b": _api(url="alpha-b"),
    "alpha.com:a": _api(url="alpha-a"),            # same provider as alpha.com:b; sorts first
    "beta.com": _api(origin_format="swagger", origin_version="2.0", url="beta"),  # converted, excluded
    "gamma.com": _api(openapi_ver="2.0", url="gamma"),                           # Swagger 2.0, excluded
    "delta.com": _api(url="delta"),                # too few operations
    "eps.com": _api(url="eps"),                    # too many operations
    "mu.com": _api(origin_format="openapi", origin_version="3.1", openapi_ver="3.1.0", url="mu"),
}
DOCS = {"zeta": _doc(5), "alpha-a": _doc(7), "alpha-b": _doc(9), "beta": _doc(10), "gamma": _doc(10),
        "delta": _doc(4), "eps": _doc(101), "mu": _doc(100)}


def test_selection_is_deterministic_and_applies_every_rule() -> None:
    fetched = []

    def fetch(url):
        fetched.append(url)
        return DOCS[url]

    first = corpus_select.select(DIRECTORY, fetch, "2026-10-19")
    second = corpus_select.select(dict(reversed(list(DIRECTORY.items()))), lambda u: DOCS[u], "2026-10-19")
    assert first == second
    assert [(e["id"], e["operations"]) for e in first] == [("alpha.com:a", 7), ("mu.com", 100), ("zeta.com", 5)]
    assert first[0]["origin_url"] == "https://origin/alpha-a" and first[0]["license"] == "MIT"
    assert "beta" not in fetched and "gamma" not in fetched and "alpha-b" not in fetched
    assert [e["id"] for e in corpus_select.select(DIRECTORY, lambda u: DOCS[u], "d", target=1)] == ["alpha.com:a"]


# --- the execution checker -----------------------------------------------------------------------

_OK = {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object"}}}}}
SPEC = {"openapi": "3.0.3", "info": {"title": "Check", "version": "1"}, "paths": {"/things/{id}": {"get": {
    "operationId": "getThing", "responses": _OK, "parameters": [
        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
        {"name": "q", "in": "query", "required": True, "schema": {"type": "string", "enum": ["a", "b"]}},
        {"name": "X-Req", "in": "header", "required": True, "schema": {"type": "boolean"}},
        {"name": "opt", "in": "query", "schema": {"type": "string"}}]}}}}


def _op(item: dict) -> dict:
    return {"op_id": "GET /things/{id}", "method_name": item["method_name"], "params": item["params"]}


def _check(item: dict, tmp_path: Path) -> dict:
    return corpus_check.check_operation(load_client(item, tmp_path), _op(item), BASE_URL)


@pytest.fixture
def item(tmp_path: Path) -> dict:
    return generate_endpoint_client(SPEC, "/things/{id}", "get", tmp_path / "gen")


def test_checker_passes_a_correct_client(item, tmp_path: Path) -> None:
    assert _check(item, tmp_path) == {"ok": True}


def test_checker_catches_a_literal_path_template(item, tmp_path: Path) -> None:
    broken = dict(item, client_code=item["client_code"].replace("{_relay_path_value(id)}", "{{id}}"))
    assert broken["client_code"] != item["client_code"]
    result = _check(broken, tmp_path)
    assert (result["ok"], result["error"]) == (False, "UnfilledPathTemplate")


def test_checker_catches_a_missing_required_header(item, tmp_path: Path) -> None:
    lines = [line for line in item["client_code"].splitlines() if "headers=_relay_header_dict" not in line]
    broken = dict(item, client_code="\n".join(lines) + "\n")
    result = _check(broken, tmp_path)
    assert (result["ok"], result["error"]) == (False, "MissingRequiredHeader")


def test_checker_catches_a_signature_that_disagrees_with_the_table(item, tmp_path: Path) -> None:
    table = [dict(p, local_name="renamed") if p["wire_name"] == "q" else p for p in item["params"]]
    result = corpus_check.check_operation(load_client(item, tmp_path), dict(_op(item), params=table), BASE_URL)
    assert (result["ok"], result["error"]) == (False, "SignatureMismatch")


def test_checker_reports_an_exception_from_the_call(item, tmp_path: Path) -> None:
    broken = dict(item, client_code=item["client_code"].replace("response = requests.get(", "response = 1 / 0 or requests.get(", 1))
    result = _check(broken, tmp_path)
    assert (result["ok"], result["error"]) == (False, "ZeroDivisionError")


def test_checker_skips_a_required_body(tmp_path: Path) -> None:
    spec = {"openapi": "3.0.3", "info": {"title": "B", "version": "1"}, "paths": {"/x": {"post": {
        "operationId": "postX", "responses": _OK,
        "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}}}}}
    item = generate_endpoint_client(spec, "/x", "post", tmp_path / "gen")
    assert corpus_check.check_operation(load_client(item, tmp_path), _op(item), BASE_URL) == {
        "ok": None, "skipped": "required_body"}


@pytest.mark.parametrize("hint,value", [
    (int, 1), (float, 1.0), (bool, True), (str, "x"), (Any, "x"), (Literal["b", "a"], "b"),
    (list[int], [1]), (list[Literal[3]], [3]), (int | None, 1), (typing.Optional[str], "x"), (list, ["x"]),
])
def test_dummy_values(hint, value) -> None:
    assert corpus_check.dummy(hint) == value


@pytest.mark.parametrize("reason,code", [
    ("GET /x has no JSON response schema to generate a model from", "no_json_response"),
    ("GET /x skipped: required_cookie_parameter", "required_cookie_parameter"),
    ("generator_error: TypeError", "generator_error:TypeError"),
])
def test_skip_reason_codes(reason, code) -> None:
    assert run_corpus.skip_code(reason) == code
