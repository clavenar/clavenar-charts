# clavenar-charts — sequence diagrams

Helm chart shape: nine Deployments + Services, optional NetworkPolicy
perimeter, optional PodDisruptionBudgets, optional TLS bundle Secret,
opt-in dashboards + alerts ConfigMaps, opt-in Alertmanager Secret. The
templates live under `charts/clavenar/templates/`; the values surface is
in `charts/clavenar/values.yaml`. Seven flows below cover render + apply,
pod boot, cross-service URL wiring, observability discovery, alert
fan-out, the NetworkPolicy ingress check, and the TLS auto-mint + Vault
hook lifecycle — plus a flowchart of the value-driven render-time
branches.

## 1. `helm install <release> charts/clavenar`

Render is client-side (Helm 3) — the operator's kubectl context posts
the rendered batch straight to the apiserver; no Tiller. The nine
services + their PVCs are created in one apply round; kubelet reconciles
the schedule once the rendered Deployments land.

Before any object is emitted, `deploymentProfile=production` validates the
whole security posture as one unit: an operator-managed HIL authentication
Secret, enabled NetworkPolicy, existing workload TLS with auto-mint off,
console operator mTLS with separate public trust, and the exact website
trusted-proxy identity plus one positive selector on ledger mTLS.

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant Helm as helm CLI
    participant Tpl as charts/clavenar<br/>templates/*
    participant API as kube-apiserver
    participant Sched as kube-scheduler
    participant Kube as kubelet (node)

    Op->>Helm: helm install my-clavenar charts/clavenar --set nats.url=...
    Helm->>Helm: merge values.yaml + --set overrides
    Helm->>Tpl: render with .Release + .Values + .Chart
    Tpl->>Tpl: _helpers.tpl resolves serviceFullname + imageRef per svc
    Tpl->>Tpl: services.yaml loops services.* emitting Deployment+Service+PVC
    Tpl->>Tpl: configmap.yaml writes NATS_URL + drain cap
    Tpl->>Tpl: networkpolicy.yaml fires only if networkPolicy.enabled
    Tpl->>Tpl: pdb.yaml fires only when any services.x.replicas greater than 1
    Tpl->>Tpl: dashboards-configmap fires only if dashboards.enabled
    Tpl->>Tpl: PrometheusRule fires only if alerting.enabled
    Tpl->>Tpl: AlertmanagerConfig fires only if alerting AND alertmanager
    Tpl-->>Helm: rendered manifests batch
    Helm->>API: POST batch (Deployments, Services, PVCs, ConfigMap, NOTES)
    API-->>Helm: accepted
    Helm-->>Op: NOTES.txt printed (warns if tlsBundle unset, networkPolicy disabled)
    API->>Sched: Deployment created
    Sched->>API: bind pods to nodes
    API->>Kube: pod spec
    Kube->>Kube: pull image, mount certs Secret + data PVC
    Kube->>API: Pod Ready (once readyz probe passes)
```

## 2. Pod boot under `tlsBundle.secretName` set

Each pod mounts only `ca.crt` + its own `service-<name>.{crt,key}` — the
per-pod `items:` projection on the `certs` Secret volume in
`services.yaml` scopes the projection so a compromised pod can't read
another service's private key. Proxy also mounts `server.{crt,key}` +
`client.{crt,key}`. Assurance mounts the generic client pair only for its
synthetic proxy/NATS traffic; `service-assurance.{crt,key}` terminates the
exact-console control listener. Under TLS mode brain /
policy / hil / identity / ledger move `/health` + `/readyz` + `/metrics`
to a plain-HTTP `healthPort` so kubelet probes and Prometheus scrapes
land without a client cert. Exact dependency init gates reach the same
ClusterIP-only ports through reviewed NetworkPolicy edges. Assurance similarly moves only `/health` and
`/readyz` to plain diagnostics `9088`; status and mutations remain on mTLS
`8088`.

```mermaid
sequenceDiagram
    autonumber
    participant Kube as kubelet
    participant API as kube-apiserver
    participant Sec as Secret<br/>clavenar-certs
    participant CM as ConfigMap<br/>clavenar-config
    participant Init as workload-tls-projector
    participant Pod as brain Pod
    participant Brain as clavenar-brain bin

    Kube->>API: GET secret clavenar-certs, items filter applied
    API-->>Kube: ca.crt + service-brain.crt + service-brain.key
    Kube->>API: GET configmap clavenar-config
    API-->>Kube: NATS_URL, CLAVENAR_GRACEFUL_DRAIN_SECS, optional VAULT_ADDR
    Kube->>Init: mount exact Secret items mode 0440
    Init->>Pod: copy to memory volume; key mode 0600, cert mode 0444
    Kube->>Pod: mount owner-bound /certs read-only, inject env, start container

    Note over Pod: chart-injected envs:<br/>CLAVENAR_BRAIN_TLS_DIR=/certs<br/>CLAVENAR_BRAIN_ALLOWED_CALLERS=proxy-only inspect prefix<br/>EXPLAIN_CALLER=exact policy-engine<br/>NARRATE_CALLER=exact console<br/>strict body/rate/spend/timeout controls<br/>CLAVENAR_BRAIN_HEALTH_ADDR=0.0.0.0:9081

    Pod->>Brain: PID 1 startup
    Brain->>Brain: read /certs/ca.crt, service-brain.crt, service-brain.key
    Brain->>Brain: bind rustls + route-aware SPIFFE authorization on :8081
    Brain->>Brain: bind plain HTTP /, /health, /readyz, /metrics on :9081
    Note over Brain: no explain, narrate, model, or provider operation on :9081

    Kube->>Brain: httpGet /readyz on healthPort 9081
    Brain-->>Kube: 200 OK
    Kube->>API: pod condition Ready=True
    Note over Kube,API: readinessProbe: initialDelaySeconds 2, periodSeconds 5
```

## 3. Cross-service backend URL wiring under `tlsBundle.secretName` set

`_helpers.tpl::clavenar.backendEnvs` flips cross-service URLs to
`https://` and injects `service-<caller>.{crt,key}` mount paths when
`tlsBundle.secretName` is non-empty. Proxy → brain is the canonical
hop; the same shape covers proxy → policy / hil / identity and console
→ brain / ledger / hil / policy / identity / assurance. Policy-engine's
explanation URL and console's narration/model URL are always the Brain HTTPS
application listener; they never fall back to diagnostics. Brain accepts the
exact policy-engine identity for explain and exact console identity for
narrate/model after the client certificate verifies. The assurance hop likewise
accepts only the exact console SPIFFE URI.

```mermaid
sequenceDiagram
    autonumber
    actor Agent as Agent (in-cluster)
    participant Proxy as proxy Pod<br/>(service-proxy.{crt,key})
    participant Svc as Service<br/>my-clavenar-brain
    participant Brain as brain Pod<br/>(rustls listener, SPIFFE gate)
    participant Ledger as ledger Pod
    participant NATS as nats:4222

    Note over Proxy: CLAVENAR_BRAIN_URL=https://my-clavenar-brain:8081/inspect<br/>CLAVENAR_PROXY_OUTBOUND_CERT_PATH=/certs/service-proxy.crt<br/>CLAVENAR_PROXY_OUTBOUND_KEY_PATH=/certs/service-proxy.key<br/>CLAVENAR_PROXY_OUTBOUND_CA_PATH=/certs/ca.crt

    Agent->>Proxy: POST /mcp on :8443 (mTLS, client cert)
    Proxy->>Svc: resolve cluster DNS -> brain pod IP
    Proxy->>Brain: TLS ClientHello with service-proxy.crt
    Brain->>Brain: WebPkiClientVerifier checks chain against ca.crt
    Brain->>Brain: SPIFFE-URI gate matches CLAVENAR_BRAIN_ALLOWED_CALLERS
    Brain-->>Proxy: handshake OK, application data unlocked
    Proxy->>Brain: POST /inspect body
    Brain-->>Proxy: classification verdict
    Proxy->>Ledger: publish to NATS clavenar.forensic (mTLS)
    Ledger->>NATS: consumer drains, appends to hash chain
    Proxy-->>Agent: MCP response
```

## 4. Prometheus scrape + Grafana dashboard discovery

`clavenar.metricsAnnotations` writes `prometheus.io/scrape="true"` at the
pod level with a port fallback chain `metrics.port → healthPort → port`,
so under TLS the scrape lands on the plain-HTTP health listener. Rules ship as
a standard Prometheus Operator `PrometheusRule`; dashboards remain ConfigMaps
labelled `grafana_dashboard:"1"` for the Grafana sidecar.

```mermaid
sequenceDiagram
    autonumber
    participant API as kube-apiserver
    participant Prom as Prometheus<br/>(in-cluster)
    participant Sidecar as Grafana sidecar
    participant Rule as PrometheusRule<br/>clavenar-alerts
    participant CM2 as ConfigMap<br/>clavenar-dashboards
    participant Pod as brain Pod<br/>(annotations: scrape, port 9081)

    Note over Prom: kubernetes_sd_configs role: pod<br/>filter on prometheus.io/scrape=true
    Prom->>API: LIST pods, watch
    API-->>Prom: pod list including brain with annotations
    loop every 30s
        Prom->>Pod: GET /metrics on prometheus.io/port (9081 under TLS)
        Pod-->>Prom: clavenar_brain_requests_total, ...
    end

    Sidecar->>API: LIST configmaps where label grafana_dashboard=1
    API-->>Sidecar: clavenar-dashboards bundles overview + cost JSON
    Sidecar->>CM2: read clavenar-overview.json, clavenar-cost.json
    Sidecar->>Sidecar: provision to Grafana via /api/dashboards/db

    Note over Prom: Prometheus Operator ruleSelector
    Prom->>API: LIST PrometheusRules matching configured labels
    API-->>Prom: clavenar-alerts PrometheusRule
    Prom->>Rule: load spec.groups
    Prom->>Prom: load synthetic, critical, and warning rule groups
```

## 5. Alert fan-out — `LedgerChainCorrupted` fires

The chart emits a standard `AlertmanagerConfig` that routes every Clavenar
alert to an operator-owned durable inbox. The webhook URL and bearer token are
referenced from an existing Secret; neither credential is accepted inline.

```mermaid
sequenceDiagram
    autonumber
    participant Prom as Prometheus
    participant AM as Alertmanager
    participant AMC as AlertmanagerConfig
    participant Sec as Secret<br/>operator-owned routing
    participant Inbox as Durable operator inbox
    actor Op as Oncall

    Note over Prom: rule clavenar_ledger_chain_valid == 0<br/>for 1m, severity=critical
    Prom->>Prom: rule eval, sample over threshold for 1m
    Prom->>AM: POST /api/v1/alerts (alertname=LedgerChainCorrupted)

    AM->>AMC: select operator-inbox route
    AMC->>Sec: resolve webhook URL + bearer token
    AM->>AM: group_wait 5s, group_by alertname,severity,operation_id
    AM->>Inbox: authenticated POST (status=firing)
    Inbox-->>Op: durable notification
    Op->>Inbox: authenticated acknowledgement
    Note over Prom,AM: when sample clears
    AM->>Inbox: authenticated POST (status=resolved)
    Inbox-->>Op: durable lifecycle receipt
```

## 6. NetworkPolicy perimeter — sidecar tries to reach `brain`

The proxy agent listener is the only core rule admitting arbitrary sources.
Console ingress is default-deny until an operator supplies an exact peer for
its operator or demo trust class. Backends restrict each destination port to
the named in-stack callers. Brain `:8081` admits only proxy, policy-engine, and
console pods; `:9081` admits no application pod. The Prometheus exception is
added only when `prometheusNamespaceLabel` names its namespace.

In the production profile, the separately deployed website selector gets one
additional path to ledger `:8183` only. Ledger authenticates the exact
`spiffe://clavenar.local/service/website` certificate before honoring a
forwarded client address. Its canonical pod label and explicit namespace are
required to differ from in-release and Prometheus selectors. The selector is
absent from public ledger `:8083`, so direct callers remain keyed by their
socket address.

```mermaid
sequenceDiagram
    autonumber
    participant Side as Sidecar Pod<br/>(no clavenar label)
    participant CNI as CNI (calico / cilium)
    participant NP as NetworkPolicy<br/>brain
    participant Brain as brain Pod
    participant Proxy as proxy Pod<br/>(component=proxy)

    Note over NP: podSelector component=brain<br/>:8081 from exact components:<br/>{proxy, policy-engine, console}<br/>:9081 only from configured Prometheus namespace

    Side->>CNI: dial brain:8081
    CNI->>NP: evaluate ingress for destination brain
    NP-->>CNI: source pod has no app.kubernetes.io/component label match
    CNI-->>Side: drop (TCP RST, connect refused)

    Note over Proxy: same dial, different source label
    Proxy->>CNI: dial brain:8081
    CNI->>NP: evaluate ingress
    NP-->>CNI: source component=proxy matches first podSelector
    CNI->>Brain: forward SYN
    Brain-->>Proxy: TLS handshake proceeds (see flow 3)
```

## 7. Install-time hooks — TLS auto-mint then Vault bootstrap

The prerequisite for flows 2 and 3. Under `tlsBundle.autoMint` the chart
mints the `clavenar-certs` bundle from a `pre-install,pre-upgrade` hook
Job before any workload pod schedules; under `vault.bundled.enabled` two
`post-install,post-upgrade` hook Jobs provision the transit key + lab
agent credential after the release lands. Hook weights order the
pre-install set — RBAC `-20` (`tls-automint-rbac.yaml`) → script
ConfigMap `-15` (`tls-automint-script.yaml`) → Job `0`
(`tls-automint-job.yaml`) — so the ServiceAccount and all three governed
scripts exist before the Job starts. A kubectl `snapshot` initContainer reads
the exact qualified layout label and complete existing Secret, an openssl
`mint` initContainer validates or stages a fresh candidate, and the kubectl
`apply` container performs the transaction. The `/state` and `/work`
emptyDirs are memory-backed. Ordinary `reconcile` upgrades preserve valid
Secret bytes exactly; missing, foreign, partial, mismatched, or implicit trust
changes fail closed. Default membership covers the nine chart-managed
services plus website, demo-mint, and simulator peer identities; the product
pods still project only their own keys. An explicit `rotate` advances a
generation through
old-leaf/dual-root, new-leaf/dual-root, and new-only phases, waiting for every
TLS consumer to become Ready at each boundary. Failure restores the prior
generation, while success retains only the old public CA in a history Secret.
The Vault Jobs run
post-install at weights `0` (`vault-bootstrap-job.yaml`) then `1`
(`vault-seed-job.yaml`).

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant Helm as helm CLI
    participant API as kube-apiserver
    participant Snap as Job initContainer<br/>snapshot (alpine/k8s)
    participant Mint as Job initContainer<br/>mint (alpine/openssl)
    participant Apply as Job container<br/>apply (alpine/k8s)
    participant Sec as Secret<br/>clavenar-certs
    participant Vault as bundled Vault<br/>(dev-mode, unsealed)
    participant VBoot as Job<br/>vault-bootstrap
    participant VSeed as Job<br/>vault-agent-seed

    Op->>Helm: helm install/upgrade<br/>tlsBundle.autoMint=true, vault.bundled.enabled=true

    Note over Helm,API: pre-install / pre-upgrade hooks, by weight
    Helm->>API: apply SA + Role + RoleBinding (weight -20)
    Helm->>API: apply tls-automint script ConfigMap (weight -15)
    Helm->>API: create tls-automint Job (weight 0)

    API->>Snap: GET Secret JSON; validate exact label + key inventory
    Snap->>Sec: snapshot existing bytes + stable metadata into memory
    API->>Mint: start initContainer mint
    Mint->>Mint: validate existing bundle and requested policy
    opt operation=rotate with advanced generation
        Mint->>Mint: mint and validate wholly fresh CA + leaves
    end
    Mint-->>API: initContainer exits 0
    API->>Apply: start main container apply
    alt operation=reconcile and canonical Secret present
        Apply->>Apply: no API write — Secret bytes remain exact
    else operation=rotate
        Apply->>Sec: old leaves + old/new public roots
        Apply->>API: roll TLS consumers; wait Ready within deadline
        Apply->>Sec: new leaves + old/new public roots
        Apply->>API: roll TLS consumers; wait Ready within deadline
        Apply->>Sec: new leaves + new public root only
        Apply->>API: roll TLS consumers; wait Ready
        Apply->>Sec: archive retired public ca.crt only
    end
    Apply-->>API: Job succeeded, hook-delete-policy reaps SA/Role/ConfigMap/Job

    Note over Helm,API: main release — Deployments, Services, configmap,<br/>vault-token Secret, bundled Vault subchart
    Helm->>API: POST release manifests
    API->>Vault: bundled Vault StatefulSet schedules

    Note over Helm,VSeed: post-install / post-upgrade hooks, by weight
    Helm->>API: create vault-bootstrap Job (weight 0)
    API->>VBoot: start
    VBoot->>Vault: poll vault status until reachable (up to 60x, sleep 2)
    VBoot->>Vault: secrets enable transit (tolerate exists); write transit/keys/clavenar-identity
    VBoot->>Vault: read transit/keys/clavenar-identity (post-condition)
    VBoot-->>API: Job succeeded

    Helm->>API: create vault-agent-seed Job (weight 1)
    API->>VSeed: start
    VSeed->>Vault: poll vault status until reachable
    VSeed->>Vault: kv put secret/agents/_legacy_unqualified/agent-001 api_key=stub-key
    VSeed->>Vault: kv get secret/agents/_legacy_unqualified/agent-001 (post-condition)
    VSeed-->>API: Job succeeded
    Helm-->>Op: release deployed — pods mount clavenar-certs (flow 2);<br/>identity signs SVIDs via transit
```

## Chart render decision tree

Every value-driven branch in the chart in one tree. The leaves are the
objects that actually land on the apiserver; the gates above them are
the values keys that decide whether they land.

```mermaid
flowchart TD
    H[helm install / upgrade] --> V[merge values.yaml plus --set]
    V --> Prof{deploymentProfile?}
    Prof -->|evaluation| Loop[range over .Values.services]
    Prof -->|production and all prerequisites valid| Loop
    Prof -->|production missing auth TLS policy trust or website peer| Refuse[fail render before apply]

    Loop --> En{services.x.enabled?}
    En -->|false| Skip[skip service]
    En -->|true| Dep[emit Deployment + Service]

    Dep --> Pers{persistence.x.enabled?}
    Pers -->|yes| Pvc[emit PVC mounted at /var/lib/clavenar]
    Pers -->|no| NoPvc[no PVC, emptyDir-equivalent]

    Dep --> Repl{replicas greater than 1?}
    Repl -->|yes AND podDisruptionBudget.enabled| Pdb[emit PDB minAvailable = ceil/2]
    Repl -->|no| NoPdb[no PDB]

    Dep --> Tls{tlsBundle.secretName set?}
    Tls -->|yes| Mount[mount /certs with per-pod items filter]
    Tls -->|yes| Envs[backendEnvs flips URLs to https, injects route callers]
    Tls -->|no| Plain[legacy hops use HTTP; Brain auxiliary clients remain HTTPS and fail soft]

    Loop --> Np{networkPolicy.enabled?}
    Np -->|yes| NpEmit[emit NetworkPolicy per service]
    NpEmit --> NpFront{service is proxy?}
    NpFront -->|yes| Open[agent mTLS ingress allows arbitrary sources]
    NpFront -->|no| Restrict[exact listener-specific callers; console default-deny]
    NpEmit --> Web{production website peer configured?}
    Web -->|yes| LedgerTls[admit exact selector to ledger 8183 only]
    NpEmit --> Scrape{prometheusNamespaceLabel set?}
    Scrape -->|yes| AddScrape[add namespaceSelector rule for scraper]
    Scrape -->|no| NoScrape[scraper must run in same ns or be excluded]

    V --> Dash{dashboards.enabled?}
    Dash -->|yes| DashCm[emit ConfigMap label grafana_dashboard=1]
    V --> Alerts{alerting.enabled?}
    Alerts -->|yes| AlertsRule[emit PrometheusRule with discovery labels]
    Alerts --> Am{alertmanager.enabled?}
    Am -->|yes| AmConfig[emit AlertmanagerConfig with Secret references]
    Am -->|no| NoAm[operator wires alerts into their own Alertmanager]

    V --> Exec{exec.enabled?}
    Exec -->|yes| ExecDep[emit evaluation-only exec mTLS Deployment + Service + workspace PVC + Proxy-only NetworkPolicy]
    ExecDep --> ExecTrust[project only CA + service-exec cert/key; Proxy uses exact workload-mTLS client]
    ExecDep --> ExecHealth[plain health-only 9002; no MCP fallback]
    Exec -->|no| NoExec[no exec gateway]

    V --> Stub{upstreamStub.enabled?}
    Stub -->|yes| StubDep[emit upstream-stub Deployment + Service, auto-wire proxy CLAVENAR_UPSTREAM_URL]

    V --> Alias{proxyAlias.enabled?}
    Alias -->|yes| AliasSvc[emit ExternalName Service named proxy]

    V --> Auto{tlsBundle.autoMint?}
    Auto -->|yes| AutoRbac[pre-install hook, weight -20, SA + Role + RoleBinding]
    AutoRbac --> AutoCm[pre-install hook, weight -15, tls-automint script ConfigMap]
    AutoCm --> AutoJob[pre-install hook, weight 0, mint then apply Secret unless ca.crt + scheme match]

    V --> Vault{vault.bundled.enabled?}
    Vault -->|yes| VaultTok[emit vault-token Secret with dev root token]
    Vault -->|yes| VaultBoot[post-install hook, weight 0, enable transit + create clavenar-identity key]
    VaultBoot --> Seed{agentVaultSeed.enabled?}
    Seed -->|yes| SeedJob[post-install hook, weight 1, kv put secret/agents/_legacy_unqualified/agent-001]
    Seed -->|no| NoSeed[BYO per-agent creds]
```
