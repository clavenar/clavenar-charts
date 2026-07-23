from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
VALUES = ROOT / "tests" / "values-production.yaml"
SCHEMA = CHART / "files" / "backup-set-manifest-v1.schema.json"
FIXTURE = CHART / "files" / "backup-set-manifest-v1.fixture.json"


def render(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "smoke", str(CHART), *arguments],
        check=check,
        text=True,
        capture_output=True,
    )


class ScheduledBackupChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = render("-f", str(VALUES))
        cls.documents = [
            item for item in yaml.safe_load_all(result.stdout) if isinstance(item, dict)
        ]
        cls.configmap = next(
            item
            for item in cls.documents
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "smoke-scheduled-backup"
        )
        cls.plan = json.loads(
            cls.configmap["data"]["helm-scheduled-backup-plan.json"]
        )

    def test_contract_mirrors_public_bytes(self) -> None:
        public = ROOT.parent / "clavenar-specs" / "contracts"
        if not public.is_dir():
            self.skipTest("assembled public specification is not present")
        for path in (SCHEMA, FIXTURE):
            self.assertEqual(path.read_bytes(), (public / path.name).read_bytes())

    def test_immutable_configmap_preserves_contract_bytes(self) -> None:
        self.assertTrue(self.configmap["immutable"])
        self.assertEqual(
            SCHEMA.read_bytes(),
            self.configmap["data"][SCHEMA.name].encode(),
        )
        self.assertEqual(
            FIXTURE.read_bytes(),
            self.configmap["data"][FIXTURE.name].encode(),
        )

    def test_production_operator_contract_is_exact(self) -> None:
        self.assertTrue(self.plan["enabled"])
        self.assertEqual("*/5 * * * *", self.plan["schedule"])
        self.assertEqual(420, self.plan["maximumAgeSeconds"])
        self.assertEqual("restic-repository-v2", self.plan["encryptionFormat"])
        self.assertEqual(
            "content-addressed-or-versioned", self.plan["offsiteClass"]
        )
        self.assertEqual(
            "external-secret", self.plan["repositoryCredential"]["source"]
        )

    def test_plan_partitions_all_twenty_states(self) -> None:
        groups = [
            set(self.plan["coverage"][name])
            for name in (
                "capturedStateIds",
                "externalCustodyStateIds",
                "reconstructibleStateIds",
                "signedSourceStateIds",
            )
        ]
        for index, group in enumerate(groups):
            for other in groups[index + 1 :]:
                self.assertFalse(group & other)
        self.assertEqual(20, len(set().union(*groups)))
        self.assertNotIn("workload-current-identities", groups[0])
        self.assertNotIn("simulator-agent-identities", groups[0])

    def test_chart_never_embeds_repository_credentials(self) -> None:
        rendered = yaml.safe_dump_all(self.documents)
        self.assertNotIn("RESTIC_PASSWORD", rendered)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", rendered)
        self.assertNotIn("stringData:", rendered)
        self.assertNotEqual(
            self.plan["repositoryCredential"]["name"],
            self.plan["receipt"]["name"],
        )

    def test_evaluation_render_is_explicitly_disabled(self) -> None:
        documents = [
            item
            for item in yaml.safe_load_all(render().stdout)
            if isinstance(item, dict)
        ]
        configmap = next(
            item
            for item in documents
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "smoke-scheduled-backup"
        )
        plan = json.loads(configmap["data"]["helm-scheduled-backup-plan.json"])
        self.assertFalse(plan["enabled"])
        self.assertEqual("", plan["repositoryCredential"]["name"])

    def test_enabled_contract_rejects_missing_operator(self) -> None:
        result = render("--set", "scheduledBackup.enabled=true", check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("scheduledBackup.operator is required", result.stderr)

    def test_enabled_contract_rejects_shared_secret(self) -> None:
        result = render(
            "--set",
            "scheduledBackup.enabled=true",
            "--set",
            "scheduledBackup.operator=backup-controller",
            "--set",
            "scheduledBackup.repositorySecretName=shared",
            "--set",
            "scheduledBackup.receiptSecretName=shared",
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("must use different Secrets", result.stderr)

    def test_future_operational_claims_remain_withheld(self) -> None:
        self.assertEqual(
            {"isolated-restore", "disaster-recovery", "upgrade-safety"},
            set(self.plan["doesNotAssert"]),
        )


if __name__ == "__main__":
    unittest.main()
