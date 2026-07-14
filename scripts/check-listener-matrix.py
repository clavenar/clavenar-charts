#!/usr/bin/env python3
"""Validate rendered Services and NetworkPolicies against listeners.yaml."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

import yaml


REQUIRED_LISTENER_FIELDS = {
    "service", "listenerId", "owner", "enabledWhen", "bind",
    "containerPort", "servicePort", "servicePublished", "ingressPaths",
    "transportProtocol", "applicationProtocol", "authentication",
    "authorizedCallers", "allowedPeers", "bodyLimit", "rateLimit",
    "hostPublication", "externalPublication",
}


def merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        result = copy.deepcopy(base)
        for key, value in override.items():
            result[key] = merge(result.get(key), value)
        return result
    return copy.deepcopy(override)


def value_at(values, path):
    current = values
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def condition_enabled(expression, values):
    return all(bool(value_at(values, part.strip())) for part in expression.split(" and "))


def selector(release, component):
    return {
        "app.kubernetes.io/name": "clavenar",
        "app.kubernetes.io/instance": release,
        "app.kubernetes.io/component": component,
    }


def peer(release, component):
    return {"podSelector": {"matchLabels": selector(release, component)}}


def workload_selector(release, service):
    if service == "nats":
        return {
            "app.kubernetes.io/name": "nats",
            "app.kubernetes.io/instance": release,
            "app.kubernetes.io/component": "nats",
        }
    if service == "vault":
        return {
            "app.kubernetes.io/name": "vault",
            "app.kubernetes.io/instance": release,
            "component": "server",
        }
    return selector(release, service)


def rule(port, peers=None):
    result = {"ports": [{"protocol": "TCP", "port": int(port)}]}
    if peers is not None:
        result["from"] = peers
    return result


def canonical(items):
    return sorted(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in items)


def effective_values(chart, overlays):
    values = yaml.safe_load((chart / "values.yaml").read_text())
    for overlay in overlays:
        values = merge(values, yaml.safe_load(Path(overlay).read_text()) or {})
    return values


def expected_services(matrix, values, release, namespace="default"):
    result = {}
    tls = bool(value_at(values, "tlsBundle.secretName"))
    for item in matrix["serviceObjects"]:
        if not condition_enabled(item["enabledWhen"], values):
            continue
        ports = copy.deepcopy(item["ports"])
        if tls:
            ports.extend(copy.deepcopy(item.get("tlsPorts", [])))
        for port_spec in ports:
            if port_spec.get("appProtocol") == "tlsWhenTlsElseTcp":
                port_spec["appProtocol"] = "tls" if tls else "tcp"
            port_spec.setdefault("protocol", "TCP")
        owner = item["selectorOwner"]
        if owner == "none":
            service_selector = None
        elif owner in {"nats", "vault"}:
            service_selector = workload_selector(release, owner)
        else:
            service_selector = selector(release, item["id"])
        name = item["name"].format(release=release)
        if name in result:
            raise ValueError(f"duplicate Service object name {name}")
        spec = {
            "type": item["type"],
            "ports": sorted(ports, key=lambda port: (int(port["port"]), port["name"])),
        }
        if service_selector is not None:
            spec["selector"] = service_selector
        if item.get("headless"):
            spec["clusterIP"] = "None"
        if item.get("publishNotReadyAddresses"):
            spec["publishNotReadyAddresses"] = True
        if item.get("externalName"):
            spec["externalName"] = item["externalName"].format(
                release=release, namespace=namespace
            )
        result[name] = spec
    return result


def matrix_digest(matrix):
    governed = copy.deepcopy(matrix)
    governed.pop("reviewedContractSha256", None)
    encoded = json.dumps(governed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def contract_enabled(item, values):
    enabled = item.get("enabledWhen")
    disabled = item.get("disabledWhen")
    return (not enabled or condition_enabled(enabled, values)) and not (
        disabled and condition_enabled(disabled, values)
    )


def expected_policies(matrix, values, release):
    if not value_at(values, "networkPolicy.enabled"):
        return {}
    prometheus = value_at(values, "networkPolicy.prometheusNamespaceLabel")
    prom_peer = [{"namespaceSelector": {"matchLabels": {
        "kubernetes.io/metadata.name": prometheus
    }}}]
    listener_by_id = {
        (item["service"], item["listenerId"]): item
        for item in matrix.get("listeners", [])
    }
    policies = {}
    for contract in matrix.get("networkPolicies", []):
        if not contract_enabled(contract, values):
            continue
        service = contract["service"]
        owner = contract["owner"]
        target = workload_selector(release, service)
        rules = []
        for governed_rule in contract.get("rules", []):
            if not contract_enabled(governed_rule, values):
                continue
            listener = listener_by_id.get((service, governed_rule["listenerId"]))
            if listener is None:
                raise ValueError(
                    f"NetworkPolicy contract {service}/{governed_rule['listenerId']} references a missing listener"
                )
            tokens = governed_rule["peers"]
            if "any" in tokens:
                if tokens != ["any"]:
                    raise ValueError(f"NetworkPolicy contract {service} mixes 'any' with selected peers")
                sources = None
            else:
                sources = []
                for token in tokens:
                    if token == "prometheus":
                        if not prometheus:
                            raise ValueError(f"NetworkPolicy contract {service} enabled Prometheus without a namespace")
                        sources.extend(prom_peer)
                    elif token == "configured-console-peers":
                        sources.extend(value_at(values, "networkPolicy.console.allowedPeers") or [])
                    elif token == "vault":
                        sources.append({"podSelector": {"matchLabels": target}})
                    elif token in matrix.get("peerSelectors", {}):
                        sources.append(copy.deepcopy(matrix["peerSelectors"][token]))
                    else:
                        sources.append(peer(release, token))
            rules.append(rule(listener["containerPort"], sources))
        policies[f"{release}-{service}"] = {
            "podSelector": {"matchLabels": target},
            "policyTypes": ["Ingress"],
            "ingress": rules,
        }
    return policies


def validate_console_peers(values, errors):
    for index, configured in enumerate(value_at(values, "networkPolicy.console.allowedPeers") or []):
        if not isinstance(configured, dict) or "ipBlock" in configured:
            errors.append(f"networkPolicy.console.allowedPeers[{index}] must be selector-driven")
            continue
        selectors = [configured.get("podSelector"), configured.get("namespaceSelector")]
        if not any(isinstance(s, dict) and (s.get("matchLabels") or s.get("matchExpressions")) for s in selectors):
            errors.append(f"networkPolicy.console.allowedPeers[{index}] has an empty selector")


def normalized_service_spec(spec):
    normalized = copy.deepcopy(spec)
    normalized.setdefault("type", "ClusterIP")
    ports = normalized.get("ports") or []
    for port_spec in ports:
        port_spec.setdefault("protocol", "TCP")
    normalized["ports"] = sorted(
        ports, key=lambda port: (int(port["port"]), str(port.get("name", "")))
    )
    return normalized


def pod_selector_matches(selector_spec, labels):
    if not all(labels.get(key) == value for key, value in selector_spec.get("matchLabels", {}).items()):
        return False
    for expression in selector_spec.get("matchExpressions", []):
        key = expression.get("key")
        operator = expression.get("operator")
        if operator == "DoesNotExist" and key in labels:
            return False
        if operator == "Exists" and key not in labels:
            return False
        if operator not in {"DoesNotExist", "Exists"}:
            return False
    return True


def validate_vault_test_hook(matrix, values, docs, release, namespace, expected_policies_by_name, errors):
    tests = [
        doc for doc in docs
        if doc.get("kind") == "Pod"
        and doc.get("metadata", {}).get("name") == f"{release}-vault-server-test"
    ]
    if not value_at(values, "vault.bundled.enabled"):
        if tests:
            errors.append("bundled Vault test Pod rendered while bundled Vault is disabled")
        return
    if len(tests) != 1:
        errors.append(f"bundled Vault must render exactly one test Pod; found {len(tests)}")
        return
    test = tests[0]
    metadata = test.get("metadata", {})
    labels = metadata.get("labels") or {}
    expected_label = {"clavenar.io/network-client": "vault-server-test"}
    if labels != expected_label:
        errors.append(f"Vault test Pod labels {labels} != {expected_label}")
    hook_tokens = {
        token.strip() for token in str((metadata.get("annotations") or {}).get("helm.sh/hook", "")).split(",")
    }
    if "test" not in hook_tokens:
        errors.append("Vault test Pod is not a Helm test hook")
    expected_addr = f"http://{release}-vault.{namespace}.svc:8200"
    addresses = [
        env.get("value")
        for container in test.get("spec", {}).get("containers", [])
        for env in container.get("env", [])
        if env.get("name") == "VAULT_ADDR"
    ]
    if addresses != [expected_addr]:
        errors.append(f"Vault test Pod VAULT_ADDR {addresses} != {[expected_addr]}")
    expected_peer = matrix.get("peerSelectors", {}).get("vault-server-test")
    expected_selector = (expected_peer or {}).get("podSelector", {})
    if not expected_selector or not pod_selector_matches(expected_selector, labels):
        errors.append("Vault test Pod does not match its exact governed peer selector")
    vault_server_labels = [
        doc.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
        for doc in docs
        if doc.get("kind") == "StatefulSet"
        and doc.get("metadata", {}).get("name") == f"{release}-vault"
    ]
    if len(vault_server_labels) != 1:
        errors.append(f"bundled Vault must render exactly one server StatefulSet; found {len(vault_server_labels)}")
    elif pod_selector_matches(expected_selector, vault_server_labels[0]):
        errors.append("Vault test peer selector also matches the Vault server Pod")
    policy = expected_policies_by_name.get(f"{release}-vault", {})
    reachable = any(
        any(int(port.get("port", -1)) == 8200 for port in entry.get("ports", []))
        and any(
            source == expected_peer
            for source in entry.get("from", [])
        )
        for entry in policy.get("ingress", [])
    )
    if not reachable:
        errors.append("Vault test Pod has no exact NetworkPolicy path to Vault API port 8200")


def validate(matrix, values, docs, release, namespace="default"):
    errors = []
    if matrix.get("schemaVersion") != "1.0":
        errors.append("listeners.yaml schemaVersion must be the string '1.0'")
    digest = matrix_digest(matrix)
    if matrix.get("reviewedContractSha256") != digest:
        errors.append(
            f"listeners.yaml reviewedContractSha256 {matrix.get('reviewedContractSha256')!r} != {digest!r}"
        )
    seen = set()
    for index, listener in enumerate(matrix.get("listeners", [])):
        missing = REQUIRED_LISTENER_FIELDS - listener.keys()
        if missing:
            errors.append(f"listener[{index}] missing metadata: {sorted(missing)}")
        ident = (listener.get("service"), listener.get("listenerId"))
        if ident in seen:
            errors.append(f"duplicate listener id {ident}")
        seen.add(ident)
        if any(listener.get(field) is None for field in REQUIRED_LISTENER_FIELDS):
            errors.append(f"listener {ident} contains implicit null metadata")
        if listener.get("owner") not in matrix.get("ownership", {}):
            errors.append(f"listener {ident} has unknown owner {listener.get('owner')}")
        paths = listener.get("ingressPaths")
        if not isinstance(paths, list) or not paths:
            errors.append(f"listener {ident} must inventory at least one exact route or protocol surface")
        elif len(paths) != len(set(paths)):
            errors.append(f"listener {ident} contains duplicate ingress paths")
        if listener.get("owner") == "core" and any(
            "/*" in str(path) or str(path).rstrip().endswith("*") for path in paths or []
        ):
            errors.append(f"listener {ident} uses a wildcard instead of exact core routes")
    service_object_ids = set()
    for index, service_object in enumerate(matrix.get("serviceObjects", [])):
        required = {"id", "selectorOwner", "enabledWhen", "name", "type", "ports"}
        missing = required - service_object.keys()
        if missing:
            errors.append(f"serviceObjects[{index}] missing metadata: {sorted(missing)}")
            continue
        if service_object["id"] in service_object_ids:
            errors.append(f"duplicate Service object id {service_object['id']}")
        service_object_ids.add(service_object["id"])
        if service_object["selectorOwner"] not in {"core", "none", "nats", "vault"}:
            errors.append(f"Service object {service_object['id']} has unknown selectorOwner")
        if service_object["type"] not in {"ClusterIP", "ExternalName"}:
            errors.append(f"Service object {service_object['id']} has unsafe type {service_object['type']}")
        for port_spec in service_object.get("ports", []) + service_object.get("tlsPorts", []):
            required_port = {"name", "port", "targetPort", "protocol"}
            allowed_port = required_port | {"appProtocol"}
            if set(port_spec) - allowed_port or required_port - set(port_spec):
                errors.append(f"Service object {service_object['id']} has an inexact port contract {port_spec}")
    policy_services = set()
    contract_peers = {}
    for contract in matrix.get("networkPolicies", []):
        service = contract.get("service")
        if service in policy_services:
            errors.append(f"duplicate NetworkPolicy contract for {service}")
        policy_services.add(service)
        if contract.get("owner") not in matrix.get("ownership", {}):
            errors.append(f"NetworkPolicy contract {service} has unknown owner")
        for governed_rule in contract.get("rules", []):
            reference = (service, governed_rule.get("listenerId"))
            if reference not in seen:
                errors.append(f"NetworkPolicy contract {reference} references a missing listener")
            contract_peers.setdefault(reference, set()).update(governed_rule.get("peers", []))
    for listener in matrix.get("listeners", []):
        reference = (listener.get("service"), listener.get("listenerId"))
        allowed = set(listener.get("allowedPeers") or [])
        governed = contract_peers.get(reference, set())
        if allowed != governed:
            errors.append(
                f"listener {reference} allowedPeers {sorted(allowed)} != policy tokens {sorted(governed)}"
            )
        authorized = set(listener.get("authorizedCallers") or [])
        for token in allowed - {"any"}:
            if token not in authorized:
                errors.append(f"listener {reference} allows {token} but does not authorize it")
        if "any" in allowed and "none" in str(listener.get("authentication", "")).lower():
            errors.append(f"listener {reference} allows any peer without authentication")
    validate_console_peers(values, errors)

    active_listeners = [
        item for item in matrix.get("listeners", [])
        if condition_enabled(item["enabledWhen"], values)
    ]

    service_docs = [d for d in docs if d.get("kind") == "Service"]
    service_names = [d.get("metadata", {}).get("name") for d in service_docs]
    if len(service_names) != len(set(service_names)):
        errors.append("rendered Service names are not unique")
    services = {d["metadata"]["name"]: d for d in service_docs}
    try:
        expected = expected_services(matrix, values, release, namespace)
    except ValueError as exc:
        errors.append(str(exc))
        expected = {}
    if set(services) != set(expected):
        errors.append(f"Service inventory mismatch: missing={sorted(set(expected)-set(services))} extra={sorted(set(services)-set(expected))}")
    for name in sorted(set(services) & set(expected)):
        spec = normalized_service_spec(services[name].get("spec", {}))
        if spec.get("type") in {"LoadBalancer", "NodePort"}:
            errors.append(f"Service {name} is externally reachable type {spec['type']}")
        if spec != expected[name]:
            errors.append(
                f"Service {name} spec does not exactly match the governed contract: "
                f"actual={json.dumps(spec, sort_keys=True)} expected={json.dumps(expected[name], sort_keys=True)}"
            )

    publications = {}
    for listener in active_listeners:
        if listener["servicePublished"]:
            publications.setdefault(listener["service"], set()).add(int(listener["servicePort"]))
    aliases = {"proxy-alias": "proxy", "nats-headless": "nats", "vault-internal": "vault"}
    published_by_objects = set()
    tls = bool(value_at(values, "tlsBundle.secretName"))
    for obj in matrix["serviceObjects"]:
        if not condition_enabled(obj["enabledWhen"], values):
            continue
        service = aliases.get(obj["id"], obj["id"])
        ports = list(obj["ports"]) + (list(obj.get("tlsPorts", [])) if tls else [])
        for port_spec in ports:
            port = int(port_spec["port"])
            published_by_objects.add((service, port))
            if port not in publications.get(service, set()):
                errors.append(f"Service object {obj['id']} port {port} has no active published listener")
    for service, ports in publications.items():
        for port in ports:
            if (service, port) not in published_by_objects:
                errors.append(f"published listener {service}:{port} has no governed Service object")

    workload_docs = [
        doc for doc in docs if doc.get("kind") in {"Deployment", "StatefulSet", "DaemonSet"}
    ]
    pod_specs = []
    for doc in docs:
        kind = doc.get("kind")
        if kind == "Pod":
            pod_specs.append((f"Pod/{doc.get('metadata', {}).get('name')}", doc.get("spec", {})))
        elif kind in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
            pod_specs.append((
                f"{kind}/{doc.get('metadata', {}).get('name')}",
                doc.get("spec", {}).get("template", {}).get("spec", {}),
            ))
        elif kind == "CronJob":
            pod_specs.append((
                f"CronJob/{doc.get('metadata', {}).get('name')}",
                doc.get("spec", {}).get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {}),
            ))
    for resource_name, pod_spec in pod_specs:
        if pod_spec.get("hostNetwork"):
            errors.append(f"{resource_name} uses forbidden hostNetwork")
        for field in ("initContainers", "containers", "ephemeralContainers"):
            for container in pod_spec.get(field, []) or []:
                for port_spec in container.get("ports", []) or []:
                    if "hostPort" in port_spec:
                        errors.append(f"{resource_name} uses forbidden hostPort")
                    if "hostIP" in port_spec:
                        errors.append(f"{resource_name} uses forbidden hostIP")
    service_targets = {
        service: workload_selector(release, service)
        for service in {item["service"] for item in active_listeners}
    }
    workload_labels = []
    rendered_by_service = {service: [] for service in service_targets}
    for doc in workload_docs:
        template = doc.get("spec", {}).get("template", {})
        labels = template.get("metadata", {}).get("labels", {})
        workload_labels.append(labels)
        pod_spec = template.get("spec", {})
        workload_name = f"{doc.get('kind')}/{doc.get('metadata', {}).get('name')}"
        containers = []
        for field in ("initContainers", "containers", "ephemeralContainers"):
            containers.extend(pod_spec.get(field, []) or [])
        exposed_ports = []
        for container in containers:
            for port_spec in container.get("ports", []) or []:
                if "containerPort" in port_spec:
                    exposed_ports.append(int(port_spec["containerPort"]))
        matching_services = [
            service for service, target in service_targets.items()
            if all(labels.get(key) == value for key, value in target.items())
        ]
        if exposed_ports and not matching_services:
            errors.append(
                f"unlisted listening workload {workload_name} exposes container ports {sorted(exposed_ports)}"
            )
        if len(matching_services) > 1:
            errors.append(f"{workload_name} ambiguously matches listener services {matching_services}")
        for service in matching_services:
            rendered_by_service[service].extend(exposed_ports)
            for container in pod_spec.get("containers", []) or []:
                named_ports = {
                    port_spec.get("name"): int(port_spec["containerPort"])
                    for port_spec in container.get("ports", []) or []
                    if port_spec.get("name") and "containerPort" in port_spec
                }
                for probe_name in ("startupProbe", "livenessProbe", "readinessProbe"):
                    http_get = (container.get(probe_name) or {}).get("httpGet")
                    if not http_get:
                        continue
                    probe_port = http_get.get("port")
                    if isinstance(probe_port, int):
                        resolved_port = probe_port
                    elif isinstance(probe_port, str) and probe_port.isdigit():
                        resolved_port = int(probe_port)
                    else:
                        resolved_port = named_ports.get(probe_port)
                    if resolved_port is None:
                        errors.append(
                            f"{workload_name} {probe_name} has unresolved HTTP port {probe_port!r}"
                        )
                        continue
                    probe_listeners = [
                        listener for listener in active_listeners
                        if listener["service"] == service
                        and int(listener["containerPort"]) == resolved_port
                    ]
                    if not probe_listeners:
                        errors.append(
                            f"{workload_name} {probe_name} port {resolved_port} has no active listener"
                        )
                    elif not any(
                        "kubelet" in (listener.get("authorizedCallers") or [])
                        for listener in probe_listeners
                    ):
                        errors.append(
                            f"{workload_name} {probe_name} port {resolved_port} is not authorized for kubelet"
                        )
    for service, target in service_targets.items():
        governed_ports = sorted(
            int(item["containerPort"])
            for item in active_listeners if item["service"] == service
        )
        rendered_ports = sorted(rendered_by_service[service])
        if rendered_ports != governed_ports:
            errors.append(
                f"workload {service} container ports {rendered_ports} != active listeners {governed_ports}"
            )
    policy_docs = [d for d in docs if d.get("kind") == "NetworkPolicy"]
    policy_names = [d.get("metadata", {}).get("name") for d in policy_docs]
    if len(policy_names) != len(set(policy_names)):
        errors.append("rendered NetworkPolicy names are not unique")
    actual_policies = {d["metadata"]["name"]: d for d in policy_docs}
    try:
        expected_p = expected_policies(matrix, values, release)
    except ValueError as exc:
        errors.append(str(exc))
        expected_p = {}
    if set(actual_policies) != set(expected_p):
        errors.append(f"NetworkPolicy inventory mismatch: missing={sorted(set(expected_p)-set(actual_policies))} extra={sorted(set(actual_policies)-set(expected_p))}")
    for name in sorted(set(actual_policies) & set(expected_p)):
        annotation = actual_policies[name].get("metadata", {}).get("annotations", {}).get(
            "clavenar.io/listener-matrix-sha256"
        )
        if annotation != digest:
            errors.append(f"NetworkPolicy {name} listener-matrix digest {annotation!r} != {digest!r}")
        spec = actual_policies[name].get("spec", {})
        target = spec.get("podSelector", {}).get("matchLabels", {})
        if not any(all(labels.get(k) == v for k, v in target.items()) for labels in workload_labels):
            errors.append(f"NetworkPolicy {name} selector matches no rendered workload")
        if spec != expected_p[name]:
            errors.append(f"NetworkPolicy {name} spec does not exactly match the governed contract")
        ingress = spec.get("ingress") or []
        for entry in ingress:
            if not entry.get("ports") or len(entry["ports"]) != 1:
                errors.append(f"NetworkPolicy {name} has a portless or multi-port ingress rule")
    validate_vault_test_hook(matrix, values, docs, release, namespace, expected_p, errors)
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--chart", default="charts/clavenar", type=Path)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--values", action="append", default=[])
    parser.add_argument("--release", default="smoke")
    parser.add_argument("--namespace", default="default")
    args = parser.parse_args(argv)
    matrix_path = args.matrix or args.chart / "listeners.yaml"
    matrix = yaml.safe_load(matrix_path.read_text())
    values = effective_values(args.chart, args.values)
    docs = [d for d in yaml.safe_load_all(args.manifest.read_text()) if isinstance(d, dict)]
    errors = validate(matrix, values, docs, args.release, args.namespace)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"listener matrix OK: {len(matrix['listeners'])} listeners, "
          f"{sum(d.get('kind') == 'Service' for d in docs)} Services, "
          f"{sum(d.get('kind') == 'NetworkPolicy' for d in docs)} NetworkPolicies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
