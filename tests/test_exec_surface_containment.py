#!/usr/bin/env python3
import json
import subprocess
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/clavenar"
SCHEMA_PATH = CHART / "files/exec-surface-containment-v1.schema.json"
FIXTURE_PATH = CHART / "files/exec-surface-containment-v1.fixture.json"


def test_fixture_is_strict_and_rendered_byte_exact():
    schema = json.loads(SCHEMA_PATH.read_text())
    fixture = json.loads(FIXTURE_PATH.read_text())
    jsonschema.Draft7Validator(schema).validate(fixture)

    output = subprocess.run(
        ["helm", "template", "containment", str(CHART)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    config_map = next(
        document
        for document in yaml.safe_load_all(output)
        if isinstance(document, dict)
        and document.get("kind") == "ConfigMap"
        and document["metadata"]["name"] == "containment-exec-surface-containment"
    )
    assert config_map["immutable"] is True
    assert json.loads(config_map["data"][SCHEMA_PATH.name]) == schema
    assert json.loads(config_map["data"][FIXTURE_PATH.name]) == fixture
