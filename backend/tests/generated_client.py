"""Test helper: import a generated client package in-process and capture the request its method
would send. Session.send is patched to raise instead of sending, so the captured PreparedRequest is
exactly what requests would put on the wire, with no network."""
from __future__ import annotations

import importlib
import sys
import uuid
from pathlib import Path
from unittest import mock

import requests

BASE_URL = "https://api.example.test"


class _Sent(Exception):
    def __init__(self, prepared: requests.PreparedRequest) -> None:
        self.prepared = prepared


def load_client(item: dict, root: Path):
    """Write the generated models.py/client.py as a uniquely named package under root, import it,
    and return the client instance."""
    name = f"relay_generated_{uuid.uuid4().hex}"
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "models.py").write_text(item["models_code"])
    (pkg / "client.py").write_text(item["client_code"])
    sys.path.insert(0, str(root))
    try:
        module = importlib.import_module(f"{name}.client")
    finally:
        sys.path.remove(str(root))
    return getattr(module, item["class_name"])(BASE_URL)


def capture(item: dict, root: Path, *args, **kwargs) -> requests.PreparedRequest:
    client = load_client(item, root)

    def send(self, request, **_kw):
        raise _Sent(request)

    with mock.patch.object(requests.sessions.Session, "send", send):
        try:
            getattr(client, item["method_name"])(*args, **kwargs)
        except _Sent as sent:
            return sent.prepared
    raise AssertionError("the generated method returned without sending a request")
