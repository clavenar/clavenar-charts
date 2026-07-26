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
SCHEMA_PATH = CHART / "files/execution-ceilings-v1.schema.json"
FIXTURE_PATH = CHART / "files/execution-ceilings-v1.fixture.json"
OPTIONAL_VALUES = ROOT / "tests/values-optional.yaml"


def render_optional():
    output = subprocess.run(
        ["helm", "template", "ceilings", str(CHART), "-f", str(OPTIONAL_VALUES)],
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
    weakened["process"]["wallClockMillis"] = 60000
    with unittest.TestCase().assertRaises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(weakened)

    config_map = next(
        document
        for document in render_optional()
        if document.get("kind") == "ConfigMap"
        and document["metadata"]["name"] == "ceilings-execution-ceilings"
    )
    assert config_map["immutable"] is True
    assert json.loads(config_map["data"][SCHEMA_PATH.name]) == schema
    assert json.loads(config_map["data"][FIXTURE_PATH.name]) == fixture


def test_exec_binds_fixed_contract_and_resources():
    deployment = next(
        document
        for document in render_optional()
        if document.get("kind") == "Deployment"
        and document["metadata"]["name"] == "ceilings-exec"
    )
    template = deployment["spec"]["template"]
    pod = template["spec"]
    container = next(item for item in pod["containers"] if item["name"] == "exec")

    assert template["metadata"]["annotations"][
        "clavenar.io/execution-ceilings-sha256"
    ] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert container["resources"] == {
        "requests": {"cpu": "50m", "memory": "64Mi"},
        "limits": {"cpu": "250m", "memory": "256Mi"},
    }
    assert "CLAVENAR_EXEC_TIMEOUT_SECS" not in {
        item["name"] for item in container["env"]
    }

    mounts = {item["mountPath"]: item for item in container["volumeMounts"]}
    assert mounts["/etc/clavenar/execution-ceilings"]["readOnly"] is True
    volumes = {item["name"]: item for item in pod["volumes"]}
    assert volumes["execution-ceilings"]["configMap"] == {
        "name": "ceilings-execution-ceilings",
        "items": [
            {
                "key": "execution-ceilings-v1.fixture.json",
                "path": "execution-ceilings-v1.fixture.json",
            }
        ],
    }


def test_runtime_or_resource_overrides_fail_rendering():
    cases = [
        (
            ["-f", str(OPTIONAL_VALUES), "--set", "exec.timeoutSecs=31"],
            "Additional property timeoutSecs is not allowed",
        ),
        (
            [
                "-f",
                str(OPTIONAL_VALUES),
                "--set-string",
                "exec.resources.limits.memory=512Mi",
            ],
            "exec.resources",
        ),
        (
            [
                "-f",
                str(OPTIONAL_VALUES),
                "--set-string",
                "exec.resources.limits.cpu=500m",
            ],
            "exec.resources",
        ),
    ]
    for arguments, message in cases:
        result = subprocess.run(
            ["helm", "template", "ceilings", str(CHART), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert message in result.stderr


class ExecutionCeilingsTests(unittest.TestCase):
    def test_contract_is_strict_and_rendered_byte_exact(self):
        test_contract_is_strict_and_rendered_byte_exact()

    def test_exec_binds_fixed_contract_and_resources(self):
        test_exec_binds_fixed_contract_and_resources()

    def test_runtime_or_resource_overrides_fail_rendering(self):
        test_runtime_or_resource_overrides_fail_rendering()
