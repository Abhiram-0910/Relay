"""In-container entrypoint: run one generated client method and report a structured result.

Reads a call-spec JSON (mounted read-only), dynamically imports the generated client package
(also mounted read-only), invokes the requested method, and prints exactly one result line:

    RELAY_RESULT:{"status": ..., "detail": ..., "http_status": ..., "failure_kind": ..., "exception": ...}

This process has NO network policy of its own — egress restriction and SSRF checks all happen
on the host before this container is ever started. It only distinguishes *what kind* of outcome
the call produced, so the host (and later Task 9's self-correction) knows what to fix:
  - verified_pass                  HTTP call succeeded AND response validated against the model
  - verified_live_validation_failed  HTTP call succeeded, but Pydantic rejected the response
  - call_failed                    the HTTP call itself errored; `failure_kind` says how (one of
                                   FAILURE_KINDS), and the host (app.sandbox) maps it to a final status

A call_failed report carries only structured data: the kind, the HTTP status code and the exception
CLASS name. Never a response body, header, URL or exception message: those can carry target data.
"""
from __future__ import annotations

import importlib
import json
import sys

RESULT_PREFIX = "RELAY_RESULT:"
CALL_SPEC_PATH = "/sandbox/call_spec.json"
CLIENT_PKG_ROOT = "/sandbox"


FAILURE_KINDS = ("http_status", "connection", "timeout", "non_json", "decode", "client_exception")


def _emit(status: str, detail: str, http_status: int | None = None, failure_kind: str | None = None,
          exception: str | None = None) -> None:
    print(RESULT_PREFIX + json.dumps({"status": status, "detail": detail, "http_status": http_status,
                                      "failure_kind": failure_kind, "exception": exception}))


def _call_failed(kind: str, exc: BaseException, http_status: int | None = None) -> None:
    """The detail is built from the structured fields only, never from str(exc)."""
    name = type(exc).__name__
    _emit("call_failed", f"{kind}: {name}" + (f" (HTTP {http_status})" if http_status else ""),
          http_status, kind, name)


def main() -> None:
    # Imported here (not at module top) so a broken install surfaces as call_failed, not a crash
    # before we can emit anything.
    import requests
    from pydantic import ValidationError

    with open(CALL_SPEC_PATH) as spec_file:
        spec = json.load(spec_file)

    sys.path.insert(0, CLIENT_PKG_ROOT)
    module = importlib.import_module(f"client_pkg.{spec['module']}")
    client = getattr(module, spec["class_name"])(spec["base_url"])
    method = getattr(client, spec["method"])

    # requests raises the same JSONDecodeError for "not JSON at all" and "JSON, but malformed", and
    # doesn't attach the response; note whether the response CLAIMED to be JSON. Only that bool is read.
    original_json = requests.models.Response.json

    def json_noting_content_type(self, **kwargs):
        try:
            return original_json(self, **kwargs)
        except requests.JSONDecodeError as exc:
            exc.relay_claimed_json = "json" in self.headers.get("Content-Type", "").lower()
            raise

    requests.models.Response.json = json_noting_content_type
    try:
        method(**spec.get("kwargs", {}))
    except ValidationError as exc:
        # Reached the live API; the response shape disagreed with the generated model.
        _emit("verified_live_validation_failed", str(exc))
    except requests.HTTPError as exc:
        _call_failed("http_status", exc, exc.response.status_code if exc.response is not None else None)
    except requests.Timeout as exc:  # before ConnectionError: ConnectTimeout is both
        _call_failed("timeout", exc)
    except requests.ConnectionError as exc:
        _call_failed("connection", exc)
    except requests.JSONDecodeError as exc:
        _call_failed("decode" if getattr(exc, "relay_claimed_json", False) else "non_json", exc)
    except Exception as exc:  # anything else the generated client raised: a patch may fix it
        _call_failed("client_exception", exc)
    else:
        _emit("verified_pass", "HTTP call succeeded and response validated against the generated model")
    finally:
        requests.models.Response.json = original_json


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # last-resort: the generated package fails to import, a missing attr, ...
        _call_failed("client_exception", exc)
