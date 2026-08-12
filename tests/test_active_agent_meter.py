from __future__ import annotations

import json
from pathlib import Path
import unittest

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"


class ActiveAgentMeterChartTests(unittest.TestCase):
    def test_contract_fixture_validates(self) -> None:
        schema = json.loads(
            (CHART / "files/active-agent-meter-v1.schema.json").read_text()
        )
        fixture = json.loads(
            (CHART / "files/active-agent-meter-v1.fixture.json").read_text()
        )
        jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        ).validate(fixture)

    def test_ledger_rollout_binds_exact_contract_bytes(self) -> None:
        template = (CHART / "templates/services.yaml").read_text()
        self.assertIn("checksum/active-agent-meter-schema", template)
        self.assertIn("files/active-agent-meter-v1.schema.json", template)
        self.assertIn("checksum/active-agent-meter-fixture", template)
        self.assertIn("files/active-agent-meter-v1.fixture.json", template)

    def test_chart_minor_version_is_bumped(self) -> None:
        chart = (CHART / "Chart.yaml").read_text()
        self.assertIn("version: 0.39.2", chart)


if __name__ == "__main__":
    unittest.main()
