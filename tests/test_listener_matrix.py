#!/usr/bin/env python3
import copy
import importlib.util
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

    def test_unsafe_service_type_and_empty_console_selector_are_rejected(self):
        cases = (
            ["--set", "services.console.serviceType=LoadBalancer"],
            ["--set-json", 'networkPolicy.console.allowedPeers=[{"podSelector":{}}]'],
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
