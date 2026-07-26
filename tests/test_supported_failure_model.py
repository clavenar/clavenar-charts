from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = CHART / "files" / "supported-failure-model-v1.schema.json"
FIXTURE = CHART / "files" / "supported-failure-model-v1.fixture.json"


def render() -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "failure-model", str(CHART)],
        check=True,
        capture_output=True,
        text=True,
    )
    return [item for item in yaml.safe_load_all(result.stdout) if isinstance(item, dict)]


class SupportedFailureModelChartTests(unittest.TestCase):
    def test_contract_validates_and_all_mirrors_are_exact(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(fixture)
        for mirror_root in (
            ROOT.parent / "clavenar-specs" / "contracts",
            ROOT.parent / "clavenar-e2e" / "contracts",
        ):
            if mirror_root.is_dir():
                self.assertEqual(
                    SCHEMA.read_bytes(), (mirror_root / SCHEMA.name).read_bytes()
                )
                self.assertEqual(
                    FIXTURE.read_bytes(), (mirror_root / FIXTURE.name).read_bytes()
                )

    def test_contract_is_packaged_immutably(self) -> None:
        configmap = next(
            item
            for item in render()
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "failure-model-supported-failure-model"
        )
        self.assertTrue(configmap["immutable"])
        self.assertEqual(SCHEMA.read_bytes(), configmap["data"][SCHEMA.name].encode())
        self.assertEqual(FIXTURE.read_bytes(), configmap["data"][FIXTURE.name].encode())

    def test_default_and_postgres_topologies_remain_single_writer(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        topologies = {row["id"]: row for row in fixture["topologies"]}
        self.assertEqual(1, topologies["helm-single-writer"]["writerCount"])
        self.assertEqual(1, topologies["staged-postgres-ledger"]["writerCount"])
        self.assertEqual("none", topologies["staged-postgres-ledger"]["promotion"])
        self.assertIn("whole-stack-high-availability", fixture["nonClaims"])


if __name__ == "__main__":
    unittest.main()
