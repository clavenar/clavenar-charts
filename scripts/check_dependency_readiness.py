#!/usr/bin/env python3
"""Verify the public dependency-readiness contract in Helm renders."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/clavenar"
SCHEMA = CHART / "files/dependency-readiness-v1.schema.json"
FIXTURE = CHART / "files/dependency-readiness-v1.fixture.json"
RELEASE = "readiness"
APPLICATIONS = {
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
HELM_INVENTORY = APPLICATIONS | {"nats", "vault", "upstream-stub"}
VALUES_CASES = {
    "default": None,
    "bundled": ROOT / "tests/values-bundled.yaml",
    "production": ROOT / "tests/values-production.yaml",
}


class ReadinessError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReadinessError(message)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def values_for(path: Path | None) -> dict[str, Any]:
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    if path is not None:
        values = deep_merge(values, yaml.safe_load(path.read_text()))
    return values


def render(path: Path | None) -> list[dict[str, Any]]:
    command = ["helm", "template", RELEASE, str(CHART)]
    if path is not None:
        command.extend(["-f", str(path)])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ReadinessError(f"Helm render failed: {result.stderr.strip()}")
    return [
        item
        for item in yaml.safe_load_all(result.stdout)
        if isinstance(item, dict)
    ]


def contract_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    schema = json.loads(SCHEMA.read_text())
    fixture = json.loads(FIXTURE.read_text())
    require(schema.get("type") == "object", "readiness schema root must be an object")
    require(
        fixture.get("contract") == "clavenar.dependency-readiness/v1",
        "unexpected readiness contract identifier",
    )
    require(fixture.get("schemaVersion") == 1, "unexpected readiness schema version")
    services = fixture.get("services")
    require(isinstance(services, list), "readiness services must be an array")
    ids = [item.get("id") for item in services]
    require(len(ids) == len(set(ids)), "readiness service IDs must be unique")
    helm = {
        item["id"]
        for item in services
        if "helm" in item.get("topologies", [])
    }
    require(helm == HELM_INVENTORY, f"unexpected Helm readiness inventory: {sorted(helm)}")
    return schema, fixture


def verify_source_mirror(source_root: Path | None, required: bool) -> int:
    if source_root is None:
        require(not required, "--require-source needs --source-root")
        return 0
    specs = source_root / "clavenar-specs"
    if source_root.name == "clavenar-specs":
        specs = source_root
    source_contracts = specs / "contracts"
    checks = 0
    for local in (SCHEMA, FIXTURE):
        source = source_contracts / local.name
        require(source.is_file(), f"missing public source mirror {source}")
        require(local.read_bytes() == source.read_bytes(), f"public mirror drift: {local.name}")
        checks += 1
    return checks


def contract_services(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["id"]: item
        for item in fixture["services"]
        if "helm" in item.get("topologies", [])
    }


def helm_dependencies(service: dict[str, Any]) -> list[str]:
    return [
        edge["service"]
        for edge in service["startupAfter"]
        if "helm" in edge.get("topologies", [])
    ]


def endpoint_port_path(target: str) -> tuple[int, str]:
    parsed = urlparse(target)
    require(parsed.scheme == "http", f"contract endpoint must be HTTP: {target}")
    require(parsed.port is not None, f"contract endpoint requires a port: {target}")
    return parsed.port, parsed.path


def target_url(
    dependency: str,
    services: dict[str, dict[str, Any]],
    values: dict[str, Any],
) -> str:
    if dependency == "nats":
        if values["nats"]["bundled"]["enabled"]:
            return f"http://{RELEASE}-nats:8222/healthz?js-enabled-only=true"
        return values["nats"]["readinessUrl"]
    if dependency == "vault":
        if values["vault"]["bundled"]["enabled"]:
            return f"http://{RELEASE}-vault:8200/v1/sys/health"
        return values["vault"]["readinessUrl"]
    if dependency == "upstream-stub":
        if values["upstreamStub"]["enabled"]:
            return (
                f"http://{RELEASE}-upstream-stub:"
                f"{values['upstreamStub']['port']}/readyz"
            )
        return values["upstreamStub"]["readinessUrl"]
    port, path = endpoint_port_path(services[dependency]["readiness"]["target"])
    return f"http://{RELEASE}-{dependency}:{port}{path}"


def env_map(deployment: dict[str, Any]) -> dict[str, Any]:
    env = deployment["spec"]["template"]["spec"]["containers"][0].get("env", [])
    return {item["name"]: item.get("value") for item in env}


def deployment_map(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    prefix = f"{RELEASE}-"
    return {
        item["metadata"]["name"].removeprefix(prefix): item
        for item in documents
        if item.get("kind") == "Deployment"
        and item["metadata"]["name"].startswith(prefix)
    }


def service_map(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        item["metadata"]["name"]: item
        for item in documents
        if item.get("kind") == "Service"
    }


def network_policy_allows(
    documents: list[dict[str, Any]], destination: str, port: int, caller: str
) -> bool:
    name = f"{RELEASE}-{destination}"
    policy = next(
        (
            item
            for item in documents
            if item.get("kind") == "NetworkPolicy"
            and item["metadata"]["name"] == name
        ),
        None,
    )
    if policy is None:
        return False
    for rule in policy["spec"].get("ingress", []):
        ports = {item.get("port") for item in rule.get("ports", [])}
        callers = {
            peer.get("podSelector", {})
            .get("matchLabels", {})
            .get("app.kubernetes.io/component")
            for peer in rule.get("from", [])
        }
        if port in ports and caller in callers:
            return True
    return False


def verify_configmap(
    documents: list[dict[str, Any]], schema: dict[str, Any], fixture: dict[str, Any]
) -> int:
    del schema, fixture
    config = next(
        item
        for item in documents
        if item.get("kind") == "ConfigMap"
        and item["metadata"]["name"] == f"{RELEASE}-dependency-readiness-contract"
    )
    require(config.get("immutable") is True, "readiness ConfigMap must be immutable")
    for path in (SCHEMA, FIXTURE):
        require(
            config["data"][path.name].encode() == path.read_bytes(),
            f"ConfigMap byte drift: {path.name}",
        )
    return 3


def verify_case(
    case: str,
    path: Path | None,
    services: dict[str, dict[str, Any]],
    schema: dict[str, Any],
    fixture: dict[str, Any],
) -> int:
    documents = render(path)
    values = values_for(path)
    deployments = deployment_map(documents)
    rendered_services = service_map(documents)
    checks = verify_configmap(documents, schema, fixture)
    require(APPLICATIONS <= deployments.keys(), f"{case}: missing application Deployment")

    for name in sorted(APPLICATIONS):
        contract = services[name]
        deployment = deployments[name]
        pod = deployment["spec"]["template"]
        container = pod["spec"]["containers"][0]
        live_port, live_path = endpoint_port_path(contract["liveness"]["target"])
        ready_port, ready_path = endpoint_port_path(contract["readiness"]["target"])
        require(
            container["livenessProbe"]["httpGet"] == {"path": live_path, "port": live_port},
            f"{case}/{name}: liveness probe drift",
        )
        require(
            container["readinessProbe"]["httpGet"]
            == {"path": ready_path, "port": ready_port},
            f"{case}/{name}: readiness probe drift",
        )
        require(live_path != ready_path, f"{case}/{name}: liveness aliases readiness")
        annotations = pod["metadata"]["annotations"]
        require(
            "checksum/dependency-readiness-schema" in annotations
            and "checksum/dependency-readiness-fixture" in annotations,
            f"{case}/{name}: readiness contract checksum is missing",
        )
        dependencies = helm_dependencies(contract)
        if dependencies:
            gate = next(
                (
                    item
                    for item in pod["spec"].get("initContainers", [])
                    if item["name"] == "dependency-readiness-gate"
                ),
                None,
            )
            require(gate is not None, f"{case}/{name}: startup gate is missing")
            script = gate["args"][0]
            require("wget -q -T 2 -O /dev/null" in script, f"{case}/{name}: probe timeout drift")
            require('"$attempt" -ge 30' in script, f"{case}/{name}: failure threshold drift")
            for dependency in dependencies:
                require(
                    target_url(dependency, services, values) in script,
                    f"{case}/{name}: startup edge {dependency} is missing",
                )
        checks += 5 + len(dependencies)

    proxy_env = env_map(deployments["proxy"])
    proxy_names = {
        "brain": "CLAVENAR_BRAIN_READINESS_URL",
        "policy-engine": "CLAVENAR_POLICY_READINESS_URL",
        "hil": "CLAVENAR_HIL_READINESS_URL",
        "ledger": "CLAVENAR_LEDGER_READINESS_URL",
        "identity": "CLAVENAR_IDENTITY_READINESS_URL",
        "upstream-stub": "CLAVENAR_UPSTREAM_READINESS_URL",
    }
    for dependency, variable in proxy_names.items():
        require(
            proxy_env.get(variable) == target_url(dependency, services, values),
            f"{case}/proxy: runtime readiness URL drift for {dependency}",
        )

    console_env = env_map(deployments["console"])
    for dependency in ("brain", "policy-engine", "ledger", "hil", "identity", "assurance"):
        token = dependency.replace("-engine", "").replace("-", "_").upper()
        variable = f"CLAVENAR_CONSOLE_{token}_READINESS_URL"
        require(
            console_env.get(variable) == target_url(dependency, services, values),
            f"{case}/console: runtime readiness URL drift for {dependency}",
        )
    require(
        "CLAVENAR_CONSOLE_SIMULATOR_READINESS_URL" not in console_env,
        f"{case}/console: Compose-only Simulator must be omitted",
    )
    assurance_env = env_map(deployments["assurance"])
    require(
        assurance_env.get("CLAVENAR_ASSURANCE_PROXY_READINESS_URL")
        == target_url("proxy", services, values),
        f"{case}/assurance: Proxy readiness URL drift",
    )
    checks += 14

    for destination in ("brain", "policy-engine", "ledger", "hil", "identity", "proxy", "assurance"):
        port, _ = endpoint_port_path(services[destination]["readiness"]["target"])
        service = rendered_services[f"{RELEASE}-{destination}"]
        require(
            port in {item["port"] for item in service["spec"]["ports"]},
            f"{case}/{destination}: readiness Service port {port} is missing",
        )
    checks += 7

    if case == "bundled":
        require(
            8222
            in {
                item["port"]
                for item in rendered_services[f"{RELEASE}-nats"]["spec"]["ports"]
            },
            "bundled/NATS: monitoring Service port is missing",
        )
        for destination, caller in (
            ("nats", "identity"),
            ("vault", "identity"),
            ("identity", "brain"),
            ("brain", "proxy"),
            ("policy-engine", "proxy"),
            ("ledger", "proxy"),
            ("hil", "proxy"),
            ("proxy", "assurance"),
            ("assurance", "console"),
            ("upstream-stub", "proxy"),
        ):
            port = (
                8222
                if destination == "nats"
                else 8200
                if destination == "vault"
                else endpoint_port_path(services[destination]["readiness"]["target"])[0]
            )
            require(
                network_policy_allows(documents, destination, port, caller),
                f"bundled/{destination}: readiness caller {caller} is denied",
            )
        checks += 11
    return checks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--require-source", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        schema, fixture = contract_documents()
        checks = verify_source_mirror(args.source_root, args.require_source)
        services = contract_services(fixture)
        for case, path in VALUES_CASES.items():
            checks += verify_case(case, path, services, schema, fixture)
    except (OSError, ValueError, KeyError, StopIteration, ReadinessError) as error:
        print(f"dependency readiness check failed: {error}", file=sys.stderr)
        return 1
    print(f"dependency readiness check passed ({checks} Helm evidence checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
