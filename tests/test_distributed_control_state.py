from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = CHART / "files" / "distributed-control-state-v1.schema.json"
FIXTURE = CHART / "files" / "distributed-control-state-v1.fixture.json"


def render(*settings: str) -> list[dict]:
    command = [
        "helm",
        "template",
        "smoke",
        str(CHART),
        "-f",
        str(ROOT / "tests" / "values-production.yaml"),
    ]
    for setting in settings:
        command.extend(("--set", setting))
    output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    return [item for item in yaml.safe_load_all(output) if isinstance(item, dict)]


class DistributedControlStateChartTests(unittest.TestCase):
    def test_public_contract_mirrors_are_byte_identical(self) -> None:
        public = ROOT.parent / "clavenar-specs" / "contracts"
        if not public.is_dir():
            self.skipTest("assembled public specification is not present")
        self.assertEqual(SCHEMA.read_bytes(), (public / SCHEMA.name).read_bytes())
        self.assertEqual(FIXTURE.read_bytes(), (public / FIXTURE.name).read_bytes())

    def test_inventory_configmap_preserves_exact_bytes(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["contract"], "clavenar.distributed-control-state/v1")
        self.assertEqual(len(fixture["controls"]), 7)
        configmap = next(
            item
            for item in render()
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "smoke-distributed-control-state"
        )
        self.assertTrue(configmap["immutable"])
        self.assertEqual(SCHEMA.read_bytes(), configmap["data"][SCHEMA.name].encode())
        self.assertEqual(FIXTURE.read_bytes(), configmap["data"][FIXTURE.name].encode())

    def test_proxy_and_identity_share_exact_replica_count(self) -> None:
        for expected, setting in (("1", ()), ("3", ("controlState.replicas=3",))):
            deployments = {
                item["metadata"]["name"].removeprefix("smoke-"): item
                for item in render(*setting)
                if item.get("kind") == "Deployment"
            }
            for service in ("proxy", "identity"):
                env = {
                    entry["name"]: entry.get("value")
                    for entry in deployments[service]["spec"]["template"]["spec"]["containers"][0]["env"]
                }
                self.assertEqual(env["CLAVENAR_CONTROL_STATE_REPLICAS"], expected)

    def test_replica_count_outside_supported_range_is_rejected(self) -> None:
        for replicas in (0, 6):
            result = subprocess.run(
                [
                    "helm",
                    "template",
                    "smoke",
                    str(CHART),
                    "--set",
                    f"controlState.replicas={replicas}",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("controlState.replicas", result.stderr)

    def test_extra_env_cannot_shadow_replica_contract(self) -> None:
        result = subprocess.run(
            [
                "helm",
                "template",
                "smoke",
                str(CHART),
                "--set",
                "services.proxy.extraEnv[0].name=CLAVENAR_CONTROL_STATE_REPLICAS",
                "--set",
                "services.proxy.extraEnv[0].value=5",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("services.proxy.extraEnv", result.stderr)


if __name__ == "__main__":
    unittest.main()
