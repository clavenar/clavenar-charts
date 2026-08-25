<!-- public repo — do not add internal topology, secrets, deploy/runbook, strategy, or absolute host paths -->
# clavenar-charts — Helm chart for the nine-service Clavenar sidecar control plane (k8s)

Umbrella chart that deploys the Clavenar stack — **proxy, brain, policy-engine,
ledger, hil, identity, deep-review, assurance, console** — as Deployments + ClusterIP
Services with `/health` + `/readyz` probes, PVCs for the SQLite-backed services,
a default-on NetworkPolicy perimeter, PodDisruptionBudgets, and an opt-in
auto-mint mTLS bundle. NATS + Vault are BYO by default; opt-in subcharts bundle
them. (Pure-Terraform AWS/GCP/Azure modules are roadmap, not in-repo today.)

## Build, test, lint
This is a Helm/YAML chart — no compiled code. The CI matrix
(`.github/workflows/ci.yml`) is the source of truth:
```bash
helm dependency build charts/clavenar      # replay Chart.lock for ordinary validation
# Intentional dependency-version changes only; this can rewrite Chart.lock:
helm dependency update charts/clavenar
helm lint charts/clavenar
python3 scripts/check_dependency_readiness.py --source-root . --require-source
# CI checkouts clavenar-specs into ./clavenar-specs. In a sibling workspace
# (../clavenar-specs) pass --source-root .. instead.
python3 scripts/check-nats-authorization.py

# render all governed value sets (the workflow manifests plus the passkey
# fixture exercised by the Python suite), then kubeconform each locally:
helm template smoke charts/clavenar > /tmp/default.yaml
helm template smoke charts/clavenar -f tests/values-all-on.yaml > /tmp/all-on.yaml
helm template smoke charts/clavenar --set persistence.ledger.enabled=false \
  --set services.ledger.postgres.enabled=true \
  --set services.ledger.postgres.dsnSecretName=ledger-postgres-dsn \
  --set services.ledger.postgres.tlsCaSecretName=ledger-postgres-ca > /tmp/postgres.yaml
helm template smoke charts/clavenar -f tests/values-bundled.yaml > /tmp/bundled.yaml
helm template smoke charts/clavenar -f tests/values-optional.yaml > /tmp/optional.yaml
helm template smoke charts/clavenar -f tests/values-production.yaml > /tmp/production.yaml
helm template smoke charts/clavenar -f tests/values-passkey.yaml > /tmp/passkey.yaml

for f in /tmp/{default,all-on,postgres,bundled,optional,production,passkey}.yaml; do
  kubeconform -summary -strict -ignore-missing-schemas -kubernetes-version 1.30.0 "$f"
done
python3 scripts/check-listener-matrix.py --manifest /tmp/default.yaml
python3 -m unittest discover -v -s tests -p 'test_*.py'
```
Plus `shellcheck -S warning scripts/*.sh charts/clavenar/files/tls-rotation/*.sh`.
Run the listener-matrix checker with each fixture's matching `--values`
argument as CI does. Every render must emit ≥ 9
`kind: Deployment` — CI fails the matrix otherwise (catches a service template
that silently stops rendering).
Prometheus rules must pass both syntax validation and the executable console
alert fixture:
```bash
repo_root="$(git rev-parse --show-toplevel)"
docker run --rm --entrypoint=/bin/promtool -v "$repo_root:/workspace:ro" -w /workspace \
  prom/prometheus:v2.55.0 check rules charts/clavenar/alerts/clavenar-alerts.yaml
docker run --rm --entrypoint=/bin/promtool -v "$repo_root:/workspace:ro" -w /workspace \
  prom/prometheus:v2.55.0 test rules tests/promtool-console-alerts.yml
```
Run: Helm chart, no binary. `helm install <release> charts/clavenar -n
clavenar --create-namespace` mutates the selected Kubernetes cluster and must
only run against an explicitly authorized context. All Services are ClusterIP;
port-forward to reach them.

## Layout
```
charts/clavenar/
  Chart.yaml            # appVersion mirrors root VERSION (the image set on ghcr.io/clavenar); nats + vault deps
  values.yaml           # every commented block doubles as the values reference
  values.schema.json    # fixed console listener/trust values and unsafe override rejection
  templates/
    _helpers.tpl        # serviceFullname, imageRef, natsUrl, backendEnvs, probe/metrics helpers — the load-bearing logic
    NOTES.txt           # post-install kebab-name/port-forward cheat-sheet
    services.yaml       # the 9 Deployments + Services
    brain-provider-routing.yaml # generated provider-neutral v2 routing ConfigMap
    configmap.yaml workload-capability-bundle.yaml attestation-verifier-contract.yaml
    dependency-readiness-contract.yaml structured-execution-contract.yaml
    execution-ceilings-contract.yaml outbound-resolution-pinning-contract.yaml
    distributed-control-state.yaml residual-product-disposition-contract.yaml
    rooted-path-target-validation-contract.yaml stateful-upgrade-contract.yaml
    shared-tokens-secret.yaml vault-token-secret.yaml
    networkpolicy.yaml pdb.yaml proxy-alias.yaml upstream-stub.yaml exec.yaml
    tls-automint-{job,rbac,script}.yaml   # pre-install/upgrade hook: CA + chart/peer workload certs
    vault-{bootstrap,seed}-job.yaml       # dev-mode transit engine + stub agent credential
    dashboards-configmap.yaml prometheus-rule.yaml alertmanager-config.yaml
  dashboards/ alerts/   # Grafana JSON + Prometheus rules, label-discovered by kube-prometheus-stack
tests/values-bundled.yaml   # nats + vault subcharts + auto-mint TLS; CI bundled fixture
scripts/push-images.sh      # fail-closed tombstone for the retired mutable/partial publisher
lab/                        # optional in-cluster Claude Code agent pod (proxy→brain→policy→hil→ledger demo)
docs/SEQUENCES.md           # seven flow diagrams + the render decision tree
```
Service ports (container; Service names are `<release>-<service>`):
proxy 8443 (mTLS `/`, `/health`, `/readyz`, `/mcp`, `/tool/{name}`) / 8080
(plain HTTP `/`, `/health`, `/readyz`, `/metrics` for kubelet and Prometheus;
both ports are published by the ClusterIP Service) · brain 8081
(workload-mTLS application; exact policy-engine explain and console
narrate/model callers) / 9081 (plain diagnostics only; no provider routes) ·
policy-engine 8082 (9082) · ledger 8083 plain + 8183 mTLS · hil 8084 (9084) ·
identity 8086 plain + 8186 mTLS · deep-review 8087 · assurance 8088 mTLS
control + 9088 plain diagnostics · console
8085 primary (demo-only, WebAuthn passkey, or native operator mTLS) + 9085
optional demo + 9185 diagnostics ·
upstream-stub 9000 · exec 9001 mutual-TLS authority + 9002 unpublished plain
health.

## Conventions & invariants
- **`clavenar.serviceFullname` kebab-cases values keys.** camelCase values paths
  (`services.policyEngine`, `services.deepReview`) become RFC-1123 object names
  (`<release>-policy-engine`, `<release>-deep-review`). In NOTES.txt / README /
  kubectl examples always use the **kebab** name — copy-paste must resolve.
- **SQLite-on-shared-PVC is unsafe.** ledger/hil/identity are SQLite-backed and
  pinned to `replicas: 1` (concurrent writers corrupt the file even on RWX).
  Don't lift the pin. Ledger's staged PostgreSQL mode is a structured opt-in
  with persistence disabled, Secret-backed DSN and private CA, verified TLS,
  and exactly one replica until its separate HA failure model is accepted.
- **Postgres route scope is exact.** The packaged
  `postgres-ledger-topology-v1` contract defines 16 supported paths and 21
  stable-503 paths. Regulatory and compliance handlers are supported;
  SQLite-direct analytics, cases, replay, allowlists, and cold-tier export are
  unavailable. Do not add a chart-side SQLite fallback or an insecure TLS env.
- **Image identity:** `services.<svc>.image.digest` selects the exact protected
  release image and takes precedence over the legacy
  `services.<svc>.image.tag → .Values.imageTag → .Chart.AppVersion` fallback.
  Production refuses every enabled core service without a digest and also
  requires one for the curated upstream when enabled. Publication emits an
  exact digest values file; use it for any supported install. That file also
  carries the exact `stackRelease`, which owns Console `/version.json` and
  `app.kubernetes.io/version`; digest-bound renders fail if it is absent.
  Root `VERSION` and `appVersion` remain frozen at the last legacy image set.
  The local publisher is a fail-closed tombstone; protected releases
  stage digest-only component artifacts and one signed stack-BOM reference
  from clavenar-e2e.
  The evaluation-only Exec workload accepts `exec.image.tag` for unique local
  builds, with the same digest-wins behavior; `latest` is refused and the
  production profile continues to forbid Exec.
- **tlsBundle drives mTLS.** Empty `tlsBundle.secretName` → no `/certs` mount →
  proxy + identity panic at boot. When set, backend services flip their app port
  to rustls; Ledger, Policy Engine, HIL, and Identity then require the packaged
  digest-bound generated route capabilities. Health/`/metrics` move to a
  plain-HTTP health port (so kubelet + Prometheus need no client cert). The
  exact-key Secret source is copied by a nonroot init container into an
  owner-bound memory volume: private keys are mode 0600 and certificates 0444.
  Each pod sees only `ca.crt` + its own `service-<name>.{crt,key}`; Identity
  alone adds `ca.key`, and Proxy alone adds `server.{crt,key}`. Generic and peer
  private keys stay absent. The bundle also mints website, demo-mint, and
  simulator peer identities for separately deployed consumers; chart-managed
  pods never receive those keys. Don't collapse that isolation.
- **Brain provider operations never use diagnostics.** The chart renders
  strict exact callers and body/rate/spend/timeout controls for
  `/explain-pattern` and `/narrate-decision`; `:9081` is only `/`, `/health`,
  `/readyz`, and `/metrics`. Policy-engine and console dial HTTPS `:8081`, and
  `CLAVENAR_BRAIN_ALLOWED_CALLERS` remains the inspect/scan prefix boundary —
  do not add policy-engine merely to make explain work.
- **Brain provider credentials are Secret references only.** Managed
  generation renders one `clavenar.brain-provider-routing/v2` ConfigMap and
  independently configures embeddings. Hosted keys enter only through the
  selected `providerCredentials.*` `secretKeyRef`; provider/model variables
  and legacy inline API-key `extraEnv` entries are chart-governed and rejected.
  `mock` is the credential-free default. External multi-target routing mounts
  an operator ConfigMap and rolls through `providerRouting.rotationId`.
- **Public readiness owns startup ordering.** The byte-identical
  `dependency-readiness-v1` contract drives distinct `/health` liveness and
  `/readyz` readiness probes, bounded 2-second/30-attempt init gates, runtime
  dependency URLs, internal diagnostics Service ports, and exact
  NetworkPolicy callers. Update the contract mirror and verifier together;
  never replace readiness with process-only or TCP-only gating.
- **Exec process calls are structured and evaluation-only.** The exact
  `structured-execution-v1` schema/fixture is mounted immutable and read-only.
  Exec requires either a digest image or a unique non-`latest` evaluation tag,
  nonroot/read-only container, 64 MiB memory scratch, RuntimeDefault seccomp,
  dropped capabilities, no privilege escalation, and default-deny egress
  admitting only cluster DNS plus the exact upstream-stub peer. Do not restore
  `bash`, `cmd`, mutable fallback tags, writable root, or unrestricted egress.
- **Exec ceilings are fixed.** The exact `execution-ceilings-v1` contract is
  immutable in the chart and compiled into Exec. Do not restore
  `timeoutSecs`/`CLAVENAR_EXEC_TIMEOUT_SECS`, arbitrary Exec resources,
  direct-child-only timeout handling, or unbounded file/fetch/output paths.
- **Outbound addresses are pinned.** The exact
  `outbound-resolution-pinning-v1` contract is immutable in the chart and
  compiled into Exec. Each connection validates the complete bounded DNS set,
  pins one deterministic public answer without changing hostname identity, and
  repeats normalization, allowlisting, resolution, and pinning for at most five
  manual redirect hops. Production Exec absence remains unchanged.
- **Bundled-NATS + tlsBundle coupling:** if `tlsBundle.secretName` is set you
  must also enable TLS on the bundled NATS subchart — the `clavenar.natsUrl`
  helper `fail`s the render otherwise (plaintext server + TLS-only clients =
  `InvalidContentType` crash). Mirror `tests/values-bundled.yaml`.
- **NetworkPolicy** defaults on and is destination/port-specific. Proxy is
  open to arbitrary sources only on 8443; console passkey, operator, and demo trust
  classes default denied until their independent `allowedPeers` lists supply
  explicit selectors. The separately deployed demo-mint may reach bundled
  NATS `:4222` only through its canonical external-namespace selector; it never
  reaches the unauthenticated monitor. The optional separately deployed
  Simulator peer substitutes one canonical external-namespace selector across
  only its governed application/readiness ports and the token-authenticated
  bundled-Vault API needed for evaluation seeding. The transient demo-reset
  selector remains same-namespace and reaches only HIL application `:8084` and
  Ledger mTLS `:8183` with Console cleanup authority. Console probes/scrapes use
  diagnostics-only 9185. Keep
  `listeners.yaml`, its checker, and policy templates in lockstep. **PDB** emits only where
  `replicas > 1` (SQLite singletons skip naturally; `minAvailable=ceil/2`).
- **All pods run nonroot UID 65532** (`podSecurityContext`); `fsGroup` remounts
  the SQLite PVCs writable.
- **Pin GitHub Actions by exact 40-character commit SHA** and retain the
  readable release in a trailing comment. Toolchain inputs use exact patch
  versions; never restore moving major, `stable`, or `latest` selectors.
- **Bash** (`scripts/*.sh`): `set -euo pipefail`, must pass `shellcheck -S
  warning` (CI runs it), quote everything, prefer `[ "$x" = "y" ]`.
- **Helm template logic stays in helpers, not handlers.** Compute derived
  strings (image refs, backend URLs, kebab names) in `_helpers.tpl`; templates
  consume the result. Don't duplicate a fallback chain inline.
- Commit subjects must start with a lowercase letter.

## Pointers

[README](README.md) · [chart quickstart and values](charts/clavenar/README.md) ·
[security policy](SECURITY.md) · [render/apply and mTLS flows](docs/SEQUENCES.md) ·
[in-cluster agent demo](lab/README.md).
