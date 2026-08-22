from __future__ import annotations

import socket
import subprocess
from datetime import datetime
from typing import Any
from urllib import error, request

from oracle_app.configuration.domain_models import DirectProbeAdapter

from .network_observations import NetworkProbeObservation


class NetworkProbeBridge:
    provider_name = "probe"

    def get_internet_status(self, *, settings: dict[str, Any]) -> NetworkProbeObservation:
        return NetworkProbeObservation.from_dict(self._get_internet_status_dict(settings=settings))

    def get_typed_internet_status(
        self,
        *,
        adapter: DirectProbeAdapter,
    ) -> NetworkProbeObservation:
        """Observe the configured canonical domain probe without rebuilding V1 settings."""

        return NetworkProbeObservation.from_dict(
            self._run_internet_checks(
                dns_host=str(adapter.dns_host or "").strip(),
                http_url=str(adapter.http_url or "").strip(),
                timeout_seconds=int(adapter.timeout_seconds),
            )
        )

    def _get_internet_status_dict(self, *, settings: dict[str, Any]) -> dict[str, Any]:
        checked_at = datetime.now().astimezone().isoformat()
        if not bool(settings.get("enabled", False)):
            return {
                "status": "unknown",
                "checked_at": checked_at,
                "source": self.provider_name,
                "detail": "Network probe is disabled.",
                "problems": [],
            }

        return self._run_internet_checks(
            dns_host=str(settings.get("dns_host") or "").strip(),
            http_url=str(settings.get("http_url") or "").strip(),
            timeout_seconds=max(1, int(settings.get("timeout_seconds") or 3)),
        )

    def _run_internet_checks(
        self,
        *,
        dns_host: str,
        http_url: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        checked_at = datetime.now().astimezone().isoformat()
        checks: list[dict[str, str]] = []

        if dns_host:
            checks.append(self._run_dns_check(dns_host, timeout_seconds=timeout_seconds))
        if http_url:
            checks.append(self._run_http_check(http_url, timeout_seconds=timeout_seconds))

        if not checks:
            return {
                "status": "unknown",
                "checked_at": checked_at,
                "source": self.provider_name,
                "detail": "Network probe is not configured.",
                "problems": [],
            }

        successes = [item for item in checks if item["status"] == "healthy"]
        failures = [item for item in checks if item["status"] != "healthy"]
        if successes and not failures:
            status = "healthy"
            detail = "Direct network checks succeeded."
        elif not successes:
            status = "down"
            detail = "Direct network checks failed."
        else:
            status = "degraded"
            detail = "Some direct network checks failed."

        problems = [str(item.get("detail") or "").strip() for item in failures if str(item.get("detail") or "").strip()]
        return {
            "status": status,
            "checked_at": checked_at,
            "source": self.provider_name,
            "detail": detail,
            "problems": problems,
            "checks": checks,
        }

    def check_host_reachable(self, address: str, *, timeout_seconds: int = 2) -> dict[str, Any]:
        normalized_address = str(address or "").strip()
        if not normalized_address:
            return {
                "status": "unknown",
                "source": self.provider_name,
                "detail": "Host address is not configured.",
            }
        bounded_timeout = max(1, min(10, int(timeout_seconds or 2)))
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(bounded_timeout), normalized_address],
                check=False,
                capture_output=True,
                text=True,
                timeout=bounded_timeout + 2,
            )
        except (OSError, subprocess.SubprocessError):
            return {
                "status": "down",
                "source": self.provider_name,
                "detail": "Direct host reachability check could not be completed.",
            }
        return {
            "status": "healthy" if result.returncode == 0 else "down",
            "source": self.provider_name,
            "detail": (
                "Direct host reachability check succeeded."
                if result.returncode == 0
                else "Direct host reachability check did not pass."
            ),
        }

    def check_tcp_reachable(
        self,
        address: str,
        *,
        port: int,
        timeout_seconds: int = 2,
    ) -> dict[str, Any]:
        normalized_address = str(address or "").strip()
        if not normalized_address:
            return {
                "status": "unknown",
                "source": self.provider_name,
                "detail": "Host address is not configured.",
            }
        bounded_timeout = max(1, min(10, int(timeout_seconds or 2)))
        try:
            with socket.create_connection(
                (normalized_address, int(port)),
                timeout=bounded_timeout,
            ):
                pass
        except OSError:
            return {
                "status": "down",
                "source": self.provider_name,
                "detail": "Direct host TCP reachability check did not pass.",
            }
        return {
            "status": "healthy",
            "source": self.provider_name,
            "detail": "Direct host TCP reachability check succeeded.",
        }

    def check_readiness(
        self,
        *,
        profile: dict[str, Any],
        internet_settings: dict[str, Any],
    ) -> dict[str, Any]:
        checks: list[dict[str, str]] = []
        for raw_check in profile.get("checks") or []:
            if not isinstance(raw_check, dict):
                continue
            check_id = str(raw_check.get("id") or "").strip()
            kind = str(raw_check.get("kind") or "").strip().lower()
            if kind == "host_reachable":
                result = self.check_host_reachable(
                    str(raw_check.get("address") or ""),
                    timeout_seconds=int(raw_check.get("timeout_seconds") or 2),
                )
            elif kind == "tcp_reachable":
                result = self.check_tcp_reachable(
                    str(raw_check.get("address") or ""),
                    port=int(raw_check.get("port") or 0),
                    timeout_seconds=int(raw_check.get("timeout_seconds") or 2),
                )
            elif kind == "internet":
                result = self.get_internet_status(settings=internet_settings)
            else:
                result = {"status": "unknown"}
            checks.append(
                {
                    "id": check_id,
                    "kind": kind,
                    "status": "passed" if str(result.get("status") or "").lower() == "healthy" else "failed",
                }
            )
        failed = [item for item in checks if item["status"] != "passed"]
        return {
            "ok": bool(checks) and not failed,
            "status": "passed" if checks and not failed else "failed",
            "check_count": len(checks),
            "passed_count": len(checks) - len(failed),
            "failed_check_ids": [item["id"] for item in failed],
            "checks": checks,
        }

    def _run_dns_check(self, host: str, *, timeout_seconds: int) -> dict[str, str]:
        original_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout_seconds)
        try:
            socket.getaddrinfo(host, None)
        except OSError as exc:
            return {
                "kind": "dns",
                "status": "down",
                "detail": f"DNS resolution failed for {host}: {exc}",
            }
        finally:
            socket.setdefaulttimeout(original_timeout)
        return {
            "kind": "dns",
            "status": "healthy",
            "detail": f"DNS resolution succeeded for {host}.",
        }

    def _run_http_check(self, url: str, *, timeout_seconds: int) -> dict[str, str]:
        req = request.Request(url, method="HEAD")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200) or 200)
        except error.HTTPError as exc:
            if 200 <= int(exc.code) < 500:
                return {
                    "kind": "http",
                    "status": "healthy",
                    "detail": f"HTTP reachability succeeded with status {exc.code}.",
                }
            return {
                "kind": "http",
                "status": "down",
                "detail": f"HTTP reachability failed with status {exc.code}.",
            }
        except error.URLError as exc:
            return {
                "kind": "http",
                "status": "down",
                "detail": f"HTTP reachability failed for {url}: {exc.reason}",
            }

        if 200 <= status_code < 500:
            return {
                "kind": "http",
                "status": "healthy",
                "detail": f"HTTP reachability succeeded with status {status_code}.",
            }
        return {
            "kind": "http",
            "status": "down",
            "detail": f"HTTP reachability failed with status {status_code}.",
        }
