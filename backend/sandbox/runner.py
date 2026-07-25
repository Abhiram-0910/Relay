"""In-container entrypoint: run one generated client method and report a structured result.

Reads a call-spec JSON (mounted read-only), dynamically imports the generated client package
(also mounted read-only), invokes the requested method, and prints exactly one result line:

    RELAY_RESULT:{"status": ..., "detail": ..., "http_status": ...}

This process has NO network policy of its own — egress restriction and SSRF checks all happen
on the host before this container is ever started. It only distinguishes *what kind* of outcome
the call produced, so the host (and later Task 9's self-correction) knows what to fix:
  - verified_pass                  HTTP call succeeded AND response validated against the model
  - verified_live_validation_failed  HTTP call succeeded, but Pydantic rejected the response
  - call_failed                    the HTTP call itself errored (network/timeout/non-2xx/non-JSON/crash)
"""
from __future__ import annotations

import importlib
import json
import sys
import traceback

RESULT_PREFIX = "RELAY_RESULT:"
CALL_SPEC_PATH = "/sandbox/call_spec.json"
CLIENT_PKG_ROOT = "/sandbox"


def _emit(status: str, detail: str, http_status: int | None = None) -> None:
    print(RESULT_PREFIX + json.dumps({"status": status, "detail": detail, "http_status": http_status}))


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

    try:
        method(**spec.get("kwargs", {}))
    except ValidationError as exc:
        # Reached the live API; the response shape disagreed with the generated model.
        _emit("verified_live_validation_failed", str(exc))
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        _emit("call_failed", f"HTTP {code}: {exc}", http_status=code)
    except requests.RequestException as exc:
        # Connection error, timeout, or non-JSON body (JSONDecodeError subclasses this).
        _emit("call_failed", f"{type(exc).__name__}: {exc}")
    else:
        _emit("verified_pass", "HTTP call succeeded and response validated against the generated model")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # last-resort: import/attr errors, malformed call-spec, etc.
        _emit("call_failed", f"runner crash: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
