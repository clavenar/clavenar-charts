from __future__ import annotations

import json
from pathlib import Path
import unittest

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"


class HilNotificationLifecycleChartTests(unittest.TestCase):
    def test_contract_fixture_validates(self) -> None:
        schema = json.loads(
            (CHART / "files/hil-notification-lifecycle-v1.schema.json").read_text()
        )
        fixture = json.loads(
            (CHART / "files/hil-notification-lifecycle-v1.fixture.json").read_text()
        )
        jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        ).validate(fixture)

    def test_default_posture_is_visible_warn_mode(self) -> None:
        values = yaml.safe_load((CHART / "values.yaml").read_text())
        notifications = values["services"]["hil"]["notifications"]
        self.assertEqual("warn", notifications["mode"])
        self.assertEqual("", notifications["webhook"]["url"])
        self.assertEqual("", notifications["webhook"]["tokenSecretName"])

    def test_template_mounts_external_webhook_authority(self) -> None:
        template = (CHART / "templates/services.yaml").read_text()
        for token in (
            "CLAVENAR_HIL_NOTIFICATION_MODE",
            "CLAVENAR_HIL_NOTIFICATION_TIMEOUT_SECS",
            "CLAVENAR_HIL_WEBHOOK_URL",
            "CLAVENAR_HIL_WEBHOOK_BEARER_TOKEN_FILE",
            "hil-notification-delivery-token",
        ):
            self.assertIn(token, template)
        self.assertIn(
            "tokenSecretName is required when webhook.url is set",
            template,
        )

    def test_delivery_failure_alert_is_canonical(self) -> None:
        alerts = (CHART / "alerts/clavenar-alerts.yaml").read_text()
        self.assertIn("HILNotificationDeliveryFailing", alerts)
        self.assertIn(
            'clavenar_hil_notification_deliveries_total{outcome="failure"}',
            alerts,
        )


if __name__ == "__main__":
    unittest.main()
