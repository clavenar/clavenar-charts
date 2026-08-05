from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = CHART / "files" / "residual-product-disposition-v1.schema.json"
FIXTURE = CHART / "files" / "residual-product-disposition-v1.fixture.json"


def render() -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "residual-product", str(CHART)],
        check=True,
        capture_output=True,
        text=True,
    )
    return [item for item in yaml.safe_load_all(result.stdout) if isinstance(item, dict)]


class ResidualProductDispositionChartTests(unittest.TestCase):
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
        digest = hashlib.sha256(
            SCHEMA.read_bytes() + b"\n" + FIXTURE.read_bytes()
        ).hexdigest()[:12]
        configmap = next(
            item
            for item in render()
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"]
            == f"residual-product-residual-product-disposition-{digest}"
        )
        self.assertTrue(configmap["immutable"])
        self.assertEqual(SCHEMA.read_bytes(), configmap["data"][SCHEMA.name].encode())
        self.assertEqual(FIXTURE.read_bytes(), configmap["data"][FIXTURE.name].encode())

    def test_exact_nine_rows_and_deferred_boundaries(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        dispositions = {row["id"]: row for row in fixture["dispositions"]}
        self.assertEqual(9, len(dispositions))
        self.assertEqual(1, fixture["shippedCount"])
        self.assertEqual(8, fixture["deferredCount"])
        self.assertEqual(
            "shipped",
            dispositions["sandbox-severity-adversarial-corpus"]["status"],
        )
        for row_id, row in dispositions.items():
            if row_id != "sandbox-severity-adversarial-corpus":
                self.assertEqual("deferred", row["status"])
                self.assertTrue(row["promotionRequirements"])
                self.assertFalse(row["evidence"])


if __name__ == "__main__":
    unittest.main()
