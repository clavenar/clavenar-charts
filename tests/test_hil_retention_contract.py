import hashlib
import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = CHART / "files" / "hil-retention-v1.schema.json"
FIXTURE = CHART / "files" / "hil-retention-v1.fixture.json"


def render() -> list[dict]:
    output = subprocess.run(
        ["helm", "template", "smoke", str(CHART)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [
        item
        for item in yaml.safe_load_all(output)
        if isinstance(item, dict)
    ]


class HilRetentionContractChartTests(unittest.TestCase):
    def test_packaged_contract_validates_and_stays_d08_bounded(self) -> None:
        schema = json.loads(SCHEMA.read_text())
        fixture = json.loads(FIXTURE.read_text())
        jsonschema.Draft202012Validator(schema).validate(fixture)
        self.assertEqual(fixture["contract"], "clavenar.hil-retention/v1")
        self.assertEqual(
            max(tier["terminalPayloadRetentionSeconds"] for tier in fixture["tiers"]),
            fixture["d08Binding"]["maximumRetentionSeconds"],
        )
        self.assertFalse(fixture["protection"]["plaintextPersistence"])
        self.assertEqual(fixture["migration"]["maximumRowsPerTransaction"], 100)

    def test_hil_mounts_only_the_dedicated_external_key_file(self) -> None:
        deployment = next(
            item
            for item in render()
            if item.get("kind") == "Deployment"
            and item["metadata"]["name"] == "smoke-hil"
        )
        pod = deployment["spec"]["template"]
        container = pod["spec"]["containers"][0]
        env = {item["name"]: item for item in container["env"]}
        self.assertEqual(
            env["CLAVENAR_HIL_PAYLOAD_KEY_FILE"]["value"],
            "/run/secrets/hil-payload-key",
        )
        self.assertNotIn("CLAVENAR_HIL_PAYLOAD_KEY", env)
        mount = next(
            item for item in container["volumeMounts"]
            if item["name"] == "hil-payload-key"
        )
        self.assertEqual(mount["mountPath"], "/run/secrets/hil-payload-key")
        self.assertTrue(mount["readOnly"])
        volume = next(
            item for item in pod["spec"]["volumes"]
            if item["name"] == "hil-payload-key"
        )
        self.assertEqual(volume["secret"]["secretName"], "clavenar-hil-payload")
        self.assertEqual(
            volume["secret"]["items"],
            [{"key": "hil-payload-key", "path": "hil-payload-key"}],
        )
        self.assertEqual(
            pod["metadata"]["annotations"]["checksum/hil-retention-schema"],
            hashlib.sha256(SCHEMA.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            pod["metadata"]["annotations"]["checksum/hil-retention-fixture"],
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        )

    def test_hil_helper_owns_exact_contract_names(self) -> None:
        source = (CHART / "templates" / "hil.yaml").read_text()
        self.assertIn("CLAVENAR_HIL_PAYLOAD_KEY_FILE", source)
        self.assertIn("hil-payload-key", source)


if __name__ == "__main__":
    unittest.main()
