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
SCHEMA_PATH = CHART / "files/rooted-path-target-validation-v1.schema.json"
FIXTURE_PATH = CHART / "files/rooted-path-target-validation-v1.fixture.json"
OPTIONAL_VALUES = ROOT / "tests/values-optional.yaml"


def render_optional(*arguments):
    output = subprocess.run(
        [
            "helm",
            "template",
            "rooted",
            str(CHART),
            "-f",
            str(OPTIONAL_VALUES),
            *arguments,
        ],
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
    weakened["filesystem"]["reopenResolvedPath"] = True
    with unittest.TestCase().assertRaises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(weakened)

    config_map = next(
        document
        for document in render_optional()
        if document.get("kind") == "ConfigMap"
        and document["metadata"]["name"] == "rooted-rooted-path-target-validation"
    )
    assert config_map["immutable"] is True
    assert json.loads(config_map["data"][SCHEMA_PATH.name]) == schema
    assert json.loads(config_map["data"][FIXTURE_PATH.name]) == fixture


def test_exec_mounts_exact_contract_and_normalized_url_allowlist():
    documents = render_optional(
        "--set-string",
        "exec.fetchAllowlist[0]=https://api.example.com/v1",
    )
    deployment = next(
        document
        for document in documents
        if document.get("kind") == "Deployment"
        and document["metadata"]["name"] == "rooted-exec"
    )
    template = deployment["spec"]["template"]
    container = next(
        item for item in template["spec"]["containers"] if item["name"] == "exec"
    )
    assert template["metadata"]["annotations"][
        "clavenar.io/rooted-path-target-validation-sha256"
    ] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    environment = {item["name"]: item["value"] for item in container["env"]}
    assert (
        environment["CLAVENAR_EXEC_NETPOLICY_ALLOWLIST"]
        == "https://api.example.com/v1"
    )
    mounts = {item["mountPath"]: item for item in container["volumeMounts"]}
    assert mounts["/etc/clavenar/rooted-path-target-validation"]["readOnly"] is True
    volumes = {item["name"]: item for item in template["spec"]["volumes"]}
    assert volumes["rooted-path-target-validation"]["configMap"] == {
        "name": "rooted-rooted-path-target-validation",
        "items": [
            {
                "key": "rooted-path-target-validation-v1.fixture.json",
                "path": "rooted-path-target-validation-v1.fixture.json",
            }
        ],
    }


def test_bare_host_or_query_allowlist_fails_rendering():
    for value in ["api.example.com", "https://api.example.com/v1?key=value"]:
        result = subprocess.run(
            [
                "helm",
                "template",
                "rooted",
                str(CHART),
                "-f",
                str(OPTIONAL_VALUES),
                "--set-string",
                f"exec.fetchAllowlist[0]={value}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "exec.fetchAllowlist.0" in result.stderr


class RootedPathTargetValidationTests(unittest.TestCase):
    def test_contract_is_strict_and_rendered_byte_exact(self):
        test_contract_is_strict_and_rendered_byte_exact()

    def test_exec_mounts_exact_contract_and_normalized_url_allowlist(self):
        test_exec_mounts_exact_contract_and_normalized_url_allowlist()

    def test_bare_host_or_query_allowlist_fails_rendering(self):
        test_bare_host_or_query_allowlist_fails_rendering()
