import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = json.loads(
    (CHART / "files" / "tenant-lifecycle-saga-v1.schema.json").read_text()
)
FIXTURE = json.loads(
    (CHART / "files" / "tenant-lifecycle-saga-v1.fixture.json").read_text()
)


class TenantLifecycleSagaChartTests(unittest.TestCase):
    def test_packaged_fixture_validates_with_authority_first(self) -> None:
        jsonschema.Draft202012Validator(SCHEMA).validate(FIXTURE)
        offboard = next(plan for plan in FIXTURE["plans"] if plan["kind"] == "offboard")
        self.assertEqual("authority_fence", offboard["steps"][0]["id"])
        self.assertLess(
            [step["id"] for step in offboard["steps"]].index("ledger_final_export"),
            [step["id"] for step in offboard["steps"]].index("ledger_tombstone"),
        )

    def test_proxy_mounts_exact_contract_with_rollout_digest(self) -> None:
        rendered = subprocess.run(
            ["helm", "template", "smoke", "."],
            cwd=CHART,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        documents = [document for document in yaml.safe_load_all(rendered) if document]
        deployment = next(
            document
            for document in documents
            if document.get("kind") == "Deployment"
            and document["metadata"]["name"] == "smoke-proxy"
        )
        pod = deployment["spec"]["template"]
        self.assertIn(
            "checksum/tenant-lifecycle-saga", pod["metadata"]["annotations"]
        )
        volume = next(
            item
            for item in pod["spec"]["volumes"]
            if item["name"] == "distributed-control-state"
        )
        self.assertTrue(
            {
                "tenant-lifecycle-saga-v1.schema.json",
                "tenant-lifecycle-saga-v1.fixture.json",
            }.issubset({item["key"] for item in volume["configMap"]["items"]})
        )


if __name__ == "__main__":
    unittest.main()
