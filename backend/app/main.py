import json
from typing import Iterator

import requests
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from openapi_spec_validator import validate as validate_spec
from prance import ResolvingParser
from pydantic import BaseModel, HttpUrl

app = FastAPI()

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


class ParseSpecRequest(BaseModel):
    url: HttpUrl


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def parse_spec_stream(url: str) -> Iterator[str]:
    yield _sse("progress", {"stage": "fetching"})
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        yield _sse("error", {"message": f"failed to fetch spec: {exc}"})
        return

    yield _sse("progress", {"stage": "parsing"})
    try:
        spec = ResolvingParser(spec_string=response.text).specification
    except Exception as exc:
        yield _sse("error", {"message": f"failed to parse spec: {exc}"})
        return

    yield _sse("progress", {"stage": "validating"})
    try:
        validate_spec(spec)
    except Exception as exc:
        yield _sse("error", {"message": f"spec failed validation: {exc}"})
        return

    endpoints = [
        {"method": method.upper(), "path": path}
        for path, methods in spec.get("paths", {}).items()
        for method in methods
        if method.lower() in HTTP_METHODS
    ]

    yield _sse(
        "result",
        {
            "title": spec.get("info", {}).get("title"),
            "version": spec.get("info", {}).get("version"),
            "endpoint_count": len(endpoints),
            "endpoints": endpoints,
        },
    )


@app.post("/api/parse-spec")
def parse_spec(payload: ParseSpecRequest) -> StreamingResponse:
    return StreamingResponse(parse_spec_stream(str(payload.url)), media_type="text/event-stream")
