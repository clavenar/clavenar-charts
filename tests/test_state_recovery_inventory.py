from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = CHART / "files" / "state-recovery-inventory-v1.schema.json"
FIXTURE = CHART / "files" / "state-recovery-inventory-v1.fixture.json"
PRODUCTION_VALUES = ROOT / "tests" / "values-production.yaml"


def render() -> list[dict]:
    result = subprocess.run(
        [
            "helm",
            "template",
            "smoke",
            str(CHART),
            "-f",
            str(PRODUCTION_VALUES),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [item for item in yaml.safe_load_all(result.stdout) if isinstance(item, dict)]


class StateRecoveryInventoryChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.documents = render()
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_public_contract_mirrors_are_byte_identical(self) -> None:
        public = ROOT.parent / "clavenar-specs" / "contracts"
        if not public.is_dir():
            self.skipTest("assembled public specification is not present")
        self.assertEqual(SCHEMA.read_bytes(), (public / SCHEMA.name).read_bytes())
        self.assertEqual(FIXTURE.read_bytes(), (public / FIXTURE.name).read_bytes())

    def test_inventory_configmap_preserves_exact_bytes(self) -> None:
        configmap = next(
            item
            for item in self.documents
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "smoke-state-recovery-inventory"
        )
        self.assertTrue(configmap["immutable"])
        self.assertEqual(SCHEMA.read_bytes(), configmap["data"][SCHEMA.name].encode())
        self.assertEqual(FIXTURE.read_bytes(), configmap["data"][FIXTURE.name].encode())

    def test_production_render_has_every_single_writer_and_workload_claim(self) -> None:
        claims = {
            item["metadata"]["name"]
            for item in self.documents
            if item.get("kind") == "PersistentVolumeClaim"
        }
        expected_service_claims = {
            "smoke-hil-data",
            "smoke-identity-data",
            "smoke-ledger-data",
            "smoke-policy-engine-data",
            "smoke-proxy-data",
        }
        expected_workload_claims = {
            f"smoke-{service}-workload-svid"
            for service in (
                "brain",
                "console",
                "hil",
                "identity",
                "ledger",
                "policy-engine",
                "proxy",
            )
        }
        self.assertTrue(expected_service_claims <= claims)
        self.assertTrue(expected_workload_claims <= claims)
        self.assertEqual(12, len(expected_service_claims | expected_workload_claims))

    def test_production_secret_custody_boundaries_are_referenced(self) -> None:
        secret_names: set[str] = set()
        for document in self.documents:
            template = document.get("spec", {}).get("template", {})
            pod = template.get("spec", {}) if isinstance(template, dict) else {}
            for volume in pod.get("volumes") or []:
                secret = volume.get("secret")
                if isinstance(secret, dict) and isinstance(secret.get("secretName"), str):
                    secret_names.add(secret["secretName"])
            for container in pod.get("containers") or []:
                for entry in container.get("env") or []:
                    reference = entry.get("valueFrom", {}).get("secretKeyRef", {})
                    if isinstance(reference.get("name"), str):
                        secret_names.add(reference["name"])
        self.assertTrue(
            {
                "clavenar-attestation-trust",
                "clavenar-operator-public-trust",
                "clavenar-runtime-auth",
                "clavenar-vault-token",
                "clavenar-workload-tls",
            }
            <= secret_names
        )

    def test_inventory_does_not_claim_ha_or_future_delivery(self) -> None:
        self.assertEqual(20, len(self.fixture["states"]))
        self.assertTrue(
            all(
                topology["wholeStackHaClaim"] is False
                for topology in self.fixture["topologies"]
            )
        )
        self.assertEqual(
            {
                "isolated-restore",
                "disaster-recovery",
                "upgrade-safety",
            },
            set(self.fixture["approval"]["doesNotAssert"]),
        )
        for state in self.fixture["states"]:
            self.assertEqual(
                "pending-wp-10.6", state["protection"]["restoreProofStatus"]
            )


if __name__ == "__main__":
    unittest.main()
