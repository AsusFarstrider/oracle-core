from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from oracle_runtime_config import choose_config_report_format


class _ConfigRequestHandler(BaseHTTPRequestHandler):
    server_version = "OracleSatelliteConfig/0.1"

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path != "/health/config":
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return

        query = {
            key: values[-1]
            for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
            if values
        }
        response_format = choose_config_report_format(query, self.headers.get("Accept"))
        if response_format == "text":
            self._write_text(HTTPStatus.OK, self.server.render_config_report_text())
            return
        self._write_json(HTTPStatus.OK, self.server.build_config_report_payload())

    def log_message(self, fmt: str, *args: object) -> None:
        logging.info("%s - %s", self.address_string(), fmt % args)

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_text(self, status: HTTPStatus, payload: str) -> None:
        body = payload.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ConfigServer(ThreadingHTTPServer):
    def __init__(self, server_address, build_config_report_payload, render_config_report_text) -> None:
        super().__init__(server_address, _ConfigRequestHandler)
        self._build_config_report_payload = build_config_report_payload
        self._render_config_report_text = render_config_report_text

    def build_config_report_payload(self) -> dict[str, object]:
        return self._build_config_report_payload()

    def render_config_report_text(self) -> str:
        return self._render_config_report_text()


def start_config_http_server(
    *,
    bind_host: str,
    bind_port: int,
    build_config_report_payload,
    render_config_report_text,
    logger: logging.Logger,
):
    server = _ConfigServer(
        (bind_host, bind_port),
        build_config_report_payload,
        render_config_report_text,
    )
    thread = threading.Thread(target=server.serve_forever, name="oracle-satellite-config-http", daemon=True)
    thread.start()
    logger.info("Config report server listening on %s:%s", bind_host, bind_port)
    return server
