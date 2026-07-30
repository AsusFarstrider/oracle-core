from __future__ import annotations

import unittest

from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from oracle_app.configuration import (
    GenerationIntegrityError,
    SatelliteProjectionAuthenticationError,
)
from oracle_app.satellite_projection_routes import (
    configure_satellite_projection_routes,
    register_satellite_projection_routes,
    satellite_projection_enrollment,
    satellite_projection_pull,
)


class _Envelope:
    def canonical_bytes(self) -> bytes:
        return b'{"format":"oracle-satellite-projection-pull-v1"}'


class _Resolver:
    def __init__(self, result: object | BaseException) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def resolve_pull(self, satellite_id: str, credential: str) -> _Envelope:
        self.calls.append((satellite_id, credential))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result  # type: ignore[return-value]

    def resolve_enrollment_pull(self, satellite_id: str, credential: str) -> _Envelope:
        self.calls.append((satellite_id, credential))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result  # type: ignore[return-value]


def _request(
    app: FastAPI,
    authorization: str | None = None,
    *,
    enrollment: bool = False,
) -> Request:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("latin-1")))
    return Request(
        {
            "type": "http",
            "method": "POST" if enrollment else "GET",
            "scheme": "http",
            "path": (
                "/api/satellite/enrollment/living_room_satellite"
                if enrollment
                else "/api/satellite/projection/living_room_satellite"
            ),
            "raw_path": (
                b"/api/satellite/enrollment/living_room_satellite"
                if enrollment
                else b"/api/satellite/projection/living_room_satellite"
            ),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("oracle", 80),
            "app": app,
        }
    )


class SatelliteProjectionRoutesTests(unittest.TestCase):
    def test_authenticated_pull_returns_canonical_no_store_response(self) -> None:
        app = FastAPI()
        resolver = _Resolver(_Envelope())
        configure_satellite_projection_routes(app, resolver)  # type: ignore[arg-type]

        response = satellite_projection_pull(
            "living_room_satellite",
            _request(app, "Bearer directional-token"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b'{"format":"oracle-satellite-projection-pull-v1"}')
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(resolver.calls, [("living_room_satellite", "directional-token")])

    def test_authentication_failures_are_indistinguishable_and_not_cached(self) -> None:
        cases = (
            (None, _Resolver(_Envelope())),
            ("Basic directional-token", _Resolver(_Envelope())),
            ("Bearer wrong", _Resolver(SatelliteProjectionAuthenticationError("specific reason"))),
        )
        for authorization, resolver in cases:
            with self.subTest(authorization=authorization):
                app = FastAPI()
                configure_satellite_projection_routes(app, resolver)  # type: ignore[arg-type]
                with self.assertRaises(HTTPException) as raised:
                    satellite_projection_pull("unknown_satellite", _request(app, authorization))
                self.assertEqual(raised.exception.status_code, 401)
                self.assertEqual(raised.exception.detail, "Satellite projection authentication failed.")
                self.assertEqual(raised.exception.headers["WWW-Authenticate"], "Bearer")
                self.assertEqual(raised.exception.headers["Cache-Control"], "no-store")
                self.assertNotIn("specific reason", str(raised.exception.detail))

    def test_enrollment_uses_distinct_authentication_boundary_and_same_envelope(self) -> None:
        app = FastAPI()
        resolver = _Resolver(_Envelope())
        configure_satellite_projection_routes(app, resolver)  # type: ignore[arg-type]

        response = satellite_projection_enrollment(
            "living_room_satellite",
            _request(app, "Bearer enrollment-token", enrollment=True),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b'{"format":"oracle-satellite-projection-pull-v1"}')
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(resolver.calls, [("living_room_satellite", "enrollment-token")])

        with self.assertRaises(HTTPException) as raised:
            satellite_projection_enrollment(
                "living_room_satellite",
                _request(app, enrollment=True),
            )
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Satellite enrollment authentication failed.")

    def test_unconfigured_or_invalid_store_is_generic_unavailable(self) -> None:
        cases = (
            (None, "Bearer token"),
            (_Resolver(GenerationIntegrityError("secret internal path")), "Bearer token"),
        )
        for resolver, authorization in cases:
            with self.subTest(resolver=resolver):
                app = FastAPI()
                if resolver is not None:
                    configure_satellite_projection_routes(app, resolver)  # type: ignore[arg-type]
                with self.assertRaises(HTTPException) as raised:
                    satellite_projection_pull(
                        "living_room_satellite",
                        _request(app, authorization),
                    )
                self.assertEqual(raised.exception.status_code, 503)
                self.assertEqual(raised.exception.detail, "Satellite projection delivery is unavailable.")
                self.assertEqual(raised.exception.headers["Cache-Control"], "no-store")
                self.assertNotIn("secret internal path", str(raised.exception.detail))

    def test_registers_exact_pull_path(self) -> None:
        app = FastAPI()
        register_satellite_projection_routes(app)
        routes = {
            (method, route.path)
            for route in app.routes
            for method in (getattr(route, "methods", set()) or set())
        }
        self.assertIn(("GET", "/api/satellite/projection/{satellite_id}"), routes)
        self.assertIn(("POST", "/api/satellite/enrollment/{satellite_id}"), routes)


if __name__ == "__main__":
    unittest.main()
