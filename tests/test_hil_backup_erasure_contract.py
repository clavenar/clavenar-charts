import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
FILES = (
    "hil-backup-erasure-v1.schema.json",
    "hil-backup-erasure-v1.fixture.json",
    "backup-set-manifest-v1.schema.json",
    "backup-set-manifest-v1.fixture.json",
    "isolated-restore-receipt-v1.schema.json",
    "isolated-restore-receipt-v1.fixture.json",
)


class HilBackupErasureContractChartTests(unittest.TestCase):
    def test_contract_and_privacy_receipts_validate(self) -> None:
        policy_schema = json.loads((CHART / "files" / FILES[0]).read_text())
        policy = json.loads((CHART / "files" / FILES[1]).read_text())
        jsonschema.Draft202012Validator(policy_schema).validate(policy)
        self.assertEqual(100, policy["sanitization"]["maximumRowsPerTransaction"])
        self.assertEqual(
            "latest-external-generation",
            policy["dispositionAuthority"]["restoreMinimum"],
        )
        for schema_name, fixture_name in (
            (FILES[2], FILES[3]),
            (FILES[4], FILES[5]),
        ):
            jsonschema.Draft202012Validator(
                json.loads((CHART / "files" / schema_name).read_text()),
                format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
            ).validate(json.loads((CHART / "files" / fixture_name).read_text()))

    def test_packaged_files_are_exact_and_immutable(self) -> None:
        public = ROOT.parent / "clavenar-specs" / "contracts"
        if not public.is_dir():
            self.skipTest("assembled public specification is not present")
        for name in FILES:
            self.assertEqual(
                (CHART / "files" / name).read_bytes(),
                (public / name).read_bytes(),
            )
        output = subprocess.run(
            [
                "helm",
                "template",
                "smoke",
                str(CHART),
                "-f",
                str(ROOT / "tests" / "values-production.yaml"),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        configmap = next(
            item
            for item in yaml.safe_load_all(output)
            if isinstance(item, dict)
            and item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "smoke-scheduled-backup"
        )
        self.assertTrue(configmap["immutable"])
        for name in FILES:
            self.assertEqual(
                (CHART / "files" / name).read_bytes(),
                configmap["data"][name].encode(),
            )


if __name__ == "__main__":
    unittest.main()
