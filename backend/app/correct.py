"""Gemini-powered self-correction loop for a generated client that failed sandbox validation.

The deterministic pipeline generates the first-pass client; the LLM is ONLY used here, to patch
code that failed to run against the live API. Free tier only — see CLAUDE.md. The loop is capped:
2 Gemini Flash attempts, then 1 Gemini Pro attempt, then hard fail with the full attempt history.
The cap exists to protect the free-tier quota (Flash ~1,500/day, Pro ~50/day), never to control a
bill — this project never has billing enabled.

Every patched attempt is re-run through the FULL sandbox (SSRF pre-flight + internal network +
pinned socat sidecar) — no exceptions, since the patched code is untrusted.

The corrector reads the sandbox report's structured status to know what to fix:
  - verified_live_validation_failed → the HTTP call worked but the response model rejected the
    real response → patch models.py.
  - call_failed → the request itself failed → patch client.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.sandbox import STATUS_PASS, run_in_sandbox

# Model IDs kept as constants so swapping is one line. gemini-2.5-flash was retired for new API
# keys ("no longer available to new users"), so Flash is pinned to the current stable 3.x line;
# verified live against this project's key. Pro is reachable but free-tier quota is scarce (~50/day)
# and was 429ing at pin time — escalation stays a rare last resort by design.
GEMINI_FLASH = "gemini-3.5-flash"
GEMINI_PRO = "gemini-2.5-pro"
# The capped ladder: two cheap Flash tries, then one scarce Pro try, then give up.
DEFAULT_LADDER = (GEMINI_FLASH, GEMINI_FLASH, GEMINI_PRO)

_MODELS_FILE = "models.py"
_CLIENT_FILE = "client.py"

# A patch is the full corrected contents of each file (unchanged files are echoed back verbatim).
Patch = dict  # {"models_py": str, "client_py": str}
# corrector(model_id, call_spec, report, models_code, client_code) -> Patch
Corrector = Callable[[str, dict, dict, str, str], Patch]

_SYSTEM_INSTRUCTION = """\
You fix a generated Python API client that failed to validate against a live API.
You are given two files (models.py: pydantic v2 response/request models; client.py: a `requests`
based client), the exact call being made, and the structured failure from a real sandbox run.

Rules:
- Change ONLY what is necessary to make the live call succeed and its response validate.
- If the failure status is "verified_live_validation_failed", the HTTP call already reached the
  API; the pydantic model in models.py rejected the real response — fix models.py.
- If the failure status is "call_failed", the request itself failed (bad path/params/body/etc.) —
  fix client.py.
- Keep the public class name, method name, and imports working. models.py stays pydantic v2.
- Return the FULL contents of BOTH files. If a file needs no change, return it byte-for-byte."""


@dataclass
class Attempt:
    """One correction attempt: which model, what it changed, and the sandbox verdict after."""
    model: str
    status_before: str
    changed_files: list[str]
    status_after: str
    detail: str


@dataclass
class CorrectionResult:
    succeeded: bool
    final_report: dict
    attempts: list[Attempt] = field(default_factory=list)


def _apply_patch(pkg_dir: Path, patch: Patch, models_code: str, client_code: str) -> list[str]:
    """Write back only the files the patch actually changed; return their names."""
    changed: list[str] = []
    if patch.get("models_py") is not None and patch["models_py"] != models_code:
        (pkg_dir / _MODELS_FILE).write_text(patch["models_py"])
        changed.append(_MODELS_FILE)
    if patch.get("client_py") is not None and patch["client_py"] != client_code:
        (pkg_dir / _CLIENT_FILE).write_text(patch["client_py"])
        changed.append(_CLIENT_FILE)
    return changed


def gemini_corrector(model_id: str, call_spec: dict, report: dict,
                     models_code: str, client_code: str, *, api_key: str | None = None) -> Patch:
    """Ask Gemini for corrected file contents. Imported lazily so the loop's logic stays testable
    without the SDK or an API key present. `api_key`, when set, is the user's BYOK key and is used
    for this call instead of the shared GEMINI_API_KEY env var; it is never logged or persisted."""
    from google import genai
    from google.genai import types
    from pydantic import BaseModel

    class _PatchSchema(BaseModel):
        models_py: str
        client_py: str

    prompt = (
        f"Call being made: {call_spec.get('method')} on {call_spec.get('endpoint')} "
        f"at base_url {call_spec.get('base_url')} with kwargs {call_spec.get('kwargs')}\n\n"
        f"Sandbox failure status: {report['status']}\n"
        f"Failure detail:\n{report['detail']}\n\n"
        f"=== {_MODELS_FILE} ===\n{models_code}\n\n"
        f"=== {_CLIENT_FILE} ===\n{client_code}\n"
    )

    # BYOK key overrides the shared env key for this call only; else fall back to GEMINI_API_KEY.
    client = genai.Client(api_key=api_key) if api_key else genai.Client()
    response = client.models.generate_content(
        model=model_id,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTION,
            temperature=0.0,  # deterministic-as-possible patches
            response_mime_type="application/json",
            response_schema=_PatchSchema,
        ),
    )
    parsed = response.parsed
    if parsed is not None:
        return {"models_py": parsed.models_py, "client_py": parsed.client_py}
    import json
    return json.loads(response.text)


def self_correct(
    pkg_dir: Path,
    call_spec: dict,
    failing_report: dict,
    *,
    run_sandbox: Callable[..., dict] = run_in_sandbox,
    corrector: Corrector = gemini_corrector,
    ladder: tuple[str, ...] = DEFAULT_LADDER,
    api_key: str | None = None,
) -> CorrectionResult:
    """Iteratively patch the client and re-run it through the full sandbox until it passes or the
    capped ladder is exhausted. `failing_report` is the sandbox report from the pre-correction run.

    `api_key`, when set (BYOK), is passed to the corrector to use in place of the shared
    GEMINI_API_KEY. It's only forwarded when non-None, so custom correctors that don't accept the
    keyword keep working unchanged."""
    assert failing_report["status"] != STATUS_PASS, "self_correct called on an already-passing run"

    report = failing_report
    attempts: list[Attempt] = []

    for model_id in ladder:
        if report["status"] == STATUS_PASS:
            break

        models_code = (pkg_dir / _MODELS_FILE).read_text()
        client_code = (pkg_dir / _CLIENT_FILE).read_text()
        status_before = report["status"]

        try:
            patch = (corrector(model_id, call_spec, report, models_code, client_code, api_key=api_key)
                     if api_key is not None
                     else corrector(model_id, call_spec, report, models_code, client_code))
        except Exception as exc:  # quota/network/parse — stop, don't burn more of the ladder
            attempts.append(Attempt(model_id, status_before, [], "corrector_error", str(exc)[:300]))
            break

        changed = _apply_patch(pkg_dir, patch, models_code, client_code)
        report = run_sandbox(pkg_dir, call_spec)
        attempts.append(Attempt(
            model=model_id,
            status_before=status_before,
            changed_files=changed,
            status_after=report["status"],
            detail=(report.get("detail") or "")[:300],
        ))

    return CorrectionResult(report["status"] == STATUS_PASS, report, attempts)
