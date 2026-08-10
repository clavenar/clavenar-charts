from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
ROUTING_SCHEMA = json.loads(
    (CHART / "files/brain-provider-routing-v2.schema.json").read_text(
        encoding="utf-8"
    )
)


def render(values: str = "") -> subprocess.CompletedProcess[str]:
    arguments = ["helm", "template", "brain-provider", str(CHART)]
    if not values:
        return subprocess.run(
            arguments, text=True, capture_output=True, check=False
        )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as handle:
        handle.write(values)
        handle.flush()
        return subprocess.run(
            [*arguments, "--values", handle.name],
            text=True,
            capture_output=True,
            check=False,
        )


def documents(output: str) -> list[dict]:
    return [item for item in yaml.safe_load_all(output) if isinstance(item, dict)]


def brain_deployment(items: list[dict]) -> dict:
    return next(
        item
        for item in items
        if item.get("kind") == "Deployment"
        and item["metadata"]["name"] == "brain-provider-brain"
    )


class BrainProviderConfigurationTests(unittest.TestCase):
    def test_default_is_credential_free_mock_with_valid_v2_routing(self) -> None:
        result = render()
        self.assertEqual(0, result.returncode, result.stderr)
        items = documents(result.stdout)
        config = next(
            item
            for item in items
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"]
            == "brain-provider-brain-provider-routing"
        )
        routing = yaml.safe_load(config["data"]["models.yaml"])
        jsonschema.Draft202012Validator(ROUTING_SCHEMA).validate(routing)
        self.assertTrue(
            all(
                assignment["fallback"]
                == {"policy": "disabled", "models": []}
                for name, assignment in routing["workloads"].items()
                if name != "classifier"
            )
        )

        deployment = brain_deployment(items)
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = {entry["name"]: entry for entry in container["env"]}
        self.assertEqual("true", env["CLAVENAR_BRAIN_MOCK_MODE"]["value"])
        self.assertEqual(
            "disabled", env["CLAVENAR_BRAIN_EMBEDDING_PROVIDER"]["value"]
        )
        self.assertNotIn("CLAVENAR_BRAIN_ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        mount = next(
            item
            for item in container["volumeMounts"]
            if item["name"] == "brain-provider-routing"
        )
        self.assertEqual("/etc/clavenar-brain/models.yaml", mount["mountPath"])

    def test_live_generation_and_embeddings_use_only_secret_references(self) -> None:
        result = render(
            """
services:
  brain:
    providerRouting:
      provider: openai
      fastModel: gpt-4o-mini
      deepModel: gpt-4o
    providerCredentials:
      openai:
        secretName: customer-generation
        secretKey: api-key
      voyage:
        secretName: customer-embeddings
        secretKey: api-key
    embedding:
      provider: voyage
      model: voyage-3
      dimensions: 1024
"""
        )
        self.assertEqual(0, result.returncode, result.stderr)
        items = documents(result.stdout)
        config = next(
            item
            for item in items
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"]
            == "brain-provider-brain-provider-routing"
        )
        routing = yaml.safe_load(config["data"]["models.yaml"])
        jsonschema.Draft202012Validator(ROUTING_SCHEMA).validate(routing)
        self.assertEqual("openai", routing["providers"]["managed-provider"]["kind"])
        self.assertEqual("gpt-4o-mini", routing["models"]["managed-fast"]["model"])
        self.assertEqual("gpt-4o", routing["models"]["managed-deep"]["model"])

        deployment = brain_deployment(items)
        env = {
            entry["name"]: entry
            for entry in deployment["spec"]["template"]["spec"]["containers"][0][
                "env"
            ]
        }
        self.assertNotIn("CLAVENAR_BRAIN_MOCK_MODE", env)
        self.assertEqual(
            {"name": "customer-generation", "key": "api-key"},
            env["CLAVENAR_BRAIN_OPENAI_API_KEY"]["valueFrom"]["secretKeyRef"],
        )
        self.assertEqual(
            {"name": "customer-embeddings", "key": "api-key"},
            env["CLAVENAR_BRAIN_EMBEDDING_API_KEY"]["valueFrom"][
                "secretKeyRef"
            ],
        )
        self.assertEqual(
            "voyage", env["CLAVENAR_BRAIN_EMBEDDING_PROVIDER"]["value"]
        )
        self.assertNotIn("not-a-real-secret", result.stdout)

    def test_external_v2_config_map_is_mounted_without_generated_copy(self) -> None:
        result = render(
            """
services:
  brain:
    providerRouting:
      provider: external
      existingConfigMapName: customer-routing
      existingConfigMapKey: routing.yaml
    providerCredentials:
      anthropic:
        secretName: customer-anthropic
        secretKey: api-key
"""
        )
        self.assertEqual(0, result.returncode, result.stderr)
        items = documents(result.stdout)
        self.assertFalse(
            any(
                item.get("kind") == "ConfigMap"
                and item["metadata"]["name"]
                == "brain-provider-brain-provider-routing"
                for item in items
            )
        )
        deployment = brain_deployment(items)
        volume = next(
            item
            for item in deployment["spec"]["template"]["spec"]["volumes"]
            if item["name"] == "brain-provider-routing"
        )
        self.assertEqual("customer-routing", volume["configMap"]["name"])
        self.assertEqual(
            "routing.yaml", volume["configMap"]["items"][0]["key"]
        )

    def test_missing_selected_secret_or_embedding_identity_fails_render(self) -> None:
        cases = (
            (
                "services:\n  brain:\n    providerRouting:\n"
                "      provider: anthropic\n",
                "providerCredentials.anthropic.secretName is required",
            ),
            (
                "services:\n  brain:\n    embedding:\n"
                "      provider: ollama\n",
                "embedding.model is required",
            ),
        )
        for values, marker in cases:
            with self.subTest(marker=marker):
                result = render(values)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(marker, result.stderr)

    def test_inline_or_duplicate_provider_environment_is_rejected(self) -> None:
        result = render(
            """
services:
  brain:
    extraEnv:
      - name: CLAVENAR_BRAIN_OPENAI_API_KEY
        value: not-a-real-secret
"""
        )
        self.assertNotEqual(0, result.returncode)
        self.assertTrue(
            "duplicates a chart-governed environment variable" in result.stderr
            or "Must not validate the schema" in result.stderr,
            result.stderr,
        )

    def test_embedding_limits_match_the_brain_runtime(self) -> None:
        cases = (
            (
                "services:\n  brain:\n    embedding:\n"
                "      provider: ollama\n      model: embeddinggemma\n"
                "      dimensions: 8193\n",
                "8192",
            ),
            (
                "services:\n  brain:\n    embedding:\n"
                "      provider: ollama\n      model: embeddinggemma\n"
                "      dimensions: 768\n"
                "      baseUrl: http://ollama:11434/api\n",
                "pattern",
            ),
        )
        for values, marker in cases:
            with self.subTest(marker=marker):
                result = render(values)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(marker, result.stderr)


if __name__ == "__main__":
    unittest.main()
