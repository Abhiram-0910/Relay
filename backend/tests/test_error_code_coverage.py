"""Every code the backend can emit must have a frontend entry: a plain-language message in
frontend/lib/errors.ts and a tone + label in frontend/lib/status.ts (status.ts has been caught
incomplete twice before). Codes are read from the classifier functions themselves, so a new
`return "some_code"` fails this test until the frontend is updated."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app import sandbox

ROOT = Path(__file__).resolve().parents[2]


def _returned_strings(source_file: Path, function: str) -> set[str]:
    tree = ast.parse(source_file.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
    codes = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            for value in ast.walk(node.value):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    codes.add(value.value)
    return codes


def _ts_record_keys(ts_file: Path, const: str) -> set[str]:
    text = ts_file.read_text()
    body = re.search(rf"const {const}\b[^=]*=\s*{{(.*?)\n}};", text, re.S)
    assert body, f"{const} not found in {ts_file.name}"
    return set(re.findall(r"^\s+([a-z_0-9]+):", body.group(1), re.M))


PIPELINE_CODES = _returned_strings(ROOT / "backend/app/ci_runner.py", "_classify_pipeline_error")
CORRECTOR_CODES = _returned_strings(ROOT / "backend/app/correct.py", "_classify_corrector_exception")
SANDBOX_STATUSES = {v for k, v in vars(sandbox).items() if k.startswith("STATUS_") and isinstance(v, str)}
ERROR_CODES = PIPELINE_CODES | CORRECTOR_CODES

ERRORS_TS = _ts_record_keys(ROOT / "frontend/lib/errors.ts", "ERROR_MESSAGES")
TONES = _ts_record_keys(ROOT / "frontend/lib/status.ts", "TONE_BY_STATUS")
LABELS = _ts_record_keys(ROOT / "frontend/lib/status.ts", "LABELS")


def test_the_code_lists_were_actually_found() -> None:
    assert {"spec_invalid", "unsupported_spec_version", "unsupported_spec_construct",
            "no_operations_generated"} <= PIPELINE_CODES
    assert {"model_unsupported", "corrector_error"} <= CORRECTOR_CODES
    assert {"verified_pass", "call_failed"} <= SANDBOX_STATUSES


@pytest.mark.parametrize("code", sorted(ERROR_CODES))
def test_every_error_code_has_a_message_tone_and_label(code: str) -> None:
    assert code in ERRORS_TS, f"{code} has no errors.ts message"
    assert code in TONES, f"{code} has no status.ts tone"
    assert code in LABELS, f"{code} has no status.ts label"


@pytest.mark.parametrize("status", sorted(SANDBOX_STATUSES | {"generated_only"}))
def test_every_endpoint_status_has_a_tone_and_label(status: str) -> None:
    assert status in TONES and status in LABELS, f"{status} missing from status.ts"
