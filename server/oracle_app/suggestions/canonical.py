from __future__ import annotations

from oracle_app.configuration.domain_models import (
    OpenClawHttpProvider,
    OpenClawMockProvider,
    OpenClawSshCliProvider,
)
from oracle_app.configuration.information_runtime_settings import SuggestionsRuntimeSettings
from oracle_app.provider_bridges.openclaw import generate_suggestions
from oracle_app.provider_bridges.openclaw.schemas import OpenClawBridgeOptions


class CanonicalSuggestionsExecution:
    """Typed OpenClaw edge selected from one immutable configuration snapshot."""

    def __init__(self, settings: SuggestionsRuntimeSettings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def max_suggestions(self, requested: int | None) -> int:
        return int(requested or self.settings.max_suggestions)

    def status(self) -> dict[str, object]:
        provider = self.settings.provider
        adapter = "" if provider is None else provider.adapter
        configured = self.enabled and provider is not None
        return {
            "ok": configured,
            "provider": "openclaw",
            "adapter": adapter,
            "configured": configured,
            "base_url_configured": isinstance(provider, OpenClawHttpProvider)
            and bool(self.settings.resolved_base_url),
            "ssh_target_configured": isinstance(provider, OpenClawSshCliProvider)
            and bool(provider.target),
            "endpoint_path_configured": isinstance(provider, OpenClawHttpProvider)
            and bool(provider.endpoint_path),
            "detail": (
                "OpenClaw transport is configured."
                if configured
                else "Suggestions is disabled in canonical configuration."
            ),
        }

    def generate(
        self,
        packet: dict[str, object],
        *,
        max_suggestions: int,
        use_mock: bool,
    ) -> dict[str, object]:
        return generate_suggestions(
            packet,
            self._bridge_options(
                max_suggestions=max_suggestions,
                use_mock=use_mock,
            ),
        )

    def _bridge_options(
        self,
        *,
        max_suggestions: int,
        use_mock: bool,
    ) -> OpenClawBridgeOptions:
        if not self.enabled or self.settings.provider is None:
            raise ValueError("Canonical Suggestions is disabled or not configured.")
        provider = self.settings.provider
        if use_mock:
            return OpenClawBridgeOptions(
                adapter="mock",
                use_mock=True,
                max_suggestions=max_suggestions,
            )
        if isinstance(provider, OpenClawHttpProvider):
            return OpenClawBridgeOptions(
                adapter="http",
                base_url=str(self.settings.resolved_base_url or ""),
                endpoint_path=provider.endpoint_path,
                timeout_seconds=provider.timeout_seconds,
                max_suggestions=max_suggestions,
            )
        if isinstance(provider, OpenClawSshCliProvider):
            return OpenClawBridgeOptions(
                adapter="ssh_cli",
                max_suggestions=max_suggestions,
                ssh_target=provider.target,
                ssh_password=str(self.settings.resolved_password or ""),
                ssh_identity_file=str(provider.identity_file or ""),
                ssh_connect_timeout_seconds=provider.connect_timeout_seconds,
                cli_path=str(provider.cli_path),
                cli_mode=provider.cli_mode,
                agent_name=str(provider.agent or ""),
                model=str(provider.model or ""),
                start_gateway=provider.start_gateway,
                gateway_port=provider.gateway_port,
                timeout_seconds=provider.timeout_seconds,
            )
        if isinstance(provider, OpenClawMockProvider):
            return OpenClawBridgeOptions(
                adapter="mock",
                use_mock=True,
                max_suggestions=max_suggestions,
            )
        raise TypeError("Canonical Suggestions provider is not implemented.")
