#!/usr/bin/env python3
import copy
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

        output = subprocess.run(
            [
                "helm", "template", "smoke", str(ROOT / "charts/clavenar"),
                "--set", "nats.bundled.enabled=true",
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        docs = [d for d in yaml.safe_load_all(output) if isinstance(d, dict)]
        values = CHECKER.effective_values(ROOT / "charts/clavenar", [])
        values["nats"]["bundled"]["enabled"] = True
        self.assertEqual([], CHECKER.validate(self.matrix, values, docs, "smoke"))

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
            {(item["key"], item["path"]) for item in volumes["certs"]["secret"]["items"]},
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
            if volume["name"] == "certs"
        )["secret"]
        self.assertEqual(
            {
                ("ca.crt", "ca.crt"),
                ("service-assurance.crt", "service-assurance.crt"),
                ("service-assurance.key", "service-assurance.key"),
                ("client.crt", "client.crt"),
                ("client.key", "client.key"),
            },
            {(item["key"], item["path"]) for item in certs["items"]},
        )

        automint = next(
            doc for doc in self.rendered["bundled"]
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "smoke-tls-automint"
        )
        self.assertIn(
            'EXPECTED_SAN_SCHEME="release-prefixed-v3-assurance"',
            automint["data"]["apply.sh"],
        )
        job = next(
            doc for doc in self.rendered["bundled"]
            if doc.get("kind") == "Job"
            and doc.get("metadata", {}).get("name") == "smoke-tls-automint"
        )
        mint = job["spec"]["template"]["spec"]["initContainers"][0]
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
        CHECKER.validate_console_peers(valid, errors)
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
                CHECKER.validate_console_peers(values, errors)
                self.assertTrue(errors)

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
                     if volume["name"] == "certs")
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
            volume for volume in pod["volumes"] if volume["name"] == "certs"
        )["secret"]["items"]
        certs.remove(next(item for item in certs if item["key"] == "service-assurance.key"))
        mutations.append(("missing server key", values_tls, projection))

        for name, values, docs in mutations:
            with self.subTest(name=name):
                errors = CHECKER.validate(self.matrix, values, docs, "smoke")
                self.assertTrue(errors)

    def test_console_values_schema_carries_fixed_listener_contract(self):
        schema = json.loads((ROOT / "charts/clavenar/values.schema.json").read_text())
        console = schema["properties"]["services"]["properties"]["console"]
        self.assertEqual(8085, console["properties"]["port"]["const"])
        self.assertEqual(9085, console["properties"]["demoPort"]["const"])
        self.assertEqual(9185, console["properties"]["diagnosticsPort"]["const"])
        self.assertTrue(console["properties"]["metrics"]["properties"]["enabled"]["const"])
        self.assertEqual(9185, console["properties"]["probes"]["properties"]["port"]["const"])
        self.assertFalse(console["properties"]["operatorMtls"]["additionalProperties"])
        forbidden_env = console["properties"]["extraEnv"]["items"]["properties"]["name"]["not"]["enum"]
        self.assertIn("CLAVENAR_CONSOLE_RELEASE_VERSION", forbidden_env)
        self.assertIn("CLAVENAR_CONSOLE_MUTATION_ORIGINS", forbidden_env)

        assurance = schema["properties"]["services"]["properties"]["assurance"]
        self.assertEqual(8088, assurance["properties"]["port"]["const"])
        self.assertEqual(9088, assurance["properties"]["healthPort"]["const"])
        self.assertEqual(9088, assurance["properties"]["probes"]["properties"]["port"]["const"])
        assurance_forbidden = assurance["properties"]["extraEnv"]["items"]["properties"]["name"]["not"]["enum"]
        self.assertIn("CLAVENAR_ASSURANCE_ALLOWED_CALLERS", assurance_forbidden)

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

    def test_unsafe_service_type_and_console_configuration_are_rejected(self):
        cases = (
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
