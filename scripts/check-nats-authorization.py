#!/usr/bin/env python3
"""Validate Helm parity with clavenar.nats-authorization/v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
CONTRACT = CHART / "files" / "nats-authorization-v1.fixture.json"
BUNDLED_VALUES = ROOT / "tests" / "values-bundled.yaml"
CHART_CLIENTS = {
    "assurance",
    "deep-review",
    "hil",
    "identity",
    "ledger",
    "policy-engine",
    "proxy",
}


class ContractError(ValueError):
    """A sanitized contract failure suitable for CI logs."""


def _fail(scope: str, reason: str) -> None:
    raise ContractError(f"{scope}: {reason}")


def permissions(contract: dict, service: str) -> tuple[set[str], set[str]]:
    client = next(item for item in contract["clients"] if item["service"] == service)
    publish: set[str] = set()
    subscribe = {f'{client["inboxPrefix"]}.>'}
    for subject in contract["subjects"]:
        subject_name = subject["subject"]
        stream = subject["stream"]
        if service in subject["publishers"]:
            publish.add(subject_name)
            if stream:
                # Workloads perform an exact metadata lookup before publishing;
                # creation authority remains limited to the stream manager.
                publish.add(f"$JS.API.STREAM.INFO.{stream}")
        if service in subject["subscribers"]:
            subscribe.add(subject_name)
        if stream and service == subject["streamManager"]:
            publish.update(
                {f"$JS.API.STREAM.INFO.{stream}", f"$JS.API.STREAM.CREATE.{stream}"}
            )
        for consumer in subject["durableConsumers"]:
            if service == consumer["service"]:
                durable = consumer["name"]
                publish.update(
                    {
                        f"$JS.API.STREAM.INFO.{stream}",
                        f"$JS.API.CONSUMER.INFO.{stream}.{durable}",
                        f"$JS.API.CONSUMER.CREATE.{stream}.{durable}",
                        f"$JS.API.CONSUMER.CREATE.{stream}.{durable}.>",
                        f"$JS.API.CONSUMER.MSG.NEXT.{stream}.{durable}",
                        f"$JS.ACK.{stream}.{durable}.>",
                    }
                )
    for bucket in contract["kvBuckets"]:
        bucket_name = bucket["bucket"]
        stream = f"KV_{bucket_name}"
        if service in bucket["readers"]:
            publish.update(
                {
                    f"$JS.API.STREAM.INFO.{stream}",
                    f"$JS.API.DIRECT.GET.{stream}.>",
                    f"$JS.API.STREAM.MSG.GET.{stream}",
                    f"$JS.API.CONSUMER.CREATE.{stream}",
                    f"$JS.API.CONSUMER.CREATE.{stream}.>",
                    f"$JS.API.CONSUMER.INFO.{stream}.>",
                    f"$JS.API.CONSUMER.DELETE.{stream}.>",
                    f"$JS.API.CONSUMER.MSG.NEXT.{stream}.>",
                    f"$JS.ACK.{stream}.>",
                }
            )
            subscribe.add(f"$KV.{bucket_name}.>")
        if service in bucket["writers"]:
            publish.add(f"$KV.{bucket_name}.>")
        if service == bucket["manager"]:
            publish.update({"$JS.API.INFO", f"$JS.API.STREAM.CREATE.{stream}"})
    return publish, subscribe


def expected_authorization(contract: dict) -> dict:
    users = []
    for client in contract["clients"]:
        publish, subscribe = permissions(contract, client["service"])
        users.append(
            {
                "user": client["dnsSan"],
                "permissions": {
                    "publish": {"allow": sorted(publish)},
                    "subscribe": {"allow": sorted(subscribe)},
                },
            }
        )
    return {"timeout": 2, "users": users}


def sync_values_authorization() -> None:
    """Rewrite only the generated inline authorization value."""
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    path = CHART / "values.yaml"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    marker = "      authorization: "
    matches = [index for index, line in enumerate(lines) if line.startswith(marker)]
    if len(matches) != 1:
        _fail("values authorization", "expected one generated inline value")
    compact = json.dumps(expected_authorization(contract), separators=(",", ":"))
    lines[matches[0]] = marker + compact
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    validation_path = CHART / "templates" / "validate.yaml"
    validation = validation_path.read_text(encoding="utf-8")
    # Helm's toRawJson uses Go's deterministic map-key ordering.
    canonical = json.dumps(
        expected_authorization(contract), separators=(",", ":"), sort_keys=True
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    pattern = r'(?m)(if ne \$authorizationDigest ")[0-9a-f]{64}(" \}\})'
    validation, count = re.subn(pattern, rf"\g<1>{digest}\g<2>", validation)
    if count != 1:
        _fail("values authorization", "expected one validation digest")
    validation_path.write_text(validation, encoding="utf-8")


def _authorization_from_nats_config(text: str) -> dict:
    marker = '"authorization": '
    start = text.find(marker)
    if start < 0:
        _fail("rendered NATS config", "authorization block is missing")
    value, _end = json.JSONDecoder().raw_decode(text[start + len(marker) :])
    if not isinstance(value, dict):
        _fail("rendered NATS config", "authorization must be an object")
    return value


def _render(chart: Path, bundled_values: Path) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "smoke", str(chart), "-f", str(bundled_values)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        _fail("bundled render", result.stderr.strip().splitlines()[-1])
    return [item for item in yaml.safe_load_all(result.stdout) if isinstance(item, dict)]


def check_repository(
    root: Path = ROOT,
    chart: Path | None = None,
    bundled_values: Path | None = None,
) -> dict[str, int]:
    chart = chart or root / "charts" / "clavenar"
    bundled_values = bundled_values or root / "tests" / "values-bundled.yaml"
    contract_path = chart / "files" / "nats-authorization-v1.fixture.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("contract") != "clavenar.nats-authorization/v1":
        _fail("contract", "unexpected version")
    for sibling in (
        root.parent / "clavenar-specs" / "contracts" / "nats-authorization-v1.fixture.json",
        root.parent / "clavenar-e2e" / "NATS_AUTHORIZATION_CONTRACT.json",
    ):
        if sibling.exists() and sibling.read_bytes() != contract_path.read_bytes():
            _fail("contract mirror", f"must be byte-identical to {sibling.name}")

    values = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))
    expected = expected_authorization(contract)
    actual = values["nats"]["config"]["merge"]["authorization"]
    if actual != expected:
        _fail("values authorization", "must equal the generated v1 permissions")
    jetstream = values["nats"]["config"]["jetstream"]
    if not (
        jetstream["enabled"]
        and jetstream["fileStore"]["enabled"]
        and jetstream["fileStore"]["pvc"]["enabled"]
    ):
        _fail("values persistence", "bundled JetStream must use a persistent PVC")
    if values["nats"]["config"]["nats"]["tls"]["merge"] != {
        "verify_and_map": True
    }:
        _fail("values TLS", "must use only verify_and_map")

    documents = _render(chart, bundled_values)
    config_map = next(
        (
            item
            for item in documents
            if item.get("kind") == "ConfigMap"
            and item.get("metadata", {}).get("name") == "smoke-nats-config"
        ),
        None,
    )
    if config_map is None:
        _fail("bundled render", "NATS ConfigMap is missing")
    nats_config = config_map["data"]["nats.conf"]
    if _authorization_from_nats_config(nats_config) != expected:
        _fail("rendered NATS config", "authorization permission drift")
    if '"verify_and_map": true' not in nats_config or '"verify": true' in nats_config:
        _fail("rendered NATS config", "certificate mapping drift")
    if '"store_dir": "/data"' not in nats_config:
        _fail("rendered NATS config", "persistent JetStream store drift")

    stateful_set = next(
        item
        for item in documents
        if item.get("kind") == "StatefulSet"
        and item.get("metadata", {}).get("name") == "smoke-nats"
    )
    claims = stateful_set["spec"].get("volumeClaimTemplates", [])
    if len(claims) != 1 or claims[0]["metadata"]["name"] != "smoke-nats-js":
        _fail("bundled NATS StatefulSet", "must retain one exact JetStream PVC")

    network_policy = next(
        item
        for item in documents
        if item.get("kind") == "NetworkPolicy"
        and item.get("metadata", {}).get("name") == "smoke-nats"
    )
    ingress = network_policy["spec"].get("ingress", [])
    broker_rule = next(
        (
            rule
            for rule in ingress
            if rule.get("ports") == [{"protocol": "TCP", "port": 4222}]
        ),
        None,
    )
    if broker_rule is None:
        _fail("NATS NetworkPolicy", "exact TCP 4222 rule is missing")
    peers = {
        peer.get("podSelector", {}).get("matchLabels", {}).get(
            "app.kubernetes.io/component"
        )
        for peer in broker_rule.get("from", [])
    }
    if peers != CHART_CLIENTS:
        _fail("NATS NetworkPolicy", "peers must equal the seven chart clients")

    deployments = {
        item["metadata"]["name"].removeprefix("smoke-"): item
        for item in documents
        if item.get("kind") == "Deployment"
        and item.get("metadata", {}).get("name", "").startswith("smoke-")
    }
    actual_clients: set[str] = set()
    for name, deployment in deployments.items():
        container = next(
            (
                item
                for item in deployment["spec"]["template"]["spec"]["containers"]
                if item.get("name") == name
            ),
            None,
        )
        if container is None:
            continue
        environment = {item["name"]: item for item in container.get("env", [])}
        if "NATS_URL" not in environment:
            if name in {"brain", "console"} and "NATS_INBOX_PREFIX" in environment:
                _fail(name, "unused inbox authority must remain absent")
            continue
        actual_clients.add(name)
        prefix = environment.get("NATS_INBOX_PREFIX", {}).get("value")
        if prefix != f"_INBOX.clavenar.{name}":
            _fail(name, "private inbox prefix drift")
        cert = environment.get("NATS_TLS_CERT_PATH", {}).get("value")
        if cert != f"/certs/service-{name}.crt":
            _fail(name, "must use its exact workload certificate")
    if actual_clients != CHART_CLIENTS:
        _fail("rendered clients", "must equal the seven chart clients")
    return {"users": len(expected["users"]), "chartClients": len(actual_clients)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-values",
        action="store_true",
        help="synchronize the generated values.yaml authorization block",
    )
    args = parser.parse_args()
    try:
        if args.write_values:
            sync_values_authorization()
        counts = check_repository()
    except (ContractError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"NATS authorization check failed: {error}", file=sys.stderr)
        return 1
    print(
        "NATS authorization check passed "
        f"({counts['users']} users, {counts['chartClients']} chart clients)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
