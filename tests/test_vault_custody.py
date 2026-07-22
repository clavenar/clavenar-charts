import pathlib
import shutil
import subprocess
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/clavenar"


@unittest.skipUnless(shutil.which("helm"), "requires helm")
class VaultCustodyRenderTests(unittest.TestCase):
    def render(self, *extra: str) -> list[dict]:
        command = [
            "helm",
            "template",
            "custody",
            str(CHART),
            "-f",
            str(ROOT / "tests/values-production.yaml"),
            *extra,
        ]
        result = subprocess.run(command, text=True, capture_output=True, check=True)
        return [item for item in yaml.safe_load_all(result.stdout) if item]

    def test_production_projects_distinct_scoped_token_files(self):
        rendered = self.render()
        deployments = {
            item["metadata"]["name"]: item
            for item in rendered
            if item.get("kind") == "Deployment"
        }
        for service, expected_key in (
            ("identity", "identity-token"),
            ("proxy", "proxy-token"),
        ):
            pod = deployments[f"custody-{service}"]["spec"]["template"]["spec"]
            container = pod["containers"][0]
            env = {item["name"]: item for item in container["env"]}
            self.assertNotIn("VAULT_TOKEN", env)
            self.assertEqual(
                env["VAULT_TOKEN_FILE"]["value"], "/run/secrets/vault-token"
            )
            volume = next(v for v in pod["volumes"] if v["name"] == "vault-token")
            secret = volume["secret"]
            self.assertEqual(secret["secretName"], "clavenar-vault-token")
            self.assertEqual(secret["defaultMode"], 0o440)
            self.assertEqual(
                secret["items"], [{"key": expected_key, "path": "vault-token"}]
            )

    def test_production_rejects_a_shared_token_key(self):
        command = [
            "helm",
            "template",
            "custody",
            str(CHART),
            "-f",
            str(ROOT / "tests/values-production.yaml"),
            "--set",
            "vault.proxyTokenKey=identity-token",
        ]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires distinct Identity and Proxy Vault token keys", result.stderr)


if __name__ == "__main__":
    unittest.main()
