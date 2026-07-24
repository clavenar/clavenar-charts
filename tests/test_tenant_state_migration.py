import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = json.loads(
    (CHART / "files" / "tenant-state-migration-v1.schema.json").read_text()
)
FIXTURE = json.loads(
    (CHART / "files" / "tenant-state-migration-v1.fixture.json").read_text()
)


class TenantStateMigrationChartTests(unittest.TestCase):
    def test_packaged_fixture_validates_and_is_complete(self) -> None:
        jsonschema.Draft202012Validator(SCHEMA).validate(FIXTURE)
        ids = [state["id"] for state in FIXTURE["states"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(13, len(ids))
        self.assertEqual(13, len(set(ids)))
        self.assertTrue(
            all(state["cutoverWrite"] == "qualified-only" for state in FIXTURE["states"])
        )

    def test_proxy_mounts_exact_contract(self) -> None:
        rendered = subprocess.run(
            ["helm", "template", "smoke", "."],
            cwd=CHART,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        documents = [doc for doc in yaml.safe_load_all(rendered) if doc]
        deployment = next(
            document
            for document in documents
            if document.get("kind") == "Deployment"
            and document["metadata"]["name"] == "smoke-proxy"
        )
        pod = deployment["spec"]["template"]
        self.assertIn(
            "checksum/tenant-state-migration",
            pod["metadata"]["annotations"],
        )
        volume = next(
            item
            for item in pod["spec"]["volumes"]
            if item["name"] == "distributed-control-state"
        )
        self.assertTrue(
            {
                "tenant-state-migration-v1.schema.json",
                "tenant-state-migration-v1.fixture.json",
            }.issubset(
                {item["key"] for item in volume["configMap"]["items"]}
            )
        )


if __name__ == "__main__":
    unittest.main()
