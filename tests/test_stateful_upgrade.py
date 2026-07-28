from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = CHART / "files" / "stateful-upgrade-v1.schema.json"
FIXTURE = CHART / "files" / "stateful-upgrade-v1.fixture.json"
SERVICES = {"ledger", "hil", "identity", "policy-engine", "proxy"}


def render(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "upgrade-test", str(CHART), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def documents(output: str) -> list[dict]:
    return [item for item in yaml.safe_load_all(output) if isinstance(item, dict)]


class StatefulUpgradeChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = render()
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.items = documents(result.stdout)

    def test_contract_mirrors_are_exact_and_rendered_immutable(self) -> None:
        public = ROOT.parent / "clavenar-specs" / "contracts"
        if public.is_dir():
            self.assertEqual(SCHEMA.read_bytes(), (public / SCHEMA.name).read_bytes())
            self.assertEqual(FIXTURE.read_bytes(), (public / FIXTURE.name).read_bytes())
        contract = next(
            item
            for item in self.items
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "upgrade-test-stateful-upgrade-contract"
        )
        self.assertTrue(contract["immutable"])
        self.assertEqual(SCHEMA.read_bytes(), contract["data"][SCHEMA.name].encode())
        self.assertEqual(FIXTURE.read_bytes(), contract["data"][FIXTURE.name].encode())

    def test_every_sqlite_deployment_is_recreate_and_contract_bound(self) -> None:
        for service in SERVICES:
            deployment = next(
                item
                for item in self.items
                if item.get("kind") == "Deployment"
                and item["metadata"]["name"] == f"upgrade-test-{service}"
            )
            self.assertEqual({"type": "Recreate"}, deployment["spec"]["strategy"])
            annotations = deployment["metadata"]["annotations"]
            self.assertRegex(
                annotations["clavenar.io/stateful-upgrade-contract"],
                r"^sha256:[a-f0-9]{64}$",
            )
            self.assertEqual("0.35.1", annotations["clavenar.io/release-version"])

    def test_postgres_ledger_is_outside_sqlite_recreate(self) -> None:
        result = render(
            "--set",
            "persistence.ledger.enabled=false",
            "--set",
            "services.ledger.postgres.enabled=true",
            "--set",
            "services.ledger.postgres.dsnSecretName=ledger-postgres-dsn",
            "--set",
            "services.ledger.postgres.tlsCaSecretName=ledger-postgres-ca",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        items = documents(result.stdout)
        ledger = next(
            item
            for item in items
            if item.get("kind") == "Deployment"
            and item["metadata"]["name"] == "upgrade-test-ledger"
        )
        self.assertNotIn("strategy", ledger["spec"])
        self.assertNotIn("annotations", ledger["metadata"])

    def test_exact_backup_and_restore_hook_inventory_is_rendered(self) -> None:
        jobs = [item for item in self.items if item.get("kind") == "Job"]
        backup = {
            item["metadata"]["labels"]["clavenar.io/stateful-service"]: item
            for item in jobs
            if item["metadata"]["annotations"]["helm.sh/hook"] == "pre-upgrade"
        }
        restore = {
            item["metadata"]["labels"]["clavenar.io/stateful-service"]: item
            for item in jobs
            if item["metadata"]["annotations"]["helm.sh/hook"] == "pre-rollback"
        }
        self.assertEqual(SERVICES, set(backup))
        self.assertEqual(SERVICES, set(restore))
        for service in SERVICES:
            for mode, job in (("backup", backup[service]), ("restore", restore[service])):
                pod = job["spec"]["template"]["spec"]
                self.assertEqual("upgrade-test-stateful-upgrade", pod["serviceAccountName"])
                self.assertEqual("Never", pod["restartPolicy"])
                container = pod["containers"][0]
                self.assertRegex(container["image"], r"@sha256:[a-f0-9]{64}$")
                env = {row["name"]: row.get("value") for row in container["env"]}
                self.assertEqual(mode, env["MODE"])
                self.assertEqual(service, env["SERVICE"])
                self.assertEqual("0.35.1", env["TARGET_RELEASE"])
                claim = next(
                    volume["persistentVolumeClaim"]["claimName"]
                    for volume in pod["volumes"]
                    if volume["name"] == "data"
                )
                self.assertEqual(f"upgrade-test-{service}-data", claim)

    def test_hook_script_compiles_and_contains_atomic_restore_boundary(self) -> None:
        script = next(
            item
            for item in self.items
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "upgrade-test-stateful-upgrade"
        )["data"]["stateful-upgrade.py"]
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "stateful-upgrade.py"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["python3", "-m", "py_compile", str(path)],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        for token in (
            "source.backup(target",
            'result != ("ok",)',
            "force_recreate_strategy()",
            "scale_deployment(0)",
            "wait_for_application_writers(0)",
            "os.replace(temporary, DATABASE)",
            "restored database digest mismatch",
        ):
            self.assertIn(token, script)

    def test_unsafe_hook_configuration_fails_render(self) -> None:
        mutations = (
            (
                "statefulUpgrade.enabled=false",
                "statefulUpgrade.enabled does not match: true",
            ),
            (
                "statefulUpgrade.backupMethod=filesystem-copy",
                'statefulUpgrade.backupMethod does not match: "sqlite-online-backup"',
            ),
            (
                "statefulUpgrade.rollbackMode=best-effort",
                'statefulUpgrade.rollbackMode does not match: "verified-backup-restore"',
            ),
            (
                "statefulUpgrade.verifierImage=python:latest",
                "Does not match pattern",
            ),
        )
        for setting, message in mutations:
            with self.subTest(setting=setting):
                result = render("--set", setting)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)

    def test_contract_fixture_is_strict_json(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual("clavenar.stateful-upgrade/v1", fixture["contract"])
        self.assertEqual(1, fixture["maximumObservedApplicationWriters"])


if __name__ == "__main__":
    unittest.main()
