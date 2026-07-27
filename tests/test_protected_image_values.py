import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ProtectedImageValuesTests(unittest.TestCase):
    def test_every_governed_chart_image_accepts_an_exact_digest(self) -> None:
        values = yaml.safe_load(
            (ROOT / "charts/clavenar/values.yaml").read_text(encoding="utf-8")
        )
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


if __name__ == "__main__":
    unittest.main()
