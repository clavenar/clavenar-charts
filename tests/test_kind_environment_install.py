import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "kind-environment-install.sh"
CRDS = ROOT / "tests" / "kind-environment-crds.yaml"


class KindEnvironmentInstallTests(unittest.TestCase):
    def test_crd_fixture_has_exact_overlay_and_monitoring_inventory(self):
        documents = list(yaml.safe_load_all(CRDS.read_text()))
        actual = {
            (
                item["spec"]["group"],
                item["spec"]["names"]["kind"],
                item["spec"]["scope"],
                item["spec"]["versions"][0]["name"],
            )
            for item in documents
        }
        expected = {
            ("cert-manager.io", "Certificate", "Namespaced", "v1"),
            ("cert-manager.io", "ClusterIssuer", "Cluster", "v1"),
            ("traefik.io", "IngressRoute", "Namespaced", "v1alpha1"),
            ("traefik.io", "IngressRouteTCP", "Namespaced", "v1alpha1"),
            ("traefik.io", "Middleware", "Namespaced", "v1alpha1"),
            ("monitoring.coreos.com", "PrometheusRule", "Namespaced", "v1"),
            (
                "monitoring.coreos.com",
                "AlertmanagerConfig",
                "Namespaced",
                "v1alpha1",
            ),
        }
        self.assertEqual(actual, expected)

    def test_script_pins_cluster_and_all_cross_repository_inputs(self):
        text = SCRIPT.read_text()
        self.assertIn(
            "kindest/node:v1.30.8@sha256:"
            "17cd608b3971338d9180b00776cb766c50d0a0b6b904ab4ff52fd3fc5c6369bf",
            text,
        )
        for relative_path in (
            "k8s/charts/clavenar-env",
            "k8s/${environment}/clavenar-values.yaml",
            "k8s/tests/${environment}-core-runtime-values.yaml",
            "k8s/${environment}/env-values.yaml",
            "k8s/tests/${environment}-runtime-values.yaml",
        ):
            self.assertIn(relative_path, text)
        self.assertIn('kind delete cluster --name "$cluster_name"', text)

    def test_script_server_validates_hooks_then_installs_both_releases(self):
        text = SCRIPT.read_text()
        self.assertEqual(text.count("apply --dry-run=server"), 2)
        self.assertEqual(text.count('helm install "$'), 2)
        self.assertEqual(text.count("--no-hooks"), 2)
        self.assertIn('helm status "$release"', text)
        self.assertIn('[[ "$environment" == prod ]]', text)

if __name__ == "__main__":
    unittest.main()
