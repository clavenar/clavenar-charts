import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = json.loads(
    (CHART / "files" / "state-namespace-isolation-v1.schema.json").read_text()
)
FIXTURE = json.loads(
    (CHART / "files" / "state-namespace-isolation-v1.fixture.json").read_text()
)


class StateNamespaceIsolationChartTests(unittest.TestCase):
    def test_packaged_fixture_validates_and_is_complete(self) -> None:
        jsonschema.Draft202012Validator(SCHEMA).validate(FIXTURE)
        ids = [component["id"] for component in FIXTURE["components"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(6, len(ids))
        self.assertEqual(["operator", "demo"], FIXTURE["namespaces"])
        self.assertEqual("retain", FIXTURE["cleanup"]["volumeAction"])

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
        self.assertIn("checksum/state-namespace-isolation", pod["metadata"]["annotations"])
        volume = next(
            item
            for item in pod["spec"]["volumes"]
            if item["name"] == "distributed-control-state"
        )
        self.assertTrue(
            {
                "state-namespace-isolation-v1.schema.json",
                "state-namespace-isolation-v1.fixture.json",
            }.issubset({item["key"] for item in volume["configMap"]["items"]})
        )


if __name__ == "__main__":
    unittest.main()
