from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "clavenar"
SCHEMA = CHART / "files" / "postgres-ledger-topology-v1.schema.json"
FIXTURE = CHART / "files" / "postgres-ledger-topology-v1.fixture.json"


def render(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "postgres-test", str(CHART), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def postgres_arguments() -> tuple[str, ...]:
    return (
        "--set",
        "persistence.ledger.enabled=false",
        "--set",
        "services.ledger.postgres.enabled=true",
        "--set",
        "services.ledger.postgres.dsnSecretName=ledger-postgres-dsn",
        "--set",
        "services.ledger.postgres.tlsCaSecretName=ledger-postgres-ca",
    )


def resources(output: str) -> list[dict]:
    return [item for item in yaml.safe_load_all(output) if isinstance(item, dict)]


class PostgresLedgerTopologyChartTests(unittest.TestCase):
    def test_contract_validates_and_all_mirrors_are_exact(self) -> None:
        schema = json.loads(SCHEMA.read_text())
        fixture = json.loads(FIXTURE.read_text())
        jsonschema.Draft202012Validator(schema).validate(fixture)
        for mirror_root in (
            ROOT.parent / "clavenar-specs" / "contracts",
            ROOT.parent / "clavenar-e2e" / "contracts",
        ):
            if mirror_root.is_dir():
                self.assertEqual(SCHEMA.read_bytes(), (mirror_root / SCHEMA.name).read_bytes())
                self.assertEqual(FIXTURE.read_bytes(), (mirror_root / FIXTURE.name).read_bytes())

    def test_contract_is_rendered_immutably_and_bound_to_ledger_rollout(self) -> None:
        result = render()
        self.assertEqual(0, result.returncode, result.stderr)
        items = resources(result.stdout)
        configmap = next(
            item
            for item in items
            if item.get("kind") == "ConfigMap"
            and item["metadata"]["name"]
            == "postgres-test-postgres-ledger-topology-contract"
        )
        self.assertTrue(configmap["immutable"])
        self.assertEqual(SCHEMA.read_bytes(), configmap["data"][SCHEMA.name].encode())
        self.assertEqual(FIXTURE.read_bytes(), configmap["data"][FIXTURE.name].encode())
        ledger = next(
            item
            for item in items
            if item.get("kind") == "Deployment"
            and item["metadata"]["name"] == "postgres-test-ledger"
        )
        annotations = ledger["spec"]["template"]["metadata"]["annotations"]
        self.assertRegex(
            annotations["checksum/postgres-ledger-topology-schema"], r"^[a-f0-9]{64}$"
        )
        self.assertRegex(
            annotations["checksum/postgres-ledger-topology-fixture"], r"^[a-f0-9]{64}$"
        )

    def test_default_remains_single_replica_sqlite(self) -> None:
        result = render()
        self.assertEqual(0, result.returncode, result.stderr)
        ledger = next(
            item
            for item in resources(result.stdout)
            if item.get("kind") == "Deployment"
            and item["metadata"]["name"] == "postgres-test-ledger"
        )
        self.assertEqual(1, ledger["spec"]["replicas"])
        self.assertEqual({"type": "Recreate"}, ledger["spec"]["strategy"])
        env = {
            item["name"]: item
            for item in ledger["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual("sqlite", env["CLAVENAR_LEDGER_BACKEND"]["value"])
        self.assertEqual(
            "/var/lib/clavenar/ledger.db", env["CLAVENAR_LEDGER_DB"]["value"]
        )
        self.assertNotIn("CLAVENAR_LEDGER_PG_URL", env)
        self.assertNotIn("CLAVENAR_LEDGER_PG_TLS_CA_FILE", env)

    def test_postgres_render_is_secret_backed_verified_tls_without_sqlite(self) -> None:
        result = render(*postgres_arguments())
        self.assertEqual(0, result.returncode, result.stderr)
        ledger = next(
            item
            for item in resources(result.stdout)
            if item.get("kind") == "Deployment"
            and item["metadata"]["name"] == "postgres-test-ledger"
        )
        self.assertEqual(1, ledger["spec"]["replicas"])
        self.assertNotIn("strategy", ledger["spec"])
        self.assertEqual(
            "initial",
            ledger["spec"]["template"]["metadata"]["annotations"][
                "clavenar.io/ledger-postgres-rotation-id"
            ],
        )
        pod = ledger["spec"]["template"]["spec"]
        container = pod["containers"][0]
        env = {item["name"]: item for item in container["env"]}
        self.assertEqual("postgres", env["CLAVENAR_LEDGER_BACKEND"]["value"])
        self.assertEqual(
            {"name": "ledger-postgres-dsn", "key": "url"},
            env["CLAVENAR_LEDGER_PG_URL"]["valueFrom"]["secretKeyRef"],
        )
        self.assertEqual(
            "/etc/clavenar/postgres/ca.crt",
            env["CLAVENAR_LEDGER_PG_TLS_CA_FILE"]["value"],
        )
        for forbidden in (
            "CLAVENAR_LEDGER_DB",
            "CLAVENAR_LEDGER_PG_DSN",
            "CLAVENAR_LEDGER_PG_ALLOW_INSECURE_TEST_ONLY",
        ):
            self.assertNotIn(forbidden, env)
        mount = next(
            item for item in container["volumeMounts"] if item["name"] == "ledger-postgres-ca"
        )
        self.assertEqual("/etc/clavenar/postgres/ca.crt", mount["mountPath"])
        self.assertTrue(mount["readOnly"])
        volume = next(item for item in pod["volumes"] if item["name"] == "ledger-postgres-ca")
        self.assertEqual("ledger-postgres-ca", volume["secret"]["secretName"])
        self.assertEqual(
            [{"key": "ca.crt", "path": "ca.crt"}], volume["secret"]["items"]
        )
        self.assertNotIn("data", {item["name"] for item in pod["volumes"]})

    def test_postgres_invalid_topologies_fail_closed(self) -> None:
        invalid = (
            (
                (
                    "--set",
                    "services.ledger.postgres.enabled=true",
                    "--set",
                    "services.ledger.postgres.dsnSecretName=dsn",
                    "--set",
                    "services.ledger.postgres.tlsCaSecretName=ca",
                ),
                "requires persistence.ledger.enabled=false",
            ),
            (
                postgres_arguments()
                + (
                    "--set",
                    "services.ledger.replicas=2",
                ),
                "requires services.ledger.replicas=1",
            ),
            (
                (
                    "--set",
                    "persistence.ledger.enabled=false",
                    "--set",
                    "services.ledger.postgres.enabled=true",
                    "--set",
                    "services.ledger.postgres.tlsCaSecretName=ca",
                ),
                "requires services.ledger.postgres.dsnSecretName",
            ),
            (
                postgres_arguments()
                + (
                    "--set",
                    "services.ledger.extraEnv[0].name=CLAVENAR_LEDGER_PG_ALLOW_INSECURE_TEST_ONLY",
                    "--set",
                    "services.ledger.extraEnv[0].value=true",
                ),
                "Must not validate the schema",
            ),
        )
        for arguments, message in invalid:
            with self.subTest(message=message):
                result = render(*arguments)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
