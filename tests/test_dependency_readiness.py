from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_dependency_readiness.py"
SPEC = importlib.util.spec_from_file_location("dependency_readiness_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


class DependencyReadinessChartTests(unittest.TestCase):
    def test_complete_helm_projection_passes(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--source-root",
                str(ROOT.parent),
                "--require-source",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("298 Helm evidence checks", result.stdout)

    def test_public_contract_mirror_is_byte_identical(self) -> None:
        specs = ROOT.parent / "clavenar-specs"
        if not specs.is_dir():
            specs = ROOT / "clavenar-specs"
        for name in (
            "dependency-readiness-v1.schema.json",
            "dependency-readiness-v1.fixture.json",
        ):
            self.assertEqual(
                (ROOT / "charts/clavenar/files" / name).read_bytes(),
                (specs / "contracts" / name).read_bytes(),
            )

    def test_enabled_service_cannot_drop_required_dependency(self) -> None:
        result = subprocess.run(
            [
                "helm",
                "template",
                "readiness",
                str(ROOT / "charts/clavenar"),
                "--set",
                "services.assurance.enabled=false",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "services.console.enabled requires readiness dependency "
            "services.assurance.enabled",
            result.stderr,
        )

    def test_optional_simulator_url_must_be_uniform_readyz(self) -> None:
        result = subprocess.run(
            [
                "helm",
                "template",
                "readiness",
                str(ROOT / "charts/clavenar"),
                "--set",
                "services.console.simulatorReadinessUrl=http://simulator:9200/health",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("simulatorReadinessUrl", result.stderr)


if __name__ == "__main__":
    unittest.main()
