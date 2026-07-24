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
cd charts/clavenar
helm dep update .                          # materialize nats + vault subchart tarballs (gitignored)
helm lint .
python3 ../../scripts/check_dependency_readiness.py --source-root ../../.. --require-source

# render across the six value sets CI checks, then kubeconform each:
helm template smoke . > /tmp/default.yaml                                          # default
helm template smoke . --set tlsBundle.secretName=clavenar-certs \
  --set vault.addr=http://vault:8200 --set vault.tokenSecretName=clavenar-vault \
  --set networkPolicy.enabled=true --set networkPolicy.prometheusNamespaceLabel=monitoring \
  --set services.brain.replicas=3 --set services.policyEngine.replicas=2 > /tmp/all-on.yaml   # all-on
helm template smoke . --set services.ledger.replicas=3 --set persistence.ledger.enabled=false \
  --set services.ledger.extraEnv[0].name=CLAVENAR_LEDGER_BACKEND \
  --set services.ledger.extraEnv[0].value=postgres > /tmp/postgres.yaml                       # postgres
helm template smoke . -f ../../tests/values-bundled.yaml > /tmp/bundled.yaml                   # bundled
helm template smoke . -f ../../tests/values-optional.yaml > /tmp/optional.yaml                 # optional listeners
helm template smoke . -f ../../tests/values-production.yaml > /tmp/production.yaml             # fail-closed production

for f in /tmp/{default,all-on,postgres,bundled,optional,production}.yaml; do
  kubeconform -summary -strict -kubernetes-version 1.30.0 "$f"; done
```
Plus `shellcheck -S warning scripts/*.sh`. Every render must emit ≥ 9
`kind: Deployment` — CI fails the matrix otherwise (catches a service template
that silently stops rendering).
Prometheus rules must pass both syntax validation and the executable console
alert fixture:
```bash
repo_root="$(git rev-parse --show-toplevel)"
docker run --rm --entrypoint=/bin/promtool -v "$repo_root:/workspace:ro" -w /workspace \
  prom/prometheus:v2.55.0 test rules tests/promtool-console-alerts.yml
```
Run: Helm chart, no binary — `helm install <release> charts/clavenar -n clavenar --create-namespace`. All Services are ClusterIP; port-forward to reach them.

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
    configmap.yaml workload-capability-bundle.yaml attestation-verifier-contract.yaml
    dependency-readiness-contract.yaml
    shared-tokens-secret.yaml vault-token-secret.yaml
    networkpolicy.yaml pdb.yaml proxy-alias.yaml upstream-stub.yaml exec.yaml
    tls-automint-{job,rbac,script}.yaml   # pre-install/upgrade hook: self-signed CA + per-service workload certs
    vault-{bootstrap,seed}-job.yaml       # dev-mode transit engine + stub agent credential
    dashboards-configmap.yaml alerts-configmap.yaml alertmanager-config-secret.yaml
  dashboards/ alerts/   # Grafana JSON + Prometheus rules, label-discovered by kube-prometheus-stack
tests/values-bundled.yaml   # nats + vault subcharts + auto-mint TLS; CI bundled fixture
scripts/push-images.sh      # fail-closed tombstone for the retired mutable/partial publisher
lab/                        # optional in-cluster Claude Code agent pod (proxy→brain→policy→hil→ledger demo)
docs/SEQUENCES.md           # seven flow diagrams + the render decision tree
```
Service ports (container; Service names are `<release>-<service>`):
proxy 8443 (mTLS `/`, `/health`, `/readyz`, `/mcp`, `/tool/{name}`; the only
Service-published port) / 8080 (plain HTTP `/`, `/health`, `/readyz`, `/metrics`
for kubelet and Prometheus) · brain 8081 (workload-mTLS application; exact
policy-engine explain and console narrate/model callers) / 9081 (plain
diagnostics only under mTLS; no provider routes) ·
policy-engine 8082 (9082) · ledger 8083 plain + 8183 mTLS · hil 8084 (9084) ·
identity 8086 plain + 8186 mTLS · deep-review 8087 · assurance 8088 mTLS
control + 9088 plain diagnostics · console
8085 primary (demo-only by default; native operator mTLS when enabled) + 9085
optional demo + 9185 diagnostics ·
upstream-stub 9000 · exec 9001.

## Conventions & invariants
- After adding or updating a feature, also update the relevant `MANUAL_TESTS*` file(s) when needed.
- **`clavenar.serviceFullname` kebab-cases values keys.** camelCase values paths
  (`services.policyEngine`, `services.deepReview`) become RFC-1123 object names
  (`<release>-policy-engine`, `<release>-deep-review`). In NOTES.txt / README /
  kubectl examples always use the **kebab** name — copy-paste must resolve.
- **SQLite-on-shared-PVC is unsafe.** ledger/hil/identity are SQLite-backed and
  pinned to `replicas: 1` (concurrent writers corrupt the file even on RWX).
  Don't lift the pin without switching backends. Only ledger has a Postgres mode
  (`CLAVENAR_LEDGER_BACKEND=postgres` + `CLAVENAR_LEDGER_PG_URL`, drop the PVC,
  then scale). Document the constraint at any new SQLite-backed key.
- **Postgres mode disables SQLite-only features** (cold-tier export, regulatory
  bundles, Iceberg metadata, egress sweeper → 503). Wire SIEM ingest directly.
- **Image tag fallback:** `services.<svc>.image.tag → .Values.imageTag →
  .Chart.AppVersion` remains a legacy install boundary until WP-14.5. Root
  `VERSION` and `appVersion` are frozen at the last legacy image set. The local
  publisher is a fail-closed tombstone; protected releases stage digest-only
  component artifacts and one signed stack-BOM reference from clavenar-e2e.
- **tlsBundle drives mTLS.** Empty `tlsBundle.secretName` → no `/certs` mount →
  proxy + identity panic at boot. When set, backend services flip their app port
  to rustls; Ledger, Policy Engine, HIL, and Identity then require the packaged
  digest-bound generated route capabilities. Health/`/metrics` move to a
  plain-HTTP health port (so kubelet + Prometheus need no client cert). The
  exact-key Secret source is copied by a nonroot init container into an
  owner-bound memory volume: private keys are mode 0600 and certificates 0444.
  Each pod sees only `ca.crt` + its own `service-<name>.{crt,key}`; Identity
  alone adds `ca.key`, and Proxy alone adds `server.{crt,key}`. Generic and peer
  private keys stay absent. Don't collapse that isolation.
- **Brain provider operations never use diagnostics.** The chart renders
  strict exact callers and body/rate/spend/timeout controls for
  `/explain-pattern` and `/narrate-decision`; `:9081` is only `/`, `/health`,
  `/readyz`, and `/metrics`. Policy-engine and console dial HTTPS `:8081`, and
  `CLAVENAR_BRAIN_ALLOWED_CALLERS` remains the inspect/scan prefix boundary —
  do not add policy-engine merely to make explain work.
- **Public readiness owns startup ordering.** The byte-identical
  `dependency-readiness-v1` contract drives distinct `/health` liveness and
  `/readyz` readiness probes, bounded 2-second/30-attempt init gates, runtime
  dependency URLs, internal diagnostics Service ports, and exact
  NetworkPolicy callers. Update the contract mirror and verifier together;
  never replace readiness with process-only or TCP-only gating.
- **Bundled-NATS + tlsBundle coupling:** if `tlsBundle.secretName` is set you
  must also enable TLS on the bundled NATS subchart — the `clavenar.natsUrl`
  helper `fail`s the render otherwise (plaintext server + TLS-only clients =
  `InvalidContentType` crash). Mirror `tests/values-bundled.yaml`.
- **NetworkPolicy** defaults on and is destination/port-specific. Proxy is
  open to arbitrary sources only on 8443; console operator and demo trust
  classes default denied until their independent `allowedPeers` lists supply
  explicit selectors. Console probes/scrapes use diagnostics-only 9185. Keep
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
README.md · charts/clavenar/README.md (quickstart + values reference) ·
SECURITY.md · docs/SEQUENCES.md (render/apply + mTLS wiring flows) ·
lab/README.md (in-cluster agent demo).
