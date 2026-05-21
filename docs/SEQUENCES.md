# warden-charts — sequence diagrams

Helm chart shape: eight Deployments + Services, optional NetworkPolicy
perimeter, optional PodDisruptionBudgets, optional TLS bundle Secret,
opt-in dashboards + alerts ConfigMaps, opt-in Alertmanager Secret. The
templates live under `charts/warden/templates/`; the values surface is
in `charts/warden/values.yaml`. Six flows below cover render + apply,
pod boot, cross-service URL wiring, observability discovery, alert
fan-out, and the NetworkPolicy ingress check — plus a flowchart of the
value-driven render-time branches.

## 1. `helm install <release> charts/warden`

Render is client-side (Helm 3) — the operator's kubectl context posts
the rendered batch straight to the apiserver; no Tiller. The eight
services + their PVCs are created in one apply round; kubelet reconciles
the schedule once the rendered Deployments land.

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant Helm as helm CLI
    participant Tpl as charts/warden<br/>templates/*
    participant API as kube-apiserver
    participant Sched as kube-scheduler
    participant Kube as kubelet (node)

    Op->>Helm: helm install my-warden charts/warden --set nats.url=...
    Helm->>Helm: merge values.yaml + --set overrides
    Helm->>Tpl: render with .Release + .Values + .Chart
    Tpl->>Tpl: _helpers.tpl resolves serviceFullname + imageRef per svc
    Tpl->>Tpl: services.yaml loops services.* emitting Deployment+Service+PVC
    Tpl->>Tpl: configmap.yaml writes NATS_URL + drain cap
    Tpl->>Tpl: networkpolicy.yaml fires only if networkPolicy.enabled
    Tpl->>Tpl: pdb.yaml fires only when any services.x.replicas greater than 1
    Tpl->>Tpl: dashboards-configmap fires only if dashboards.enabled
    Tpl->>Tpl: alerts-configmap fires only if alerting.enabled
    Tpl->>Tpl: alertmanager-config-secret fires only if alerting AND alertmanager
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
Secret-items filter in `services.yaml:109-125` scopes the projection so
a compromised pod can't read another service's private key. Proxy also
mounts `server.{crt,key}` + `client.{crt,key}`. Under TLS mode brain /
policy / hil / identity / ledger move `/health` + `/readyz` + `/metrics`
to a plain-HTTP `healthPort` so kubelet probes and Prometheus scrapes
land without a client cert.

```mermaid
sequenceDiagram
    autonumber
    participant Kube as kubelet
    participant API as kube-apiserver
    participant Sec as Secret<br/>warden-certs
    participant CM as ConfigMap<br/>warden-config
    participant Pod as brain Pod
    participant Brain as warden-brain bin

    Kube->>API: GET secret warden-certs, items filter applied
    API-->>Kube: ca.crt + service-brain.crt + service-brain.key
    Kube->>API: GET configmap warden-config
    API-->>Kube: NATS_URL, WARDEN_GRACEFUL_DRAIN_SECS, optional VAULT_ADDR
    Kube->>Pod: mount /certs (defaultMode 0644), inject env, start container

    Note over Pod: chart-injected envs:<br/>WARDEN_BRAIN_TLS_DIR=/certs<br/>WARDEN_BRAIN_ALLOWED_CALLERS=spiffe://warden.local/service/proxy<br/>WARDEN_BRAIN_HEALTH_ADDR=0.0.0.0:9081

    Pod->>Brain: PID 1 startup
    Brain->>Brain: read /certs/ca.crt, service-brain.crt, service-brain.key
    Brain->>Brain: bind rustls + SPIFFE-URI allowlist on :8081
    Brain->>Brain: bind plain HTTP /health, /readyz, /metrics on :9081

    Kube->>Brain: httpGet /readyz on healthPort 9081
    Brain-->>Kube: 200 OK
    Kube->>API: pod condition Ready=True
    Note over Kube,API: readinessProbe: initialDelaySeconds 2, periodSeconds 5
```

## 3. Cross-service backend URL wiring under `tlsBundle.secretName` set

`_helpers.tpl::warden.backendEnvs` flips every cross-service URL to
`https://` and injects `service-<caller>.{crt,key}` mount paths when
`tlsBundle.secretName` is non-empty. Proxy → brain is the canonical
hop; the same shape covers proxy → policy / hil / identity and console
→ ledger / hil / policy / identity.

```mermaid
sequenceDiagram
    autonumber
    actor Agent as Agent (in-cluster)
    participant Proxy as proxy Pod<br/>(service-proxy.{crt,key})
    participant Svc as Service<br/>my-warden-brain
    participant Brain as brain Pod<br/>(rustls listener, SPIFFE gate)
    participant Ledger as ledger Pod
    participant NATS as nats:4222

    Note over Proxy: WARDEN_BRAIN_URL=https://my-warden-brain:8081/inspect<br/>WARDEN_PROXY_OUTBOUND_CERT_PATH=/certs/service-proxy.crt<br/>WARDEN_PROXY_OUTBOUND_KEY_PATH=/certs/service-proxy.key<br/>WARDEN_PROXY_OUTBOUND_CA_PATH=/certs/ca.crt

    Agent->>Proxy: POST /mcp on :8443 (mTLS, client cert)
    Proxy->>Svc: resolve cluster DNS -> brain pod IP
    Proxy->>Brain: TLS ClientHello with service-proxy.crt
    Brain->>Brain: WebPkiClientVerifier checks chain against ca.crt
    Brain->>Brain: SPIFFE-URI gate matches WARDEN_BRAIN_ALLOWED_CALLERS
    Brain-->>Proxy: handshake OK, application data unlocked
    Proxy->>Brain: POST /inspect body
    Brain-->>Proxy: classification verdict
    Proxy->>Ledger: publish to NATS warden.forensic (mTLS)
    Ledger->>NATS: consumer drains, appends to hash chain
    Proxy-->>Agent: MCP response
```

## 4. Prometheus scrape + Grafana dashboard discovery

`warden.metricsAnnotations` writes `prometheus.io/scrape="true"` at the
pod level with a port fallback chain `metrics.port → healthPort → port`,
so under TLS the scrape lands on the plain-HTTP health listener. Rules
+ dashboards ship as ConfigMaps labelled `prometheus_rule:"1"` /
`grafana_dashboard:"1"` for the kube-prometheus-stack sidecar to pick up.

```mermaid
sequenceDiagram
    autonumber
    participant API as kube-apiserver
    participant Prom as Prometheus<br/>(in-cluster)
    participant Sidecar as Grafana sidecar
    participant CM1 as ConfigMap<br/>warden-alerts
    participant CM2 as ConfigMap<br/>warden-dashboards
    participant Pod as brain Pod<br/>(annotations: scrape, port 9081)

    Note over Prom: kubernetes_sd_configs role: pod<br/>filter on prometheus.io/scrape=true
    Prom->>API: LIST pods, watch
    API-->>Prom: pod list including brain with annotations
    loop every 30s
        Prom->>Pod: GET /metrics on prometheus.io/port (9081 under TLS)
        Pod-->>Prom: warden_brain_requests_total, ...
    end

    Sidecar->>API: LIST configmaps where label grafana_dashboard=1
    API-->>Sidecar: warden-dashboards bundles overview + cost JSON
    Sidecar->>CM2: read warden-overview.json, warden-cost.json
    Sidecar->>Sidecar: provision to Grafana via /api/dashboards/db

    Note over Prom: PrometheusRule sidecar
    Prom->>API: LIST configmaps where label prometheus_rule=1
    API-->>Prom: warden-alerts ConfigMap
    Prom->>CM1: read warden-alerts.yaml
    Prom->>Prom: load warden.critical + warden.warning rule groups
```

## 5. Alert fan-out — `LedgerChainCorrupted` fires

Severity-based routing splits critical vs. warning. `warden.critical`
matches against the `critical` receiver (Slack #warden-ops + email when
SMTP is configured); the default `slack-ops` receiver catches the rest.
The Secret form keeps webhook + SMTP creds off ConfigMaps.

```mermaid
sequenceDiagram
    autonumber
    participant Prom as Prometheus
    participant AM as Alertmanager
    participant Sec as Secret<br/>warden-alertmanager
    participant Slack as Slack<br/>#warden-ops
    participant SMTP as SMTP relay
    actor Op as Oncall

    Note over Prom: rule warden_ledger_chain_valid == 0<br/>for 1m, severity=critical
    Prom->>Prom: rule eval, sample over threshold for 1m
    Prom->>AM: POST /api/v1/alerts (alertname=LedgerChainCorrupted)

    AM->>Sec: load alertmanager.yml at boot
    Sec-->>AM: route + receivers config
    AM->>AM: group_wait 30s, group_by alertname,severity
    AM->>AM: route matches severity=critical -> receiver "critical"

    par Slack hop
        AM->>Slack: POST webhook (channel #warden-ops, send_resolved=true)
        Slack-->>Op: incoming alert
    and email hop (only if alertmanager.email.to set)
        AM->>SMTP: EHLO + AUTH PLAIN, MAIL FROM
        SMTP-->>Op: alert email
    end

    Note over Prom,AM: when sample clears<br/>AM fires resolved notification on same channels
```

## 6. NetworkPolicy perimeter — sidecar tries to reach `brain`

Front-door services (`proxy`, `console`) get `ingress: [{}]` so any pod
in the namespace can hit them; backends restrict to the three legitimate
in-stack callers. The Prometheus exception kicks in only when
`prometheusNamespaceLabel` is set — without that label the scrape lives
in the same namespace as warden and matches via the catch-all from
caller pods.

```mermaid
sequenceDiagram
    autonumber
    participant Side as Sidecar Pod<br/>(no warden label)
    participant CNI as CNI (calico / cilium)
    participant NP as NetworkPolicy<br/>brain
    participant Brain as brain Pod
    participant Proxy as proxy Pod<br/>(component=proxy)

    Note over NP: podSelector component=brain<br/>ingress from podSelectors:<br/>component in {proxy, console, deep-review}

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

## Chart render decision tree

Every value-driven branch in the chart in one tree. The leaves are the
objects that actually land on the apiserver; the gates above them are
the values keys that decide whether they land.

```mermaid
flowchart TD
    H[helm install / upgrade] --> V[merge values.yaml plus --set]
    V --> Loop[range over .Values.services]

    Loop --> En{services.x.enabled?}
    En -->|false| Skip[skip service]
    En -->|true| Dep[emit Deployment + Service]

    Dep --> Pers{persistence.x.enabled?}
    Pers -->|yes| Pvc[emit PVC mounted at /var/lib/warden]
    Pers -->|no| NoPvc[no PVC, emptyDir-equivalent]

    Dep --> Repl{replicas greater than 1?}
    Repl -->|yes AND podDisruptionBudget.enabled| Pdb[emit PDB minAvailable = ceil/2]
    Repl -->|no| NoPdb[no PDB]

    Dep --> Tls{tlsBundle.secretName set?}
    Tls -->|yes| Mount[mount /certs with per-pod items filter]
    Tls -->|yes| Envs[backendEnvs flips URLs to https, injects ALLOWED_CALLERS]
    Tls -->|no| Plain[plain HTTP cross-service hops]

    Loop --> Np{networkPolicy.enabled?}
    Np -->|yes| NpEmit[emit NetworkPolicy per service]
    NpEmit --> NpFront{service is proxy or console?}
    NpFront -->|yes| Open[ingress empty rule, namespace-wide]
    NpFront -->|no| Restrict[ingress from proxy + console + deep-review]
    NpEmit --> Scrape{prometheusNamespaceLabel set?}
    Scrape -->|yes| AddScrape[add namespaceSelector rule for scraper]
    Scrape -->|no| NoScrape[scraper must run in same ns or be excluded]

    V --> Dash{dashboards.enabled?}
    Dash -->|yes| DashCm[emit ConfigMap label grafana_dashboard=1]
    V --> Alerts{alerting.enabled?}
    Alerts -->|yes| AlertsCm[emit ConfigMap label prometheus_rule=1]
    Alerts --> Am{alertmanager.enabled?}
    Am -->|yes| AmSec[emit Alertmanager Secret with Slack plus SMTP routing]
    Am -->|no| NoAm[operator wires alerts into their own AM]
```
