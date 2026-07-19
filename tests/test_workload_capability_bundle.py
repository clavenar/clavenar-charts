from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/clavenar"
BUNDLE = CHART / "files/workload-capability-bundle.json"
TARGETS = ("ledger", "policy-engine", "hil", "identity")


def render(*args: str) -> list[dict]:
    output = subprocess.run(
        ["helm", "template", "smoke", str(CHART), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [item for item in yaml.safe_load_all(output) if isinstance(item, dict)]


class WorkloadCapabilityBundleTests(unittest.TestCase):
    def test_configmap_preserves_exact_generated_bytes(self) -> None:
        bundle_bytes = BUNDLE.read_bytes()
        bundle = json.loads(bundle_bytes)
        self.assertEqual(1, bundle["schemaVersion"])
        self.assertEqual("WP-02.9", bundle["feature"])
        self.assertEqual(11, len(bundle["workloadIdentities"]))
        self.assertEqual(set(TARGETS), set(bundle["services"]))
        self.assertEqual(
            58,
            len(
                {
                    route["family"]
                    for policy in bundle["services"].values()
                    for route in policy["routes"]
                }
            ),
        )
        self.assertEqual(
            123,
            sum(len(policy["routes"]) for policy in bundle["services"].values()),
        )
        configmap = next(
            item
            for item in render("-f", str(ROOT / "tests/values-production.yaml"))
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "smoke-workload-capabilities"
        )
        self.assertTrue(configmap["immutable"])
        self.assertEqual(
            bundle_bytes,
            configmap["data"]["workload-capability-bundle.json"].encode(),
        )

    def test_all_target_services_mount_and_bind_the_same_bundle(self) -> None:
        bundle_bytes = BUNDLE.read_bytes()
        bundle = json.loads(bundle_bytes)
        expected_env = {
            "CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE": (
                "/etc/clavenar/workload-capability-bundle.json"
            ),
            "CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE_SHA256": (
                "sha256:" + hashlib.sha256(bundle_bytes).hexdigest()
            ),
            "CLAVENAR_ENDPOINT_CAPABILITY_MATRIX_SHA256": bundle["matrixSha256"],
        }
        documents = render("-f", str(ROOT / "tests/values-production.yaml"))
        for target in TARGETS:
            with self.subTest(target=target):
                deployment = next(
                    item
                    for item in documents
                    if item.get("kind") == "Deployment"
                    and item["metadata"]["name"] == f"smoke-{target}"
                )
                pod = deployment["spec"]["template"]["spec"]
                container = pod["containers"][0]
                env = {
                    item["name"]: item.get("value")
                    for item in container["env"]
                    if item["name"] in expected_env
                }
                self.assertEqual(expected_env, env)
                self.assertFalse(
                    any(item["name"].endswith("_ALLOWED_CALLERS") for item in container["env"])
                )
                mount = next(
                    item
                    for item in container["volumeMounts"]
                    if item["name"] == "workload-capabilities"
                )
                self.assertEqual(
                    "/etc/clavenar/workload-capability-bundle.json",
                    mount["mountPath"],
                )
                self.assertTrue(mount["readOnly"])

    def test_plain_evaluation_render_does_not_project_runtime_policy(self) -> None:
        documents = render()
        for target in TARGETS:
            deployment = next(
                item
                for item in documents
                if item.get("kind") == "Deployment"
                and item["metadata"]["name"] == f"smoke-{target}"
            )
            pod = deployment["spec"]["template"]["spec"]
            container = pod["containers"][0]
            self.assertFalse(
                any(
                    item["name"].startswith("CLAVENAR_WORKLOAD_CAPABILITY_")
                    for item in container["env"]
                )
            )
            self.assertFalse(
                any(
                    item["name"] == "workload-capabilities"
                    for item in container.get("volumeMounts", [])
                )
            )


if __name__ == "__main__":
    unittest.main()
