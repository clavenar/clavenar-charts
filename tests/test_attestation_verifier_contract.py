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
    def test_production_selects_only_the_real_provider(self) -> None:
        deployments = {
            item["metadata"]["name"].removeprefix("smoke-"): item
            for item in render()
            if item.get("kind") == "Deployment"
        }
        for name in ("proxy", "identity"):
            env = {
                item["name"]: item.get("value")
                for item in deployments[name]["spec"]["template"]["spec"]["containers"][0]["env"]
            }
            self.assertEqual(env["CLAVENAR_RUNTIME_ENVIRONMENT"], "production")
            self.assertEqual(
                env["CLAVENAR_ATTESTATION_PROVIDER"], "identity-k8s-key-bound"
            )
        identity_env = {
            item["name"]: item.get("value")
            for item in deployments["identity"]["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(
            identity_env["CLAVENAR_ATTESTATION_TRUST_ANCHORS_FILE"],
            "/etc/clavenar/public-trust/k8s-trust-anchors.json",
        )

    def test_tpm_registry_adds_combined_provider_and_identity_only_public_trust(self) -> None:
        output = subprocess.run(
            [
                "helm",
                "template",
                "smoke",
                str(CHART),
                "-f",
                str(ROOT / "tests/values-production.yaml"),
                "--set",
                "tpm2AttestationTrust.secretName=clavenar-tpm2-attestation-trust",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        deployments = {
            item["metadata"]["name"].removeprefix("smoke-"): item
            for item in yaml.safe_load_all(output)
            if isinstance(item, dict) and item.get("kind") == "Deployment"
        }
        for name in ("proxy", "identity"):
            env = {
                item["name"]: item.get("value")
                for item in deployments[name]["spec"]["template"]["spec"]["containers"][0]["env"]
            }
            self.assertEqual(
                env["CLAVENAR_ATTESTATION_PROVIDER"],
                "identity-k8s-key-bound+tpm2-quote",
            )
        identity_pod = deployments["identity"]["spec"]["template"]["spec"]
        identity_env = {
            item["name"]: item.get("value")
            for item in identity_pod["containers"][0]["env"]
        }
        self.assertEqual(
            identity_env["CLAVENAR_TPM2_TRUST_ANCHORS_FILE"],
            "/etc/clavenar/tpm2-trust/tpm2-trust-anchors.json",
        )
        volumes = {item["name"]: item for item in identity_pod["volumes"]}
        self.assertEqual(
            volumes["tpm2-attestation-trust"]["secret"],
            {
                "secretName": "clavenar-tpm2-attestation-trust",
                "defaultMode": 292,
                "items": [
                    {
                        "key": "tpm2-trust-anchors.json",
                        "path": "tpm2-trust-anchors.json",
                    }
                ],
            },
        )
        for name, deployment in deployments.items():
            if name == "identity":
                continue
            volume_names = {
                item["name"] for item in deployment["spec"]["template"]["spec"].get("volumes", [])
            }
            self.assertNotIn("tpm2-attestation-trust", volume_names)

    def test_evaluation_mock_is_explicit_and_proxy_only(self) -> None:
        output = subprocess.run(
            ["helm", "template", "smoke", str(CHART)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        deployments = {
            item["metadata"]["name"].removeprefix("smoke-"): item
            for item in yaml.safe_load_all(output)
            if isinstance(item, dict) and item.get("kind") == "Deployment"
        }
        providers = {}
        for name in ("proxy", "identity"):
            env = {
                item["name"]: item.get("value")
                for item in deployments[name]["spec"]["template"]["spec"]["containers"][0]["env"]
            }
            self.assertEqual(env["CLAVENAR_RUNTIME_ENVIRONMENT"], "development")
            providers[name] = env["CLAVENAR_ATTESTATION_PROVIDER"]
        self.assertEqual(
            providers,
            {"proxy": "mock", "identity": "identity-k8s-key-bound"},
        )

    def test_production_requires_external_signed_registry_authority(self) -> None:
        cases = (
            ("vault.addr=", "requires vault.addr"),
            ("vault.tokenSecretName=", "requires vault.tokenSecretName"),
            ("vault.bundled.enabled=true", "bundled dev-mode Vault is forbidden"),
        )
        for setting, expected in cases:
            with self.subTest(setting=setting):
                result = subprocess.run(
                    [
                        "helm",
                        "template",
                        "smoke",
                        str(CHART),
                        "-f",
                        str(ROOT / "tests/values-production.yaml"),
                        "--set",
                        setting,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

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
