# warden helm chart

Umbrella Helm chart for the eight-service Agent Warden stack:
**proxy, brain, policy-engine, ledger, hil, identity, deep-review, console.**

This is the canonical chart — the older skeleton at
`warden-e2e/charts/warden/` is preserved for HA_RUNBOOK reference
only and will not receive new features.

## Quickstart

Two paths — pick by what's already in your cluster.

### Bundled (evaluation / kind / dev cluster)

One `helm install` brings the warden stack **plus** NATS + Vault +
auto-minted mTLS bundle:

```bash
# From the repo root
helm dep update charts/warden
helm install my-warden charts/warden \
  --namespace warden --create-namespace \
  -f tests/values-bundled.yaml
```

`tests/values-bundled.yaml` enables `nats.bundled` + `vault.bundled`
(dev-mode) + `tlsBundle.autoMint`. Reasonable for an evaluation
cluster; **not** for production.

### BYO (production)

Operator brings their own NATS + Vault + PKI bundle:

```bash
# From the chart root (warden-charts/charts/warden)
helm lint .
helm template my-warden . | less

# Real install
helm install my-warden . --namespace warden --create-namespace \
  --set nats.url=nats://my-nats:4222 \
  --set vault.addr=https://vault.internal:8200 \
  --set vault.tokenSecretName=warden-vault-token \
  --set tlsBundle.secretName=warden-certs \
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
| Upstream MCP target | `warden-upstream-stub` (echo MCP) bundled when `upstreamStub.enabled=true`, auto-wired into the proxy | Operator sets `services.proxy.extraEnv` `WARDEN_UPSTREAM_URL` at a real MCP server |
| Execution gateway | `warden-exec` deployed when `exec.enabled=true`. Sits between proxy and upstream-stub; exposes 7 Claude-Code-built-in-parity tools (`bash`, `read_file`, …) so an agent whose built-ins are denylisted still has a shell, but every call lands in the ledger | Lab-only; production still routes to a real MCP via `WARDEN_UPSTREAM_URL` |
| Agent Vault credential | Stub `secret/data/agents/agent-001` seeded by post-install Job when `agentVaultSeed.enabled=true` | Operator seeds per-agent entries against their own Vault |
| Proxy DNS alias | ExternalName `proxy` → `<release>-proxy` (CNAME) emitted when `proxyAlias.enabled=true` so in-cluster clients can dial bare `https://proxy:8443/mcp` and match the cert SAN | Skip when an Ingress / Gateway terminates mTLS upstream (it'll send the right SNI on the agent's behalf) |
| Audience | Evaluation / kind / single-tenant dev clusters | Production / multi-tenant clusters |
| State durability | Vault loses state on pod restart (re-bootstrapped) | Whatever your external Vault does |

### Lab agent (interactive Claude Code in-cluster)

After the chart is up, an optional scaffold under `warden-charts/lab/`
drops an actual Claude Code CLI pod into the same namespace, routed
through warden-proxy. Useful for evaluating the full Brain + Policy +
HIL + ledger pipeline against real agent traffic without leaving the
cluster. See [`lab/README.md`](../../lab/README.md) for the build +
apply walkthrough.

## What's wired

- **Eight Deployments + eight Services**, ClusterIP, one replica each.
- **HTTP probes** wired to `/health` (liveness) + `/readyz`
  (readiness) for every service. The proxy exposes a second
  container port (`healthPort: 8080`) bound to a non-mTLS listener
  serving only `/health` + `/readyz`; kubelet probes target this
  port. The agent-facing mTLS port (8443) stays exclusive to
  `/mcp` + `/metrics` and is the only port published by the
  proxy's k8s Service.
- **terminationGracePeriodSeconds** set to `drainCapSecs + 5` so
  the in-process watchdog (env `WARDEN_GRACEFUL_DRAIN_SECS`) fires
  before kubelet's SIGKILL.
- **PVCs** for ledger / hil / identity (the SQLite-backed services),
  mounted at `/var/lib/warden`, which matches the
  `WARDEN_*_DB=/var/lib/warden/*.db` defaults.
- **Shared ConfigMap** carries `NATS_URL`, `WARDEN_GRACEFUL_DRAIN_SECS`,
  and (when set) `VAULT_ADDR`.
- **Vault token** (when `vault.tokenSecretName` is set) is
  injected into proxy + identity via secretKeyRef.
- **Backend URL envs** wired automatically by component — proxy
  knows where to find brain/policy/hil/identity; console knows
  where to find ledger/hil/policy-engine/identity; deep-review
  knows where to find ledger. Service mesh overriding the
  Service names? Set `services.<svc>.extraEnv` to shadow.
- **NetworkPolicy** (opt-in via `networkPolicy.enabled=true`) —
  backend services accept ingress only from proxy + console +
  deep-review. Spec §"Threat model" §"Cross-cutting" treats this
  as the deployment perimeter.
- **PodDisruptionBudget** auto-emitted for any service where
  `replicas > 1`. SQLite-pinned services (`replicas: 1`) skip
  naturally; once an operator flips ledger to Postgres mode +
  `replicas: 3`, a PDB lands with `minAvailable = ceil(replicas/2)`.
- **TLS bundle mount** — when `tlsBundle.secretName` is set, a k8s
  Secret carrying the warden CA + per-service workload certs gets
  mounted read-only at `/certs`. Each pod sees only what it needs:
  `ca.crt` + its own `service-<name>.{crt,key}`. Proxy additionally
  mounts `server.{crt,key}` (agent-facing mTLS) and `client.{crt,key}`
  (legacy starter-agent client). No pod can read another service's
  private key. Generate the bundle with
  `warden-proxy/scripts/gen_certs.sh --env prod` then
  `kubectl create secret generic warden-tls --from-file=warden-proxy/certs/`.
- **Deep-review** singleton — same posture as brain. Per-agent
  history rides NATS, daily token budget is per-pod (scale the
  cap, not the pods).
- **Execution gateway** (opt-in via `exec.enabled=true`) —
  `warden-exec` becomes the proxy's upstream, exposes seven tools
  (`bash`, `read_file`, `write_file`, `edit_file`, `list_directory`,
  `search_files`, `fetch_url`) that mirror Claude Code's built-ins,
  and forwards anything else (initialize, `resources/*`,
  `tools/list` discovery for non-exec tools) to the upstream-stub.
  Pairs with the lab pod's `permissions.deny` posture so an agent
  cannot reach a shell except through the warden pipeline. Single
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
- **No ingress / TLS termination.** Add an Ingress, Gateway, or
  service-mesh layer downstream of this chart. The proxy's mTLS
  port (8443) typically faces agents directly via LoadBalancer;
  the console UI sits behind your OIDC-aware ingress.
- **No HPA.** Add one against the proxy / brain / policy-engine
  Deployments if you need it. Ledger / hil / identity stay pinned
  to `replicas: 1` while SQLite-backed.
- **No image build pipeline.** Default `imageRegistry` is
  `ghcr.io/vanteguardlabs`, expecting images named `warden-proxy`,
  `warden-brain`, etc., tagged with `appVersion`.
- **No demo-mint, no simulator.** These are demo-VPS artefacts —
  not part of the production sidecar deploy.

## Values reference

See `values.yaml` — every commented block doubles as documentation.
The top-level shape is:

```yaml
imageRegistry: ghcr.io/vanteguardlabs   # global registry override
imageTag: ""                             # global tag override → falls back to appVersion
imagePullPolicy: IfNotPresent
imagePullSecrets: []

nats:  { url: nats://nats:4222 }
vault: { addr: "", tokenSecretName: "" }

drainCapSecs: 30                         # WARDEN_GRACEFUL_DRAIN_SECS

probeDefaults:
  liveness:  { initialDelaySeconds: 5, periodSeconds: 10, timeoutSeconds: 2, failureThreshold: 3 }
  readiness: { initialDelaySeconds: 2, periodSeconds: 5,  timeoutSeconds: 2, failureThreshold: 3 }

services:
  proxy:        { ... extraEnv: [{name: WARDEN_PROXY_HEALTH_ADDR, value: 0.0.0.0:8080}] }
  brain:        { ... extraEnv: [{name: ANTHROPIC_API_KEY, value: mock-key}] }
  policyEngine: { ... }
  ledger:       { ... }            # replicas: 1 under SQLite; lift to N with WARDEN_LEDGER_BACKEND=postgres
  hil:          { ... }            # replicas: 1 (SQLite-pinned)
  identity:     { ... }            # replicas: 1 (SQLite-pinned)
  deepReview:   { ... }            # singleton; daily token budget is per-pod
  console:      { ... }            # WARDEN_CONSOLE_AUTH=disabled by default (lab); flip to oidc/webauthn/etc. for prod

persistence:
  ledger:   { enabled: true, size: 5Gi, ... }
  hil:      { enabled: true, size: 1Gi, ... }
  identity: { enabled: true, size: 1Gi, ... }

tlsBundle:
  secretName: ""                         # Required for any non-trivial deploy
  mountPath: /certs

networkPolicy:
  enabled: false                         # Flip to true under a policy-supporting CNI
  prometheusNamespaceLabel: ""           # Set to allow scrapes from a specific namespace

podDisruptionBudget:
  enabled: true                          # Only emits when services.<svc>.replicas > 1
```

Per-service `image.tag` overrides global `imageTag`, which falls
back to `Chart.appVersion`. Per-service `extraEnv` is appended
after the common env block (NATS_URL + WARDEN_GRACEFUL_DRAIN_SECS +
auto-wired backend URLs).

The values keys use camelCase (`policyEngine`, `deepReview`) for
valid Go-template paths; the helper kebab-cases them for k8s object
names (`my-warden-policy-engine`, `my-warden-deep-review`). Copy-
paste the kebab-cased form into `kubectl` / port-forward commands.

## SQLite vs. Postgres for ledger

Ledger defaults to SQLite mode (`replicas: 1` + PVC at
`/var/lib/warden/ledger.db`). To run multi-replica Postgres mode:

```yaml
services:
  ledger:
    replicas: 3
    extraEnv:
      - name: WARDEN_LEDGER_BACKEND
        value: postgres
      - name: WARDEN_LEDGER_PG_URL
        valueFrom:
          secretKeyRef:
            name: warden-ledger-pg
            key: url
persistence:
  ledger:
    enabled: false                # No PVC under Postgres mode
```

Cold-tier export, regulatory bundles, Iceberg metadata, and the
egress sweeper are SQLite-only and return 503 under Postgres mode —
wire your SIEM ingest directly against the Postgres chain table
instead. See the HA_RUNBOOK in `warden-e2e/`.

## Verify locally

```bash
helm lint .                  # smoke-check the chart
helm template my-warden . --debug      # dump rendered YAML + see template errors

# With everything on:
helm template my-warden . \
  --set tlsBundle.secretName=warden-certs \
  --set vault.addr=http://vault:8200 \
  --set vault.tokenSecretName=warden-vault \
  --set networkPolicy.enabled=true \
  --set services.brain.replicas=3
```

If you have `kubeval` or `helm unittest` installed, they run too.
The `.github/workflows/ci.yml` in this repo runs `helm lint` +
`helm template` against multiple value combinations on every push.
