import hashlib
import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = CHART / "files" / "hil-erasure-v1.schema.json"
FIXTURE = CHART / "files" / "hil-erasure-v1.fixture.json"


class HilErasureContractChartTests(unittest.TestCase):
    def test_packaged_contract_validates_and_is_bounded(self) -> None:
        schema = json.loads(SCHEMA.read_text())
        fixture = json.loads(FIXTURE.read_text())
        jsonschema.Draft202012Validator(schema).validate(fixture)
        self.assertEqual(fixture["contract"], "clavenar.hil-erasure/v1")
        self.assertEqual(fixture["deadlineErasure"]["maximumRowsPerSweep"], 100)
        self.assertEqual(
            fixture["legalHold"]["reasonPersistence"],
            "sha256-commitment-only",
        )
        self.assertIn("backup-copy-erasure", fixture["doesNotAssert"])

    def test_hil_rollout_is_bound_to_both_contract_files(self) -> None:
        output = subprocess.run(
            ["helm", "template", "smoke", str(CHART)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        deployment = next(
            item
            for item in yaml.safe_load_all(output)
            if isinstance(item, dict)
            and item.get("kind") == "Deployment"
            and item["metadata"]["name"] == "smoke-hil"
        )
        annotations = deployment["spec"]["template"]["metadata"]["annotations"]
        self.assertEqual(
            annotations["checksum/hil-erasure-schema"],
            hashlib.sha256(SCHEMA.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            annotations["checksum/hil-erasure-fixture"],
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
