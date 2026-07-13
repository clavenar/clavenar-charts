<!-- public repo — do not add internal topology, secrets, deploy/runbook, strategy, or absolute host paths -->
# clavenar-charts — Helm chart for the eight-service Clavenar sidecar control plane (k8s)

Umbrella chart that deploys the Clavenar stack — **proxy, brain, policy-engine,
ledger, hil, identity, deep-review, console** — as Deployments + ClusterIP
Services with `/health` + `/readyz` probes, PVCs for the SQLite-backed services,
an optional NetworkPolicy perimeter, PodDisruptionBudgets, and an opt-in
auto-mint mTLS bundle. NATS + Vault are BYO by default; opt-in subcharts bundle
them. (Pure-Terraform AWS/GCP/Azure modules are roadmap, not in-repo today.)

## Build, test, lint
This is a Helm/YAML chart — no compiled code. The CI matrix
(`.github/workflows/ci.yml`) is the source of truth:
```bash
cd charts/clavenar
helm dep update .                          # materialize nats + vault subchart tarballs (gitignored)
helm lint .

# render across the four value sets CI checks, then kubeconform each:
helm template smoke . > /tmp/default.yaml                                          # default
helm template smoke . --set tlsBundle.secretName=clavenar-certs \
  --set vault.addr=http://vault:8200 --set vault.tokenSecretName=clavenar-vault \
  --set networkPolicy.enabled=true --set networkPolicy.prometheusNamespaceLabel=monitoring \
  --set services.brain.replicas=3 --set services.policyEngine.replicas=2 > /tmp/all-on.yaml   # all-on
helm template smoke . --set services.ledger.replicas=3 --set persistence.ledger.enabled=false \
  --set services.ledger.extraEnv[0].name=CLAVENAR_LEDGER_BACKEND \
  --set services.ledger.extraEnv[0].value=postgres > /tmp/postgres.yaml                       # postgres
helm template smoke . -f ../../tests/values-bundled.yaml > /tmp/bundled.yaml                   # bundled

for f in /tmp/{default,all-on,postgres,bundled}.yaml; do
  kubeconform -summary -strict -kubernetes-version 1.30.0 "$f"; done
```
Plus `shellcheck -S warning scripts/*.sh`. Every render must emit ≥ 8
`kind: Deployment` — CI fails the matrix otherwise (catches a service template
that silently stops rendering).
Run: Helm chart, no binary — `helm install <release> charts/clavenar -n clavenar --create-namespace`. All Services are ClusterIP; port-forward to reach them.

## Layout
```
charts/clavenar/
  Chart.yaml            # appVersion mirrors root VERSION (the image set on ghcr.io/clavenar); nats + vault deps
  values.yaml           # every commented block doubles as the values reference
  templates/
    _helpers.tpl        # serviceFullname, imageRef, natsUrl, backendEnvs, probe/metrics helpers — the load-bearing logic
    NOTES.txt           # post-install kebab-name/port-forward cheat-sheet
    services.yaml       # the 8 Deployments + Services
    configmap.yaml shared-tokens-secret.yaml vault-token-secret.yaml
    networkpolicy.yaml pdb.yaml proxy-alias.yaml upstream-stub.yaml exec.yaml
    tls-automint-{job,rbac,script}.yaml   # pre-install/upgrade hook: self-signed CA + per-service workload certs
    vault-{bootstrap,seed}-job.yaml       # dev-mode transit engine + stub agent credential
    dashboards-configmap.yaml alerts-configmap.yaml alertmanager-config-secret.yaml
  dashboards/ alerts/   # Grafana JSON + Prometheus rules, label-discovered by kube-prometheus-stack
tests/values-bundled.yaml   # nats + vault subcharts + auto-mint TLS; CI bundled fixture
scripts/push-images.sh      # builds/pushes the 10 service images (8 core + simulator + exec) to GHCR, bumps VERSION + Chart.appVersion
lab/                        # optional in-cluster Claude Code agent pod (proxy→brain→policy→hil→ledger demo)
docs/SEQUENCES.md           # seven flow diagrams + the render decision tree
```
Service ports (container; Service names are `<release>-<service>`):
proxy 8443 (mTLS `/mcp`+`/metrics`, the only Service-published port) / 8080
(plain-HTTP health, kubelet probes) · brain 8081 (9081 health under mTLS) ·
policy-engine 8082 (9082) · ledger 8083 plain + 8183 mTLS · hil 8084 (9084) ·
identity 8086 plain + 8186 mTLS · deep-review 8087 · console 8085 ·
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
  .Chart.AppVersion`. `appVersion` (and root `VERSION`) track the image set
  already published to `ghcr.io/clavenar/<service>`. Chart `version` (0.8.x,
  the chart's own SemVer) is hand-bumped and deliberately decoupled;
  push-images.sh syncs only `VERSION` + `appVersion` and errors if they
  diverge going in.
- **tlsBundle drives mTLS.** Empty `tlsBundle.secretName` → no `/certs` mount →
  proxy + identity panic at boot. When set, backend services flip their app port
  to rustls + SPIFFE-URI allowlist and move health/`/metrics` to a plain-HTTP
  health port (so kubelet + Prometheus need no client cert). Per-pod projection:
  each pod sees only `ca.crt` + its own `service-<name>.{crt,key}` — no pod reads
  another's private key. Don't collapse that isolation.
- **Bundled-NATS + tlsBundle coupling:** if `tlsBundle.secretName` is set you
  must also enable TLS on the bundled NATS subchart — the `clavenar.natsUrl`
  helper `fail`s the render otherwise (plaintext server + TLS-only clients =
  `InvalidContentType` crash). Mirror `tests/values-bundled.yaml`.
- **NetworkPolicy** is opt-in (`networkPolicy.enabled`); backends then accept
  ingress only from proxy + console + deep-review. **PDB** emits only where
  `replicas > 1` (SQLite singletons skip naturally; `minAvailable=ceil/2`).
- **All pods run nonroot UID 65532** (`podSecurityContext`); `fsGroup` remounts
  the SQLite PVCs writable.
- **Pin GitHub Actions by major version** (`actions/checkout@v5`,
  `azure/setup-helm@v4`, `actions/setup-go@v5`) — never `@latest`.
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
