from __future__ import annotations

import sys
import asyncio
import unittest
from pathlib import Path
from types import ModuleType


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

python_multipart_stub = ModuleType("python_multipart")
python_multipart_stub.__version__ = "0.0.13"
python_multipart_stub.__all__ = []
python_multipart_stub.__author__ = ""
python_multipart_stub.__copyright__ = ""
python_multipart_stub.__license__ = ""
python_multipart_multipart_stub = ModuleType("python_multipart.multipart")
python_multipart_multipart_stub.parse_options_header = lambda value: (value, {})
sys.modules.setdefault("python_multipart", python_multipart_stub)
sys.modules.setdefault("python_multipart.multipart", python_multipart_multipart_stub)

from fastapi import Request
from fastapi.responses import JSONResponse

from oracle_app.api import attach_correlation_id
from oracle_app.memory.correlation import get_correlation_id


def build_request(correlation_id: str | None = None) -> Request:
    headers = []
    if correlation_id is not None:
        headers.append((b"x-oracle-correlation-id", correlation_id.encode("utf-8")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/memory-correlation",
        "headers": headers,
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


async def call_middleware(correlation_id: str | None = None) -> tuple[JSONResponse, dict[str, object]]:
    observed: dict[str, object] = {}

    async def call_next(request: Request) -> JSONResponse:
        observed["request_state_correlation_id"] = request.state.correlation_id
        observed["context_correlation_id"] = get_correlation_id()
        return JSONResponse({"ok": True}, status_code=202)

    response = await attach_correlation_id(build_request(correlation_id), call_next)
    return response, observed


class OracleMemoryHttpCorrelationTests(unittest.TestCase):
    def test_missing_header_generates_response_header_without_body_shape_change(self) -> None:
        response, observed = asyncio.run(call_middleware())

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.body, b'{"ok":true}')
        correlation_id = response.headers["X-Oracle-Correlation-Id"]
        self.assertTrue(correlation_id.startswith("corr_"))
        self.assertEqual(observed["request_state_correlation_id"], correlation_id)
        self.assertEqual(observed["context_correlation_id"], correlation_id)

    def test_valid_inbound_header_is_echoed(self) -> None:
        response, observed = asyncio.run(call_middleware("external-123"))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["X-Oracle-Correlation-Id"], "external-123")
        self.assertEqual(observed["request_state_correlation_id"], "external-123")
        self.assertEqual(observed["context_correlation_id"], "external-123")

    def test_invalid_inbound_header_generates_id_without_4xx(self) -> None:
        response, _observed = asyncio.run(call_middleware("bad value"))

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.headers["X-Oracle-Correlation-Id"].startswith("corr_"))
        self.assertNotEqual(response.headers["X-Oracle-Correlation-Id"], "bad value")

    def test_context_is_cleared_after_request(self) -> None:
        response, _observed = asyncio.run(call_middleware("request-1"))

        self.assertEqual(response.status_code, 202)
        self.assertIsNone(get_correlation_id())

    def test_downstream_exceptions_are_not_transformed(self) -> None:
        async def run() -> None:
            async def call_next(_request: Request) -> JSONResponse:
                raise RuntimeError("intentional test error")

            await attach_correlation_id(build_request("request-error"), call_next)

        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(run())

        self.assertEqual(str(raised.exception), "intentional test error")
        self.assertIsNone(get_correlation_id())


if __name__ == "__main__":
    unittest.main()
