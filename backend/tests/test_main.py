import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app, parse_spec_stream

MINIMAL_SPEC = """
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /ping:
    get:
      responses:
        "200":
          description: ok
"""


class _FakeResponse:
    text = MINIMAL_SPEC

    def raise_for_status(self) -> None:
        pass


def test_parse_spec_streams_result_event() -> None:
    with patch("app.main.requests.get", return_value=_FakeResponse()):
        client = TestClient(app)
        resp = client.post("/api/parse-spec", json={"url": "https://example.com/spec.yaml"})

    assert resp.status_code == 200
    assert "event: progress" in resp.text
    assert "event: result" in resp.text
    assert '"endpoint_count": 1' in resp.text
    assert '"title": "Test API"' in resp.text


def test_parse_spec_streams_error_on_fetch_failure() -> None:
    import requests

    with patch("app.main.requests.get", side_effect=requests.RequestException("boom")):
        client = TestClient(app)
        resp = client.post("/api/parse-spec", json={"url": "https://example.com/spec.yaml"})

    assert resp.status_code == 200
    assert "event: error" in resp.text
    assert "failed to fetch spec" in resp.text


def test_parse_spec_rejects_invalid_url() -> None:
    client = TestClient(app)
    resp = client.post("/api/parse-spec", json={"url": "not-a-url"})
    assert resp.status_code == 422


def _sse_event_data(chunk: str) -> tuple[str, dict]:
    header, _, data_line = chunk.partition("\n")
    event = header.removeprefix("event: ")
    payload = data_line.removeprefix("data: ")
    return event, json.loads(payload)


@pytest.mark.live
def test_parse_spec_against_real_petstore_spec_generates_every_endpoint() -> None:
    events = list(parse_spec_stream("https://petstore3.swagger.io/api/v3/openapi.json"))
    parsed = [_sse_event_data(e) for e in events]

    result_data = next(data for event, data in parsed if event == "result")
    assert result_data["title"] == "Swagger Petstore - OpenAPI 3.0"
    assert result_data["endpoint_count"] == 19
    assert len(result_data["generated"]) + len(result_data["skipped"]) == 19
    assert len(result_data["generated"]) > 0
    assert len(result_data["skipped"]) > 0  # DELETE/logout endpoints have no JSON response, expected

    for entry in result_data["generated"]:
        compile(entry["models_code"], "models.py", "exec")
        compile(entry["client_code"], "client.py", "exec")

    generating_events = [data for event, data in parsed if event == "progress" and data.get("stage") == "generating"]
    assert len(generating_events) == 19
    assert generating_events[-1]["current"] == generating_events[-1]["total"] == 19
