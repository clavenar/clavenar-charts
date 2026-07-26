#!/usr/bin/env python3
import copy
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/clavenar"
SCHEMA_PATH = CHART / "files/structured-execution-v1.schema.json"
FIXTURE_PATH = CHART / "files/structured-execution-v1.fixture.json"
OPTIONAL_VALUES = ROOT / "tests/values-optional.yaml"


def render_optional():
    output = subprocess.run(
        ["helm", "template", "structured", str(CHART), "-f", str(OPTIONAL_VALUES)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [
        document
        for document in yaml.safe_load_all(output)
        if isinstance(document, dict)
    ]


def test_contract_is_strict_and_rendered_byte_exact():
    schema = json.loads(SCHEMA_PATH.read_text())
    fixture = json.loads(FIXTURE_PATH.read_text())
    jsonschema.Draft7Validator(schema).validate(fixture)

    weakened = copy.deepcopy(fixture)
    weakened["process"]["shell"] = True
    with unittest.TestCase().assertRaises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(weakened)

    config_map = next(
        document
        for document in render_optional()
        if document.get("kind") == "ConfigMap"
        and document["metadata"]["name"] == "structured-structured-execution"
    )
    assert config_map["immutable"] is True
    assert json.loads(config_map["data"][SCHEMA_PATH.name]) == schema
    assert json.loads(config_map["data"][FIXTURE_PATH.name]) == fixture


def test_exec_binds_digest_policy_and_real_container_isolation():
    documents = render_optional()
    deployment = next(
        document
        for document in documents
        if document.get("kind") == "Deployment"
        and document["metadata"]["name"] == "structured-exec"
    )
    template = deployment["spec"]["template"]
    pod = template["spec"]
    container = next(item for item in pod["containers"] if item["name"] == "exec")
    fixture_bytes = FIXTURE_PATH.read_bytes()

    assert template["metadata"]["annotations"][
        "clavenar.io/structured-execution-sha256"
    ] == hashlib.sha256(fixture_bytes).hexdigest()
    assert container["image"] == (
        "ghcr.io/clavenar/clavenar-exec@"
        "sha256:6bcabdcf6f211299a38a786e7f3e11eede1b92ccb31dfb38f0323fe35b0592ff"
    )
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["runAsUser"] == 65532
    assert pod["securityContext"]["runAsGroup"] == 65532
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert container["securityContext"] == {
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }

    env = {item["name"]: item["value"] for item in container["env"]}
    assert "CLAVENAR_EXEC_TIMEOUT_SECS" not in env
    assert env["CLAVENAR_EXEC_STRUCTURED_POLICY_FILE"] == (
        "/etc/clavenar/structured-execution/"
        "structured-execution-v1.fixture.json"
    )
    assert env["TMPDIR"] == "/scratch"

    mounts = {item["mountPath"]: item for item in container["volumeMounts"]}
    assert mounts["/workspace"].get("readOnly") is not True
    assert mounts["/scratch"].get("readOnly") is not True
    assert mounts["/etc/clavenar/structured-execution"]["readOnly"] is True
    assert mounts["/certs"]["readOnly"] is True
    volumes = {item["name"]: item for item in pod["volumes"]}
    assert volumes["scratch"]["emptyDir"] == {
        "medium": "Memory",
        "sizeLimit": "64Mi",
    }
    assert volumes["structured-execution"]["configMap"] == {
        "name": "structured-structured-execution",
        "items": [
            {
                "key": "structured-execution-v1.fixture.json",
                "path": "structured-execution-v1.fixture.json",
            }
        ],
    }


def test_exec_egress_is_default_deny_with_only_dns_and_fallback():
    policy = next(
        document
        for document in render_optional()
        if document.get("kind") == "NetworkPolicy"
        and document["metadata"]["name"] == "structured-exec"
    )
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert policy["spec"]["egress"] == [
        {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {
                            "kubernetes.io/metadata.name": "kube-system"
                        }
                    }
                }
            ],
            "ports": [
                {"protocol": "UDP", "port": 53},
                {"protocol": "TCP", "port": 53},
            ],
        },
        {
            "to": [
                {
                    "podSelector": {
                        "matchLabels": {
                            "app.kubernetes.io/name": "clavenar",
                            "app.kubernetes.io/instance": "structured",
                            "app.kubernetes.io/component": "upstream-stub",
                        }
                    }
                }
            ],
            "ports": [{"protocol": "TCP", "port": 9000}],
        },
    ]


def test_mutable_or_weakened_exec_values_fail():
    cases = [
        (
            [
                "-f",
                str(OPTIONAL_VALUES),
                "--set-string",
                "exec.image.digest=",
            ],
            "requires exec.image.digest",
        ),
        (
            [
                "-f",
                str(OPTIONAL_VALUES),
                "--set-string",
                "exec.scratchSizeLimit=128Mi",
            ],
            "scratchSizeLimit",
        ),
        (
            [
                "-f",
                str(OPTIONAL_VALUES),
                "--set",
                "exec.image.tag=latest",
            ],
            "Additional property tag is not allowed",
        ),
    ]
    for arguments, message in cases:
        result = subprocess.run(
            ["helm", "template", "structured", str(CHART), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert message in result.stderr


class StructuredExecutionTests(unittest.TestCase):
    def test_contract_is_strict_and_rendered_byte_exact(self):
        test_contract_is_strict_and_rendered_byte_exact()

    def test_exec_binds_digest_policy_and_real_container_isolation(self):
        test_exec_binds_digest_policy_and_real_container_isolation()

    def test_exec_egress_is_default_deny_with_only_dns_and_fallback(self):
        test_exec_egress_is_default_deny_with_only_dns_and_fallback()

    def test_mutable_or_weakened_exec_values_fail(self):
        test_mutable_or_weakened_exec_values_fail()
