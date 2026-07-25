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
RESILIENCE_SCHEMA = CHART / "files" / "distributed-control-resilience-v1.schema.json"
RESILIENCE_FIXTURE = CHART / "files" / "distributed-control-resilience-v1.fixture.json"


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
        self.assertEqual(
            RESILIENCE_SCHEMA.read_bytes(),
            (public / RESILIENCE_SCHEMA.name).read_bytes(),
        )
        self.assertEqual(
            RESILIENCE_FIXTURE.read_bytes(),
            (public / RESILIENCE_FIXTURE.name).read_bytes(),
        )

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
        self.assertEqual(
            RESILIENCE_SCHEMA.read_bytes(),
            configmap["data"][RESILIENCE_SCHEMA.name].encode(),
        )
        self.assertEqual(
            RESILIENCE_FIXTURE.read_bytes(),
            configmap["data"][RESILIENCE_FIXTURE.name].encode(),
        )

    def test_proxy_mounts_fail_closed_resilience_and_exact_quota_posture(self) -> None:
        proxy = next(
            item
            for item in render()
            if item.get("kind") == "Deployment"
            and item["metadata"]["name"] == "smoke-proxy"
        )
        pod = proxy["spec"]["template"]
        self.assertIn("checksum/distributed-control-state", pod["metadata"]["annotations"])
        self.assertIn("checksum/tenant-state-migration", pod["metadata"]["annotations"])
        self.assertIn(
            "checksum/distributed-control-resilience", pod["metadata"]["annotations"]
        )
        container = pod["spec"]["containers"][0]
        env = {entry["name"]: entry.get("value") for entry in container["env"]}
        self.assertEqual("true", env["CLAVENAR_PROXY_QUOTA_GATE_ENABLED"])
        self.assertEqual("300", env["CLAVENAR_PROXY_QUOTA_CACHE_TTL_SECS"])
        self.assertNotIn("CLAVENAR_CONTROL_STATE_SNAPSHOT_PATH", env)
        mount = next(
            item
            for item in container["volumeMounts"]
            if item["name"] == "distributed-control-state"
        )
        self.assertEqual("/etc/clavenar/control-state", mount["mountPath"])
        volume = next(
            item
            for item in pod["spec"]["volumes"]
            if item["name"] == "distributed-control-state"
        )
        self.assertEqual(
            {
                "distributed-control-state-v1.schema.json",
                "distributed-control-state-v1.fixture.json",
                "tenant-state-migration-v1.schema.json",
                "tenant-state-migration-v1.fixture.json",
                "distributed-control-resilience-v1.schema.json",
                "distributed-control-resilience-v1.fixture.json",
                "state-namespace-isolation-v1.schema.json",
                "state-namespace-isolation-v1.fixture.json",
            },
            {item["key"] for item in volume["configMap"]["items"]},
        )

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

    def test_extra_env_cannot_weaken_fail_closed_resilience(self) -> None:
        for name, value in (
            ("CLAVENAR_PROXY_QUOTA_GATE_ENABLED", "false"),
            ("CLAVENAR_PROXY_QUOTA_CACHE_TTL_SECS", "301"),
            ("CLAVENAR_CONTROL_STATE_SNAPSHOT_PATH", "/tmp/cache"),
        ):
            result = subprocess.run(
                [
                    "helm",
                    "template",
                    "smoke",
                    str(CHART),
                    "--set",
                    f"services.proxy.extraEnv[0].name={name}",
                    "--set",
                    f"services.proxy.extraEnv[0].value={value}",
                ],
                capture_output=True,
                text=True,
            )
            with self.subTest(name=name):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("services.proxy.extraEnv", result.stderr)


if __name__ == "__main__":
    unittest.main()
