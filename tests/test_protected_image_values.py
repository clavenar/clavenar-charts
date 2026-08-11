import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ProtectedImageValuesTests(unittest.TestCase):
    def test_release_metadata_accepts_dev_rc_and_rejects_invalid_identity(
        self,
    ) -> None:
        result = subprocess.run(
            [
                "helm",
                "template",
                "dev",
                str(ROOT / "charts/clavenar"),
                "--set-string",
                "stackRelease=1.250.2rc3",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        documents = [
            document
            for document in yaml.safe_load_all(result.stdout)
            if isinstance(document, dict)
        ]
        console = next(
            document
            for document in documents
            if document.get("kind") == "Deployment"
            and document.get("metadata", {}).get("name") == "dev-console"
        )
        console_env = {
            entry["name"]: entry.get("value")
            for entry in console["spec"]["template"]["spec"]["containers"][0][
                "env"
            ]
        }
        self.assertEqual(
            "1.250.2rc3", console_env["CLAVENAR_CONSOLE_RELEASE_VERSION"]
        )
        self.assertEqual(
            "1.250.2rc3",
            console["metadata"]["labels"]["app.kubernetes.io/version"],
        )

        invalid = subprocess.run(
            [
                "helm",
                "template",
                "dev",
                str(ROOT / "charts/clavenar"),
                "--set-string",
                "stackRelease=1.250",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(0, invalid.returncode)
        self.assertIn("stackRelease", invalid.stderr)

    def test_every_governed_chart_image_accepts_an_exact_digest(self) -> None:
        values = yaml.safe_load(
            (ROOT / "charts/clavenar/values.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual("", values["stackRelease"])
        services = {
            "proxy",
            "brain",
            "policyEngine",
            "ledger",
            "hil",
            "identity",
            "deepReview",
            "assurance",
            "console",
        }
        for service in services:
            self.assertEqual(values["services"][service]["image"]["digest"], "")
        self.assertEqual(values["upstreamStub"]["image"]["digest"], "")
        self.assertEqual(values["exec"]["image"]["digest"], "")
        self.assertEqual(values["exec"]["image"]["tag"], "")

    def test_digest_precedes_every_legacy_tag_fallback(self) -> None:
        helpers = (
            ROOT / "charts/clavenar/templates/_helpers.tpl"
        ).read_text(encoding="utf-8")
        digest = helpers.index('$digest := default "" $svcCfg.image.digest')
        tag = helpers.index("$tag := default $ctx.Values.imageTag")
        self.assertLess(digest, tag)
        self.assertIn('printf "%s/%s@%s" $registry $repo $digest', helpers)
        self.assertIn(
            '^sha256:[a-f0-9]{64}$',
            helpers,
        )

    def test_upstream_stub_uses_the_same_exact_digest_boundary(self) -> None:
        template = (
            ROOT / "charts/clavenar/templates/upstream-stub.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("$digest := default", template)
        self.assertIn("@{{ $digest }}", template)
        self.assertIn("^sha256:[a-f0-9]{64}$", template)

    def test_exec_uses_the_shared_digest_precedence_helper(self) -> None:
        template = (
            ROOT / "charts/clavenar/templates/exec.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'include "clavenar.imageRef" (dict "ctx" . "svcCfg" .Values.exec)',
            template,
        )
        self.assertNotIn(
            ".Values.exec.image.repository }}@{{ .Values.exec.image.digest",
            template,
        )

    def test_production_requires_and_renders_every_governed_digest(self) -> None:
        values_path = ROOT / "tests/values-production.yaml"
        values = yaml.safe_load(values_path.read_text(encoding="utf-8"))
        services = values["services"]
        self.assertEqual(
            {
                "proxy",
                "brain",
                "policyEngine",
                "ledger",
                "hil",
                "identity",
                "deepReview",
                "assurance",
                "console",
            },
            {
                name
                for name, config in services.items()
                if config.get("image", {}).get("digest")
            },
        )
        output = subprocess.run(
            [
                "helm",
                "template",
                "production",
                str(ROOT / "charts/clavenar"),
                "-f",
                str(values_path),
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        documents = [
            document
            for document in yaml.safe_load_all(output)
            if isinstance(document, dict)
        ]
        images = {
            container["image"]
            for document in documents
            if document.get("kind") == "Deployment"
            and document.get("metadata", {}).get(
                "name", ""
            ).startswith("production-")
            for container in document.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
            if container.get("name") in {
                "proxy",
                "brain",
                "policy-engine",
                "ledger",
                "hil",
                "identity",
                "deep-review",
                "assurance",
                "console",
            }
        }
        self.assertEqual(9, len(images))
        self.assertTrue(all("@sha256:" in image for image in images))

        console = next(
            document
            for document in documents
            if document.get("kind") == "Deployment"
            and document.get("metadata", {}).get("name") == "production-console"
        )
        console_env = {
            entry["name"]: entry.get("value")
            for entry in console["spec"]["template"]["spec"]["containers"][0][
                "env"
            ]
        }
        self.assertEqual(
            values["stackRelease"],
            console_env["CLAVENAR_CONSOLE_RELEASE_VERSION"],
        )
        self.assertEqual(
            values["stackRelease"],
            console["metadata"]["labels"]["app.kubernetes.io/version"],
        )

        missing_release = subprocess.run(
            [
                "helm",
                "template",
                "production",
                str(ROOT / "charts/clavenar"),
                "-f",
                str(values_path),
                "--set-string",
                "stackRelease=",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(0, missing_release.returncode)
        self.assertIn(
            "stackRelease is required when protected service image digests are configured",
            missing_release.stderr,
        )

        mutations = {
            "missing": "",
            "zero": "sha256:" + "0" * 64,
        }
        for name, digest in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                mutation = Path(directory) / "invalid-digest.yaml"
                mutation.write_text(
                    "services:\n  proxy:\n    image:\n"
                    f"      digest: '{digest}'\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [
                        "helm",
                        "template",
                        "production",
                        str(ROOT / "charts/clavenar"),
                        "-f",
                        str(values_path),
                        "-f",
                        str(mutation),
                    ],
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("services.proxy.image.digest", result.stderr)


if __name__ == "__main__":
    unittest.main()
