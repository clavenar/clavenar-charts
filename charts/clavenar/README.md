# clavenar helm chart

Umbrella Helm chart for the nine-service Clavenar stack:
**proxy, brain, policy-engine, ledger, hil, identity, deep-review,
assurance, console.**

This is the canonical chart — the older skeleton at
`clavenar-e2e/charts/clavenar/` is preserved for HA_RUNBOOK reference
only and will not receive new features.

## Quickstart

Two paths — pick by what's already in your cluster.

### Bundled (evaluation / kind / dev cluster)

One `helm install` brings the clavenar stack **plus** NATS + Vault +
auto-minted mTLS bundle:

```bash
# From the repo root
helm dep update charts/clavenar
helm install my-clavenar charts/clavenar \
  --namespace clavenar --create-namespace \
  -f tests/values-bundled.yaml
```

`tests/values-bundled.yaml` enables `nats.bundled` + `vault.bundled`
(dev-mode) + `tlsBundle.autoMint`. Reasonable for an evaluation
cluster; **not** for production.

### BYO (production)

Operator brings their own NATS + Vault + workload PKI bundle. To enable
the R1 operator console, also pre-provision a separate public-trust Secret
containing `ca.crt` and `operators.json`; never put the operator CA private
key or an operator leaf private key in Kubernetes:

```bash
# From the chart root (clavenar-charts/charts/clavenar)
helm lint .
helm template my-clavenar . | less

# Real install
helm install my-clavenar . --namespace clavenar --create-namespace \
  --set nats.url=nats://my-nats:4222 \
  --set vault.addr=https://vault.internal:8200 \
  --set vault.tokenSecretName=clavenar-vault-token \
  --set tlsBundle.secretName=clavenar-certs \
  --set services.console.operatorMtls.enabled=true \
  --set services.console.operatorMtls.publicTrustSecretName=clavenar-operator-trust \
  --set services.console.mutationOrigins[0]=https://console.example.com \
  --set services.brain.extraEnv[0].name=ANTHROPIC_API_KEY \
  --set services.brain.extraEnv[0].valueFrom.secretKeyRef.name=anthropic \
  --set services.brain.extraEnv[0].valueFrom.secretKeyRef.key=key
```

### Bundled vs BYO matrix

| Concern | Bundled (`*.bundled.enabled=true`) | BYO (default) |
|---|---|---|
| NATS deployment | Subchart `nats-io/nats` installed by the release | External, operator-managed |
| JetStream persistence | 5Gi PVC (configure under `nats.config.jetstream.fileStore.pvc.size`) | Whatever your external NATS does |
| Vault deployment | Subchart `hashicorp/vault` in **dev mode** (in-memory, root token) | External, operator-managed |
| Transit engine | Auto-provisioned by post-install Job | Operator runs `vault secrets enable transit && vault write -f transit/keys/<name>` |
| mTLS bundle | Auto-minted by pre-install Job (self-signed CA) | Operator pre-populates Secret with managed-PKI certs |
| Console identity | Safe `demo-only` mode; optional signed prefix-scoped demo Viewer, never operator/Admin authority | Optional native operator mTLS using a dedicated public CA + exact identity registry Secret |
| Upstream MCP target | `clavenar-upstream-stub` (echo MCP) bundled when `upstreamStub.enabled=true`, auto-wired into the proxy | Operator sets `services.proxy.extraEnv` `CLAVENAR_UPSTREAM_URL` at a real MCP server |
| Execution gateway | `clavenar-exec` deployed when `exec.enabled=true`. Sits between proxy and upstream-stub; exposes 7 Claude-Code-built-in-parity tools (`bash`, `read_file`, …) so an agent whose built-ins are denylisted still has a shell, but every call lands in the ledger | Lab-only; production still routes to a real MCP via `CLAVENAR_UPSTREAM_URL` |
| Agent Vault credential | Stub `secret/data/agents/agent-001` seeded by post-install Job when `agentVaultSeed.enabled=true` | Operator seeds per-agent entries against their own Vault |
| Proxy DNS alias | ExternalName `proxy` → `<release>-proxy` (CNAME) emitted when `proxyAlias.enabled=true` so in-cluster clients can dial bare `https://proxy:8443/mcp` and match the cert SAN | Skip when an Ingress / Gateway terminates mTLS upstream (it'll send the right SNI on the agent's behalf) |
| Audience | Evaluation / kind / single-tenant dev clusters | Production / multi-tenant clusters |
| State durability | Vault loses state on pod restart (re-bootstrapped) | Whatever your external Vault does |

### Lab agent (interactive Claude Code in-cluster)

After the chart is up, an optional scaffold under `clavenar-charts/lab/`
drops an actual Claude Code CLI pod into the same namespace, routed
through clavenar-proxy. Useful for evaluating the full Brain + Policy +
HIL + ledger pipeline against real agent traffic without leaving the
cluster. See [`lab/README.md`](../../lab/README.md) for the build +
apply walkthrough.

## What's wired

- **Nine Deployments + nine Services**, ClusterIP, one replica each.
- **HTTP probes** wired to `/health` (liveness) + `/readyz`
  (readiness) for every service. The proxy exposes a second
  container port (`healthPort: 8080`) bound to a non-mTLS listener
  serving `/`, `/health`, `/readyz`, and `/metrics`; kubelet probes and
  Prometheus target this port. The agent-facing mTLS port (8443) serves
  `/`, `/health`, `/readyz`, `/mcp`, and `/tool/{name}` but never
  `/metrics`, and it is the only port published by the proxy's k8s Service.
  Assurance likewise splits required-mTLS control (`8088`) from plain,
  container-only `/health` + `/readyz` diagnostics (`9088`); no mutation or
  status route is installed on diagnostics.
- **terminationGracePeriodSeconds** set to `drainCapSecs + 5` so
  the in-process watchdog (env `CLAVENAR_GRACEFUL_DRAIN_SECS`) fires
  before kubelet's SIGKILL.
- **PVCs** for ledger / hil / identity (the SQLite-backed services),
  mounted at `/var/lib/clavenar`, which matches the
  `CLAVENAR_*_DB=/var/lib/clavenar/*.db` defaults.
- **Shared ConfigMap** carries `NATS_URL`, `CLAVENAR_GRACEFUL_DRAIN_SECS`,
  and (when set) `VAULT_ADDR`.
- **Vault token** (when `vault.tokenSecretName` is set) is
  injected into proxy + identity via secretKeyRef.
- **Backend URL envs** wired automatically by component — proxy
  knows where to find brain/policy/hil/identity; console knows
  where to find ledger/hil/policy-engine/identity and, in operator-mTLS
  posture, assurance; deep-review
  knows where to find ledger. Service mesh overriding the
  Service names? Set `services.<svc>.extraEnv` to shadow.
- **NetworkPolicy** (default on) — every enabled core, optional, and
  bundled workload gets an ingress-isolating policy. Rules name the
  exact destination port and caller selectors. Proxy admits an
  unrestricted source only on agent mTLS port 8443. Console operator and
  demo ingress use independent, empty-by-default peer lists. Prometheus
  can reach only the diagnostics listener (`9185`) when the global
  namespace selector is configured. The chart-default console posture is
  `demo-only`; a valid demo cookie creates only a prefix-scoped demo Viewer,
  never an operator session or Admin authority. The chart does not auto-mint
  that cookie's HMAC key or bundle a demo-token issuer: without operator
  configuration the anonymous `/demo` preview works, session exchange returns
  unavailable, and authenticated demo routes remain closed. To enable them,
  create one dedicated Secret and reference its same key through
  `services.console.extraEnv` (`CLAVENAR_CONSOLE_DEMO_SESSION_HS256`),
  `services.hil.extraEnv` (`CLAVENAR_HIL_DEMO_SESSION_HS256`), and
  `services.ledger.extraEnv` (`CLAVENAR_LEDGER_DEMO_SESSION_HS256`); keep it
  separate from operator/workload trust and provide a reviewed token issuer.
- **Governed listener inventory** — `listeners.yaml` records every
  application and probe-only bind, Service publication, protocol,
  authentication/callers, limits, and external-publication posture.
  It also records ownership of the NATS and Vault subchart listeners.
  `scripts/check-listener-matrix.py` rejects inventory drift.
- **PodDisruptionBudget** auto-emitted for any service where
  `replicas > 1`. SQLite-pinned services (`replicas: 1`) skip
  naturally; once an operator flips ledger to Postgres mode +
  `replicas: 3`, a PDB lands with `minAvailable = ceil(replicas/2)`.
- **TLS bundle mount** — when `tlsBundle.secretName` is set, a k8s
  Secret carrying the clavenar CA + per-service workload certs gets
  mounted read-only at `/certs`. Each pod sees only what it needs:
  `ca.crt` + its own `service-<name>.{crt,key}`. Proxy additionally
  mounts `server.{crt,key}` (agent-facing mTLS) and `client.{crt,key}`
  (legacy starter-agent client); assurance also mounts that generic client
  pair for synthetic proxy attacks while its control listener uses only
  `service-assurance.{crt,key}`. No pod can read another service's
  private key. Generate the bundle with
  `clavenar-proxy/scripts/gen_certs.sh --env prod` then
  `kubectl create secret generic clavenar-tls --from-file=clavenar-proxy/certs/`.
- **Dedicated operator trust projection** — when
  `services.console.operatorMtls.enabled=true`, the console additionally
  mounts only `ca.crt` + `operators.json` from
  `publicTrustSecretName`. Its `/certs` projection remains limited to the
  public workload CA and `service-console.{crt,key}`. The two Secret names
  must differ, and chart rendering fails on missing or partial settings.
  Admin mutation forms also require a principal-bound CSRF token and an exact
  `services.console.mutationOrigins` match before any backend call.
- **Deep-review** singleton — same posture as brain. Per-agent
  history rides NATS, daily token budget is per-pod (scale the
  cap, not the pods).
- **Execution gateway** (opt-in via `exec.enabled=true`) —
  `clavenar-exec` becomes the proxy's upstream, exposes seven tools
  (`bash`, `read_file`, `write_file`, `edit_file`, `list_directory`,
  `search_files`, `fetch_url`) that mirror Claude Code's built-ins,
  and forwards anything else (initialize, `resources/*`,
  `tools/list` discovery for non-exec tools) to the upstream-stub.
  Pairs with the lab pod's `permissions.deny` posture so an agent
  cannot reach a shell except through the clavenar pipeline. Single
  replica because the workspace PVC is shared RW with the lab
  agent pod. Egress for `fetch_url` defaults to deny-all until
  `exec.fetchAllowlist` names a host. Sandboxing is pod-level
  (`readOnlyRootFilesystem`, capability drop, RuntimeDefault
  seccomp) — gVisor / Kata is v2.

## What's not wired

- **No production Vault.** The bundled Vault path runs dev mode only
  (in-memory, root token, no Raft, no auto-unseal). Production
  deployments must turn `vault.bundled.enabled` off and point at an
  externally-managed Vault via `vault.addr` + `vault.tokenSecretName`.
- **No ingress / TLS termination.** Add an operator-controlled Ingress,
  Gateway, or service-mesh layer downstream of this chart. Chart-owned
  Services are fixed to ClusterIP; `NodePort` and `LoadBalancer` values
  fail rendering. Publish proxy 8443 through an mTLS-capable edge and
  select console peers by trust class. Operator TLS must terminate inside
  clavenar-console, so use end-to-end TLS passthrough or an SSH tunnel;
  forwarded client-certificate headers are not supported.
- **No HPA.** Add one against the proxy / brain / policy-engine
  Deployments if you need it. Ledger / hil / identity stay pinned
  to `replicas: 1` while SQLite-backed.
- **No image build pipeline.** Default `imageRegistry` is
  `ghcr.io/clavenar`, expecting images named `clavenar-proxy`,
  `clavenar-brain`, etc., tagged with `appVersion`.
- **No demo-mint, no simulator.** These are demo-VPS artefacts —
  not part of the production sidecar deploy.

## Values reference

See `values.yaml` — every commented block doubles as documentation.
The top-level shape is:

```yaml
imageRegistry: ghcr.io/clavenar   # global registry override
imageTag: ""                             # global tag override → falls back to appVersion
imagePullPolicy: IfNotPresent
imagePullSecrets: []

nats:  { url: nats://nats:4222 }
vault: { addr: "", tokenSecretName: "" }

drainCapSecs: 30                         # CLAVENAR_GRACEFUL_DRAIN_SECS

probeDefaults:
  liveness:  { initialDelaySeconds: 5, periodSeconds: 10, timeoutSeconds: 2, failureThreshold: 3 }
  readiness: { initialDelaySeconds: 2, periodSeconds: 5,  timeoutSeconds: 2, failureThreshold: 3 }

services:
  proxy:        { ... extraEnv: [{name: CLAVENAR_PROXY_HEALTH_ADDR, value: 0.0.0.0:8080}] }
  brain:        { ... extraEnv: [{name: ANTHROPIC_API_KEY, value: mock-key}] }
  policyEngine: { ... }
  ledger:       { ... }            # replicas: 1 under SQLite; lift to N with CLAVENAR_LEDGER_BACKEND=postgres
  hil:          { ... }            # replicas: 1 (SQLite-pinned)
  identity:     { ... }            # replicas: 1 (SQLite-pinned)
  deepReview:   { ... }            # singleton; daily token budget is per-pod
  assurance:
    port: 8088                      # exact-console workload mTLS control
    healthPort: 9088                # plain /health + /readyz only; not published
  console:
    port: 8085                      # demo-only by default; operator mTLS when enabled
    demoPort: 9085                  # optional curated demo beside operator mTLS
    diagnosticsPort: 9185           # health/readiness/metrics; not Service-published
    operatorMtls: { enabled: false, publicTrustSecretName: "" }
    mutationOrigins: []             # exact HTTPS Admin mutation origins
    demo: { enabled: false }

persistence:
  ledger:   { enabled: true, size: 5Gi, ... }
  hil:      { enabled: true, size: 1Gi, ... }
  identity: { enabled: true, size: 1Gi, ... }

tlsBundle:
  secretName: ""                         # Required for any non-trivial deploy
  mountPath: /certs

networkPolicy:
  enabled: true                          # Baseline ingress isolation; requires a policy-capable CNI
  console:
    operatorMtls: { allowedPeers: [] }   # TLS-passthrough/operator access peers
    demo: { allowedPeers: [] }           # Public/demo reverse-proxy peers
  prometheusNamespaceLabel: ""           # Set to allow scrapes from a specific namespace

podDisruptionBudget:
  enabled: true                          # Only emits when services.<svc>.replicas > 1
```

Per-service `image.tag` overrides global `imageTag`, which falls
back to `Chart.appVersion`. Per-service `extraEnv` is appended
after the common env block (NATS_URL + CLAVENAR_GRACEFUL_DRAIN_SECS +
auto-wired backend URLs).

The values keys use camelCase (`policyEngine`, `deepReview`) for
valid Go-template paths; the helper kebab-cases them for k8s object
names (`my-clavenar-policy-engine`, `my-clavenar-deep-review`). Copy-
paste the kebab-cased form into `kubectl` / port-forward commands.

## Console trust classes

The console has three separate listener contracts. They share a pod but
not an authorization source:

| Listener | Enabled when | Service | Authentication and surface |
|---|---|---|---|
| Primary `:8085` | Always | Published | Default: plain HTTP curated demo-only router with no operator roles. With `operatorMtls.enabled`: native HTTPS, required operator client certificate, exact fingerprint + SPIFFE registry match, and role-gated operator router. |
| Demo `:9085` | `operatorMtls.enabled && demo.enabled` | Published | Plain HTTP curated demo router. Operator cookies/state are stripped; it cannot reach operator-only routes. |
| Diagnostics `:9185` | Always | Not published | Plain HTTP `/health`, `/readyz`, and `/metrics` only. Kubelet probes and Prometheus annotations target this port. |

The public operator trust Secret must contain exactly these projected keys:

- `ca.crt` — dedicated public operator trust root.
- `operators.json` — registry with `schemaVersion` and entries that bind
  `name`, `spiffeId`, `certificateSha256`, `role`, optional `tenant`,
  `status`, and `expiresAt`.

`viewer`, `approver`, and `admin` authority comes only from the verified
TLS leaf and an active, unexpired exact registry entry. A CA-valid unknown
leaf, a mismatched fingerprint/SPIFFE pair, a cookie, or caller-supplied
identity/role header fails closed. The bootstrap is an R1 operator path,
not customer-facing production authentication.

`GET /version.json` is the only release endpoint on the operator and demo
listeners. The chart pins its value to `Chart.appVersion`; console `extraEnv`
cannot replace that release marker or any listener/authentication setting.

Example hardened values (the referenced Secrets must already exist):

```yaml
tlsBundle:
  secretName: clavenar-workload-tls

services:
  console:
    operatorMtls:
      enabled: true
      publicTrustSecretName: clavenar-operator-trust
    mutationOrigins:
      - https://console.example.com
    demo:
      enabled: false

networkPolicy:
  console:
    operatorMtls:
      allowedPeers:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: operator-access
          podSelector:
            matchLabels:
              app.kubernetes.io/name: operator-tls-passthrough
    demo:
      allowedPeers: []
  prometheusNamespaceLabel: monitoring
```

Empty peer lists preserve default-deny. The operator peer must provide
end-to-end TLS passthrough; it must not terminate client TLS and convert
the certificate or role to headers. Each peer must name one workload with
non-empty `podSelector.matchLabels`; an optional `namespaceSelector` must use
non-empty `matchLabels`. Selector expressions, empty selectors, and `ipBlock`
peers are rejected so a broad negative selector cannot silently admit arbitrary
pods or namespaces.

When `alerting.enabled=true`, the bundled rules alert if console
diagnostics stay down (including fatal TLS/registry startup refusal), operator
trust reports not ready, the governed `*-console` Service changes to NodePort
or LoadBalancer, more than five operator authentication failures occur in five
minutes, or any bounded operator/login limiter throttles a request. The
Service-exposure rule has an executable promtool fixture in
`tests/promtool-console-alerts.yml`. Failure metrics use only bounded
`outcome`/`reason` labels and never certificate bodies or key bytes.

## SQLite vs. Postgres for ledger

Ledger defaults to SQLite mode (`replicas: 1` + PVC at
`/var/lib/clavenar/ledger.db`). To run multi-replica Postgres mode:

```yaml
services:
  ledger:
    replicas: 3
    extraEnv:
      - name: CLAVENAR_LEDGER_BACKEND
        value: postgres
      - name: CLAVENAR_LEDGER_PG_URL
        valueFrom:
          secretKeyRef:
            name: clavenar-ledger-pg
            key: url
persistence:
  ledger:
    enabled: false                # No PVC under Postgres mode
```

Cold-tier export, regulatory bundles, Iceberg metadata, and the
egress sweeper are SQLite-only and return 503 under Postgres mode —
wire your SIEM ingest directly against the Postgres chain table
instead. See the HA_RUNBOOK in `clavenar-e2e/`.

## Verify locally

```bash
helm lint .                  # smoke-check the chart
helm template my-clavenar . --debug      # dump rendered YAML + see template errors

# With everything on:
helm template my-clavenar . \
  --set tlsBundle.secretName=clavenar-certs \
  --set vault.addr=http://vault:8200 \
  --set vault.tokenSecretName=clavenar-vault \
  --set networkPolicy.enabled=true \
  --set services.brain.replicas=3

# Validate Service ports/types and exact NetworkPolicy caller rules:
helm template smoke . > /tmp/clavenar.yaml
python3 ../../scripts/check-listener-matrix.py --manifest /tmp/clavenar.yaml

# Validate alert syntax and the actual console-Service exposure matcher:
docker run --rm --entrypoint=/bin/promtool \
  -v "$(cd ../.. && pwd):/workspace:ro" -w /workspace \
  prom/prometheus:v2.55.0 \
  test rules tests/promtool-console-alerts.yml
```

If you have `kubeval` or `helm unittest` installed, they run too.
The `.github/workflows/ci.yml` in this repo runs `helm lint` +
`helm template` against multiple value combinations on every push.
