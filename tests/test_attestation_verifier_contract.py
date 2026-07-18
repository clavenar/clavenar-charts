from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/clavenar"
SCHEMA = CHART / "files/attestation-verifier-v1.schema.json"
FIXTURE = CHART / "files/attestation-verifier-v1.fixture.json"
TARGETS = {"proxy", "policy-engine", "identity"}


def render() -> list[dict]:
    output = subprocess.run(
        [
            "helm",
            "template",
            "smoke",
            str(CHART),
            "-f",
            str(ROOT / "tests/values-production.yaml"),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [item for item in yaml.safe_load_all(output) if isinstance(item, dict)]


class AttestationVerifierContractChartTests(unittest.TestCase):
    def test_immutable_configmap_preserves_contract_bytes(self) -> None:
        schema = json.loads(SCHEMA.read_text())
        fixture = json.loads(FIXTURE.read_text())
        self.assertEqual(schema["x-clavenar-contract"]["contractVersion"], "1.0.0")
        self.assertEqual(fixture["contractVersion"], "1.0.0")
        configmap = next(
            item
            for item in render()
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "smoke-attestation-verifier-contract"
        )
        self.assertTrue(configmap["immutable"])
        self.assertEqual(SCHEMA.read_bytes(), configmap["data"][SCHEMA.name].encode())
        self.assertEqual(FIXTURE.read_bytes(), configmap["data"][FIXTURE.name].encode())

    def test_exact_consumers_mount_the_same_contract(self) -> None:
        documents = render()
        deployments = {
            item["metadata"]["name"].removeprefix("smoke-"): item
            for item in documents
            if item.get("kind") == "Deployment"
        }
        expected_annotations = {
            "checksum/attestation-verifier-schema": hashlib.sha256(SCHEMA.read_bytes()).hexdigest(),
            "checksum/attestation-verifier-fixture": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        }
        for name, deployment in deployments.items():
            pod_template = deployment["spec"]["template"]
            container = pod_template["spec"]["containers"][0]
            mounts = {item["name"]: item for item in container.get("volumeMounts", [])}
            if name in TARGETS:
                mount = mounts["attestation-verifier-contract"]
                self.assertEqual(mount["mountPath"], "/etc/clavenar/attestation")
                self.assertTrue(mount["readOnly"])
                for key, value in expected_annotations.items():
                    self.assertEqual(pod_template["metadata"]["annotations"][key], value)
            else:
                self.assertNotIn("attestation-verifier-contract", mounts)


if __name__ == "__main__":
    unittest.main()
