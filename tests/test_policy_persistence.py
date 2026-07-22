from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "charts" / "clavenar"


def render(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "policy-test", str(CHART), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def documents(output: str) -> list[dict]:
    return [item for item in yaml.safe_load_all(output) if isinstance(item, dict)]


class PolicyPersistenceTests(unittest.TestCase):
    def test_default_claim_mount_and_database_path_are_exact(self) -> None:
        result = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        items = documents(result.stdout)
        deployment = next(
            item
            for item in items
            if item.get("kind") == "Deployment"
            and item["metadata"]["name"] == "policy-test-policy-engine"
        )
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = {entry["name"]: entry.get("value") for entry in container["env"]}
        self.assertEqual(
            env["CLAVENAR_POLICY_DB"],
            "/var/lib/clavenar-policy-engine/policies.db",
        )
        mount = next(item for item in container["volumeMounts"] if item["name"] == "data")
        self.assertEqual(mount["mountPath"], "/var/lib/clavenar-policy-engine")
        volume = next(
            item
            for item in deployment["spec"]["template"]["spec"]["volumes"]
            if item["name"] == "data"
        )
        self.assertEqual(
            volume["persistentVolumeClaim"]["claimName"],
            "policy-test-policy-engine-data",
        )
        claim = next(
            item
            for item in items
            if item.get("kind") == "PersistentVolumeClaim"
            and item["metadata"]["name"] == "policy-test-policy-engine-data"
        )
        self.assertEqual(
            claim["metadata"]["annotations"]["helm.sh/resource-policy"], "keep"
        )

    def test_existing_claim_is_mounted_without_generated_claim(self) -> None:
        result = render("--set", "persistence.policyEngine.existingClaim=operator-policy")
        self.assertEqual(result.returncode, 0, result.stderr)
        items = documents(result.stdout)
        self.assertFalse(
            any(
                item.get("kind") == "PersistentVolumeClaim"
                and item["metadata"]["name"] == "policy-test-policy-engine-data"
                for item in items
            )
        )
        deployment = next(
            item
            for item in items
            if item.get("kind") == "Deployment"
            and item["metadata"]["name"] == "policy-test-policy-engine"
        )
        volumes = deployment["spec"]["template"]["spec"]["volumes"]
        data = next(item for item in volumes if item["name"] == "data")
        self.assertEqual(data["persistentVolumeClaim"]["claimName"], "operator-policy")

    def test_sqlite_policy_store_refuses_multiple_replicas(self) -> None:
        result = render("--set", "services.policyEngine.replicas=2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SQLite-backed services must run replicas: 1", result.stderr)

    def test_database_path_cannot_be_shadowed_through_extra_env(self) -> None:
        result = render(
            "--set", "services.policyEngine.extraEnv[0].name=CLAVENAR_POLICY_DB",
            "--set", "services.policyEngine.extraEnv[0].value=/tmp/substitute.db",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(
            "duplicates a chart-governed environment variable" in result.stderr
            or "Must not validate the schema" in result.stderr,
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
