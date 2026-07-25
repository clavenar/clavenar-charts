import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = json.loads(
    (CHART / "files" / "tenant-route-authorization-v1.schema.json").read_text()
)
FIXTURE = json.loads(
    (CHART / "files" / "tenant-route-authorization-v1.fixture.json").read_text()
)


class TenantRouteAuthorizationChartTests(unittest.TestCase):
    def test_packaged_fixture_validates_and_is_complete(self) -> None:
        jsonschema.Draft202012Validator(SCHEMA).validate(FIXTURE)
        self.assertEqual(
            ["hil", "lite", "proxy"],
            [entry["owner"] for entry in FIXTURE["routes"]],
        )
        self.assertEqual("none", FIXTURE["denial"]["mutation"])
        self.assertEqual(
            "no-unqualified-identity", FIXTURE["compatibility"]["production"]
        )

    def test_proxy_mounts_exact_contract(self) -> None:
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
            "checksum/tenant-route-authorization", pod["metadata"]["annotations"]
        )
        volume = next(
            item
            for item in pod["spec"]["volumes"]
            if item["name"] == "distributed-control-state"
        )
        self.assertTrue(
            {
                "tenant-route-authorization-v1.schema.json",
                "tenant-route-authorization-v1.fixture.json",
            }.issubset({item["key"] for item in volume["configMap"]["items"]})
        )


if __name__ == "__main__":
    unittest.main()
