from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-nats-authorization.py"
SPEC = importlib.util.spec_from_file_location("nats_authorization", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class NatsAuthorizationTests(unittest.TestCase):
    def test_exact_bundled_contract_passes(self) -> None:
        self.assertEqual(gate.check_repository(), {"users": 8, "chartClients": 7})

    def test_values_permission_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            chart = Path(directory) / "clavenar"
            shutil.copytree(ROOT / "charts" / "clavenar", chart)
            path = chart / "values.yaml"
            values = yaml.safe_load(path.read_text())
            values["nats"]["config"]["merge"]["authorization"]["users"][0][
                "permissions"
            ]["publish"]["allow"].append(">")
            path.write_text(yaml.safe_dump(values, sort_keys=False))
            with self.assertRaisesRegex(gate.ContractError, "generated v1 permissions"):
                gate.check_repository(chart=chart)

    def test_bundled_identity_blind_tls_fails_render(self) -> None:
        result = subprocess.run(
            [
                "helm",
                "template",
                "smoke",
                str(ROOT / "charts" / "clavenar"),
                "-f",
                str(ROOT / "tests" / "values-bundled.yaml"),
                "--set",
                "nats.config.nats.tls.merge.verify=true",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity-blind verify", result.stderr)

    def test_bundled_ephemeral_jetstream_fails_render(self) -> None:
        result = subprocess.run(
            [
                "helm",
                "template",
                "smoke",
                str(ROOT / "charts" / "clavenar"),
                "-f",
                str(ROOT / "tests" / "values-bundled.yaml"),
                "--set",
                "nats.config.jetstream.fileStore.pvc.enabled=false",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("persistent file-store PVC", result.stderr)

    def test_external_production_requires_operator_declaration(self) -> None:
        result = subprocess.run(
            [
                "helm",
                "template",
                "smoke",
                str(ROOT / "charts" / "clavenar"),
                "-f",
                str(ROOT / "tests" / "values-production.yaml"),
                "--set-string",
                "nats.external.operator=",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nats.external.operator", result.stderr)


if __name__ == "__main__":
    unittest.main()
