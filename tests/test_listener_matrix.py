#!/usr/bin/env python3
import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-listener-matrix.py"
SPEC = importlib.util.spec_from_file_location("listener_checker", SCRIPT)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

COMMON_GOVERNED_ENV = {
    "NATS_URL",
    "CLAVENAR_GRACEFUL_DRAIN_SECS",
}
NATS_TLS_ENV = {
    "NATS_TLS_CERT_PATH",
    "NATS_TLS_KEY_PATH",
    "NATS_TLS_CA_PATH",
}
CAPABILITY_ENV = {
    "CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE",
    "CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE_SHA256",
    "CLAVENAR_ENDPOINT_CAPABILITY_MATRIX_SHA256",
}
HIL_WEBAUTHN_CEREMONY_ENV = {
    "CLAVENAR_HIL_WEBAUTHN_TTL_SECS",
    "CLAVENAR_HIL_WEBAUTHN_ATTEMPT_LIMIT",
    "CLAVENAR_HIL_WEBAUTHN_RATE_WINDOW_SECS",
    "CLAVENAR_HIL_WEBAUTHN_SUBJECT_START_LIMIT",
    "CLAVENAR_HIL_WEBAUTHN_TENANT_START_LIMIT",
    "CLAVENAR_HIL_WEBAUTHN_SOURCE_START_LIMIT",
    "CLAVENAR_HIL_WEBAUTHN_DEPLOYMENT_START_LIMIT",
    "CLAVENAR_HIL_WEBAUTHN_SUBJECT_PENDING_LIMIT",
    "CLAVENAR_HIL_WEBAUTHN_TENANT_PENDING_LIMIT",
    "CLAVENAR_HIL_WEBAUTHN_SOURCE_PENDING_LIMIT",
    "CLAVENAR_HIL_WEBAUTHN_DEPLOYMENT_PENDING_LIMIT",
}
GOVERNED_ENV_BY_SERVICE = {
    "proxy": COMMON_GOVERNED_ENV | NATS_TLS_ENV | {
        "CLAVENAR_RUNTIME_ENVIRONMENT",
        "CLAVENAR_ATTESTATION_PROVIDER",
        "CLAVENAR_PROXY_HEALTH_ADDR",
        "CLAVENAR_BRAIN_URL",
        "CLAVENAR_POLICY_URL",
        "CLAVENAR_HIL_URL",
        "CLAVENAR_IDENTITY_URL",
        "CLAVENAR_PROXY_GRANT_JWKS_URL",
        "CLAVENAR_PROXY_GRANT_JWKS_REFRESH_SECS",
        "CLAVENAR_PROXY_GRANT_JWKS_MAX_STALENESS_SECS",
        "CLAVENAR_PROXY_GRANT_JWKS_FETCH_TIMEOUT_SECS",
        "CLAVENAR_PROXY_OUTBOUND_CERT_PATH",
        "CLAVENAR_PROXY_OUTBOUND_KEY_PATH",
        "CLAVENAR_PROXY_OUTBOUND_CA_PATH",
        "VAULT_ADDR",
        "VAULT_TOKEN_FILE",
    },
    "brain": COMMON_GOVERNED_ENV | {
        "CLAVENAR_BRAIN_TLS_DIR",
        "CLAVENAR_BRAIN_ALLOWED_CALLERS",
        "CLAVENAR_BRAIN_HEALTH_ADDR",
        "CLAVENAR_BRAIN_PLAIN_ADDR",
        "CLAVENAR_BRAIN_REQUIRE_AUX_CONTROLS",
        "CLAVENAR_BRAIN_EXPLAIN_CALLER_SPIFFE",
        "CLAVENAR_BRAIN_NARRATE_CALLER_SPIFFE",
        "CLAVENAR_BRAIN_EXPLAIN_RATE_LIMIT_PER_MINUTE",
        "CLAVENAR_BRAIN_NARRATE_RATE_LIMIT_PER_MINUTE",
        "CLAVENAR_BRAIN_AUX_SPEND_BUDGET_MICRO_USD_PER_HOUR",
        "CLAVENAR_BRAIN_AUX_TIMEOUT_MILLIS",
        "CLAVENAR_BRAIN_AUX_BODY_LIMIT_BYTES",
        "CLAVENAR_BRAIN_CACHE_HMAC_KEY_FILE",
        "CLAVENAR_BRAIN_REQUIRE_CACHE_HMAC_KEY",
    },
    "policyEngine": COMMON_GOVERNED_ENV | NATS_TLS_ENV | CAPABILITY_ENV | {
        "CLAVENAR_POLICY_ENGINE_BRAIN_URL",
        "CLAVENAR_POLICY_EXPECTED_PEER_SPIFFE",
        "CLAVENAR_POLICY_TLS_DIR",
        "CLAVENAR_POLICY_ALLOWED_CALLERS",
        "CLAVENAR_POLICY_HEALTH_ADDR",
    },
    "ledger": COMMON_GOVERNED_ENV | NATS_TLS_ENV | CAPABILITY_ENV | {
        "CLAVENAR_LEDGER_ALLOWED_CALLERS",
        "CLAVENAR_LEDGER_TLS_DIR",
        "CLAVENAR_LEDGER_MTLS_ADDR",
        "CLAVENAR_LEDGER_REQUIRE_TRUSTED_PROXY",
        "CLAVENAR_LEDGER_TRUSTED_PROXY_SPIFFE",
    },
    "hil": COMMON_GOVERNED_ENV | NATS_TLS_ENV | CAPABILITY_ENV | HIL_WEBAUTHN_CEREMONY_ENV | {
        "CLAVENAR_HIL_TLS_DIR",
        "CLAVENAR_HIL_ALLOWED_CALLERS",
        "CLAVENAR_HIL_HEALTH_ADDR",
        "CLAVENAR_HIL_DECIDE_TOKEN",
        "CLAVENAR_HIL_SESSION_KEY",
        "CLAVENAR_HIL_BOOTSTRAP_TOKEN",
        "CLAVENAR_HIL_DEPLOYMENT_ID",
        "CLAVENAR_HIL_SIMULATOR_TENANT",
    },
    "identity": COMMON_GOVERNED_ENV | NATS_TLS_ENV | CAPABILITY_ENV | {
        "CLAVENAR_RUNTIME_ENVIRONMENT",
        "CLAVENAR_ATTESTATION_PROVIDER",
        "CLAVENAR_IDENTITY_TLS_DIR",
        "CLAVENAR_IDENTITY_ALLOWED_CALLERS",
        "CLAVENAR_IDENTITY_MTLS_ADDR",
        "CLAVENAR_IDENTITY_CA_DIR",
        "CLAVENAR_IDENTITY_REPLAY_REPLICAS",
        "CLAVENAR_ATTESTATION_TRUST_ANCHORS_FILE",
        "VAULT_ADDR",
        "VAULT_TOKEN_FILE",
    },
    "deepReview": COMMON_GOVERNED_ENV | NATS_TLS_ENV | {
        "CLAVENAR_DEEP_REVIEW_LEDGER_URL",
        "CLAVENAR_DEEP_REVIEW_NATS_URL",
    },
    "assurance": COMMON_GOVERNED_ENV | NATS_TLS_ENV | {
        "CLAVENAR_ASSURANCE_PROXY_URL",
        "CLAVENAR_ASSURANCE_NATS_URL",
        "CLAVENAR_ASSURANCE_ADMIN_PORT",
        "CLAVENAR_ASSURANCE_DIAGNOSTICS_PORT",
        "CLAVENAR_ASSURANCE_TLS_DIR",
        "CLAVENAR_ASSURANCE_ALLOWED_CALLERS",
        "CLAVENAR_ASSURANCE_FORENSIC_SUBJECT",
        "CLAVENAR_ASSURANCE_FORENSIC_STREAM",
        "CLAVENAR_ASSURANCE_REQUEST_TIMEOUT_SECS",
        "CLAVENAR_ASSURANCE_RUN_TIMEOUT_SECS",
        "CLAVENAR_ASSURANCE_PUBLISH_TIMEOUT_SECS",
        "CLAVENAR_ASSURANCE_CERT_DIR",
    },
    "console": COMMON_GOVERNED_ENV | {
        "CLAVENAR_CONSOLE_AUTH",
        "CLAVENAR_CONSOLE_BIND",
        "CLAVENAR_CONSOLE_PORT",
        "CLAVENAR_CONSOLE_DEMO_ADDR",
        "CLAVENAR_CONSOLE_DIAGNOSTICS_ADDR",
        "CLAVENAR_CONSOLE_OPERATOR_TLS_CERT_PATH",
        "CLAVENAR_CONSOLE_OPERATOR_TLS_KEY_PATH",
        "CLAVENAR_CONSOLE_OPERATOR_CLIENT_CA_PATH",
        "CLAVENAR_CONSOLE_OPERATOR_IDENTITIES_PATH",
        "CLAVENAR_CONSOLE_AUTH_RATE_LIMIT_MAX",
        "CLAVENAR_CONSOLE_AUTH_RATE_LIMIT_WINDOW_SECS",
        "CLAVENAR_CONSOLE_RELEASE_VERSION",
        "CLAVENAR_CONSOLE_MUTATION_ORIGINS",
        "CLAVENAR_CONSOLE_BRAIN_URL",
        "CLAVENAR_CONSOLE_LEDGER_URL",
        "CLAVENAR_CONSOLE_HIL_URL",
        "CLAVENAR_CONSOLE_POLICY_ENGINE_URL",
        "CLAVENAR_CONSOLE_IDENTITY_URL",
        "CLAVENAR_ASSURANCE_URL",
        "CLAVENAR_CONSOLE_TLS_DIR",
        "CLAVENAR_CONSOLE_OUTBOUND_CERT_PATH",
        "CLAVENAR_CONSOLE_OUTBOUND_KEY_PATH",
        "CLAVENAR_CONSOLE_OUTBOUND_CA_PATH",
        "CLAVENAR_HIL_DECIDE_TOKEN",
        "CLAVENAR_CONSOLE_ALLOW_DISABLED_NETWORK",
    },
}


class ListenerMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrix = yaml.safe_load((ROOT / "charts/clavenar/listeners.yaml").read_text())
        cls.chart_app_version = yaml.safe_load(
            (ROOT / "charts/clavenar/Chart.yaml").read_text()
        )["appVersion"]
        cls.scenarios = {
            "default": [],
            "all-on": [ROOT / "tests/values-all-on.yaml"],
            "production": [ROOT / "tests/values-production.yaml"],
            "optional": [ROOT / "tests/values-optional.yaml"],
            "bundled": [ROOT / "tests/values-bundled.yaml"],
        }
        cls.rendered = {}
        for name, overlays in cls.scenarios.items():
            command = ["helm", "template", "smoke", str(ROOT / "charts/clavenar")]
            for overlay in overlays:
                command.extend(["-f", str(overlay)])
            output = subprocess.run(command, check=True, text=True, capture_output=True).stdout
            cls.rendered[name] = [d for d in yaml.safe_load_all(output) if isinstance(d, dict)]

    def render_with_matrix(self, matrix, overlays=(), refresh_digest=True):
        changed = copy.deepcopy(matrix)
        if refresh_digest:
            changed["reviewedContractSha256"] = CHECKER.matrix_digest(changed)
        with tempfile.TemporaryDirectory() as directory:
            chart = Path(directory) / "clavenar"
            shutil.copytree(ROOT / "charts/clavenar", chart)
            (chart / "listeners.yaml").write_text(yaml.safe_dump(changed, sort_keys=False))
            command = ["helm", "template", "smoke", str(chart)]
            for overlay in overlays:
                command.extend(["-f", str(overlay)])
            output = subprocess.run(command, check=True, text=True, capture_output=True).stdout
            docs = [d for d in yaml.safe_load_all(output) if isinstance(d, dict)]
            values = CHECKER.effective_values(chart, overlays)
        return changed, values, docs

    def test_supported_render_profiles_match_inventory(self):
        for name, overlays in self.scenarios.items():
            with self.subTest(name=name):
                values = CHECKER.effective_values(ROOT / "charts/clavenar", overlays)
                self.assertEqual([], CHECKER.validate(self.matrix, values, self.rendered[name], "smoke"))

        incomplete = subprocess.run(
            [
                "helm", "template", "smoke", str(ROOT / "charts/clavenar"),
                "--set", "nats.bundled.enabled=true",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(0, incomplete.returncode)
        self.assertIn("requires tlsBundle.secretName", incomplete.stderr)

    def test_rendered_service_environment_names_are_unique(self):
        service_names = {
            "proxy", "brain", "policy-engine", "ledger", "hil", "identity",
            "deep-review", "assurance", "console",
        }
        for scenario, docs in self.rendered.items():
            with self.subTest(scenario=scenario):
                checked = set()
                for doc in docs:
                    if doc.get("kind") != "Deployment":
                        continue
                    for container in doc["spec"]["template"]["spec"]["containers"]:
                        if container.get("name") not in service_names:
                            continue
                        checked.add(container["name"])
                        names = [entry["name"] for entry in container.get("env", [])]
                        self.assertEqual(len(names), len(set(names)), container["name"])
                self.assertEqual(service_names, checked)
                errors = []
                CHECKER.validate_service_env_uniqueness(docs, "smoke", errors)
                self.assertEqual([], errors)

        mutated = copy.deepcopy(self.rendered["production"])
        ledger = next(
            doc for doc in mutated
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-ledger"
        )
        env = ledger["spec"]["template"]["spec"]["containers"][0]["env"]
        env.append(copy.deepcopy(env[0]))
        errors = []
        CHECKER.validate_service_env_uniqueness(mutated, "smoke", errors)
        self.assertTrue(any("duplicate environment variables" in item for item in errors))

    def test_proxy_requires_bounded_identity_jwks_wiring(self):
        for scenario in ("default", "production"):
            proxy = next(
                doc for doc in self.rendered[scenario]
                if doc.get("kind") == "Deployment"
                and doc.get("metadata", {}).get("name") == "smoke-proxy"
            )
            env = {
                entry["name"]: entry.get("value")
                for entry in proxy["spec"]["template"]["spec"]["containers"][0]["env"]
            }
            self.assertEqual(
                "http://smoke-identity:8086/jwks.json",
                env["CLAVENAR_PROXY_GRANT_JWKS_URL"],
            )
            self.assertEqual("30", env["CLAVENAR_PROXY_GRANT_JWKS_REFRESH_SECS"])
            self.assertEqual(
                "120", env["CLAVENAR_PROXY_GRANT_JWKS_MAX_STALENESS_SECS"]
            )
            self.assertEqual("5", env["CLAVENAR_PROXY_GRANT_JWKS_FETCH_TIMEOUT_SECS"])

        for settings, message in (
            (["--set", "services.identity.enabled=false"], "requires services.identity.enabled"),
            ([
                "--set", "services.proxy.grantJwksRefreshSeconds=30",
                "--set", "services.proxy.grantJwksMaxStalenessSeconds=30",
            ], "must exceed"),
        ):
            result = subprocess.run(
                ["helm", "template", "smoke", str(ROOT / "charts/clavenar"), *settings],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn(message, result.stderr)

        identity = next(
            doc for doc in self.rendered["default"]
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-identity"
        )
        identity_env = {
            entry["name"]: str(entry.get("value", ""))
            for entry in identity["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual("1", identity_env["CLAVENAR_IDENTITY_REPLAY_REPLICAS"])

        for replay_replicas in (0, 6):
            with self.subTest(replay_replicas=replay_replicas):
                result = subprocess.run(
                    [
                        "helm", "template", "smoke", str(ROOT / "charts/clavenar"),
                        "--skip-schema-validation", "--set",
                        f"services.identity.replayReplicas={replay_replicas}",
                    ],
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("must be between 1 and 5", result.stderr)

    def test_authentication_secret_refs_support_chart_and_operator_ownership(self):
        generated = next(
            doc for doc in self.rendered["default"]
            if doc.get("kind") == "Secret"
            and doc.get("metadata", {}).get("name") == "smoke-shared-tokens"
        )
        self.assertEqual(
            {
                "brain-cache-hmac-key",
                "hil-decide-token",
                "hil-session-key",
                "hil-bootstrap-token",
            },
            set(generated["data"]),
        )
        self.assertEqual(
            "bootstrap-v1",
            generated["metadata"]["annotations"]["clavenar.io/auth-rotation-id"],
        )

        for deployment in (
            doc for doc in self.rendered["default"]
            if doc.get("kind") == "Deployment"
        ):
            self.assertEqual(
                "bootstrap-v1",
                deployment["spec"]["template"]["metadata"]["annotations"][
                    "clavenar.io/auth-rotation-id"
                ],
            )

        default_hil = next(
            doc for doc in self.rendered["default"]
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-hil"
        )
        default_env = {
            entry["name"]: entry
            for entry in default_hil["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        for env_name, key in (
            ("CLAVENAR_HIL_DECIDE_TOKEN", "hil-decide-token"),
            ("CLAVENAR_HIL_SESSION_KEY", "hil-session-key"),
            ("CLAVENAR_HIL_BOOTSTRAP_TOKEN", "hil-bootstrap-token"),
        ):
            self.assertNotIn("value", default_env[env_name])
            self.assertEqual(
                {"name": "smoke-shared-tokens", "key": key},
                default_env[env_name]["valueFrom"]["secretKeyRef"],
            )
        self.assertEqual(
            {
                "CLAVENAR_HIL_WEBAUTHN_TTL_SECS": "300",
                "CLAVENAR_HIL_WEBAUTHN_ATTEMPT_LIMIT": "10",
                "CLAVENAR_HIL_WEBAUTHN_RATE_WINDOW_SECS": "60",
                "CLAVENAR_HIL_WEBAUTHN_SUBJECT_START_LIMIT": "5",
                "CLAVENAR_HIL_WEBAUTHN_TENANT_START_LIMIT": "100",
                "CLAVENAR_HIL_WEBAUTHN_SOURCE_START_LIMIT": "20",
                "CLAVENAR_HIL_WEBAUTHN_DEPLOYMENT_START_LIMIT": "500",
                "CLAVENAR_HIL_WEBAUTHN_SUBJECT_PENDING_LIMIT": "3",
                "CLAVENAR_HIL_WEBAUTHN_TENANT_PENDING_LIMIT": "64",
                "CLAVENAR_HIL_WEBAUTHN_SOURCE_PENDING_LIMIT": "12",
                "CLAVENAR_HIL_WEBAUTHN_DEPLOYMENT_PENDING_LIMIT": "256",
            },
            {
                name: default_env[name]["value"]
                for name in HIL_WEBAUTHN_CEREMONY_ENV
            },
        )
        self.assertEqual(default_env["CLAVENAR_HIL_SIMULATOR_TENANT"]["value"], "simulator")

        default_brain = next(
            doc for doc in self.rendered["default"]
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-brain"
        )
        brain_spec = default_brain["spec"]["template"]["spec"]
        brain_env = {
            entry["name"]: entry
            for entry in brain_spec["containers"][0]["env"]
        }
        self.assertEqual(
            "/run/secrets/brain-cache-hmac-key",
            brain_env["CLAVENAR_BRAIN_CACHE_HMAC_KEY_FILE"]["value"],
        )
        self.assertEqual(
            "true",
            brain_env["CLAVENAR_BRAIN_REQUIRE_CACHE_HMAC_KEY"]["value"],
        )
        brain_cache_volume = next(
            volume for volume in brain_spec["volumes"]
            if volume["name"] == "brain-cache-hmac-key"
        )
        self.assertEqual("smoke-shared-tokens", brain_cache_volume["secret"]["secretName"])
        self.assertEqual(0o440, brain_cache_volume["secret"]["defaultMode"])

        command = [
            "helm", "template", "smoke", str(ROOT / "charts/clavenar"),
            "-f", str(ROOT / "tests/values-all-on.yaml"),
            "-f", str(ROOT / "tests/values-existing-auth-secret.yaml"),
        ]
        output = subprocess.run(
            command, check=True, text=True, capture_output=True
        ).stdout
        rendered = [
            doc for doc in yaml.safe_load_all(output) if isinstance(doc, dict)
        ]

        rendered_secret_names = {
            doc.get("metadata", {}).get("name")
            for doc in rendered if doc.get("kind") == "Secret"
        }
        self.assertNotIn("smoke-shared-tokens", rendered_secret_names)
        self.assertNotIn("clavenar-runtime-auth", rendered_secret_names)

        expected = {
            "smoke-console": {
                "CLAVENAR_HIL_DECIDE_TOKEN": "hil-decide-token",
                "CLAVENAR_CONSOLE_DEMO_SESSION_HS256": "demo-session-hs256",
            },
            "smoke-hil": {
                "CLAVENAR_HIL_DECIDE_TOKEN": "hil-decide-token",
                "CLAVENAR_HIL_SESSION_KEY": "hil-session-key",
                "CLAVENAR_HIL_BOOTSTRAP_TOKEN": "hil-bootstrap-token",
                "CLAVENAR_HIL_DEMO_SESSION_HS256": "demo-session-hs256",
            },
            "smoke-ledger": {
                "CLAVENAR_LEDGER_DEMO_SESSION_HS256": "demo-session-hs256",
            },
        }
        brain = next(
            doc for doc in rendered
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-brain"
        )
        brain_cache_volume = next(
            volume for volume in brain["spec"]["template"]["spec"]["volumes"]
            if volume["name"] == "brain-cache-hmac-key"
        )
        self.assertEqual(
            "clavenar-runtime-auth",
            brain_cache_volume["secret"]["secretName"],
        )
        for deployment_name, secret_env in expected.items():
            deployment = next(
                doc for doc in rendered
                if doc.get("kind") == "Deployment"
                and doc.get("metadata", {}).get("name") == deployment_name
            )
            env = {
                entry["name"]: entry
                for entry in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
            }
            for env_name, key in secret_env.items():
                with self.subTest(deployment=deployment_name, env=env_name):
                    self.assertNotIn("value", env[env_name])
                    self.assertEqual(
                        {"name": "clavenar-runtime-auth", "key": key},
                        env[env_name]["valueFrom"]["secretKeyRef"],
                    )

        identity = next(
            doc for doc in rendered
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-identity"
        )["spec"]["template"]["spec"]
        identity_env = {
            entry["name"]: entry.get("value")
            for entry in identity["containers"][0]["env"]
        }
        self.assertEqual(
            "/var/run/clavenar-oidc/acme-jwks.json",
            identity_env["CLAVENAR_IDENTITY_OIDC_TENANT_ACME_RS256_JWKS_FILE"],
        )
        self.assertEqual(
            "true",
            identity_env["CLAVENAR_IDENTITY_REQUIRE_ASYMMETRIC_OIDC"],
        )
        oidc_mount = next(
            mount for mount in identity["containers"][0]["volumeMounts"]
            if mount["name"] == "oidc-jwks"
        )
        self.assertEqual(
            {"name": "oidc-jwks", "mountPath": "/var/run/clavenar-oidc", "readOnly": True},
            oidc_mount,
        )
        oidc_volume = next(
            volume for volume in identity["volumes"]
            if volume["name"] == "oidc-jwks"
        )
        self.assertEqual(
            {
                "secretName": "clavenar-runtime-auth",
                "items": [{"key": "oidc-jwks.json", "path": "acme-jwks.json"}],
            },
            oidc_volume["secret"],
        )

    def test_authentication_extra_env_rejects_literals_and_chart_owned_duplicates(self):
        cases = (
            ("hil", "CLAVENAR_HIL_SESSION_KEY"),
            ("hil", "CLAVENAR_HIL_DECIDE_TOKEN"),
            ("hil", "CLAVENAR_HIL_BOOTSTRAP_TOKEN"),
            ("hil", "CLAVENAR_HIL_DEMO_SESSION_HS256"),
            ("console", "CLAVENAR_CONSOLE_DEMO_SESSION_HS256"),
            ("ledger", "CLAVENAR_LEDGER_DEMO_SESSION_HS256"),
            ("identity", "CLAVENAR_IDENTITY_OIDC_HS256_KEY"),
            ("identity", "CLAVENAR_IDENTITY_OIDC_TENANT_ACME_HS256_KEY"),
        )
        for service, variable in cases:
            with self.subTest(service=service, variable=variable):
                command = [
                    "helm",
                    "template",
                    "smoke",
                    str(ROOT / "charts/clavenar"),
                    "--skip-schema-validation",
                    "--set-string",
                    f"services.{service}.extraEnv[0].name={variable}",
                    "--set-string",
                    f"services.{service}.extraEnv[0].value=tracked-literal-must-fail",
                ]
                result = subprocess.run(command, text=True, capture_output=True)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("authentication", result.stderr)

    def test_all_chart_governed_env_duplicates_fail_without_schema(self):
        base = yaml.safe_load((ROOT / "tests/values-production.yaml").read_text())
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "values.yaml"
            for service, variables in GOVERNED_ENV_BY_SERVICE.items():
                for variable in sorted(variables):
                    with self.subTest(service=service, variable=variable):
                        candidate = copy.deepcopy(base)
                        candidate["services"].setdefault(service, {})["extraEnv"] = [{
                            "name": variable,
                            "value": "attacker-controlled",
                        }]
                        fixture.write_text(yaml.safe_dump(candidate, sort_keys=False))
                        result = subprocess.run(
                            [
                                "helm", "template", "smoke",
                                str(ROOT / "charts/clavenar"),
                                "-f", str(fixture),
                                "--skip-schema-validation",
                            ],
                            text=True,
                            capture_output=True,
                        )
                        self.assertNotEqual(0, result.returncode, variable)

    def test_governed_env_schema_covers_every_service_family(self):
        representatives = {
            "proxy": "CLAVENAR_PROXY_OUTBOUND_CA_PATH",
            "brain": "CLAVENAR_BRAIN_TLS_DIR",
            "policyEngine": "CLAVENAR_POLICY_TLS_DIR",
            "ledger": "CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE_SHA256",
            "hil": "CLAVENAR_ENDPOINT_CAPABILITY_MATRIX_SHA256",
            "identity": "CLAVENAR_IDENTITY_MTLS_ADDR",
            "deepReview": "NATS_TLS_CA_PATH",
            "assurance": "CLAVENAR_ASSURANCE_ADMIN_PORT",
            "console": "CLAVENAR_CONSOLE_OPERATOR_CLIENT_CA_PATH",
        }
        base = yaml.safe_load((ROOT / "tests/values-production.yaml").read_text())
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "values.yaml"
            for service, variable in representatives.items():
                with self.subTest(service=service, variable=variable):
                    candidate = copy.deepcopy(base)
                    candidate["services"].setdefault(service, {})["extraEnv"] = [{
                        "name": variable,
                        "value": "attacker-controlled",
                    }]
                    fixture.write_text(yaml.safe_dump(candidate, sort_keys=False))
                    result = subprocess.run(
                        [
                            "helm", "template", "smoke",
                            str(ROOT / "charts/clavenar"),
                            "-f", str(fixture),
                        ],
                        text=True,
                        capture_output=True,
                    )
                    self.assertNotEqual(0, result.returncode, variable)
                    self.assertIn("values don't meet the specifications", result.stderr)

    def test_extra_env_name_uniqueness_and_conditional_upstream_ownership(self):
        base = yaml.safe_load((ROOT / "tests/values-production.yaml").read_text())
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "values.yaml"

            duplicate = copy.deepcopy(base)
            duplicate["services"].setdefault("brain", {})["extraEnv"] = [
                {"name": "CUSTOM_PROVIDER_OPTION", "value": "first"},
                {"name": "CUSTOM_PROVIDER_OPTION", "value": "second"},
            ]
            fixture.write_text(yaml.safe_dump(duplicate, sort_keys=False))
            result = subprocess.run(
                [
                    "helm", "template", "smoke", str(ROOT / "charts/clavenar"),
                    "-f", str(fixture), "--skip-schema-validation",
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("duplicates an earlier extraEnv entry", result.stderr)

            byo_upstream = copy.deepcopy(base)
            byo_upstream["services"].setdefault("proxy", {})["extraEnv"] = [{
                "name": "CLAVENAR_UPSTREAM_URL",
                "value": "https://operator-upstream.example/mcp",
            }]
            fixture.write_text(yaml.safe_dump(byo_upstream, sort_keys=False))
            result = subprocess.run(
                [
                    "helm", "template", "smoke", str(ROOT / "charts/clavenar"),
                    "-f", str(fixture),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            docs = [
                item for item in yaml.safe_load_all(result.stdout)
                if isinstance(item, dict)
            ]
            proxy = next(
                item for item in docs
                if item.get("kind") == "Deployment"
                and item.get("metadata", {}).get("name") == "smoke-proxy"
            )
            proxy_names = [
                entry["name"]
                for entry in proxy["spec"]["template"]["spec"]["containers"][0]["env"]
            ]
            self.assertEqual(1, proxy_names.count("CLAVENAR_UPSTREAM_URL"))

            upstream = copy.deepcopy(base)
            upstream.setdefault("upstreamStub", {})["enabled"] = True
            upstream["services"].setdefault("proxy", {})["extraEnv"] = [{
                "name": "CLAVENAR_UPSTREAM_URL",
                "value": "http://attacker.invalid/mcp",
            }]
            fixture.write_text(yaml.safe_dump(upstream, sort_keys=False))
            for schema_args in ([], ["--skip-schema-validation"]):
                with self.subTest(schema_args=schema_args):
                    result = subprocess.run(
                        [
                            "helm", "template", "smoke",
                            str(ROOT / "charts/clavenar"),
                            "-f", str(fixture),
                            *schema_args,
                        ],
                        text=True,
                        capture_output=True,
                    )
                    self.assertNotEqual(0, result.returncode)

    def test_hil_pending_summary_route_auth_posture_is_exact(self):
        listener = next(
            item
            for item in self.matrix["listeners"]
            if item["service"] == "hil" and item["listenerId"] == "application"
        )
        self.assertIn("/pending/summaries", listener["ingressPaths"])
        self.assertIn(
            "pending reads, including /pending/summaries, require no additional application credential",
            listener["authentication"],
        )
        self.assertIn("valid demo cookie narrows", listener["authentication"])
        self.assertIn(
            "digest-bound generated route capabilities",
            listener["authentication"],
        )
        self.assertIn("simulator", listener["authorizedCallers"])

    def test_brain_auxiliary_routes_render_exact_mtls_callers_and_limits(self):
        expected_aux_env = {
            "CLAVENAR_BRAIN_REQUIRE_AUX_CONTROLS": "true",
            "CLAVENAR_BRAIN_EXPLAIN_CALLER_SPIFFE": (
                "spiffe://clavenar.local/service/policy-engine"
            ),
            "CLAVENAR_BRAIN_NARRATE_CALLER_SPIFFE": (
                "spiffe://clavenar.local/service/console"
            ),
            "CLAVENAR_BRAIN_EXPLAIN_RATE_LIMIT_PER_MINUTE": "20",
            "CLAVENAR_BRAIN_NARRATE_RATE_LIMIT_PER_MINUTE": "60",
            "CLAVENAR_BRAIN_AUX_SPEND_BUDGET_MICRO_USD_PER_HOUR": "5000000",
            "CLAVENAR_BRAIN_AUX_TIMEOUT_MILLIS": "5000",
            "CLAVENAR_BRAIN_AUX_BODY_LIMIT_BYTES": "16384",
        }
        for profile, overlays in self.scenarios.items():
            with self.subTest(profile=profile):
                values = CHECKER.effective_values(ROOT / "charts/clavenar", overlays)
                tls_enabled = bool(values["tlsBundle"]["secretName"])
                brain = next(
                    doc for doc in self.rendered[profile]
                    if doc.get("kind") == "Deployment"
                    and doc["metadata"]["name"] == "smoke-brain"
                )
                container = brain["spec"]["template"]["spec"]["containers"][0]
                env = {
                    entry["name"]: entry.get("value")
                    for entry in container["env"]
                }
                self.assertEqual(
                    expected_aux_env,
                    {name: env[name] for name in expected_aux_env},
                )
                self.assertEqual(
                    {"http": 8081, **({"health": 9081} if tls_enabled else {})},
                    {
                        port["name"]: port["containerPort"]
                        for port in container["ports"]
                    },
                )
                if tls_enabled:
                    self.assertEqual(
                        "spiffe://clavenar.local/service/proxy",
                        env["CLAVENAR_BRAIN_ALLOWED_CALLERS"],
                    )
                    self.assertNotIn("policy-engine", env["CLAVENAR_BRAIN_ALLOWED_CALLERS"])
                    self.assertEqual("0.0.0.0:9081", env["CLAVENAR_BRAIN_HEALTH_ADDR"])
                else:
                    self.assertNotIn("CLAVENAR_BRAIN_ALLOWED_CALLERS", env)
                    self.assertNotIn("CLAVENAR_BRAIN_HEALTH_ADDR", env)

                policy = next(
                    doc for doc in self.rendered[profile]
                    if doc.get("kind") == "Deployment"
                    and doc["metadata"]["name"] == "smoke-policy-engine"
                )
                policy_env = {
                    entry["name"]: entry.get("value")
                    for entry in policy["spec"]["template"]["spec"]["containers"][0]["env"]
                }
                self.assertEqual(
                    "https://smoke-brain:8081",
                    policy_env["CLAVENAR_POLICY_ENGINE_BRAIN_URL"],
                )
                self.assertEqual(
                    "spiffe://clavenar.local/service/identity,"
                    "spiffe://clavenar.local/service/brain",
                    policy_env["CLAVENAR_POLICY_EXPECTED_PEER_SPIFFE"],
                )

                console = next(
                    doc for doc in self.rendered[profile]
                    if doc.get("kind") == "Deployment"
                    and doc["metadata"]["name"] == "smoke-console"
                )
                console_env = {
                    entry["name"]: entry.get("value")
                    for entry in console["spec"]["template"]["spec"]["containers"][0]["env"]
                }
                self.assertEqual(
                    "https://smoke-brain:8081",
                    console_env["CLAVENAR_CONSOLE_BRAIN_URL"],
                )
                if tls_enabled:
                    self.assertEqual("/certs", console_env["CLAVENAR_CONSOLE_TLS_DIR"])
                else:
                    self.assertNotIn("CLAVENAR_CONSOLE_TLS_DIR", console_env)

        application = next(
            listener for listener in self.matrix["listeners"]
            if listener["service"] == "brain"
            and listener["listenerId"] == "application"
        )
        self.assertTrue(
            {"/explain-pattern", "/narrate-decision", "/model-snapshot"}
            <= set(application["ingressPaths"])
        )
        self.assertIn("exact policy-engine SPIFFE URI", application["authentication"])
        self.assertIn("exact console SPIFFE URI", application["authentication"])
        self.assertIn("16,384 bytes", application["bodyLimit"])
        self.assertIn("20/minute", application["rateLimit"])
        self.assertIn("5,000 ms", application["rateLimit"])

        health = next(
            listener for listener in self.matrix["listeners"]
            if listener["service"] == "brain"
            and listener["listenerId"] == "health"
        )
        self.assertEqual(
            {"/", "/health", "/readyz", "/metrics"},
            set(health["ingressPaths"]),
        )
        self.assertEqual(["kubelet", "prometheus"], health["authorizedCallers"])
        self.assertEqual(["prometheus"], health["allowedPeers"])
        self.assertFalse(health["servicePublished"])
        self.assertEqual("forbidden", health["hostPublication"])

        default_policy = next(
            doc for doc in self.rendered["default"]
            if doc.get("kind") == "NetworkPolicy"
            and doc["metadata"]["name"] == "smoke-brain"
        )
        self.assertEqual([8081], [rule["ports"][0]["port"] for rule in default_policy["spec"]["ingress"]])
        self.assertEqual(
            {"proxy", "policy-engine", "console"},
            {
                peer["podSelector"]["matchLabels"]["app.kubernetes.io/component"]
                for peer in default_policy["spec"]["ingress"][0]["from"]
            },
        )

    def test_brain_auxiliary_manifest_drift_is_rejected(self):
        values = CHECKER.effective_values(ROOT / "charts/clavenar", [])
        mutations = {}

        wrong_caller = copy.deepcopy(self.rendered["default"])
        brain = next(
            doc for doc in wrong_caller
            if doc.get("kind") == "Deployment"
            and doc["metadata"]["name"] == "smoke-brain"
        )
        next(
            entry for entry in brain["spec"]["template"]["spec"]["containers"][0]["env"]
            if entry["name"] == "CLAVENAR_BRAIN_EXPLAIN_CALLER_SPIFFE"
        )["value"] = "spiffe://clavenar.local/service/proxy"
        mutations["wrong exact caller"] = wrong_caller

        plaintext_policy = copy.deepcopy(self.rendered["default"])
        policy = next(
            doc for doc in plaintext_policy
            if doc.get("kind") == "Deployment"
            and doc["metadata"]["name"] == "smoke-policy-engine"
        )
        next(
            entry for entry in policy["spec"]["template"]["spec"]["containers"][0]["env"]
            if entry["name"] == "CLAVENAR_POLICY_ENGINE_BRAIN_URL"
        )["value"] = "http://smoke-brain:9081"
        mutations["plaintext policy Brain URL"] = plaintext_policy

        for name, docs in mutations.items():
            with self.subTest(name=name):
                errors = CHECKER.validate(self.matrix, values, docs, "smoke")
                self.assertTrue(
                    any("brain" in error.lower() for error in errors),
                    errors,
                )

    def test_console_profiles_render_exact_trust_classes(self):
        def resource(profile, kind):
            return next(
                doc for doc in self.rendered[profile]
                if doc.get("kind") == kind
                and doc.get("metadata", {}).get("name") == "smoke-console"
            )

        default_deployment = resource("default", "Deployment")
        default_container = default_deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(
            {"demo": 8085, "diagnostics": 9185},
            {port["name"]: port["containerPort"] for port in default_container["ports"]},
        )
        default_env = {entry["name"]: entry.get("value") for entry in default_container["env"]}
        self.assertEqual("demo-only", default_env["CLAVENAR_CONSOLE_AUTH"])
        self.assertNotIn("CLAVENAR_CONSOLE_OPERATOR_TLS_CERT_PATH", default_env)
        self.assertNotIn("CLAVENAR_CONSOLE_DEMO_ADDR", default_env)
        self.assertNotIn("CLAVENAR_HIL_DECIDE_TOKEN", default_env)
        self.assertEqual(
            [{
                "port": 8085,
                "targetPort": "demo",
                "protocol": "TCP",
                "appProtocol": "http",
                "name": "demo",
            }],
            resource("default", "Service")["spec"]["ports"],
        )
        self.assertEqual([], resource("default", "NetworkPolicy")["spec"]["ingress"])

        operator_deployment = resource("all-on", "Deployment")
        operator_pod = operator_deployment["spec"]["template"]["spec"]
        operator_container = operator_pod["containers"][0]
        self.assertEqual(
            {"operator-mtls": 8085, "diagnostics": 9185},
            {port["name"]: port["containerPort"] for port in operator_container["ports"]},
        )
        operator_env = {entry["name"]: entry.get("value") for entry in operator_container["env"]}
        self.assertEqual("operator-mtls", operator_env["CLAVENAR_CONSOLE_AUTH"])
        self.assertEqual(
            self.chart_app_version,
            operator_env["CLAVENAR_CONSOLE_RELEASE_VERSION"],
        )
        self.assertIn("CLAVENAR_HIL_DECIDE_TOKEN", operator_env)
        self.assertEqual(
            "/operator-trust/operators.json",
            operator_env["CLAVENAR_CONSOLE_OPERATOR_IDENTITIES_PATH"],
        )
        self.assertEqual(
            "https://console.example.test",
            operator_env["CLAVENAR_CONSOLE_MUTATION_ORIGINS"],
        )
        self.assertEqual(
            "https://smoke-assurance:8088",
            operator_env["CLAVENAR_ASSURANCE_URL"],
        )
        volumes = {volume["name"]: volume for volume in operator_pod["volumes"]}
        self.assertEqual(
            {("ca.crt", "ca.crt"), ("service-console.crt", "service-console.crt"),
             ("service-console.key", "service-console.key")},
            {(item["key"], item["path"]) for item in volumes["certs-source"]["secret"]["items"]},
        )
        self.assertEqual(
            {("ca.crt", "ca.crt"), ("operators.json", "operators.json")},
            {(item["key"], item["path"])
             for item in volumes["operator-trust"]["secret"]["items"]},
        )
        self.assertEqual(
            "clavenar-operator-trust",
            volumes["operator-trust"]["secret"]["secretName"],
        )
        operator_policy = resource("all-on", "NetworkPolicy")["spec"]["ingress"]
        self.assertEqual([8085, 9185], [rule["ports"][0]["port"] for rule in operator_policy])

        optional_container = resource("optional", "Deployment")["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(
            {"operator-mtls": 8085, "demo": 9085, "diagnostics": 9185},
            {port["name"]: port["containerPort"] for port in optional_container["ports"]},
        )
        optional_env = {entry["name"]: entry.get("value") for entry in optional_container["env"]}
        self.assertEqual("0.0.0.0:9085", optional_env["CLAVENAR_CONSOLE_DEMO_ADDR"])
        optional_policy = resource("optional", "NetworkPolicy")["spec"]["ingress"]
        rules_by_port = {rule["ports"][0]["port"]: rule["from"] for rule in optional_policy}
        self.assertEqual({8085, 9085}, set(rules_by_port))
        self.assertNotEqual(rules_by_port[8085], rules_by_port[9085])

    def test_assurance_renders_exact_mtls_and_diagnostics_boundary(self):
        def resource(profile, kind):
            return next(
                doc for doc in self.rendered[profile]
                if doc.get("kind") == kind
                and doc.get("metadata", {}).get("name") == "smoke-assurance"
            )

        for profile in self.scenarios:
            with self.subTest(profile=profile):
                deployment = resource(profile, "Deployment")
                pod = deployment["spec"]["template"]["spec"]
                container = pod["containers"][0]
                self.assertEqual(
                    {"control-mtls": 8088, "diagnostics": 9088},
                    {
                        port["name"]: port["containerPort"]
                        for port in container["ports"]
                    },
                )
                env = {
                    entry["name"]: entry.get("value")
                    for entry in container["env"]
                }
                self.assertEqual("8088", env["CLAVENAR_ASSURANCE_ADMIN_PORT"])
                self.assertEqual("9088", env["CLAVENAR_ASSURANCE_DIAGNOSTICS_PORT"])
                self.assertEqual("/certs", env["CLAVENAR_ASSURANCE_TLS_DIR"])
                self.assertEqual(
                    "spiffe://clavenar.local/service/console",
                    env["CLAVENAR_ASSURANCE_ALLOWED_CALLERS"],
                )
                self.assertEqual(
                    "clavenar.forensic",
                    env["CLAVENAR_ASSURANCE_FORENSIC_SUBJECT"],
                )
                self.assertEqual(
                    "clavenar-forensic",
                    env["CLAVENAR_ASSURANCE_FORENSIC_STREAM"],
                )
                self.assertEqual(
                    "30",
                    env["CLAVENAR_ASSURANCE_REQUEST_TIMEOUT_SECS"],
                )
                self.assertEqual(
                    "900",
                    env["CLAVENAR_ASSURANCE_RUN_TIMEOUT_SECS"],
                )
                self.assertEqual(
                    "10",
                    env["CLAVENAR_ASSURANCE_PUBLISH_TIMEOUT_SECS"],
                )
                self.assertEqual(
                    {"path": "/health", "port": 9088},
                    container["livenessProbe"]["httpGet"],
                )
                self.assertEqual(
                    {"path": "/readyz", "port": 9088},
                    container["readinessProbe"]["httpGet"],
                )
                self.assertEqual(
                    [{
                        "port": 8088,
                        "targetPort": "control-mtls",
                        "protocol": "TCP",
                        "appProtocol": "https",
                        "name": "control-mtls",
                    }],
                    resource(profile, "Service")["spec"]["ports"],
                )

        bundled_pod = resource("bundled", "Deployment")["spec"]["template"]["spec"]
        certs = next(
            volume for volume in bundled_pod["volumes"]
            if volume["name"] == "certs-source"
        )["secret"]
        self.assertEqual(
            {
                ("ca.crt", "ca.crt"),
                ("service-assurance.crt", "service-assurance.crt"),
                ("service-assurance.key", "service-assurance.key"),
            },
            {(item["key"], item["path"]) for item in certs["items"]},
        )

        automint = next(
            doc for doc in self.rendered["bundled"]
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "smoke-tls-automint"
        )
        job = next(
            doc for doc in self.rendered["bundled"]
            if doc.get("kind") == "Job"
            and doc.get("metadata", {}).get("name") == "smoke-tls-automint"
        )
        mint = job["spec"]["template"]["spec"]["initContainers"][0]
        self.assertEqual("snapshot", mint["name"])
        expected_scheme = next(
            entry["value"] for entry in mint["env"]
            if entry["name"] == "EXPECTED_SAN_SCHEME"
        )
        self.assertEqual("release-prefixed-v3-assurance", expected_scheme)
        mint = job["spec"]["template"]["spec"]["initContainers"][1]
        bundle_services = next(
            entry["value"] for entry in mint["env"]
            if entry["name"] == "BUNDLE_SERVICES"
        ).split()
        self.assertIn("assurance", bundle_services)

    def test_console_peer_checker_rejects_broad_selectors(self):
        valid = {
            "networkPolicy": {"console": {
                "operatorMtls": {"allowedPeers": [{
                    "namespaceSelector": {"matchLabels": {"name": "operator-access"}},
                    "podSelector": {"matchLabels": {"app": "tls-passthrough"}},
                }]},
                "demo": {"allowedPeers": []},
            }}
        }
        errors = []
        CHECKER.validate_exact_positive_peers(
            valid,
            ("networkPolicy.console.operatorMtls.allowedPeers",),
            errors,
        )
        self.assertEqual([], errors)

        invalid_peers = (
            {"podSelector": {"matchExpressions": [
                {"key": "ignored", "operator": "DoesNotExist"}
            ]}},
            {"namespaceSelector": {"matchLabels": {"name": "operator-access"}}},
            {"podSelector": {"matchLabels": {"app": "operator"}},
             "namespaceSelector": {"matchExpressions": [
                 {"key": "ignored", "operator": "DoesNotExist"}
             ]}},
            {"podSelector": {"matchLabels": {}}},
            {"ipBlock": {"cidr": "0.0.0.0/0"}},
        )
        for peer in invalid_peers:
            with self.subTest(peer=peer):
                values = copy.deepcopy(valid)
                values["networkPolicy"]["console"]["operatorMtls"]["allowedPeers"] = [peer]
                errors = []
                CHECKER.validate_exact_positive_peers(
                    values,
                    ("networkPolicy.console.operatorMtls.allowedPeers",),
                    errors,
                )
                self.assertTrue(errors)

    def test_production_profile_renders_exact_ledger_trusted_proxy_boundary(self):
        values = CHECKER.effective_values(
            ROOT / "charts/clavenar", self.scenarios["production"]
        )
        self.assertEqual("production", values["deploymentProfile"])
        self.assertEqual("clavenar-runtime-auth", values["authSecrets"]["existingSecretName"])
        self.assertTrue(values["services"]["ledger"]["requireTrustedProxy"])
        self.assertEqual(
            "spiffe://clavenar.local/service/website",
            values["services"]["ledger"]["trustedProxySpiffe"],
        )

        ledger = next(
            doc for doc in self.rendered["production"]
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-ledger"
        )
        env = {
            entry["name"]: entry.get("value")
            for entry in ledger["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual("true", env["CLAVENAR_LEDGER_REQUIRE_TRUSTED_PROXY"])
        self.assertEqual(
            "spiffe://clavenar.local/service/website",
            env["CLAVENAR_LEDGER_TRUSTED_PROXY_SPIFFE"],
        )
        bundle_bytes = (
            ROOT / "charts/clavenar/files/workload-capability-bundle.json"
        ).read_bytes()
        bundle = json.loads(bundle_bytes)
        self.assertEqual(
            "/etc/clavenar/workload-capability-bundle.json",
            env["CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE"],
        )
        self.assertEqual(
            "sha256:" + hashlib.sha256(bundle_bytes).hexdigest(),
            env["CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE_SHA256"],
        )
        self.assertEqual(
            bundle["matrixSha256"],
            env["CLAVENAR_ENDPOINT_CAPABILITY_MATRIX_SHA256"],
        )
        self.assertNotIn("CLAVENAR_LEDGER_ALLOWED_CALLERS", env)
        internal_listener = next(
            item for item in self.matrix["listeners"]
            if item["service"] == "ledger" and item["listenerId"] == "internal-mtls"
        )
        self.assertIn("generated route capabilities", internal_listener["authentication"])
        self.assertIn("public /verify branch", internal_listener["authentication"])

        configured_peers = values["networkPolicy"]["ledger"]["trustedProxy"]["allowedPeers"]
        self.assertEqual(1, len(configured_peers))
        website_peer = configured_peers[0]
        self.assertEqual(
            "clavenar-website",
            website_peer["podSelector"]["matchLabels"]["app.kubernetes.io/name"],
        )
        website_namespace = website_peer["namespaceSelector"]["matchLabels"][
            "kubernetes.io/metadata.name"
        ]
        self.assertNotEqual("default", website_namespace)
        self.assertNotEqual(
            values["networkPolicy"]["prometheusNamespaceLabel"],
            website_namespace,
        )
        policy = next(
            doc for doc in self.rendered["production"]
            if doc.get("kind") == "NetworkPolicy"
            and doc.get("metadata", {}).get("name") == "smoke-ledger"
        )
        matching = [
            rule for rule in policy["spec"]["ingress"]
            if rule.get("from") == configured_peers
        ]
        self.assertEqual(
            [{"protocol": "TCP", "port": 8183}],
            matching[0]["ports"],
        )
        self.assertEqual(1, len(matching))
        for rule in policy["spec"]["ingress"]:
            if rule is matching[0]:
                continue
            self.assertFalse(
                any(peer in (rule.get("from") or []) for peer in configured_peers),
                rule,
            )

        default_values = CHECKER.effective_values(ROOT / "charts/clavenar", [])
        self.assertEqual("evaluation", default_values["deploymentProfile"])
        default_ledger = next(
            doc for doc in self.rendered["default"]
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-ledger"
        )
        default_env = {
            entry["name"]: entry.get("value")
            for entry in default_ledger["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual("false", default_env["CLAVENAR_LEDGER_REQUIRE_TRUSTED_PROXY"])
        self.assertNotIn("CLAVENAR_LEDGER_TRUSTED_PROXY_SPIFFE", default_env)

    def test_ledger_full_verify_limiter_inventory_is_complete(self):
        expected = {
            "public-read": (
                "/verify?full=true: 3 requests/source/minute, 12 requests "
                "globally/minute, and one explicit full walk in flight; direct "
                "public requests ignore forwarded addresses and share the "
                "direct-or-untrusted source bucket; at most 64 trusted-client "
                "source windows are retained, though this non-mTLS listener "
                "cannot create one; all other routes have no request-rate limiter"
            ),
            "internal-mtls": (
                "/verify?full=true: 3 requests/source/minute, 12 requests "
                "globally/minute, and one explicit full walk in flight; only "
                "the exact configured website mTLS caller with one valid "
                "normalized forwarded address uses a trusted-client source "
                "window; direct, malformed, or untrusted requests share the "
                "direct-or-untrusted source bucket; at most 64 trusted-client "
                "source windows are retained; all other routes have no "
                "request-rate limiter"
            ),
        }
        actual = {
            item["listenerId"]: item["rateLimit"]
            for item in self.matrix["listeners"]
            if item["service"] == "ledger"
        }
        self.assertEqual(expected, actual)

    def test_production_profile_value_mutations_fail_closed(self):
        base = yaml.safe_load((ROOT / "tests/values-production.yaml").read_text())

        def changed(path, value):
            candidate = copy.deepcopy(base)
            target = candidate
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            return candidate

        valid_peer = base["networkPolicy"]["ledger"]["trustedProxy"]["allowedPeers"][0]
        prometheus_overlap = copy.deepcopy(base)
        prometheus_overlap["networkPolicy"]["prometheusNamespaceLabel"] = (
            "clavenar-edge"
        )
        cases = {
            "unknown profile": changed(("deploymentProfile",), "staging"),
            "chart-managed auth Secret": changed(
                ("authSecrets", "existingSecretName"), ""
            ),
            "NetworkPolicy disabled": changed(("networkPolicy", "enabled"), False),
            "unsafe Service topology": changed(
                ("services", "ledger", "serviceType"), "LoadBalancer"
            ),
            "public and mTLS port alias": changed(
                ("services", "ledger", "mtlsPort"), 8083
            ),
            "wrong public port": changed(("services", "ledger", "port"), 8183),
            "missing workload TLS": changed(("tlsBundle", "secretName"), ""),
            "auto-minted workload TLS": changed(("tlsBundle", "autoMint"), True),
            "console disabled": changed(("services", "console", "enabled"), False),
            "operator mTLS disabled": changed(
                ("services", "console", "operatorMtls", "enabled"), False
            ),
            "missing public operator trust": changed(
                (
                    "services",
                    "console",
                    "operatorMtls",
                    "publicTrustSecretName",
                ),
                "",
            ),
            "shared workload/operator trust": changed(
                (
                    "services",
                    "console",
                    "operatorMtls",
                    "publicTrustSecretName",
                ),
                base["tlsBundle"]["secretName"],
            ),
            "ledger disabled": changed(("services", "ledger", "enabled"), False),
            "trusted proxy enforcement disabled": changed(
                ("services", "ledger", "requireTrustedProxy"), False
            ),
            "missing trusted proxy SPIFFE": changed(
                ("services", "ledger", "trustedProxySpiffe"), ""
            ),
            "wrong trusted proxy SPIFFE": changed(
                ("services", "ledger", "trustedProxySpiffe"),
                "spiffe://clavenar.local/service/proxy",
            ),
            "missing website selector": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"), []
            ),
            "multiple website selectors": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [valid_peer, copy.deepcopy(valid_peer)],
            ),
            "website selector without namespace": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{
                    "podSelector": {"matchLabels": {
                        "app.kubernetes.io/name": "clavenar-website"
                    }},
                }],
            ),
            "website selector without canonical namespace label": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{
                    "namespaceSelector": {"matchLabels": {"name": "clavenar-edge"}},
                    "podSelector": {"matchLabels": {
                        "app.kubernetes.io/name": "clavenar-website"
                    }},
                }],
            ),
            "website selector in release namespace": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{
                    "namespaceSelector": {"matchLabels": {
                        "kubernetes.io/metadata.name": "default"
                    }},
                    "podSelector": {"matchLabels": {
                        "app.kubernetes.io/name": "clavenar-website"
                    }},
                }],
            ),
            "website selector without canonical app label": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{
                    "namespaceSelector": {"matchLabels": {
                        "kubernetes.io/metadata.name": "clavenar-edge"
                    }},
                    "podSelector": {"matchLabels": {"app": "website"}},
                }],
            ),
            "website selector with wrong canonical app label": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{
                    "namespaceSelector": {"matchLabels": {
                        "kubernetes.io/metadata.name": "clavenar-edge"
                    }},
                    "podSelector": {"matchLabels": {
                        "app.kubernetes.io/name": "other-website"
                    }},
                }],
            ),
            "website selector overlaps Prometheus namespace": prometheus_overlap,
            "selector without pod identity": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{"namespaceSelector": {"matchLabels": {"name": "edge"}}}],
            ),
            "empty pod selector": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{"podSelector": {"matchLabels": {}}}],
            ),
            "negative pod selector": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{"podSelector": {"matchExpressions": [
                    {"key": "other", "operator": "DoesNotExist"}
                ]}}],
            ),
            "ipBlock website peer": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{"ipBlock": {"cidr": "0.0.0.0/0"}}],
            ),
            "empty namespace selector": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{
                    "podSelector": {"matchLabels": {"app": "website"}},
                    "namespaceSelector": {"matchLabels": {}},
                }],
            ),
            "negative namespace selector": changed(
                ("networkPolicy", "ledger", "trustedProxy", "allowedPeers"),
                [{
                    "podSelector": {"matchLabels": {"app": "website"}},
                    "namespaceSelector": {"matchExpressions": [
                        {"key": "other", "operator": "DoesNotExist"}
                    ]},
                }],
            ),
            "override enforcement env": changed(
                ("services", "ledger", "extraEnv"),
                [{
                    "name": "CLAVENAR_LEDGER_REQUIRE_TRUSTED_PROXY",
                    "value": "false",
                }],
            ),
            "override trusted identity env": changed(
                ("services", "ledger", "extraEnv"),
                [{
                    "name": "CLAVENAR_LEDGER_TRUSTED_PROXY_SPIFFE",
                    "value": "spiffe://clavenar.local/service/proxy",
                }],
            ),
            "override internal caller allowlist": changed(
                ("services", "ledger", "extraEnv"),
                [{
                    "name": "CLAVENAR_LEDGER_ALLOWED_CALLERS",
                    "value": "spiffe://clavenar.local/service/website",
                }],
            ),
            "override TLS directory": changed(
                ("services", "ledger", "extraEnv"),
                [{"name": "CLAVENAR_LEDGER_TLS_DIR", "value": "/tmp"}],
            ),
            "override mTLS bind": changed(
                ("services", "ledger", "extraEnv"),
                [{"name": "CLAVENAR_LEDGER_MTLS_ADDR", "value": "0.0.0.0:8083"}],
            ),
        }
        for name, values in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory) / "values.yaml"
                fixture.write_text(yaml.safe_dump(values, sort_keys=False))
                result = subprocess.run(
                    [
                        "helm", "template", "smoke",
                        str(ROOT / "charts/clavenar"),
                        "-f", str(fixture),
                    ],
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(0, result.returncode, name)

    def test_production_profile_rejects_prometheus_namespace_overlap_via_set(self):
        result = subprocess.run(
            [
                "helm", "template", "smoke", str(ROOT / "charts/clavenar"),
                "-f", str(ROOT / "tests/values-production.yaml"),
                "--set", "networkPolicy.prometheusNamespaceLabel=clavenar-edge",
            ],
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "website namespace must differ from networkPolicy.prometheusNamespaceLabel",
            result.stderr,
        )

    def test_checker_rejects_website_selector_overlap_boundaries(self):
        base = CHECKER.effective_values(
            ROOT / "charts/clavenar", self.scenarios["production"]
        )

        prometheus_overlap = copy.deepcopy(base)
        prometheus_overlap["networkPolicy"]["prometheusNamespaceLabel"] = (
            "clavenar-edge"
        )
        errors = CHECKER.validate(
            self.matrix,
            prometheus_overlap,
            self.rendered["production"],
            "smoke",
        )
        self.assertTrue(
            any("must differ from the Prometheus namespace" in error for error in errors),
            errors,
        )

        errors = CHECKER.validate(
            self.matrix,
            base,
            self.rendered["production"],
            "smoke",
            namespace="clavenar-edge",
        )
        self.assertTrue(
            any("must differ from the release namespace" in error for error in errors),
            errors,
        )

        wrong_app = copy.deepcopy(base)
        wrong_app["networkPolicy"]["ledger"]["trustedProxy"]["allowedPeers"][0][
            "podSelector"
        ]["matchLabels"]["app.kubernetes.io/name"] = "other-website"
        errors = CHECKER.validate(
            self.matrix,
            wrong_app,
            self.rendered["production"],
            "smoke",
        )
        self.assertTrue(
            any("app.kubernetes.io/name]=clavenar-website" in error for error in errors),
            errors,
        )

    def test_production_ledger_manifest_mutations_fail_closed(self):
        values = CHECKER.effective_values(
            ROOT / "charts/clavenar", self.scenarios["production"]
        )
        configured_peers = values["networkPolicy"]["ledger"]["trustedProxy"]["allowedPeers"]
        mutations = {}

        enforcement = copy.deepcopy(self.rendered["production"])
        ledger = next(
            doc for doc in enforcement
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-ledger"
        )
        env = ledger["spec"]["template"]["spec"]["containers"][0]["env"]
        next(
            entry for entry in env
            if entry["name"] == "CLAVENAR_LEDGER_REQUIRE_TRUSTED_PROXY"
        )["value"] = "false"
        mutations["enforcement disabled"] = enforcement

        identity = copy.deepcopy(self.rendered["production"])
        ledger = next(
            doc for doc in identity
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-ledger"
        )
        env = ledger["spec"]["template"]["spec"]["containers"][0]["env"]
        next(
            entry for entry in env
            if entry["name"] == "CLAVENAR_LEDGER_TRUSTED_PROXY_SPIFFE"
        )["value"] = "spiffe://clavenar.local/service/proxy"
        mutations["wrong trusted identity"] = identity

        allowlist = copy.deepcopy(self.rendered["production"])
        ledger = next(
            doc for doc in allowlist
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name") == "smoke-ledger"
        )
        env = ledger["spec"]["template"]["spec"]["containers"][0]["env"]
        next(
            entry for entry in env
            if entry["name"] == "CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE_SHA256"
        )["value"] = "sha256:" + ("0" * 64)
        mutations["generated capability digest substitution"] = allowlist

        wrong_port = copy.deepcopy(self.rendered["production"])
        policy = next(
            doc for doc in wrong_port
            if doc.get("kind") == "NetworkPolicy"
            and doc.get("metadata", {}).get("name") == "smoke-ledger"
        )
        rule = next(
            entry for entry in policy["spec"]["ingress"]
            if entry.get("from") == configured_peers
        )
        rule["ports"][0]["port"] = 8083
        mutations["website admitted to public port"] = wrong_port

        leaked_peer = copy.deepcopy(self.rendered["production"])
        policy = next(
            doc for doc in leaked_peer
            if doc.get("kind") == "NetworkPolicy"
            and doc.get("metadata", {}).get("name") == "smoke-ledger"
        )
        public_rule = next(
            entry for entry in policy["spec"]["ingress"]
            if entry["ports"] == [{"protocol": "TCP", "port": 8083}]
        )
        public_rule["from"].append(copy.deepcopy(configured_peers[0]))
        mutations["website selector leaked to public port"] = leaked_peer

        namespace_wide = copy.deepcopy(self.rendered["production"])
        policy = next(
            doc for doc in namespace_wide
            if doc.get("kind") == "NetworkPolicy"
            and doc.get("metadata", {}).get("name") == "smoke-ledger"
        )
        public_rule = next(
            entry for entry in policy["spec"]["ingress"]
            if entry["ports"] == [{"protocol": "TCP", "port": 8083}]
        )
        public_rule["from"].append({
            "namespaceSelector": {"matchLabels": {
                "kubernetes.io/metadata.name": "clavenar-edge"
            }}
        })
        mutations["website namespace admitted namespace-wide to public port"] = (
            namespace_wide
        )

        for name, docs in mutations.items():
            with self.subTest(name=name):
                errors = CHECKER.validate(self.matrix, values, docs, "smoke")
                self.assertTrue(errors, name)

    def test_console_contract_render_mutations_fail_closed(self):
        scenarios = {
            "default": [],
            "all-on": self.scenarios["all-on"],
            "bundled": self.scenarios["bundled"],
        }
        mutations = []

        default = copy.deepcopy(self.rendered["default"])
        container = next(
            doc for doc in default
            if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "smoke-console"
        )["spec"]["template"]["spec"]["containers"][0]
        next(entry for entry in container["env"] if entry["name"] == "CLAVENAR_CONSOLE_AUTH")["value"] = "disabled"
        mutations.append(("default", "synthetic auth mode", default))

        default = copy.deepcopy(self.rendered["default"])
        deployment = next(
            doc for doc in default
            if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "smoke-console"
        )
        deployment["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]["httpGet"]["port"] = 8085
        mutations.append(("default", "operator/demo readiness probe", default))

        all_on = copy.deepcopy(self.rendered["all-on"])
        deployment = next(
            doc for doc in all_on
            if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "smoke-console"
        )
        trust = next(volume for volume in deployment["spec"]["template"]["spec"]["volumes"]
                     if volume["name"] == "operator-trust")
        trust["secret"]["items"].append({"key": "operator-ca.key", "path": "operator-ca.key"})
        mutations.append(("all-on", "operator private key projection", all_on))

        bundled = copy.deepcopy(self.rendered["bundled"])
        deployment = next(
            doc for doc in bundled
            if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "smoke-console"
        )
        certs = next(volume for volume in deployment["spec"]["template"]["spec"]["volumes"]
                     if volume["name"] == "certs-source")
        certs["secret"]["items"].append({"key": "service-proxy.key", "path": "service-proxy.key"})
        mutations.append(("bundled", "another workload private key projection", bundled))

        default = copy.deepcopy(self.rendered["default"])
        container = next(
            doc for doc in default
            if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "smoke-console"
        )["spec"]["template"]["spec"]["containers"][0]
        next(
            entry for entry in container["env"]
            if entry["name"] == "CLAVENAR_CONSOLE_RELEASE_VERSION"
        )["value"] = "forged"
        mutations.append(("default", "forged release version", default))

        for profile, name, docs in mutations:
            with self.subTest(name=name):
                values = CHECKER.effective_values(ROOT / "charts/clavenar", scenarios[profile])
                errors = CHECKER.validate(
                    self.matrix,
                    values,
                    docs,
                    "smoke",
                    chart_app_version=self.chart_app_version,
                )
                self.assertTrue(errors)

    def test_assurance_contract_render_mutations_fail_closed(self):
        values_default = CHECKER.effective_values(ROOT / "charts/clavenar", [])
        values_tls = CHECKER.effective_values(
            ROOT / "charts/clavenar", self.scenarios["all-on"]
        )
        mutations = []

        caller = copy.deepcopy(self.rendered["default"])
        container = next(
            doc for doc in caller
            if doc.get("kind") == "Deployment"
            and doc["metadata"]["name"] == "smoke-assurance"
        )["spec"]["template"]["spec"]["containers"][0]
        next(
            entry for entry in container["env"]
            if entry["name"] == "CLAVENAR_ASSURANCE_ALLOWED_CALLERS"
        )["value"] = "spiffe://clavenar.local/service/proxy"
        mutations.append(("caller identity", values_default, caller))

        stream = copy.deepcopy(self.rendered["default"])
        container = next(
            doc for doc in stream
            if doc.get("kind") == "Deployment"
            and doc["metadata"]["name"] == "smoke-assurance"
        )["spec"]["template"]["spec"]["containers"][0]
        next(
            entry for entry in container["env"]
            if entry["name"] == "CLAVENAR_ASSURANCE_FORENSIC_STREAM"
        )["value"] = "unreviewed-stream"
        mutations.append(("forensic stream", values_default, stream))

        probe = copy.deepcopy(self.rendered["default"])
        container = next(
            doc for doc in probe
            if doc.get("kind") == "Deployment"
            and doc["metadata"]["name"] == "smoke-assurance"
        )["spec"]["template"]["spec"]["containers"][0]
        container["readinessProbe"]["httpGet"]["port"] = 8088
        mutations.append(("control-plane probe", values_default, probe))

        projection = copy.deepcopy(self.rendered["all-on"])
        pod = next(
            doc for doc in projection
            if doc.get("kind") == "Deployment"
            and doc["metadata"]["name"] == "smoke-assurance"
        )["spec"]["template"]["spec"]
        certs = next(
            volume for volume in pod["volumes"] if volume["name"] == "certs-source"
        )["secret"]["items"]
        certs.remove(next(item for item in certs if item["key"] == "service-assurance.key"))
        mutations.append(("missing server key", values_tls, projection))

        for name, values, docs in mutations:
            with self.subTest(name=name):
                errors = CHECKER.validate(self.matrix, values, docs, "smoke")
                self.assertTrue(errors)

    def test_all_workload_tls_keys_are_exact_and_owner_only(self):
        deployments = [
            doc for doc in self.rendered["all-on"]
            if doc.get("kind") == "Deployment"
            and doc.get("metadata", {}).get("name", "").startswith("smoke-")
        ]
        governed = {
            "proxy", "brain", "policy-engine", "ledger", "hil", "identity",
            "deep-review", "console", "assurance",
        }
        seen = set()
        for deployment in deployments:
            service = deployment["metadata"]["name"].removeprefix("smoke-")
            if service not in governed:
                continue
            seen.add(service)
            pod = deployment["spec"]["template"]["spec"]
            volumes = {item["name"]: item for item in pod["volumes"]}
            source = volumes["certs-source"]["secret"]
            expected = {
                ("ca.crt", "ca.crt"),
                (f"service-{service}.crt", f"service-{service}.crt"),
                (f"service-{service}.key", f"service-{service}.key"),
            }
            if service == "identity":
                expected.add(("ca.key", "ca.key"))
            if service == "proxy":
                expected.update({("server.crt", "server.crt"), ("server.key", "server.key")})
            self.assertEqual(
                expected,
                {(item["key"], item["path"]) for item in source["items"]},
            )
            self.assertEqual(0o440, source["defaultMode"])
            self.assertEqual(
                {"medium": "Memory", "sizeLimit": "1Mi"},
                volumes["certs"]["emptyDir"],
            )
            projector = next(
                item for item in pod["initContainers"]
                if item["name"] == "workload-tls-projector"
            )
            self.assertIn("chmod 0600 /projected/*.key", "\n".join(projector["args"]))
            self.assertTrue(projector["securityContext"]["readOnlyRootFilesystem"])
            self.assertEqual(["ALL"], projector["securityContext"]["capabilities"]["drop"])
            app = pod["containers"][0]
            cert_mount = next(item for item in app["volumeMounts"] if item["name"] == "certs")
            self.assertTrue(cert_mount["readOnly"])
        self.assertEqual(governed, seen)

    def test_values_schema_carries_fixed_listener_contracts(self):
        schema = json.loads((ROOT / "charts/clavenar/values.schema.json").read_text())
        for service, expected in GOVERNED_ENV_BY_SERVICE.items():
            with self.subTest(governed_env_schema=service):
                actual = schema["properties"]["services"]["properties"][service][
                    "properties"
                ]["extraEnv"]["items"]["properties"]["name"]["not"]["enum"]
                self.assertEqual(expected, set(actual))
        self.assertIn("CLAVENAR_UPSTREAM_URL", json.dumps(schema["allOf"]))
        self.assertEqual(
            ["evaluation", "production"],
            schema["properties"]["deploymentProfile"]["enum"],
        )
        auth_secrets = schema["properties"]["authSecrets"]
        self.assertFalse(auth_secrets["additionalProperties"])
        self.assertEqual(["existingSecretName", "rotationId"], auth_secrets["required"])
        self.assertEqual(
            253,
            auth_secrets["properties"]["existingSecretName"]["maxLength"],
        )
        self.assertEqual(128, auth_secrets["properties"]["rotationId"]["maxLength"])
        console = schema["properties"]["services"]["properties"]["console"]
        identity = schema["properties"]["services"]["properties"]["identity"]
        self.assertEqual(1, identity["properties"]["replayReplicas"]["minimum"])
        self.assertEqual(5, identity["properties"]["replayReplicas"]["maximum"])
        self.assertEqual(8085, console["properties"]["port"]["const"])
        self.assertEqual(9085, console["properties"]["demoPort"]["const"])
        self.assertEqual(9185, console["properties"]["diagnosticsPort"]["const"])
        self.assertTrue(console["properties"]["metrics"]["properties"]["enabled"]["const"])
        self.assertEqual(9185, console["properties"]["probes"]["properties"]["port"]["const"])
        self.assertFalse(console["properties"]["operatorMtls"]["additionalProperties"])
        forbidden_env = console["properties"]["extraEnv"]["items"]["properties"]["name"]["not"]["enum"]
        self.assertIn("CLAVENAR_CONSOLE_RELEASE_VERSION", forbidden_env)
        self.assertIn("CLAVENAR_CONSOLE_MUTATION_ORIGINS", forbidden_env)
        self.assertIn("CLAVENAR_CONSOLE_BRAIN_URL", forbidden_env)

        brain = schema["properties"]["services"]["properties"]["brain"]
        self.assertEqual(8081, brain["properties"]["port"]["const"])
        self.assertEqual(9081, brain["properties"]["healthPort"]["const"])
        self.assertEqual(
            "spiffe://clavenar.local/service/policy-engine",
            brain["properties"]["explainCallerSpiffe"]["const"],
        )
        self.assertEqual(
            "spiffe://clavenar.local/service/console",
            brain["properties"]["narrateCallerSpiffe"]["const"],
        )
        self.assertEqual(30000, brain["properties"]["auxTimeoutMillis"]["maximum"])
        self.assertEqual(1048576, brain["properties"]["auxBodyLimitBytes"]["maximum"])
        self.assertTrue(
            {
                "explainCallerSpiffe",
                "narrateCallerSpiffe",
                "explainRateLimitPerMinute",
                "narrateRateLimitPerMinute",
                "auxSpendBudgetMicroUsdPerHour",
                "auxTimeoutMillis",
                "auxBodyLimitBytes",
            }
            <= set(brain["required"])
        )
        brain_forbidden = (
            brain["properties"]["extraEnv"]["items"]["properties"]["name"]["not"]["enum"]
        )
        self.assertIn("CLAVENAR_BRAIN_REQUIRE_AUX_CONTROLS", brain_forbidden)
        self.assertIn("CLAVENAR_BRAIN_ALLOWED_CALLERS", brain_forbidden)
        self.assertEqual(
            "clavenar.local",
            schema["properties"]["tlsBundle"]["properties"]["spiffeTrustDomain"]["const"],
        )

        ledger = schema["properties"]["services"]["properties"]["ledger"]
        self.assertEqual(
            ["", "spiffe://clavenar.local/service/website"],
            ledger["properties"]["trustedProxySpiffe"]["enum"],
        )
        ledger_forbidden = (
            ledger["properties"]["extraEnv"]["items"]["properties"]["name"]["not"]["enum"]
        )
        self.assertTrue(
            {
                "CLAVENAR_LEDGER_ALLOWED_CALLERS",
                "CLAVENAR_LEDGER_TLS_DIR",
                "CLAVENAR_LEDGER_MTLS_ADDR",
                "CLAVENAR_LEDGER_REQUIRE_TRUSTED_PROXY",
                "CLAVENAR_LEDGER_TRUSTED_PROXY_SPIFFE",
            }
            <= set(ledger_forbidden),
        )
        trusted_peer = schema["properties"]["networkPolicy"]["properties"]["ledger"]
        self.assertFalse(trusted_peer["additionalProperties"])
        self.assertEqual(
            "#/definitions/ledgerTrustedProxyPeerClass",
            trusted_peer["properties"]["trustedProxy"]["$ref"],
        )
        ledger_peer = schema["definitions"]["ledgerTrustedProxyPeerClass"][
            "properties"
        ]["allowedPeers"]["items"]
        self.assertEqual(
            {"podSelector", "namespaceSelector"},
            set(ledger_peer["required"]),
        )
        website_pod_labels = schema["definitions"]["canonicalWebsitePodSelector"][
            "properties"
        ]["matchLabels"]
        self.assertEqual(
            "clavenar-website",
            website_pod_labels["properties"]["app.kubernetes.io/name"]["const"],
        )
        self.assertIn("app.kubernetes.io/name", website_pod_labels["required"])
        website_namespace_labels = schema["definitions"][
            "externalWebsiteNamespaceSelector"
        ]["properties"]["matchLabels"]
        self.assertIn(
            "kubernetes.io/metadata.name",
            website_namespace_labels["required"],
        )
        self.assertEqual(
            1,
            website_namespace_labels["properties"][
                "kubernetes.io/metadata.name"
            ]["minLength"],
        )

        assurance = schema["properties"]["services"]["properties"]["assurance"]
        self.assertEqual(8088, assurance["properties"]["port"]["const"])
        self.assertEqual(9088, assurance["properties"]["healthPort"]["const"])
        self.assertEqual(9088, assurance["properties"]["probes"]["properties"]["port"]["const"])
        assurance_forbidden = assurance["properties"]["extraEnv"]["items"]["properties"]["name"]["not"]["enum"]
        self.assertIn("CLAVENAR_ASSURANCE_ALLOWED_CALLERS", assurance_forbidden)
        self.assertIn("CLAVENAR_ASSURANCE_FORENSIC_STREAM", assurance_forbidden)
        self.assertEqual(300, assurance["properties"]["requestTimeoutSecs"]["maximum"])
        self.assertEqual(3600, assurance["properties"]["runTimeoutSecs"]["maximum"])
        self.assertEqual(60, assurance["properties"]["publishTimeoutSecs"]["maximum"])

    def test_console_auth_alerts_use_bounded_metrics(self):
        alerts = yaml.safe_load(
            (ROOT / "charts/clavenar/alerts/clavenar-alerts.yaml").read_text()
        )
        rules = {
            rule["alert"]: rule
            for group in alerts["groups"]
            for rule in group["rules"]
        }
        self.assertIn("clavenar_console_auth_attempts_total", rules["ConsoleOperatorAuthFailureBurst"]["expr"])
        self.assertIn('outcome="failure"', rules["ConsoleOperatorAuthFailureBurst"]["expr"])
        self.assertIn("clavenar_console_auth_throttled_total", rules["ConsoleOperatorAuthThrottled"]["expr"])
        self.assertNotIn('scope="operator_mtls"', rules["ConsoleOperatorAuthThrottled"]["expr"])
        self.assertEqual('up{job="console"} == 0', rules["ConsoleDiagnosticsDown"]["expr"])
        self.assertIn("clavenar_console_operator_trust_ready", rules["ConsoleOperatorTrustNotReady"]["expr"])
        self.assertEqual(
            'kube_service_spec_type{service=~".*-console",type=~"LoadBalancer|NodePort"} == 1',
            rules["ConsoleOperatorServiceExposed"]["expr"],
        )
        self.assertNotIn("certificateSha256", json.dumps(rules))

    def test_console_demo_and_diagnostics_route_inventories_are_exact(self):
        expected_demo = {
            "ANY / (303 redirect to /demo)",
            "GET /version.json (anonymous)",
            "GET /demo (anonymous)",
            "GET /favicon.svg (anonymous)",
            "GET /static/console.css (anonymous)",
            "GET /static/receipt-verify.js (anonymous)",
            "POST /api/demo-session/exchange (anonymous)",
            "GET /api/demo-session/status (anonymous)",
            "GET /_partials/chain-pill (valid demo cookie)",
            "GET /_partials/stats/cost-latency/latency (valid demo cookie)",
            "GET /api/chain-status (valid demo cookie)",
            "GET /api/receipt-sample (valid demo cookie)",
            "GET /assurance (valid demo cookie)",
            "GET /audit (valid demo cookie)",
            "GET /audit/narrative (valid demo cookie)",
            "GET /hil (valid demo cookie)",
            "GET /hil/analytics (valid demo cookie)",
            "GET /hil/retroactive-review (valid demo cookie)",
            "GET /jwks.json (valid demo cookie)",
            "GET /receipt (valid demo cookie)",
            "GET /stats (valid demo cookie)",
            "GET /stats/cost-latency (valid demo cookie)",
            "GET /stats/deny-rate (valid demo cookie)",
            "GET /stats/intents (valid demo cookie)",
            "GET /stream/audit (valid demo cookie)",
            "GET /stream/hil (valid demo cookie)",
            "GET /timeline (valid demo cookie)",
            "GET /topology (valid demo cookie)",
            "GET /topology/edge (valid demo cookie)",
            "GET /velocity (valid demo cookie)",
            "GET /audit/correlation/{cid} (valid demo cookie)",
            "GET /chains/{cid} (valid demo cookie)",
            "GET /audit/agents/{agent}/narrative (valid demo cookie)",
            "GET /api/receipt/{cid} (valid demo cookie)",
            "POST /demo/fire/{scenario} (valid demo cookie)",
            "GET /demo/run/{cid} (valid demo cookie)",
            "GET /demo/pipeline/{cid} (valid demo cookie)",
            "POST /hil/{uuid}/approve (valid demo cookie)",
            "POST /hil/{uuid}/deny (valid demo cookie)",
            "POST /hil/{uuid}/modify (valid demo cookie)",
        }
        for listener_id in ("primary-demo", "demo"):
            listener = next(
                item for item in self.matrix["listeners"]
                if item["service"] == "console" and item["listenerId"] == listener_id
            )
            self.assertEqual(expected_demo, set(listener["ingressPaths"]))
        diagnostics = next(
            item for item in self.matrix["listeners"]
            if item["service"] == "console" and item["listenerId"] == "diagnostics"
        )
        self.assertEqual(
            {"GET /health", "GET /readyz", "GET /metrics"},
            set(diagnostics["ingressPaths"]),
        )

        operator = next(
            item for item in self.matrix["listeners"]
            if item["service"] == "console" and item["listenerId"] == "operator-ui"
        )
        operational_routes = {
            route for route in operator["ingressPaths"]
            if route in {
                "GET /version.json",
                "GET /health",
                "GET /readyz",
                "GET /metrics",
            }
        }
        self.assertEqual({"GET /version.json"}, operational_routes)

    def test_service_and_policy_drift_fail_closed(self):
        values = CHECKER.effective_values(ROOT / "charts/clavenar", [])
        mutations = {}

        extra_port = copy.deepcopy(self.rendered["default"])
        next(d for d in extra_port if d.get("kind") == "Service")["spec"]["ports"].append(
            {"name": "unexpected", "port": 6553, "targetPort": 6553}
        )
        mutations["extra Service port"] = extra_port

        missing_service = copy.deepcopy(self.rendered["default"])
        missing_service.remove(next(d for d in missing_service if d.get("kind") == "Service"))
        mutations["missing Service"] = missing_service

        missing_port = copy.deepcopy(self.rendered["default"])
        next(d for d in missing_port if d.get("kind") == "Service")["spec"]["ports"].clear()
        mutations["missing Service port"] = missing_port

        external_type = copy.deepcopy(self.rendered["default"])
        next(d for d in external_type if d.get("kind") == "Service")["spec"]["type"] = "LoadBalancer"
        mutations["external Service type"] = external_type

        wrong_target_port = copy.deepcopy(self.rendered["default"])
        next(d for d in wrong_target_port if d.get("kind") == "Service")["spec"]["ports"][0]["targetPort"] = "drifted"
        mutations["Service targetPort drift"] = wrong_target_port

        wrong_protocol = copy.deepcopy(self.rendered["default"])
        next(d for d in wrong_protocol if d.get("kind") == "Service")["spec"]["ports"][0]["protocol"] = "UDP"
        mutations["Service protocol drift"] = wrong_protocol

        wrong_selector = copy.deepcopy(self.rendered["default"])
        next(d for d in wrong_selector if d.get("kind") == "Service")["spec"]["selector"].pop(
            "app.kubernetes.io/component"
        )
        mutations["Service selector drift"] = wrong_selector

        external_ips = copy.deepcopy(self.rendered["default"])
        next(d for d in external_ips if d.get("kind") == "Service")["spec"]["externalIPs"] = ["203.0.113.10"]
        mutations["Service externalIPs"] = external_ips

        duplicate_service = copy.deepcopy(self.rendered["default"])
        duplicate_service.append(copy.deepcopy(next(d for d in duplicate_service if d.get("kind") == "Service")))
        mutations["duplicate Service name"] = duplicate_service

        missing_policy = copy.deepcopy(self.rendered["default"])
        missing_policy.remove(next(d for d in missing_policy if d.get("kind") == "NetworkPolicy"))
        mutations["missing NetworkPolicy"] = missing_policy

        extra_policy = copy.deepcopy(self.rendered["default"])
        duplicate = copy.deepcopy(next(d for d in extra_policy if d.get("kind") == "NetworkPolicy"))
        duplicate["metadata"]["name"] = "smoke-uninventoried"
        extra_policy.append(duplicate)
        mutations["extra NetworkPolicy"] = extra_policy

        duplicate_policy = copy.deepcopy(self.rendered["default"])
        duplicate_policy.append(copy.deepcopy(next(d for d in duplicate_policy if d.get("kind") == "NetworkPolicy")))
        mutations["duplicate NetworkPolicy name"] = duplicate_policy

        wrong_policy_port = copy.deepcopy(self.rendered["default"])
        policy = next(d for d in wrong_policy_port if d.get("kind") == "NetworkPolicy" and (d["spec"].get("ingress") or []))
        policy["spec"]["ingress"][0]["ports"][0]["port"] = 6553
        mutations["NetworkPolicy port drift"] = wrong_policy_port

        wrong_policy_types = copy.deepcopy(self.rendered["default"])
        next(d for d in wrong_policy_types if d.get("kind") == "NetworkPolicy")["spec"]["policyTypes"] = [
            "Ingress", "Egress"
        ]
        mutations["NetworkPolicy policyTypes drift"] = wrong_policy_types

        selector_expression = copy.deepcopy(self.rendered["default"])
        next(d for d in selector_expression if d.get("kind") == "NetworkPolicy")["spec"]["podSelector"][
            "matchExpressions"
        ] = [{"key": "unexpected", "operator": "Exists"}]
        mutations["NetworkPolicy selector expression"] = selector_expression

        egress_policy = copy.deepcopy(self.rendered["default"])
        next(d for d in egress_policy if d.get("kind") == "NetworkPolicy")["spec"]["egress"] = []
        mutations["NetworkPolicy extra egress"] = egress_policy

        extra_container_port = copy.deepcopy(self.rendered["default"])
        proxy = next(
            d for d in extra_container_port
            if d.get("kind") == "Deployment" and d["metadata"]["name"] == "smoke-proxy"
        )
        proxy["spec"]["template"]["spec"]["containers"][0]["ports"].append(
            {"name": "unexpected", "containerPort": 6553, "protocol": "TCP"}
        )
        mutations["extra named container port"] = extra_container_port

        extra_unnamed_container_port = copy.deepcopy(self.rendered["default"])
        proxy = next(
            d for d in extra_unnamed_container_port
            if d.get("kind") == "Deployment" and d["metadata"]["name"] == "smoke-proxy"
        )
        proxy["spec"]["template"]["spec"]["containers"][0]["ports"].append(
            {"containerPort": 6554, "protocol": "TCP"}
        )
        mutations["extra unnamed container port"] = extra_unnamed_container_port

        unknown_listener = copy.deepcopy(self.rendered["default"])
        unknown_listener.append({
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "smoke-unlisted-listener"},
            "spec": {"template": {"metadata": {"labels": {"app": "unlisted"}}, "spec": {
                "containers": [{"name": "listener", "ports": [{"containerPort": 6555}]}]
            }}},
        })
        mutations["unlisted listening workload"] = unknown_listener

        host_network = copy.deepcopy(self.rendered["default"])
        proxy = next(d for d in host_network if d.get("kind") == "Deployment" and d["metadata"]["name"] == "smoke-proxy")
        proxy["spec"]["template"]["spec"]["hostNetwork"] = True
        mutations["hostNetwork"] = host_network

        host_port = copy.deepcopy(self.rendered["default"])
        proxy = next(d for d in host_port if d.get("kind") == "Deployment" and d["metadata"]["name"] == "smoke-proxy")
        proxy["spec"]["template"]["spec"]["containers"][0]["ports"][0]["hostPort"] = 8443
        mutations["hostPort"] = host_port

        host_ip = copy.deepcopy(self.rendered["default"])
        proxy = next(d for d in host_ip if d.get("kind") == "Deployment" and d["metadata"]["name"] == "smoke-proxy")
        proxy["spec"]["template"]["spec"]["containers"][0]["ports"][0]["hostIP"] = "127.0.0.1"
        mutations["hostIP"] = host_ip

        for name, docs in mutations.items():
            with self.subTest(name=name):
                self.assertTrue(CHECKER.validate(self.matrix, values, docs, "smoke"))

        incomplete = copy.deepcopy(self.matrix)
        del incomplete["listeners"][0]["bodyLimit"]
        self.assertTrue(CHECKER.validate(incomplete, values, self.rendered["default"], "smoke"))

    def test_bundled_policy_selectors_must_match_subchart_workloads(self):
        values = CHECKER.effective_values(ROOT / "charts/clavenar", self.scenarios["bundled"])
        docs = copy.deepcopy(self.rendered["bundled"])
        nats = next(d for d in docs if d.get("kind") == "StatefulSet" and d["metadata"]["name"] == "smoke-nats")
        nats["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/component"] = "drifted"
        errors = CHECKER.validate(self.matrix, values, docs, "smoke")
        self.assertTrue(any("smoke-nats selector matches no rendered workload" in e for e in errors))

    def test_reviewed_digest_annotation_and_peer_contracts_fail_independently(self):
        changed = copy.deepcopy(self.matrix)
        changed["listeners"][0]["bodyLimit"] = "drifted but otherwise structurally valid"
        stale, values, docs = self.render_with_matrix(changed, refresh_digest=False)
        errors = CHECKER.validate(stale, values, docs, "smoke")
        self.assertEqual(1, len(errors), errors)
        self.assertIn("reviewedContractSha256", errors[0])

        values = CHECKER.effective_values(ROOT / "charts/clavenar", [])
        docs = copy.deepcopy(self.rendered["default"])
        policy = next(d for d in docs if d.get("kind") == "NetworkPolicy")
        policy["metadata"]["annotations"]["clavenar.io/listener-matrix-sha256"] = "0" * 64
        errors = CHECKER.validate(self.matrix, values, docs, "smoke")
        self.assertEqual(1, len(errors), errors)
        self.assertIn("listener-matrix digest", errors[0])

        changed = copy.deepcopy(self.matrix)
        ledger = next(
            item for item in changed["listeners"]
            if item["service"] == "ledger" and item["listenerId"] == "public-read"
        )
        ledger["allowedPeers"].remove("deep-review")
        refreshed, values, docs = self.render_with_matrix(changed)
        errors = CHECKER.validate(refreshed, values, docs, "smoke")
        self.assertEqual(1, len(errors), errors)
        self.assertIn("allowedPeers", errors[0])

        changed = copy.deepcopy(self.matrix)
        assurance_contract = next(
            item for item in changed["networkPolicies"] if item["service"] == "assurance"
        )
        assurance_contract["rules"][0]["peers"].append("proxy")
        assurance_listener = next(item for item in changed["listeners"] if item["service"] == "assurance")
        assurance_listener["allowedPeers"].append("proxy")
        assurance_listener["authorizedCallers"].append("proxy")
        refreshed, values, docs = self.render_with_matrix(changed)
        errors = CHECKER.validate(refreshed, values, docs, "smoke")
        self.assertEqual(1, len(errors), errors)
        self.assertIn("spec does not exactly match", errors[0])

        changed = copy.deepcopy(self.matrix)
        changed["listeners"][0]["authentication"] = "none"
        refreshed, values, docs = self.render_with_matrix(changed)
        errors = CHECKER.validate(refreshed, values, docs, "smoke")
        self.assertEqual(1, len(errors), errors)
        self.assertIn("allows any peer without authentication", errors[0])

        changed = copy.deepcopy(self.matrix)
        proxy_health = next(
            item for item in changed["listeners"]
            if item["service"] == "proxy" and item["listenerId"] == "health"
        )
        proxy_health["authorizedCallers"].remove("kubelet")
        refreshed, values, docs = self.render_with_matrix(changed)
        errors = CHECKER.validate(refreshed, values, docs, "smoke")
        self.assertEqual(2, len(errors), errors)
        self.assertTrue(all("is not authorized for kubelet" in error for error in errors), errors)

        changed = copy.deepcopy(self.matrix)
        changed["listeners"].append(copy.deepcopy(changed["listeners"][0]))
        refreshed, values, docs = self.render_with_matrix(changed)
        errors = CHECKER.validate(refreshed, values, docs, "smoke")
        self.assertTrue(any("duplicate listener id" in error for error in errors), errors)

    def test_bundled_service_and_vault_test_hook_contracts_fail_closed(self):
        values = CHECKER.effective_values(ROOT / "charts/clavenar", self.scenarios["bundled"])
        mutations = {}

        external_name = copy.deepcopy(self.rendered["bundled"])
        proxy_alias = next(
            d for d in external_name if d.get("kind") == "Service" and d["metadata"]["name"] == "proxy"
        )
        proxy_alias["spec"]["externalName"] = "wrong.default.svc.cluster.local"
        mutations["ExternalName target"] = external_name

        app_protocol = copy.deepcopy(self.rendered["bundled"])
        nats = next(
            d for d in app_protocol if d.get("kind") == "Service" and d["metadata"]["name"] == "smoke-nats"
        )
        nats["spec"]["ports"][0]["appProtocol"] = "http"
        mutations["NATS appProtocol"] = app_protocol

        test_label = copy.deepcopy(self.rendered["bundled"])
        test_pod = next(
            d for d in test_label if d.get("kind") == "Pod" and d["metadata"]["name"] == "smoke-vault-server-test"
        )
        test_pod["metadata"]["labels"].clear()
        mutations["Vault test peer label"] = test_label

        test_address = copy.deepcopy(self.rendered["bundled"])
        test_pod = next(
            d for d in test_address if d.get("kind") == "Pod" and d["metadata"]["name"] == "smoke-vault-server-test"
        )
        next(
            env for env in test_pod["spec"]["containers"][0]["env"] if env["name"] == "VAULT_ADDR"
        )["value"] = "http://wrong:8200"
        mutations["Vault test address"] = test_address

        server_selector_collision = copy.deepcopy(self.rendered["bundled"])
        vault_server = next(
            d for d in server_selector_collision
            if d.get("kind") == "StatefulSet" and d["metadata"]["name"] == "smoke-vault"
        )
        vault_server["spec"]["template"]["metadata"]["labels"].pop("component")
        mutations["Vault test selector excludes server"] = server_selector_collision

        missing_test = copy.deepcopy(self.rendered["bundled"])
        missing_test.remove(next(
            d for d in missing_test if d.get("kind") == "Pod" and d["metadata"]["name"] == "smoke-vault-server-test"
        ))
        mutations["missing Vault test"] = missing_test

        for name, docs in mutations.items():
            with self.subTest(name=name):
                self.assertTrue(CHECKER.validate(self.matrix, values, docs, "smoke"))

    def test_brain_required_auxiliary_values_cannot_be_omitted(self):
        required = (
            "explainCallerSpiffe",
            "narrateCallerSpiffe",
            "explainRateLimitPerMinute",
            "narrateRateLimitPerMinute",
            "auxSpendBudgetMicroUsdPerHour",
            "auxTimeoutMillis",
            "auxBodyLimitBytes",
        )
        for field in required:
            with self.subTest(field=field):
                command = [
                    "helm",
                    "template",
                    "smoke",
                    str(ROOT / "charts/clavenar"),
                    "--set-json",
                    f"services.brain.{field}=null",
                ]
                result = subprocess.run(command, text=True, capture_output=True)
                self.assertNotEqual(0, result.returncode, result.stdout)
                self.assertIn(field, result.stderr)

    def test_unsafe_service_type_and_console_configuration_are_rejected(self):
        cases = (
            ["--set", "authSecrets.existingSecretName=Invalid_Name"],
            ["--set", "authSecrets.unreviewedKey=value"],
            ["--set-string", "tlsBundle.spiffeTrustDomain=example.internal"],
            ["--set-string", "services.brain.explainCallerSpiffe="],
            ["--set-string", "services.brain.explainCallerSpiffe=spiffe://clavenar.local/service/policy"],
            ["--set-string", "services.brain.explainCallerSpiffe=spiffe://clavenar.local/service/policy-engine,spiffe://clavenar.local/service/proxy"],
            ["--set-string", "services.brain.narrateCallerSpiffe=https://console"],
            ["--set", "services.brain.explainRateLimitPerMinute=0"],
            ["--set", "services.brain.narrateRateLimitPerMinute=0"],
            ["--set", "services.brain.auxSpendBudgetMicroUsdPerHour=0"],
            ["--set", "services.brain.auxTimeoutMillis=0"],
            ["--set", "services.brain.auxTimeoutMillis=30001"],
            ["--set", "services.brain.auxBodyLimitBytes=0"],
            ["--set", "services.brain.auxBodyLimitBytes=1048577"],
            ["--set", "services.brain.extraEnv[0].name=CLAVENAR_BRAIN_REQUIRE_AUX_CONTROLS",
             "--set", "services.brain.extraEnv[0].value=false"],
            ["--set", "services.brain.extraEnv[0].name=CLAVENAR_BRAIN_ALLOWED_CALLERS",
             "--set", "services.brain.extraEnv[0].value=spiffe://clavenar.local/service/policy-engine"],
            ["--set", "services.policyEngine.extraEnv[0].name=CLAVENAR_POLICY_ENGINE_BRAIN_URL",
             "--set", "services.policyEngine.extraEnv[0].value=http://smoke-brain:9081"],
            ["--set", "services.policyEngine.extraEnv[0].name=CLAVENAR_POLICY_EXPECTED_PEER_SPIFFE",
             "--set", "services.policyEngine.extraEnv[0].value=spiffe://clavenar.local/service/proxy"],
            ["--set", "services.console.extraEnv[0].name=CLAVENAR_CONSOLE_BRAIN_URL",
             "--set", "services.console.extraEnv[0].value=http://smoke-brain:9081"],
            ["--set", "services.console.serviceType=LoadBalancer"],
            ["--set-json", 'networkPolicy.console.demo.allowedPeers=[{"podSelector":{}}]'],
            ["--set-json", 'networkPolicy.console.operatorMtls.allowedPeers=[{"ipBlock":{"cidr":"0.0.0.0/0"}}]'],
            ["--set-json", 'networkPolicy.console.demo.allowedPeers=[{"namespaceSelector":{"matchLabels":{"kubernetes.io/metadata.name":"ingress"}}}]'],
            ["--set-json", 'networkPolicy.console.demo.allowedPeers=[{"podSelector":{"matchExpressions":[{"key":"ignored","operator":"DoesNotExist"}]}}]'],
            ["--set-json", 'networkPolicy.console.demo.allowedPeers=[{"podSelector":{"matchLabels":{"app":"demo"}},"namespaceSelector":{"matchExpressions":[{"key":"ignored","operator":"DoesNotExist"}]}}]'],
            ["--set-json", 'networkPolicy.console.operatorMtls.allowedPeers=[{"podSelector":{"matchLabels":{"app":"operator"}}}]'],
            ["--set-json", 'networkPolicy.console.allowedPeers=[{"podSelector":{"matchLabels":{"app":"legacy"}}}]'],
            ["--set", "services.console.operatorMtls.enabled=true"],
            ["--set", "services.console.operatorMtls.publicTrustSecretName=operator-trust"],
            ["--set", "services.console.operatorMtls.enabled=true",
             "--set", "services.console.operatorMtls.publicTrustSecretName=operator-trust"],
            ["--set", "services.console.operatorMtls.enabled=true",
             "--set", "services.console.operatorMtls.publicTrustSecretName=same-secret",
             "--set", "tlsBundle.secretName=same-secret"],
            ["--set", "services.console.demo.enabled=true"],
            ["-f", str(ROOT / "tests/values-all-on.yaml"),
             "--set-json", 'networkPolicy.console.demo.allowedPeers=[{"podSelector":{"matchLabels":{"app":"demo"}}}]'],
            ["--set", "services.console.diagnosticsPort=8085"],
            ["--set", "services.console.metrics.enabled=false"],
            ["--set-string", "services.console.metrics.extraAnnotations.prometheus\\.io/port=8085"],
            ["--set", "services.console.probes.extraPort=8085"],
            ["--set", "services.console.probes.port=8085"],
            ["--set", "services.console.extraEnv[0].name=CLAVENAR_CONSOLE_AUTH",
             "--set", "services.console.extraEnv[0].value=disabled"],
            ["--set", "services.console.extraEnv[0].name=CLAVENAR_CONSOLE_RELEASE_VERSION",
             "--set", "services.console.extraEnv[0].value=forged"],
            ["--set", "services.assurance.port=8089"],
            ["--set", "services.assurance.healthPort=8088"],
            ["--set", "services.assurance.probes.port=8088"],
            ["--set", "services.assurance.metrics.enabled=true"],
            ["--set", "services.assurance.extraEnv[0].name=CLAVENAR_ASSURANCE_ALLOWED_CALLERS",
             "--set", "services.assurance.extraEnv[0].value=spiffe://clavenar.local/service/proxy"],
            ["--set", "services.assurance.extraEnv[0].name=CLAVENAR_ASSURANCE_FORENSIC_STREAM",
             "--set", "services.assurance.extraEnv[0].value=unreviewed"],
            ["--set-string", "services.assurance.forensicSubject=clavenar.*"],
            ["--set-string", "services.assurance.forensicStream=clavenar.forensic"],
            ["--set", "services.assurance.requestTimeoutSecs=0"],
            ["--set", "services.assurance.requestTimeoutSecs=301"],
            ["--set", "services.assurance.runTimeoutSecs=3601"],
            ["--set", "services.assurance.publishTimeoutSecs=61"],
            ["--set", "services.assurance.requestTimeoutSecs=60",
             "--set", "services.assurance.runTimeoutSecs=59"],
            ["--set", "services.assurance.publishTimeoutSecs=60",
             "--set", "services.assurance.runTimeoutSecs=59"],
            ["-f", str(ROOT / "tests/values-all-on.yaml"),
             "--set-json", "services.console.mutationOrigins=[]"],
            ["-f", str(ROOT / "tests/values-all-on.yaml"),
             "--set", "services.console.mutationOrigins[0]=https://console.example.test/path"],
            ["-f", str(ROOT / "tests/values-bundled.yaml"), "--set", "vault.server.service.type=NodePort"],
            ["-f", str(ROOT / "tests/values-bundled.yaml"), "--set", "nats.service.merge.spec.type=LoadBalancer"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                command = ["helm", "template", "smoke", str(ROOT / "charts/clavenar"), *arguments]
                result = subprocess.run(command, text=True, capture_output=True)
                self.assertNotEqual(0, result.returncode, result.stdout)


if __name__ == "__main__":
    unittest.main()
