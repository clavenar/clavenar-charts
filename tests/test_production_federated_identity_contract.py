import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
FILES = (
    "production-federated-identity-v1.schema.json",
    "production-federated-identity-v1.fixture.json",
)


class ProductionFederatedIdentityChartTests(unittest.TestCase):
    def test_contract_validates_and_is_packaged_immutably(self) -> None:
        schema = json.loads((CHART / "files" / FILES[0]).read_text())
        fixture = json.loads((CHART / "files" / FILES[1]).read_text())
        jsonschema.Draft202012Validator(schema).validate(fixture)
        self.assertTrue(fixture["saml"]["responseOrAssertionSignatureRequired"])
        self.assertEqual(1, fixture["oidc"]["jwksRefreshAttemptsAfterVerifyFailure"])

        output = subprocess.run(
            ["helm", "template", "smoke", str(CHART)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        resources = [
            item for item in yaml.safe_load_all(output) if isinstance(item, dict)
        ]
        configmap = next(
            item
            for item in resources
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"] == "smoke-federated-identity-contract"
        )
        self.assertTrue(configmap["immutable"])
        for name in FILES:
            self.assertEqual(
                (CHART / "files" / name).read_bytes(),
                configmap["data"][name].encode(),
            )

        console = next(
            item
            for item in resources
            if item.get("kind") == "Deployment"
            and item["metadata"]["name"] == "smoke-console"
        )
        container = console["spec"]["template"]["spec"]["containers"][0]
        mount = next(
            row
            for row in container["volumeMounts"]
            if row["name"] == "federated-identity-contract"
        )
        self.assertEqual("/etc/clavenar/federated-identity", mount["mountPath"])

    def test_public_mirrors_are_exact(self) -> None:
        public = ROOT.parent / "clavenar-specs" / "contracts"
        if not public.is_dir():
            self.skipTest("assembled public specification is not present")
        for name in FILES:
            self.assertEqual(
                (CHART / "files" / name).read_bytes(),
                (public / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
