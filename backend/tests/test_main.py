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


@pytest.mark.live
def test_parse_spec_against_real_petstore_spec() -> None:
    combined = "".join(parse_spec_stream("https://petstore3.swagger.io/api/v3/openapi.json"))
    assert "event: result" in combined
    assert '"title": "Swagger Petstore' in combined
    assert '"endpoint_count": 19' in combined
