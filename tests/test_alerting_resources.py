from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
PRODUCTION = ROOT / "tests" / "values-production.yaml"


def render(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "smoke", str(CHART), *arguments],
        check=check,
        text=True,
        capture_output=True,
    )


class AlertingResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.production = [
            document
            for document in yaml.safe_load_all(
                render("-f", str(PRODUCTION)).stdout
            )
            if isinstance(document, dict)
        ]

    def test_standard_operator_resources_are_emitted(self) -> None:
        rule = next(
            document
            for document in self.production
            if document.get("kind") == "PrometheusRule"
        )
        routing = next(
            document
            for document in self.production
            if document.get("kind") == "AlertmanagerConfig"
        )
        self.assertEqual("monitoring.coreos.com/v1", rule["apiVersion"])
        self.assertEqual(
            "kube-prometheus-stack", rule["metadata"]["labels"]["release"]
        )
        self.assertEqual("monitoring.coreos.com/v1alpha1", routing["apiVersion"])
        self.assertEqual(
            "clavenar", routing["metadata"]["labels"]["alertmanagerConfig"]
        )

    def test_route_is_secret_backed_and_resolution_capable(self) -> None:
        routing = next(
            document
            for document in self.production
            if document.get("kind") == "AlertmanagerConfig"
        )
        self.assertEqual("operator-inbox", routing["spec"]["route"]["receiver"])
        receiver = routing["spec"]["receivers"][0]
        self.assertEqual("operator-inbox", receiver["name"])
        webhook = receiver["webhookConfigs"][0]
        self.assertTrue(webhook["sendResolved"])
        self.assertEqual(
            {"name": "clavenar-alert-routing", "key": "webhook-url"},
            webhook["urlSecret"],
        )
        self.assertEqual(
            {
                "name": "clavenar-alert-routing",
                "key": "bearer-token",
            },
            webhook["httpConfig"]["bearerTokenSecret"],
        )
        rendered = yaml.safe_dump(routing)
        self.assertNotIn("http://localhost", rendered)
        self.assertNotIn("discard", rendered)

    def test_evaluation_does_not_emit_unconfigured_route(self) -> None:
        documents = [
            document
            for document in yaml.safe_load_all(render().stdout)
            if isinstance(document, dict)
        ]
        self.assertTrue(any(item.get("kind") == "PrometheusRule" for item in documents))
        self.assertFalse(
            any(item.get("kind") == "AlertmanagerConfig" for item in documents)
        )

    def test_enabled_route_requires_exact_secret_references(self) -> None:
        result = render("--set", "alertmanager.enabled=true", check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("urlSecretName is required", result.stderr)

    def test_rules_do_not_filter_on_external_env_label(self) -> None:
        rules = (CHART / "alerts" / "clavenar-alerts.yaml").read_text()
        self.assertNotIn('{env="prod"}', rules)
        self.assertIn("ClavenarSyntheticProductionPage", rules)


if __name__ == "__main__":
    unittest.main()
