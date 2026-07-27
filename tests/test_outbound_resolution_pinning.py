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
SCHEMA_PATH = CHART / "files/outbound-resolution-pinning-v1.schema.json"
FIXTURE_PATH = CHART / "files/outbound-resolution-pinning-v1.fixture.json"
OPTIONAL_VALUES = ROOT / "tests/values-optional.yaml"
PRODUCTION_VALUES = ROOT / "tests/values-production.yaml"


def render(values):
    output = subprocess.run(
        ["helm", "template", "pinning", str(CHART), "-f", str(values)],
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
    weakened["resolution"]["pinSelectedAddress"] = False
    with unittest.TestCase().assertRaises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(weakened)

    config_map = next(
        document
        for document in render(OPTIONAL_VALUES)
        if document.get("kind") == "ConfigMap"
        and document["metadata"]["name"] == "pinning-outbound-resolution-pinning"
    )
    assert config_map["immutable"] is True
    assert json.loads(config_map["data"][SCHEMA_PATH.name]) == schema
    assert json.loads(config_map["data"][FIXTURE_PATH.name]) == fixture


def test_exec_mounts_the_exact_pinning_contract():
    deployment = next(
        document
        for document in render(OPTIONAL_VALUES)
        if document.get("kind") == "Deployment"
        and document["metadata"]["name"] == "pinning-exec"
    )
    template = deployment["spec"]["template"]
    pod = template["spec"]
    container = next(item for item in pod["containers"] if item["name"] == "exec")

    assert template["metadata"]["annotations"][
        "clavenar.io/outbound-resolution-pinning-sha256"
    ] == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    mounts = {item["mountPath"]: item for item in container["volumeMounts"]}
    assert mounts["/etc/clavenar/outbound-resolution-pinning"]["readOnly"] is True
    volumes = {item["name"]: item for item in pod["volumes"]}
    assert volumes["outbound-resolution-pinning"]["configMap"] == {
        "name": "pinning-outbound-resolution-pinning",
        "items": [
            {
                "key": "outbound-resolution-pinning-v1.fixture.json",
                "path": "outbound-resolution-pinning-v1.fixture.json",
            }
        ],
    }


def test_production_still_contains_no_exec_surface():
    documents = render(PRODUCTION_VALUES)
    assert not any(
        document.get("kind") in {"Deployment", "Service", "PersistentVolumeClaim"}
        and document.get("metadata", {}).get("name") == "pinning-exec"
        for document in documents
    )


class OutboundResolutionPinningTests(unittest.TestCase):
    def test_contract_is_strict_and_rendered_byte_exact(self):
        test_contract_is_strict_and_rendered_byte_exact()

    def test_exec_mounts_the_exact_pinning_contract(self):
        test_exec_mounts_the_exact_pinning_contract()

    def test_production_still_contains_no_exec_surface(self):
        test_production_still_contains_no_exec_surface()
