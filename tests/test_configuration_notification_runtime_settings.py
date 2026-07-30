from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest

from pydantic import ValidationError

from oracle_app.configuration import EffectiveConfig, NotificationRuntimeSettings, inspect_candidate
from oracle_app.configuration.domain_models import NotificationType


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class NotificationRuntimeSettingsTests(unittest.TestCase):
    def test_enabled_schema_does_not_admit_user_audience(self) -> None:
        with self.assertRaises(ValidationError):
            NotificationType.model_validate(
                {
                    "id": "household_notice",
                    "enabled": True,
                    "message": "A household condition needs attention.",
                    "audience": [{"type": "user", "id": "resident_one"}],
                    "suppressed_by": [],
                    "delivery_ttl_seconds": 90,
                    "audio_policy": "pause_resume",
                }
            )

    def test_disabled_role_selects_no_type_group_or_provider(self) -> None:
        settings = NotificationRuntimeSettings.from_effective_config(self._effective_config())

        self.assertFalse(settings.enabled)
        self.assertEqual(dict(settings.types), {})
        self.assertEqual(dict(settings.recipient_groups), {})
        self.assertEqual(dict(settings.providers), {})

    def test_enabled_internal_type_preserves_policy_without_dormant_external_provider(self) -> None:
        settings = NotificationRuntimeSettings.from_effective_config(
            self._effective_config(mode="internal")
        )

        notice = settings.notification_type("household_notice")
        self.assertIsNotNone(notice)
        self.assertEqual(notice.definition.message, "A household condition needs attention.")  # type: ignore[union-attr]
        self.assertEqual(notice.source_audience_ids, ())  # type: ignore[union-attr]
        self.assertEqual(notice.definition.suppressed_by, [])  # type: ignore[union-attr]
        self.assertEqual(dict(notice.external_recipient_groups), {})  # type: ignore[union-attr]
        self.assertEqual(dict(settings.recipient_groups), {})
        self.assertEqual(dict(settings.providers), {})

    def test_external_type_binds_only_reached_group_and_resolves_its_provider(self) -> None:
        settings = NotificationRuntimeSettings.from_effective_config(
            self._effective_config(mode="external", provider_secret=True)
        )

        notice = settings.notification_type("household_notice")
        group = notice.external_recipient_groups["household"]  # type: ignore[union-attr]
        self.assertEqual(group.definition.configuration_key, "oracle")
        self.assertEqual(group.definition.routing_tag, "household")
        self.assertEqual(group.provider.provider_id, "apprise_primary")
        self.assertEqual(group.provider.resolved_base_url, "https://secret.invalid/apprise")
        self.assertEqual(tuple(settings.recipient_groups), ("household",))
        self.assertEqual(tuple(settings.providers), ("apprise_primary",))
        self.assertNotIn("https://secret.invalid/apprise", repr(settings))
        with self.assertRaises(TypeError):
            settings.types["other"] = notice  # type: ignore[index]

    def test_absent_optional_role_has_no_implicit_runtime_defaults(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            NotificationRuntimeSettings.from_effective_config(effective)

    def _effective_config(
        self,
        *,
        mode: str | None = None,
        include_role: bool = True,
        provider_secret: bool = False,
    ) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            role_path = bundle / "domains" / "notifications.yaml"
            if not include_role:
                role_path.unlink()
            elif mode is not None:
                self._write_enabled_role(bundle, external=mode == "external")
            if provider_secret:
                (bundle / "secrets.env").write_text(
                    "APPRISE_BASE_URL=https://secret.invalid/apprise\n",
                    encoding="utf-8",
                )
            inspection = inspect_candidate(bundle)
            self.assertTrue(inspection.report.activation_eligible, inspection.report)
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
                schema_version=1,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )

    @staticmethod
    def _write_enabled_role(bundle: Path, *, external: bool) -> None:
        external_policy = None
        if external:
            external_policy = {
                "enabled": True,
                "recipient_groups": ["household"],
                "delivery_ttl_seconds": 300,
                "max_attempts": 3,
                "retry_seconds": 30,
                "quiet_hours_policy": "bypass",
                "repeat_policy": "first_per_correlation",
                "failure_policy": "best_effort",
            }
        role = {
            "enabled": True,
            "providers": {
                "apprise_primary": {
                    "type": "apprise",
                    "base_url_secret": "APPRISE_BASE_URL",
                    "timeout_seconds": 9,
                },
                "dormant": {
                    "type": "apprise",
                    "base_url_secret": "DORMANT_APPRISE_BASE_URL",
                },
            },
            "types": [
                {
                    "id": "household_notice",
                    "enabled": True,
                    "message": "A household condition needs attention.",
                    "audience": [],
                    "suppressed_by": [],
                    "delivery_ttl_seconds": 90,
                    "audio_policy": "pause_resume",
                    **({} if external_policy is None else {"external_delivery": external_policy}),
                },
                {
                    "id": "disabled_notice",
                    "enabled": False,
                    "message": "This definition is dormant.",
                    "audience": [],
                },
            ],
            "recipient_groups": [
                {
                    "id": "household",
                    "enabled": external,
                    "provider": "apprise_primary",
                    "configuration_key": "oracle",
                    "routing_tag": "household",
                },
                {
                    "id": "dormant",
                    "enabled": False,
                    "provider": "dormant",
                    "configuration_key": "dormant",
                    "routing_tag": "dormant",
                },
            ],
        }
        (bundle / "domains" / "notifications.yaml").write_text(json.dumps(role), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
