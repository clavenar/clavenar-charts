import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCRIPTS = CHART / "files" / "tls-rotation"
MOCK_BIN = ROOT / "tests" / "fixtures" / "tls-rotation-bin"
TLS_ANNOTATION_PREFIX = "clavenar.com/tls-"


class TlsRotationScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.kube_state = self.root / "kube.json"
        self.events = self.root / "events.jsonl"
        self.reset_workspace()

    def tearDown(self):
        self.temporary.cleanup()

    def reset_workspace(self):
        self.state_dir = self.root / "state"
        self.work_dir = self.root / "work"
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.work_dir, ignore_errors=True)
        self.state_dir.mkdir()
        self.work_dir.mkdir()

    def environment(self, **changes):
        values = {
            "PATH": f"{MOCK_BIN}:{os.environ['PATH']}",
            "MOCK_KUBE_STATE": str(self.kube_state),
            "MOCK_KUBE_EVENTS": str(self.events),
            "MOCK_CONTROLLERS": "smoke-proxy smoke-brain smoke-ledger",
            "STATE_DIR": str(self.state_dir),
            "WORK_DIR": str(self.work_dir),
            "TLS_SECRET_NAME": "clavenar-certs",
            "POD_NAMESPACE": "test",
            "EXPECTED_SAN_SCHEME": "release-prefixed-v4-additional-dns",
            "SPIFFE_TRUST_DOMAIN": "clavenar.local",
            "BUNDLE_SERVICES": "proxy brain",
            "RELEASE_NAME": "smoke",
            "PROXY_SERVER_ADDITIONAL_DNS_NAMES": "",
            "CONSOLE_ADDITIONAL_DNS_NAMES": "",
            "IDENTITY_ADDITIONAL_DNS_NAMES": "",
            "NATS_ADDITIONAL_DNS_NAMES": "",
            "TLS_ROTATION_OPERATION": "reconcile",
            "TLS_ROTATION_GENERATION": "generation-1",
            "TLS_ROTATION_REASON": "none",
            "EXPIRY_WINDOW_SECONDS": "2592000",
            "OVERLAP_SECONDS": "900",
            "ROLLOUT_TIMEOUT_SECONDS": "300",
            "ROLLOUT_DEPLOYMENTS": "proxy brain",
            "ROLLOUT_STATEFULSETS": "",
        }
        values.update(changes)
        return {**os.environ, **values}

    def run_script(self, name, environment, check=True):
        return subprocess.run(
            ["/bin/sh", str(SCRIPTS / name)],
            env=environment,
            text=True,
            capture_output=True,
            check=check,
        )

    def transaction(self, environment, apply=True):
        self.run_script("snapshot.sh", environment)
        self.run_script("mint.sh", environment)
        if apply:
            return self.run_script("apply.sh", environment)
        return None

    def kube(self):
        return json.loads(self.kube_state.read_text())

    def active(self):
        return self.kube()["secrets"]["clavenar-certs"]

    def decoded(self, value, key):
        return base64.b64decode(value["data"][key])

    def clear_events(self):
        self.events.write_text("")

    def event_text(self):
        return self.events.read_text() if self.events.exists() else ""

    def test_initialize_noop_migration_and_membership_rotation(self):
        initial_env = self.environment()
        self.transaction(initial_env)
        original = copy.deepcopy(self.active())
        self.assertEqual(
            "generation-1",
            original["metadata"]["annotations"]["clavenar.com/tls-generation"],
        )

        self.reset_workspace()
        self.clear_events()
        self.transaction(initial_env)
        self.assertEqual(original, self.active())
        self.assertNotIn('"apply"', self.event_text())
        self.assertNotIn('"annotate"', self.event_text())

        kube = self.kube()
        legacy = kube["secrets"]["clavenar-certs"]
        legacy["metadata"]["annotations"] = {
            key: value for key, value in legacy["metadata"]["annotations"].items()
            if not key.startswith(TLS_ANNOTATION_PREFIX)
        }
        self.kube_state.write_text(json.dumps(kube))
        legacy_data = copy.deepcopy(legacy["data"])
        self.reset_workspace()
        migrated_env = self.environment(TLS_ROTATION_GENERATION="migrated-1")
        self.transaction(migrated_env)
        self.assertEqual(legacy_data, self.active()["data"])
        self.assertEqual(
            "migrated-1",
            self.active()["metadata"]["annotations"]["clavenar.com/tls-generation"],
        )

        old_client = self.decoded(self.active(), "client.crt")
        self.reset_workspace()
        self.clear_events()
        rotate_env = self.environment(
            BUNDLE_SERVICES="proxy brain ledger",
            ROLLOUT_DEPLOYMENTS="proxy brain ledger",
            TLS_ROTATION_OPERATION="rotate",
            TLS_ROTATION_GENERATION="generation-2",
            TLS_ROTATION_REASON="membership",
        )
        self.transaction(rotate_env, apply=False)
        self.assertEqual(
            2,
            (self.state_dir / "dual-ca.crt").read_text().count(
                "-----BEGIN CERTIFICATE-----"
            ),
        )
        dual_ca = self.state_dir / "dual-ca.crt"
        for certificate in (
            self.state_dir / "previous" / "client.crt",
            self.work_dir / "new" / "service-ledger.crt",
        ):
            self.assertEqual(0, subprocess.run(
                ["openssl", "verify", "-CAfile", dual_ca, certificate],
                capture_output=True,
            ).returncode)
        self.run_script("apply.sh", rotate_env)
        active = self.active()
        annotations = active["metadata"]["annotations"]
        self.assertEqual("stable", annotations["clavenar.com/tls-state"])
        self.assertEqual("generation-2", annotations["clavenar.com/tls-generation"])
        self.assertIn("service-ledger.key", active["data"])
        event_text = self.event_text()
        for phase in ("overlap-old", "overlap-new", "retiring", "stable"):
            self.assertIn(f"clavenar.com/tls-state={phase}", event_text)
        self.assertEqual("ready", annotations["clavenar.com/tls-readiness"])
        self.assertEqual("false", annotations["clavenar.com/tls-rollback-available"])
        self.assertIn("clavenar.io/tls-secret-digest", event_text)

        histories = [
            value for name, value in self.kube()["secrets"].items()
            if "-history-" in name
        ]
        self.assertEqual(1, len(histories))
        self.assertEqual({"ca.crt"}, set(histories[0]["data"]))
        old_ca = self.decoded(histories[0], "ca.crt")
        new_ca = self.decoded(active, "ca.crt")
        old_cert_path = self.root / "old-client.crt"
        old_ca_path = self.root / "old-ca.crt"
        new_ca_path = self.root / "new-ca.crt"
        old_cert_path.write_bytes(old_client)
        old_ca_path.write_bytes(old_ca)
        new_ca_path.write_bytes(new_ca)
        self.assertEqual(0, subprocess.run(
            ["openssl", "verify", "-CAfile", old_ca_path, old_cert_path],
            capture_output=True,
        ).returncode)
        self.assertNotEqual(0, subprocess.run(
            ["openssl", "verify", "-CAfile", new_ca_path, old_cert_path],
            capture_output=True,
        ).returncode)

    def test_failed_overlap_rolls_back_exact_secret_data(self):
        self.transaction(self.environment())
        prior = copy.deepcopy(self.active())
        self.reset_workspace()
        rotate_env = self.environment(
            BUNDLE_SERVICES="proxy brain ledger",
            ROLLOUT_DEPLOYMENTS="proxy brain ledger",
            TLS_ROTATION_OPERATION="rotate",
            TLS_ROTATION_GENERATION="generation-2",
            TLS_ROTATION_REASON="membership",
            MOCK_FAIL_ROLLOUT_TOKEN="generation-2-overlap-new",
        )
        self.run_script("snapshot.sh", rotate_env)
        self.run_script("mint.sh", rotate_env)
        result = self.run_script("apply.sh", rotate_env, check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(prior["data"], self.active()["data"])
        annotations = self.active()["metadata"]["annotations"]
        self.assertEqual("stable", annotations["clavenar.com/tls-state"])
        self.assertEqual("generation-1", annotations["clavenar.com/tls-generation"])
        self.assertIn("rollback-old-dual", self.event_text())

    def test_expiry_rotation_requires_due_certificate_and_unchanged_membership(self):
        short_env = self.environment(
            CERT_VALIDITY_DAYS="1",
            EXPIRY_WINDOW_SECONDS="172800",
        )
        self.transaction(short_env)
        old_ca = self.decoded(self.active(), "ca.crt")
        self.reset_workspace()
        rotate_env = self.environment(
            TLS_ROTATION_OPERATION="rotate",
            TLS_ROTATION_GENERATION="generation-2",
            TLS_ROTATION_REASON="expiry",
            EXPIRY_WINDOW_SECONDS="172800",
        )
        self.transaction(rotate_env)
        self.assertNotEqual(old_ca, self.decoded(self.active(), "ca.crt"))
        self.assertEqual(
            "expiry",
            self.active()["metadata"]["annotations"]["clavenar.com/tls-rotation-reason"],
        )

        self.reset_workspace()
        premature = self.environment(
            TLS_ROTATION_OPERATION="rotate",
            TLS_ROTATION_GENERATION="generation-3",
            TLS_ROTATION_REASON="expiry",
            EXPIRY_WINDOW_SECONDS="1",
        )
        self.run_script("snapshot.sh", premature)
        result = self.run_script("mint.sh", premature, check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("outside the configured renewal window", result.stderr)

    def test_foreign_secret_member_and_recorded_digest_fail_closed(self):
        self.transaction(self.environment())
        kube = self.kube()
        malformed = kube["secrets"]["clavenar-certs"]
        malformed["data"]["foreign.key"] = base64.b64encode(b"bad").decode()
        self.kube_state.write_text(json.dumps(kube))
        self.reset_workspace()
        result = self.run_script("snapshot.sh", self.environment(), check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("missing or foreign", result.stderr)

        malformed["data"].pop("foreign.key")
        self.kube_state.write_text(json.dumps(kube))
        self.reset_workspace()
        self.run_script("snapshot.sh", self.environment())
        (self.state_dir / "previous" / "ca.key").chmod(0o640)
        result = self.run_script("mint.sh", self.environment(), check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("owner-readable only", result.stderr)

        malformed["metadata"]["annotations"]["clavenar.com/tls-ca-sha256"] = (
            "sha256:" + "0" * 64
        )
        self.kube_state.write_text(json.dumps(kube))
        self.reset_workspace()
        self.run_script("snapshot.sh", self.environment())
        result = self.run_script("mint.sh", self.environment(), check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("recorded CA digest", result.stderr)

    def test_additional_dns_names_are_exact_and_fail_closed(self):
        environment = self.environment(
            BUNDLE_SERVICES="proxy console identity nats",
            PROXY_SERVER_ADDITIONAL_DNS_NAMES=(
                "mcp.dev.clavenar.ai "
                "clavenar-dev-proxy.clavenar.svc.cluster.local"
            ),
            CONSOLE_ADDITIONAL_DNS_NAMES="console.dev.clavenar.ai",
            IDENTITY_ADDITIONAL_DNS_NAMES=(
                "clavenar-dev-identity.clavenar.svc.cluster.local"
            ),
            NATS_ADDITIONAL_DNS_NAMES=(
                "clavenar-dev-nats.clavenar.svc.cluster.local"
            ),
        )
        self.transaction(environment)
        active = self.active()
        expected = {
            "server.crt": {
                "DNS:localhost",
                "DNS:proxy",
                "DNS:proxy.clavenar.local",
                "DNS:mcp.dev.clavenar.ai",
                "DNS:clavenar-dev-proxy.clavenar.svc.cluster.local",
            },
            "service-console.crt": {
                "URI:spiffe://clavenar.local/service/console",
                "DNS:console",
                "DNS:smoke-console",
                "DNS:localhost",
                "DNS:console.dev.clavenar.ai",
            },
            "service-identity.crt": {
                "URI:spiffe://clavenar.local/service/identity",
                "DNS:identity",
                "DNS:smoke-identity",
                "DNS:localhost",
                "DNS:clavenar-dev-identity.clavenar.svc.cluster.local",
            },
            "service-nats.crt": {
                "URI:spiffe://clavenar.local/service/nats",
                "DNS:nats",
                "DNS:smoke-nats",
                "DNS:localhost",
                "DNS:clavenar-dev-nats.clavenar.svc.cluster.local",
            },
        }
        for name, expected_sans in expected.items():
            certificate = self.root / name
            certificate.write_bytes(self.decoded(active, name))
            rendered = subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-in",
                    certificate,
                    "-noout",
                    "-ext",
                    "subjectAltName",
                ],
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            actual = {
                item.strip()
                for line in rendered.splitlines()[1:]
                for item in line.split(",")
            }
            self.assertEqual(expected_sans, actual)

        invalid = self.environment(
            PROXY_SERVER_ADDITIONAL_DNS_NAMES="valid.example INVALID.example",
        )
        self.reset_workspace()
        result = self.run_script("mint.sh", invalid, check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("noncanonical DNS name", result.stderr)


class TlsRotationRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.run(
            ["helm", "template", "smoke", str(CHART),
             "-f", str(ROOT / "tests" / "values-bundled.yaml")],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        cls.documents = [document for document in yaml.safe_load_all(rendered) if document]

    def resource(self, kind, name):
        return next(
            document for document in self.documents
            if document.get("kind") == kind
            and document.get("metadata", {}).get("name") == name
        )

    def test_rendered_transaction_is_explicit_and_memory_backed(self):
        job = self.resource("Job", "smoke-tls-automint")
        pod = job["spec"]["template"]["spec"]
        self.assertEqual(
            ["snapshot", "mint"],
            [container["name"] for container in pod["initContainers"]],
        )
        self.assertEqual(["apply"], [container["name"] for container in pod["containers"]])
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        self.assertEqual("Memory", volumes["state"]["emptyDir"]["medium"])
        self.assertEqual("Memory", volumes["work"]["emptyDir"]["medium"])
        apply_env = {
            item["name"]: item["value"]
            for item in pod["containers"][0]["env"]
        }
        self.assertEqual("reconcile", apply_env["TLS_ROTATION_OPERATION"])
        self.assertEqual("bootstrap-v1", apply_env["TLS_ROTATION_GENERATION"])
        self.assertIn("assurance", apply_env["ROLLOUT_DEPLOYMENTS"].split())
        mint = next(
            container
            for container in pod["initContainers"]
            if container["name"] == "mint"
        )
        mint_env = {item["name"]: item["value"] for item in mint["env"]}
        self.assertIn("PROXY_SERVER_ADDITIONAL_DNS_NAMES", mint_env)
        self.assertIn("CONSOLE_ADDITIONAL_DNS_NAMES", mint_env)
        self.assertIn("IDENTITY_ADDITIONAL_DNS_NAMES", mint_env)
        self.assertIn("NATS_ADDITIONAL_DNS_NAMES", mint_env)

        role = self.resource("Role", "smoke-tls-automint")
        rules = {(tuple(rule["apiGroups"]), tuple(rule["resources"])): set(rule["verbs"])
                 for rule in role["rules"]}
        self.assertEqual(
            {"get", "create", "patch", "update"},
            rules[(('',), ('secrets',))],
        )
        self.assertEqual(
            {"get", "list", "watch", "patch"},
            rules[(('apps',), ('deployments', 'statefulsets'))],
        )

        deployment = self.resource("Deployment", "smoke-proxy")
        annotations = deployment["spec"]["template"]["metadata"]["annotations"]
        self.assertEqual("bootstrap-v1", annotations["clavenar.io/tls-generation"])
        self.assertNotIn("checksum/tls-automint-script", annotations)
        apply_script = self.resource("ConfigMap", "smoke-tls-automint")["data"]["apply.sh"]
        self.assertIn("clavenar.io/tls-secret-digest", apply_script)
        self.assertIn("clavenar.com/tls-rollback-available", apply_script)

    def test_default_bundle_includes_external_peer_identities(self):
        job = self.resource("Job", "smoke-tls-automint")
        pod = job["spec"]["template"]["spec"]
        mint = next(
            container
            for container in pod["initContainers"]
            if container["name"] == "mint"
        )
        bundle_services = next(
            item["value"]
            for item in mint["env"]
            if item["name"] == "BUNDLE_SERVICES"
        ).split()

        for service in ("website", "demo-mint", "simulator"):
            with self.subTest(service=service):
                self.assertIn(service, bundle_services)
        self.assertEqual(len(bundle_services), len(set(bundle_services)))

    def test_invalid_rotation_policy_is_rejected(self):
        cases = (
            ["--set", "tlsBundle.rotation.reason=expiry"],
            ["--set", "tlsBundle.rotation.operation=rotate",
             "--set", "tlsBundle.rotation.reason=none"],
            ["--set", "tlsBundle.rotation.overlapSeconds=300"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    ["helm", "template", "smoke", str(CHART),
                     "-f", str(ROOT / "tests" / "values-bundled.yaml"), *arguments],
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
