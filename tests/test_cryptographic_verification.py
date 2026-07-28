from __future__ import annotations

import hashlib
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/clavenar"
CA = CHART / "files/tsa/freetsa-ca.pem"
SIGNER = CHART / "files/tsa/freetsa-tsa.crt"


def render(*args: str) -> list[dict]:
    output = subprocess.run(
        ["helm", "template", "smoke", str(CHART), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [item for item in yaml.safe_load_all(output) if isinstance(item, dict)]


class CryptographicVerificationChartTests(unittest.TestCase):
    def test_production_projects_pinned_trust_only_to_ledger(self) -> None:
        documents = render("-f", str(ROOT / "tests/values-production.yaml"))
        configmap = next(
            item
            for item in documents
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "smoke-tsa-trust"
        )
        self.assertEqual(CA.read_bytes(), configmap["data"][CA.name].encode())
        self.assertEqual(SIGNER.read_bytes(), configmap["data"][SIGNER.name].encode())

        deployments = {
            item["metadata"]["name"].removeprefix("smoke-"): item
            for item in documents
            if item.get("kind") == "Deployment"
        }
        ledger_template = deployments["ledger"]["spec"]["template"]
        ledger_container = ledger_template["spec"]["containers"][0]
        env = {item["name"]: item.get("value") for item in ledger_container["env"]}
        self.assertEqual(
            env["CLAVENAR_IDENTITY_URL"], "https://identity:8186"
        )
        self.assertEqual(
            env["CLAVENAR_LEDGER_SPIFFE"],
            "spiffe://clavenar.local/service/ledger",
        )
        self.assertEqual(
            env["CLAVENAR_LEDGER_CRYPTOGRAPHIC_VERIFICATION_REQUIRED"], "true"
        )
        self.assertEqual(env["CLAVENAR_LEDGER_REGULATORY_SIGNING_REQUIRED"], "true")
        self.assertEqual(env["CLAVENAR_LEDGER_TSA_REQUIRED"], "true")
        self.assertEqual(
            env["CLAVENAR_LEDGER_TSA_CA_FILE"],
            "/etc/clavenar/tsa/freetsa-ca.pem",
        )
        self.assertEqual(
            env["CLAVENAR_LEDGER_TSA_SIGNER_CERT_FILE"],
            "/etc/clavenar/tsa/freetsa-tsa.crt",
        )
        mount = next(
            item
            for item in ledger_container["volumeMounts"]
            if item["name"] == "tsa-trust"
        )
        self.assertEqual(mount["mountPath"], "/etc/clavenar/tsa")
        self.assertTrue(mount["readOnly"])
        annotations = ledger_template["metadata"]["annotations"]
        self.assertEqual(
            annotations["checksum/tsa-ca"],
            hashlib.sha256(CA.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            annotations["checksum/tsa-signer"],
            hashlib.sha256(SIGNER.read_bytes()).hexdigest(),
        )

        for name, deployment in deployments.items():
            if name == "ledger":
                continue
            container = deployment["spec"]["template"]["spec"]["containers"][0]
            self.assertNotIn(
                "tsa-trust",
                {item["name"] for item in container.get("volumeMounts", [])},
            )

    def test_production_refuses_optional_tsa_verification(self) -> None:
        result = subprocess.run(
            [
                "helm",
                "template",
                "smoke",
                str(CHART),
                "-f",
                str(ROOT / "tests/values-production.yaml"),
                "--set",
                "ledgerCryptographicVerification.tsaRequired=false",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ledgerCryptographicVerification.tsaRequired", result.stderr)
        self.assertIn("does not match: true", result.stderr)

    def test_evaluation_without_workload_tls_projects_no_trust(self) -> None:
        documents = render()
        self.assertFalse(
            any(
                item.get("kind") == "ConfigMap"
                and item.get("metadata", {}).get("name") == "smoke-tsa-trust"
                for item in documents
            )
        )


if __name__ == "__main__":
    unittest.main()
