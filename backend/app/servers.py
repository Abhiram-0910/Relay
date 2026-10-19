"""Base-URL resolution from an OpenAPI 3.x document's `servers`, pure and deterministic.

Precedence per OpenAPI: operation `servers` > path item `servers` > top-level `servers`, first entry
of the winning list; all absent or empty means "/" (the document's own origin). Server variables are
substituted with their `default` by plain string replacement of declared names only. Relative URLs
resolve against the FINAL spec URL (after redirects). The result is validated before use, and a
failure is reported by a stable reason code — never by echoing spec text.

This only picks the URL. The SSRF pre-flight in sandbox.run_in_sandbox still checks the resolved
host before anything is contacted.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

SERVER_URL_INVALID = "server_url_invalid"
SERVER_VARIABLE_INVALID = "server_variable_invalid"
SERVER_URL_MISSING_HOST = "server_url_missing_host"

MAX_URL_LENGTH = 2048
_VARIABLE_RE = re.compile(r"\{([^{}]*)\}")
_FORBIDDEN_CHAR = re.compile(r"[\s\x00-\x1f\x7f-\x9f]")
_DNS_HOSTNAME = re.compile(r"[A-Za-z0-9.-]+", re.ASCII)  # bracketed IPv6 literals are checked separately


def _winning_servers(spec: dict, path: str, method: str) -> list:
    path_item = (spec.get("paths") or {}).get(path) or {}
    operation = path_item.get(method.lower()) or {}
    for level in (operation, path_item, spec):
        servers = level.get("servers") if isinstance(level, dict) else None
        if isinstance(servers, list) and servers:
            return servers
    return []


def _substitute(url: str, variables) -> str | None:
    """Replace each {name} with its declared variable's default; None if any is unusable."""
    variables = variables if isinstance(variables, dict) else {}
    out, pos = [], 0
    for m in _VARIABLE_RE.finditer(url):
        var = variables.get(m.group(1))
        if not isinstance(var, dict) or not isinstance(var.get("default"), str):
            return None
        enum = var.get("enum")
        if enum is not None and (not isinstance(enum, list) or var["default"] not in enum):
            return None
        out.append(url[pos:m.start()] + var["default"])
        pos = m.end()
    out.append(url[pos:])
    result = "".join(out)
    return None if "{" in result or "}" in result else result


def resolve_base_url(spec: dict, path: str, method: str, spec_url: str) -> tuple[str | None, str | None]:
    """(base_url, None) on success, else (None, reason_code). `spec_url` is the final URL the spec
    was fetched from, after redirects."""
    servers = _winning_servers(spec, path, method)
    entry = servers[0] if servers else {"url": "/"}
    raw = entry.get("url") if isinstance(entry, dict) else None
    if not isinstance(raw, str) or not raw:
        return None, SERVER_URL_INVALID

    substituted = _substitute(raw, entry.get("variables"))
    if substituted is None:
        return None, SERVER_VARIABLE_INVALID

    # Checked on the raw text: urlsplit/urljoin silently DROP tab/CR/LF, which would hide them.
    if len(substituted) > MAX_URL_LENGTH or _FORBIDDEN_CHAR.search(substituted) or "\\" in substituted:
        return None, SERVER_URL_INVALID
    try:
        # Only a relative URL is joined: urljoin folds "https://" (no host) back into the base URL.
        url = substituted if urlsplit(substituted).scheme else urljoin(spec_url, substituted)
        parts = urlsplit(url)
        hostname = parts.hostname
        parts.port  # noqa: B018 (raises ValueError on a malformed port)
    except ValueError:
        return None, SERVER_URL_INVALID
    if len(url) > MAX_URL_LENGTH or parts.scheme not in ("http", "https"):
        return None, SERVER_URL_INVALID
    if "@" in parts.netloc:  # userinfo, e.g. user:pass@host
        return None, SERVER_URL_INVALID
    if not hostname:
        return None, SERVER_URL_MISSING_HOST
    if not (parts.netloc.startswith("[") or _DNS_HOSTNAME.fullmatch(hostname)):
        return None, SERVER_URL_INVALID
    if parts.query or parts.fragment:
        return None, SERVER_URL_INVALID
    return (url[:-1] if url.endswith("/") else url), None
