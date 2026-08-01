from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SOURCE_VALUES = ROOT / "tests" / "values-bundled.yaml"
PACKAGED_VALUES = CHART / "examples" / "values-bundled.yaml"
GOVERNED_COMPONENTS = {
    "assurance",
    "brain",
    "console",
    "deep-review",
    "hil",
    "identity",
    "ledger",
    "policy-engine",
    "proxy",
    "upstream-stub",
}
SERVICE_KEYS = {
    "assurance": "assurance",
    "brain": "brain",
    "console": "console",
    "deep-review": "deepReview",
    "hil": "hil",
    "identity": "identity",
    "ledger": "ledger",
    "policy-engine": "policyEngine",
    "proxy": "proxy",
}


class PublicBundledInstallTests(unittest.TestCase):
    def test_packaged_values_are_byte_identical_and_exclude_exec(self) -> None:
        self.assertEqual(SOURCE_VALUES.read_bytes(), PACKAGED_VALUES.read_bytes())
        values = yaml.safe_load(PACKAGED_VALUES.read_text(encoding="utf-8"))
        self.assertTrue(values["nats"]["bundled"]["enabled"])
        self.assertTrue(values["vault"]["bundled"]["enabled"])
        self.assertTrue(values["tlsBundle"]["autoMint"])
        self.assertTrue(values["upstreamStub"]["enabled"])
        self.assertFalse(values["alerting"]["enabled"])
        self.assertTrue(values["hilPayloadProtection"]["managedEvaluation"])
        self.assertTrue(values["evaluationPublicTrust"]["enabled"])
        self.assertTrue(values["identityAlias"]["enabled"])
        self.assertFalse(values["exec"]["enabled"])
        self.assertNotIn("image", values["exec"])

    def test_public_recipe_renders_only_protected_clavenar_images(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            image_values = Path(work) / "images.yaml"
            image_values.write_text(
                "services:\n"
                + "".join(
                    f"  {key}:\n"
                    f"    image:\n"
                    f"      digest: sha256:{index:064x}\n"
                    for index, key in enumerate(
                        SERVICE_KEYS.values(), start=1
                    )
                )
                + "upstreamStub:\n"
                + "  image:\n"
                + f"    digest: sha256:{99:064x}\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "helm",
                    "template",
                    "external-install",
                    str(CHART),
                    "-f",
                    str(PACKAGED_VALUES),
                    "-f",
                    str(image_values),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        documents = [
            item
            for item in yaml.safe_load_all(result.stdout)
            if isinstance(item, dict) and item.get("kind") == "Deployment"
        ]
        components = {
            item["metadata"]["labels"]["app.kubernetes.io/component"]
            for item in documents
        }
        self.assertEqual(GOVERNED_COMPONENTS, components)
        shared_secret = next(
            item
            for item in yaml.safe_load_all(result.stdout)
            if isinstance(item, dict)
            and item.get("kind") == "Secret"
            and item.get("metadata", {}).get("name")
            == "external-install-shared-tokens"
        )
        self.assertRegex(
            shared_secret["data"]["hil-payload-key"],
            r"^[A-Za-z0-9+/]+={0,2}$",
        )
        hil = next(
            item
            for item in documents
            if item["metadata"]["name"] == "external-install-hil"
        )
        hil_payload_volume = next(
            volume
            for volume in hil["spec"]["template"]["spec"]["volumes"]
            if volume["name"] == "hil-payload-key"
        )
        self.assertEqual(
            "external-install-shared-tokens",
            hil_payload_volume["secret"]["secretName"],
        )
        identity = next(
            item
            for item in documents
            if item["metadata"]["name"] == "external-install-identity"
        )
        identity_env = {
            entry["name"]: entry.get("value")
            for entry in identity["spec"]["template"]["spec"]["containers"][0][
                "env"
            ]
        }
        self.assertEqual(
            {
                "spiffe://clavenar.local/service/proxy",
                "spiffe://clavenar.local/service/brain",
                "spiffe://clavenar.local/service/policy-engine",
                "spiffe://clavenar.local/service/ledger",
                "spiffe://clavenar.local/service/hil",
                "spiffe://clavenar.local/service/identity",
                "spiffe://clavenar.local/service/console",
            },
            set(
                identity_env[
                    "CLAVENAR_IDENTITY_WORKLOAD_ALLOWED_CALLERS"
                ].split(",")
            ),
        )
        self.assertEqual(
            "https://identity:8186/workload-svid",
            identity_env["CLAVENAR_IDENTITY_WORKLOAD_REFRESH_URL"],
        )
        self.assertEqual(
            "https://evaluation.invalid/",
            identity_env[
                "CLAVENAR_IDENTITY_OIDC_TENANT_EVALUATION_ISSUER"
            ],
        )
        self.assertEqual(
            "/etc/clavenar/public-trust/evaluation-oidc-jwks.json",
            identity_env[
                "CLAVENAR_IDENTITY_OIDC_TENANT_EVALUATION_RS256_JWKS_FILE"
            ],
        )
        identity_trust_volume = next(
            volume
            for volume in identity["spec"]["template"]["spec"]["volumes"]
            if volume["name"] == "attestation-trust-anchors"
        )
        self.assertEqual(
            "external-install-evaluation-public-trust",
            identity_trust_volume["configMap"]["name"],
        )
        public_trust = next(
            item
            for item in yaml.safe_load_all(result.stdout)
            if isinstance(item, dict)
            and item.get("kind") == "ConfigMap"
            and item.get("metadata", {}).get("name")
            == "external-install-evaluation-public-trust"
        )
        self.assertEqual(
            {
                "evaluation-oidc-jwks.json",
                "k8s-trust-anchors.json",
            },
            set(public_trust["data"]),
        )
        identity_alias = next(
            item
            for item in yaml.safe_load_all(result.stdout)
            if isinstance(item, dict)
            and item.get("kind") == "Service"
            and item.get("metadata", {}).get("name") == "identity"
        )
        self.assertEqual("ExternalName", identity_alias["spec"]["type"])
        self.assertEqual(
            "external-install-identity.default.svc.cluster.local",
            identity_alias["spec"]["externalName"],
        )
        vault_bootstrap = next(
            item
            for item in yaml.safe_load_all(result.stdout)
            if isinstance(item, dict)
            and item.get("kind") == "Job"
            and item.get("metadata", {}).get("name")
            == "external-install-vault-bootstrap"
        )
        bootstrap_script = vault_bootstrap["spec"]["template"]["spec"][
            "containers"
        ][0]["args"][0]
        self.assertIn(
            "vault write transit/keys/${TRANSIT_KEY_NAME} type=ed25519",
            bootstrap_script,
        )
        for document in documents:
            containers = document["spec"]["template"]["spec"]["containers"]
            for container in containers:
                self.assertRegex(
                    container["image"],
                    r"^ghcr\.io/clavenar/[a-z0-9-]+@sha256:[a-f0-9]{64}$",
                )

    def test_release_uses_exact_oci_bytes_for_asset_and_checksum(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "helm pull oci://ghcr.io/clavenar/charts/clavenar",
            workflow,
        )
        self.assertIn(
            'mv "$published/clavenar-$VERSION.tgz" '
            '"dist/clavenar-$VERSION.tgz"',
            workflow,
        )
        checksum = '(cd dist && sha256sum "clavenar-$VERSION.tgz")'
        self.assertIn(checksum, workflow)
        self.assertNotIn('sha256sum "dist/clavenar-$VERSION.tgz"', workflow)
        self.assertLess(workflow.index("helm pull"), workflow.index(checksum))
        self.assertLess(
            workflow.index(checksum),
            workflow.index('gh release upload "$tag" dist/* --clobber'),
        )


if __name__ == "__main__":
    unittest.main()
