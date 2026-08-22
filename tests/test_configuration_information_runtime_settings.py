from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch

from oracle_app.configuration import EffectiveConfig, InformationRuntimeSettings, inspect_candidate
from oracle_app.configuration.domain_models import (
    OpenClawHttpProvider,
    OpenClawSshCliProvider,
    RssNewsProvider,
    StaticFactsProvider,
)
from oracle_app.admin_facts_routes import admin_facts_lookup
from oracle_app.dispatch import build_dispatch_plan, build_dispatch_registry, execute_dispatch
from oracle_app.information_runtime import CanonicalFactsExecution, CanonicalNewsExecution
from oracle_app.inference import InferenceClient, InferenceExecutionSettings
from oracle_app.news import check_news_health
from oracle_app.routing import build_route_capability_registry, choose_route
from oracle_app.schemas import CommandRequest, RouteResponse


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class InformationRuntimeSettingsTests(unittest.TestCase):
    def test_disabled_sections_do_not_select_dormant_definitions(self) -> None:
        settings = InformationRuntimeSettings.from_effective_config(self._effective_config())

        self.assertFalse(settings.facts.enabled)
        self.assertIsNone(settings.facts.provider_id)
        self.assertIsNone(settings.facts.provider)
        self.assertFalse(settings.news.enabled)
        self.assertEqual(dict(settings.news.sources), {})
        self.assertFalse(settings.suggestions.enabled)
        self.assertIsNone(settings.suggestions.provider)

    def test_maps_enabled_facts_and_per_source_news_provider_bindings(self) -> None:
        effective = self._effective_config(
            information_updates={
                "facts": {
                    "enabled": True,
                    "provider": "static",
                    "providers": {
                        "static": {
                            "type": "static",
                            "items": [
                                {
                                    "id": "oracle_fact",
                                    "status": "answered",
                                    "queries": ["What is Oracle?"],
                                    "answer": {"text": "A household assistant."},
                                }
                            ],
                        }
                    },
                    "summarizer_enabled": False,
                    "acknowledgement_enabled": True,
                    "timeout_seconds": 8,
                    "cache_enabled": True,
                    "cache_ttl_seconds": 60,
                },
                "news": {
                    "enabled": True,
                    "provider": "rss_primary",
                    "providers": {
                        "rss_primary": {"type": "rss", "timeout_seconds": 8},
                        "rss_slow": {"type": "rss", "timeout_seconds": 12},
                    },
                    "sources": [
                        {
                            "id": "local_news",
                            "display_name": "Local News",
                            "aliases": ["local headlines"],
                            "provider": "rss_slow",
                            "feed_url": "https://example.invalid/local.xml",
                        }
                    ],
                    "max_headlines": 4,
                    "fresh_seconds": 300,
                    "stale_if_error_seconds": 1800,
                },
            }
        )

        settings = InformationRuntimeSettings.from_effective_config(effective)

        self.assertIsInstance(settings.facts.provider, StaticFactsProvider)
        self.assertTrue(settings.facts.cache_enabled)
        self.assertIsInstance(settings.news.provider, RssNewsProvider)
        self.assertEqual(settings.news.provider.timeout_seconds, 8)
        self.assertEqual(settings.news.sources["local_news"].provider.timeout_seconds, 12)
        self.assertEqual(settings.news.resolve_source_id("LOCAL HEADLINES"), "local_news")
        with self.assertRaises(TypeError):
            settings.news.sources["other"] = settings.news.sources["local_news"]  # type: ignore[index]

    def test_resolves_selected_http_whole_url_secret_without_exposing_it(self) -> None:
        settings = InformationRuntimeSettings.from_effective_config(
            self._effective_config(
                information_updates={
                    "suggestions": {
                        "enabled": True,
                        "provider": "openclaw_http",
                        "providers": {
                            "openclaw_http": {
                                "adapter": "http",
                                "base_url_secret": "OPENCLAW_WHOLE_URL",
                                "endpoint_path": "/suggestions",
                                "timeout_seconds": 20,
                            }
                        },
                        "max_suggestions": 7,
                    }
                },
                secrets="OPENCLAW_WHOLE_URL=http://secret-host.invalid:18789/private\n",
            )
        )

        self.assertIsInstance(settings.suggestions.provider, OpenClawHttpProvider)
        self.assertEqual(
            settings.suggestions.resolved_base_url,
            "http://secret-host.invalid:18789/private",
        )
        self.assertNotIn("secret-host", repr(settings.suggestions))
        self.assertNotIn("secret-host", repr(settings))

    def test_resolves_only_selected_ssh_password_secret(self) -> None:
        settings = InformationRuntimeSettings.from_effective_config(
            self._effective_config(
                information_updates={
                    "suggestions": {
                        "enabled": True,
                        "provider": "openclaw_ssh",
                        "providers": {
                            "openclaw_ssh": {
                                "adapter": "ssh_cli",
                                "target": "advisor-host",
                                "password_secret": "OPENCLAW_SSH_PASSWORD",
                                "connect_timeout_seconds": 8,
                                "cli_path": "/usr/local/bin/openclaw",
                                "cli_mode": "agent",
                                "agent": "oracle_advisor",
                            },
                            "dormant_http": {
                                "adapter": "http",
                                "base_url_secret": "DORMANT_OPENCLAW_URL",
                                "endpoint_path": "/unused",
                            },
                        },
                        "max_suggestions": 10,
                    }
                },
                secrets="OPENCLAW_SSH_PASSWORD=ssh-password-value\n",
            )
        )

        self.assertIsInstance(settings.suggestions.provider, OpenClawSshCliProvider)
        self.assertEqual(settings.suggestions.resolved_password, "ssh-password-value")
        self.assertIsNone(settings.suggestions.resolved_base_url)
        self.assertNotIn("ssh-password-value", repr(settings))

    def test_canonical_facts_route_dispatch_and_admin_use_typed_execution(self) -> None:
        information = InformationRuntimeSettings.from_effective_config(
            self._effective_config(
                information_updates={
                    "facts": {
                        "enabled": True,
                        "provider": "static",
                        "providers": {
                            "static": {
                                "type": "static",
                                "items": [
                                    {
                                        "id": "oracle_fact",
                                        "status": "answered",
                                        "queries": ["What is Oracle?"],
                                        "answer": {"text": "A household assistant."},
                                    }
                                ],
                            }
                        },
                        "summarizer_enabled": False,
                        "acknowledgement_enabled": True,
                        "timeout_seconds": 8,
                        "cache_enabled": False,
                        "cache_ttl_seconds": 60,
                    }
                }
            )
        )
        execution = CanonicalFactsExecution(
            information.facts,
            inference=InferenceClient(
                InferenceExecutionSettings(
                    enabled=False,
                    base_url=None,
                    model=None,
                    timeout_seconds=None,
                    keep_alive=None,
                    options={},
                    fallback_model=None,
                    fallback_timeout_seconds=None,
                )
            ),
        )

        with patch(
            "oracle_app.config.get_facts_settings",
            side_effect=AssertionError("canonical route used V1 facts settings"),
        ), patch(
            "oracle_app.admin_facts_routes.get_facts_settings",
            side_effect=AssertionError("canonical admin lookup used V1 facts settings"),
        ):
            route = choose_route(
                "What is Oracle?",
                registry=build_route_capability_registry(
                    facts_enabled=True,
                    news_settings=information.news,
                    canonical_information=True,
                    calendar_settings=None,
                    canonical_calendar=True,
                ),
            )
            dispatched = execute_dispatch(
                build_dispatch_plan(
                    CommandRequest(text="What is Oracle?", source="test"),
                    RouteResponse(
                        target="facts",
                        confidence=0.72,
                        reason="Matched factual lookup request",
                        normalized_text="what is oracle",
                    ),
                ),
                registry=build_dispatch_registry(
                    canonical_configuration=True,
                    facts_execution=execution,
                ),
            )
            admin = admin_facts_lookup(
                "What is Oracle?",
                canonical_execution=execution,
                canonical_authority=True,
            )

        self.assertEqual(route.target, "facts")
        self.assertEqual(dispatched.result["answer"]["text"], "A household assistant.")
        self.assertEqual(admin["facts"]["answer"]["text"], "A household assistant.")

    def test_canonical_news_route_dispatch_and_health_use_typed_execution(self) -> None:
        information = InformationRuntimeSettings.from_effective_config(
            self._effective_config(
                information_updates={
                    "news": {
                        "enabled": True,
                        "provider": "rss_primary",
                        "providers": {
                            "rss_primary": {"type": "rss", "timeout_seconds": 8},
                        },
                        "sources": [
                            {
                                "id": "local_news",
                                "display_name": "Local News",
                                "aliases": ["local headlines"],
                                "provider": "rss_primary",
                                "feed_url": "https://example.invalid/local.xml",
                            }
                        ],
                        "max_headlines": 4,
                        "fresh_seconds": 300,
                        "stale_if_error_seconds": 1800,
                    }
                }
            )
        )
        execution = CanonicalNewsExecution(information.news)
        headlines = [{"title": "Typed headline", "link": "https://example.invalid/story"}]

        with patch(
            "oracle_app.news.get_news_settings",
            side_effect=AssertionError("canonical news used V1 settings"),
        ), patch(
            "oracle_app.provider_bridges.rss_news.RssNewsBridge.fetch_typed_headlines",
            return_value=headlines,
        ) as fetch:
            route = choose_route(
                "give me local headlines",
                registry=build_route_capability_registry(
                    news_settings=information.news,
                    canonical_information=True,
                    calendar_settings=None,
                    canonical_calendar=True,
                ),
            )
            dispatched = execute_dispatch(
                build_dispatch_plan(
                    CommandRequest(text="give me local headlines", source="test"),
                    route,
                ),
                registry=build_dispatch_registry(
                    canonical_configuration=True,
                    news_execution=execution,
                ),
            )
            health = check_news_health(
                canonical_execution=execution,
            )

        self.assertEqual(route.target, "news")
        self.assertEqual(dispatched.result["source"], "local_news")
        self.assertEqual(dispatched.result["headlines"], headlines)
        self.assertEqual(health["configured_sources"], ["local_news"])
        fetch.assert_called_once()

    def test_absent_optional_role_has_no_implicit_runtime_defaults(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            InformationRuntimeSettings.from_effective_config(effective)

    def _effective_config(
        self,
        *,
        information_updates: dict[str, object] | None = None,
        secrets: str | None = None,
        include_role: bool = True,
    ) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            role_path = bundle / "domains" / "information.yaml"
            if not include_role:
                role_path.unlink()
            elif information_updates:
                self._update_role(role_path, information_updates)
            if secrets is not None:
                (bundle / "secrets.env").write_text(secrets, encoding="utf-8")
            inspection = inspect_candidate(bundle)
            self.assertTrue(inspection.report.activation_eligible)
            self.assertIsNotNone(inspection.bundle)
            self.assertIsNotNone(inspection.normalized_candidate_revision)
            self.assertIsNotNone(inspection.secrets)
            return EffectiveConfig(
                activation_generation_id="activation_11111111111111111111111111111111",
                config_generation_id="config_11111111111111111111111111111111",
                secret_generation_id="secrets_11111111111111111111111111111111",
                selection_operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids=MappingProxyType({}),
                config_revision=inspection.normalized_candidate_revision,
                bundle_id="example-home",
                schema_version=2,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )

    @staticmethod
    def _update_role(path: Path, updates: dict[str, object]) -> None:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        payload = yaml.load(path.read_text(encoding="utf-8"))
        payload.update(updates)
        path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
