# clavenar helm chart

Umbrella Helm chart for the nine-service Clavenar stack:
**proxy, brain, policy-engine, ledger, hil, identity, deep-review,
assurance, console.**

This is the canonical chart — the older skeleton at
`clavenar-e2e/charts/clavenar/` is preserved for HA_RUNBOOK reference
only and will not receive new features.

The chart wires Proxy to the in-release Brain, Policy, HIL, and Identity
services as one pre-execution boundary. This is security-bearing for HIL
modification re-gating (WP-04.7): a typed diff is only a candidate, and the
Proxy image must reparse it, re-read current authority and attestation, and run
Brain plus Policy before signing, actor-token minting, or upstream execution.
A fresh Yellow candidate blocks on a distinct plain-approval row; Helm does not
offer a value that bypasses that second decision.

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
helm template my-clavenar . -f ../../tests/values-production.yaml | less

# Real install
helm install my-clavenar . --namespace clavenar --create-namespace \
  --set deploymentProfile=production \
  --set nats.url=nats://my-nats:4222 \
  --set nats.external.operator=platform-security \
  --set nats.external.authorizationContract=clavenar.nats-authorization/v1 \
  --set nats.external.jetstreamPersistence=true \
  --set vault.addr=https://vault.internal:8200 \
  --set vault.tokenSecretName=clavenar-vault-token \
  --set vault.identityTokenKey=identity-token \
  --set vault.proxyTokenKey=proxy-token \
  --set authSecrets.existingSecretName=clavenar-runtime-auth \
  --set authSecrets.rotationId=prod-20260715-01 \
  --set tlsBundle.secretName=clavenar-certs \
  --set tlsBundle.autoMint=false \
  --set services.console.operatorMtls.enabled=true \
  --set services.console.operatorMtls.publicTrustSecretName=clavenar-operator-trust \
  --set services.console.mutationOrigins[0]=https://console.example.com \
  --set services.ledger.requireTrustedProxy=true \
  --set services.ledger.trustedProxySpiffe=spiffe://clavenar.local/service/website \
  -f ../../tests/values-production.yaml \
  --set services.brain.extraEnv[0].name=ANTHROPIC_API_KEY \
  --set services.brain.extraEnv[0].valueFrom.secretKeyRef.name=anthropic \
  --set services.brain.extraEnv[0].valueFrom.secretKeyRef.key=key
```

Prefer copying `tests/values-production.yaml` and changing its Secret names,
origins, namespaces, and exact workload labels over maintaining the long
command above. The production profile refuses to render unless all of these
boundaries are present together:

- `authSecrets.existingSecretName` selects operator-managed HIL credentials;
- `authSecrets.rotationId` selects an explicit non-secret credential generation;
- `attestationTrustAnchors.secretName` selects the public Kubernetes Ed25519
  verifier keys (the cluster signing private key is never mounted);
- `vault.addr` and `vault.tokenSecretName` select the external signed-registry
  authority, while distinct `identityTokenKey` and `proxyTokenKey` values
  select its least-privilege credentials; bundled dev-mode Vault is refused;
- external NATS names the responsible operator, declares exact enforcement of
  `clavenar.nats-authorization/v1`, and declares durable JetStream storage;
- Proxy and Identity receive the chart-owned `production` runtime posture and
  `identity-k8s-key-bound` provider. Evaluation explicitly selects the Proxy
  mock and never inherits it into production;
- NetworkPolicy is enabled;
- an existing workload-TLS Secret is selected and auto-mint is disabled;
- ledger historical-key verification is enabled and RFC 3161 proofs are
  required against the immutable public TSA trust projection;
- console operator mTLS uses a distinct public operator-trust Secret;
- ledger requires the exact `spiffe://clavenar.local/service/website` caller;
- exactly one positive website workload selector reaches ledger mTLS `:8183`
  and no configured website rule reaches public HTTP `:8083`; and
- that selector fixes `app.kubernetes.io/name=clavenar-website` in an explicit
  namespace distinct from the Helm release and Prometheus namespaces.

Passing this render gate is not production-readiness certification. It proves
only that the chart's governed configuration boundary is present; operators
remain responsible for validating matching service images and release
artifacts, external PKI and CNI enforcement, persistence, backup and disaster
recovery, and runtime behavior in the target cluster.

### Bundled vs BYO matrix

| Concern | Bundled (`*.bundled.enabled=true`) | BYO (default) |
|---|---|---|
| NATS deployment | Subchart `nats-io/nats` installed by the release with exact certificate-to-user authorization | External, operator-managed; production requires an explicit owner and v1 authorization declaration |
| JetStream persistence | 10Gi PVC by default (configure under `nats.config.jetstream.fileStore.pvc.size`) | Operator must explicitly declare durable JetStream storage in production |
| Vault deployment | Subchart `hashicorp/vault` in **dev mode** (in-memory, root token) | External, operator-managed |
| Transit engine | Auto-provisioned by post-install Job | Operator runs `vault secrets enable transit && vault write -f transit/keys/<name>` |
| mTLS bundle | Auto-minted by pre-install Job (self-signed CA) | Operator pre-populates Secret with managed-PKI certs |
| HIL and Brain cache keys | Chart creates and upgrade-preserves `<release>-shared-tokens` | Set `authSecrets.existingSecretName` to an operator/secret-store-managed Secret containing all governed keys |
| HIL exact-payload encryption key | Operator pre-creates the Secret selected by `hilPayloadProtection`; only HIL receives its mode-0440 file projection | Operator or secret-store controller owns the same dedicated 32-byte key and its rotation |
| Console identity | Safe `demo-only` mode; optional signed prefix-scoped demo Viewer, never operator/Admin authority | Optional native operator mTLS using a dedicated public CA + exact identity registry Secret |
| Upstream MCP target | `clavenar-upstream-stub` (echo MCP) bundled when `upstreamStub.enabled=true`, auto-wired into the proxy | Operator sets `services.proxy.extraEnv` `CLAVENAR_UPSTREAM_URL` at a real MCP server |
| Execution gateway | `clavenar-exec` deployed when `exec.enabled=true`. Sits between proxy and upstream-stub; exposes 7 Claude-Code-built-in-parity tools (`bash`, `read_file`, …) so an agent whose built-ins are denylisted still has a shell, but every call lands in the ledger | Lab-only; production still routes to a real MCP via `CLAVENAR_UPSTREAM_URL` |
| Agent Vault credential | Stub `secret/data/agents/_legacy_unqualified/agent-001` seeded by post-install Job when `agentVaultSeed.enabled=true` | Operator seeds tenant-qualified per-agent entries against their own Vault |
| Proxy DNS alias | ExternalName `proxy` → `<release>-proxy` (CNAME) emitted when `proxyAlias.enabled=true` so in-cluster clients can dial bare `https://proxy:8443/mcp` and match the cert SAN | Skip when an Ingress / Gateway terminates mTLS upstream (it'll send the right SNI on the agent's behalf) |
| Audience | Evaluation / kind / single-tenant dev clusters | Production / multi-tenant clusters |
| State durability | Vault loses state on pod restart (re-bootstrapped) | Whatever your external Vault does |
| Scheduled backup | Disabled; no backup claim | Required operator-owned weekly application-consistent capture with external repository credentials and sanitized receipt |
| Deployment profile | `evaluation` | Explicit `production` fail-closed render gate |

### Scheduled encrypted backup contract

The chart packages the exact public
`clavenar.backup-set-manifest/v1` schema and fixture in the immutable
`<release>-scheduled-backup` ConfigMap. Its generated Helm plan partitions all
20 state classes into captured, separate-custody, reconstructible, and
signed-source sets. Current workload and simulator private identities are
excluded and must be reissued.

The same immutable ConfigMap packages `clavenar.hil-backup-erasure/v1` and
the privacy-bearing backup/isolated-restore receipt contracts. An operator
must sanitize the application-consistent HIL copy before encryption and apply
the latest externally authenticated tenant-erasure disposition before any
restored workload starts. The disposition signing key is never chart data or
backup content.

Helm does not embed an object-store credential or pretend one generic CronJob
can snapshot arbitrary CSI, external NATS, and external Vault backends
consistently. Production requires `scheduledBackup.enabled=true`, a named
operator, the exact weekly schedule, a credential Secret owned outside
this chart, and a different Secret containing the sanitized live receipt:

```yaml
scheduledBackup:
  enabled: true
  operator: platform-backup-controller
  schedule: "0 8 * * 0"
  maximumAgeSeconds: 612000
  repositorySecretName: clavenar-backup-repository
  receiptSecretName: clavenar-backup-receipt
  encryptionFormat: restic-repository-v2
  offsiteClass: content-addressed-or-versioned
```

The operator must use SQLite online backup plus `quick_check`, a native or
bounded stopped-writer snapshot for broker/Vault state, exact restricted-file
allowlists, authenticated encryption before upload, a digest-qualified or
versioned offsite object, and remote verification. The receipt Secret contains
only the sanitized manifest, never repository credentials or protected state.
This boundary does not assert isolated restoration, disaster recovery, or
upgrade rollback.

### Stateful SQLite upgrades

Every persistence-enabled SQLite Deployment renders `strategy.type: Recreate`;
a replica limit alone does not prevent the default Deployment surge from
opening the same database twice. Before each upgrade, five default-on hooks
take SQLite online backups for Ledger, HIL, Identity, Policy Engine, and Proxy,
run `PRAGMA quick_check`, bind the exact source/target chart versions and
database digest in an immutable generation, and only then enforce the live
`Recreate` strategy. A pre-rollback hook scales the candidate writer to zero,
verifies the bound backup again, restores through SQLite into a temporary
database, and atomically replaces the failed migration.

The rollback hook is carried by the rollback target revision. Establish chart
0.25.0 or newer as the source revision before relying on automatic verified
restore; the first upgrade from an older chart receives the backup and
single-writer protection, but the older rollback target has no restore hook.
Do not remove `.clavenar-upgrade-backups` from a retained PVC. PostgreSQL
Ledger is explicitly outside this SQLite path when
`persistence.ledger.enabled=false`.

The chart embeds the public `clavenar.stateful-upgrade/v1` schema and example
receipt in an immutable ConfigMap. The live acceptance runner in
`clavenar-e2e` installs the pinned host-path CSI driver, mutates all five state
classes, and injects failed scheduling plus interrupted migration while
asserting fixed PVC UIDs, exact state commitments, and at most one application
writer.

### Authentication Secrets

The default remains backwards compatible: the chart generates
`<release>-shared-tokens`, keeps its data stable across upgrades, and injects
the HIL session and console-to-HIL decision credentials only through
`secretKeyRef`; Brain receives its cache HMAC key only through a mode-0440 file
projection. Exact pending-request payloads use a separate operator-owned
XChaCha20-Poly1305 key that is mounted only into HIL. Generate 32 random bytes
as 64 lowercase hexadecimal characters, store them in a file readable by the
Secret creation command, and never put the key in chart values:

```bash
openssl rand -hex 32 > /secure/path/hil-payload-key
kubectl -n clavenar create secret generic clavenar-hil-payload \
  --from-file=hil-payload-key=/secure/path/hil-payload-key
```

Select a different Secret or data key with
`hilPayloadProtection.secretName` and `hilPayloadProtection.key`. Rotate it
only through an operational migration that keeps existing ciphertext
decryptable; HIL deliberately refuses malformed, missing, inline, or
unexpectedly permissive key material before opening SQLite.

For production, pre-provision a separate Opaque Secret containing
`hil-session-key`, `hil-decide-token`, `hil-bootstrap-token`, and
`brain-cache-hmac-key`, then select it without putting any value in a values
file or Deployment manifest:

```bash
kubectl -n clavenar create secret generic clavenar-runtime-auth \
  --from-file=hil-session-key=/secure/path/hil-session-key \
  --from-file=hil-decide-token=/secure/path/hil-decide-token \
  --from-file=hil-bootstrap-token=/secure/path/hil-bootstrap-token \
  --from-file=brain-cache-hmac-key=/secure/path/brain-cache-hmac-key

helm upgrade --install my-clavenar . --namespace clavenar \
  --set authSecrets.existingSecretName=clavenar-runtime-auth \
  --set authSecrets.rotationId=prod-20260715-01
```

The Secret can instead be reconciled by External Secrets, Secrets Store CSI,
Sealed Secrets, or another controller. When `existingSecretName` is non-empty,
this chart does not create, copy, or take ownership of that Secret. All four
keys must already exist; missing keys keep the affected Pods from starting.

Optional symmetric credentials use the same `valueFrom.secretKeyRef` shape
through per-service `extraEnv`. For example, a dedicated demo-session key
shared by console, HIL, and ledger can live in the same externally managed
Secret without appearing literally in chart values:

```yaml
authSecrets:
  existingSecretName: clavenar-runtime-auth
  rotationId: prod-20260715-01

attestationTrustAnchors:
  secretName: clavenar-attestation-trust
  key: k8s-trust-anchors.json

services:
  console:
    extraEnv:
      - name: CLAVENAR_CONSOLE_DEMO_SESSION_HS256
        valueFrom:
          secretKeyRef: { name: clavenar-runtime-auth, key: demo-session-hs256 }
  hil:
    extraEnv:
      - name: CLAVENAR_HIL_DB
        value: /var/lib/clavenar/hil.db
      - name: CLAVENAR_HIL_DEMO_SESSION_HS256
        valueFrom:
          secretKeyRef: { name: clavenar-runtime-auth, key: demo-session-hs256 }
  ledger:
    extraEnv:
      - name: CLAVENAR_LEDGER_DB
        value: /var/lib/clavenar/ledger.db
      - name: CLAVENAR_LEDGER_DEMO_SESSION_HS256
        valueFrom:
          secretKeyRef: { name: clavenar-runtime-auth, key: demo-session-hs256 }
```

The chart fails rendering if any service `extraEnv` name is duplicated or
attempts to shadow a chart-emitted authentication, TLS, listener, caller,
trust, NATS, Vault, drain, or backend setting. It also rejects literal values
for the recognized console/HIL/ledger demo-session variables; those optional
entries must contain a non-empty `valueFrom.secretKeyRef`. This keeps
authentication values out of values files, rendered Pod specs, and release
receipts and prevents Kubernetes env-list ordering from changing a governed
boundary. Identity
legacy `CLAVENAR_IDENTITY_OIDC_HS256_KEY` and tenant-scoped
`CLAVENAR_IDENTITY_OIDC_TENANT_*_HS256_KEY` entries are rejected entirely in
the chart path; use the verification-only RS256 JWKS projection below.

Identity's production OIDC verifier is asymmetric: project the IdP's public
RS256 JWKS as a read-only file with the per-service `extraVolumes` /
`extraVolumeMounts` hooks, then use `extraEnv` only for the non-secret
issuer/audience, the file path, and the strict-mode flag:

```yaml
services:
  identity:
    extraEnv:
      - { name: CLAVENAR_IDENTITY_DB, value: /var/lib/clavenar/identity.db }
      - { name: CLAVENAR_IDENTITY_OIDC_TENANT_ACME_ISSUER, value: "https://idp.example.com/" }
      - { name: CLAVENAR_IDENTITY_OIDC_TENANT_ACME_AUDIENCE, value: clavenar-identity }
      - { name: CLAVENAR_IDENTITY_OIDC_TENANT_ACME_RS256_JWKS_FILE, value: /var/run/clavenar-oidc/acme-jwks.json }
      - { name: CLAVENAR_IDENTITY_REQUIRE_ASYMMETRIC_OIDC, value: "true" }
    extraVolumeMounts:
      - { name: oidc-jwks, mountPath: /var/run/clavenar-oidc, readOnly: true }
    extraVolumes:
      - name: oidc-jwks
        secret:
          secretName: clavenar-runtime-auth
          items:
            - { key: oidc-jwks.json, path: acme-jwks.json }
```

JWKS contains verification-only public keys, never the issuer's private signing
key. An external secret-store controller can own the referenced Secret; the
chart only projects the selected key.

Actor-token replay protection uses a shared durable JetStream KV bucket, not
the Identity SQLite volume. Set `services.identity.replayReplicas` to the exact
JetStream replica count (1..5) supported by the NATS deployment. Identity
validates the live bucket's file storage, bounded size and age, value limit,
history, discard policy, deletion policy, and replica count before accepting a
redemption. Missing, partitioned, full, or mismatched replay storage fails
closed; there is no local replay fallback.

The broader distributed-control inventory is packaged byte-for-byte as
`clavenar.distributed-control-state/v1`. Set `controlState.replicas` (1..5) to
the exact JetStream replica count shared by Identity and Proxy. The chart emits
`CLAVENAR_CONTROL_STATE_REPLICAS` to both services and rejects `extraEnv`
shadowing. Revocation, force-HIL, decoy, and grant-use projections remain
file-backed; configured tenant budgets and monthly spend remain retained in
Identity and Ledger rather than being replaced by Proxy's bounded cache.
The same immutable ConfigMap packages
`clavenar.distributed-control-resilience/v1` and mounts all four exact
inventory/resilience files into Proxy. Proxy readiness waits for both mandatory
KV mirrors and the retained grant-use store; the chart always enables the
fail-closed quota gate with a 300-second maximum complete-pair age. These
chart-owned variables, and the reserved snapshot path, cannot be shadowed
through `extraEnv`. No snapshot authorizes work in this contract version.

Bundled NATS maps the verified workload certificate DNS SAN to one exact user
with `verify_and_map`. The grants are generated from
`files/nats-authorization-v1.fixture.json` and limit application subjects,
stream/consumer APIs, KV namespaces, and `_INBOX.clavenar.<service>.>` reply
traffic. Unknown or peer identities and undeclared subjects deny by default;
there is no shared token, password, CN fallback, or CA-wide role. Its ingress
NetworkPolicy admits only Proxy, Policy Engine, Ledger, HIL, Identity, Deep
Review, and Assurance. Brain and unrelated pods receive neither NATS
configuration nor broker ingress. Run `python3 scripts/check-nats-authorization.py`
whenever the fixture, values, or network policy changes.

The complete render fixture is
[`tests/values-existing-auth-secret.yaml`](../../tests/values-existing-auth-secret.yaml).

`authSecrets.rotationId` is a public compare-and-swap generation, not a
credential. In chart-managed evaluation mode, leaving it unchanged preserves
the generated HIL keys on upgrade; changing it generates both replacement
values and rolls every Deployment. With an external Secret, update all of its
credential and JWKS entries atomically first, then advance `rotationId` in the
same controlled release so every consumer rolls onto that generation. Reusing
the identifier is a no-op. Production rejects the evaluation-only
`bootstrap-v1` identifier and requires an operator-selected value.

The chart coordinates rollout but cannot retain or exercise superseded token
bytes. Operators must separately prove old HIL, OIDC, and demo tokens are
rejected after readiness, and must recover forward rather than restoring a
superseded generation.

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
  (readiness) for every service. Bounded init gates wait on the exact acyclic
  public dependency graph for at most 30 two-second probes per edge. The proxy exposes a second
  container port (`healthPort: 8080`) bound to a non-mTLS listener
  serving `/`, `/health`, `/readyz`, and `/metrics`; kubelet probes and
  Prometheus target this port. The agent-facing mTLS port (8443) serves
  `/`, `/health`, `/readyz`, `/mcp`, and `/tool/{name}` but never
  `/metrics`. Its Kubernetes Service exposes that diagnostics port only inside
  the cluster for Assurance's exact readiness edge.
  Assurance likewise splits required-mTLS control (`8088`) from plain,
  internal `/health` + `/readyz` diagnostics (`9088`); no mutation or
  status route is installed on diagnostics. Its request, whole-run, and
  publish/ack deadlines are chart-governed, and durable completion requires an
  acknowledgement from the configured exact forensic JetStream stream.
  Brain similarly keeps auxiliary `/explain-pattern`,
  `/narrate-decision`, and `/model-snapshot` on the workload-mTLS application
  listener (`8081`). Its plain `9081` listener contains only `/`, `/health`,
  `/readyz`, and `/metrics`; its ClusterIP port has no provider path and
  NetworkPolicy admits only reviewed readiness/scrape callers.
- **terminationGracePeriodSeconds** set to `drainCapSecs + 5` so
  the in-process watchdog (env `CLAVENAR_GRACEFUL_DRAIN_SECS`) fires
  before kubelet's SIGKILL.
- **PVCs** for proxy / policy-engine / ledger / hil / identity (the
  SQLite-backed services). Policy governance state is retained at the explicit
  `/var/lib/clavenar-policy-engine/policies.db` path; its generated PVC carries
  Helm's keep policy, and `persistence.policyEngine.existingClaim` supports
  operator-owned backup and recovery. The other stores mount at
  `/var/lib/clavenar`.
- **Shared ConfigMap** carries `NATS_URL`, `CLAVENAR_GRACEFUL_DRAIN_SECS`,
  and (when set) `VAULT_ADDR`. Only declared NATS clients receive `NATS_URL`;
  each also receives its exact `_INBOX.clavenar.<service>` prefix.
- **Vault tokens** (when `vault.tokenSecretName` is set) are projected as
  mode-0440 files. Identity and Proxy receive only their distinct configured
  Secret key through `VAULT_TOKEN_FILE`; tokens never enter workload env.
- **Backend URL envs** wired automatically by component — proxy knows where to
  find brain/policy/hil/identity; policy-engine receives the Brain HTTPS
  application URL plus its exact expected server identity; console knows where
  to find brain/ledger/hil/policy-engine/identity and, in operator-mTLS posture,
  assurance; deep-review knows where to find ledger. These generated endpoints,
  TLS material paths, listener addresses, caller identities, and common
  NATS/Vault settings are chart-governed and cannot be shadowed through
  `extraEnv`. `CLAVENAR_UPSTREAM_URL` remains operator-configurable only when
  neither the execution gateway nor bundled upstream stub owns it.
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
  Use `valueFrom.secretKeyRef` as shown in Authentication Secrets above; never
  put the key in an inline `value`.
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
- **Persistent workload identity** — with TLS enabled,
  `workloadIdentity.enabled=true` gives each managed HTTP service a dedicated
  PVC and process-owned `0700` state directory. The caller generates and keeps
  its private key, replays crash-safe pending requests, and renews only from its
  exact current SVID. Identity publishes its enrollment endpoint before its
  own readiness succeeds to avoid a self-enrollment deadlock. An expired SVID
  fails readiness and new TLS handshakes; bootstrap is not restored
  automatically. Recovery requires an authorized Identity recovery request,
  removal of only the stopped workload's persisted current/pending records,
  and restart. Size and storage class are configured by the values below.
  Managed Deployments are limited to one replica because each exact-current
  identity requires a distinct store; shared state is rejected at render time.
- **Generated route capabilities** — the chart packages the canonical
  `workload-capability-bundle.json` byte-for-byte in an immutable ConfigMap.
  The chart package also carries the strict
  `active-agent-meter-v1.schema.json` and conformance fixture; the Ledger
  workload template checksum binds both contract bytes to each rollout.
  Ledger, Policy Engine, HIL, and Identity mount the same file and receive its
  exact bundle and endpoint-matrix SHA-256 receipts. Any missing, stale, or
  substituted policy prevents the mTLS application listener from starting;
  the old per-service `*_ALLOWED_CALLERS` overrides are rejected.
- **Attestation verifier contract** — the chart preserves contract 1.0.0's
  strict schema and conformance fixture byte-for-byte in a second immutable
  ConfigMap. Proxy, Identity, and Policy Engine mount the same read-only
  `/etc/clavenar/attestation` projection and roll only when either governed
  file changes. This packages the verifier boundary; it does not enable a
  mock provider as a production verifier.
- **Federated identity contract** — the chart packages the exact strict OIDC
  and signed-SAML schema and fixture in an immutable ConfigMap. Console mounts
  it read-only at `/etc/clavenar/federated-identity` and rolls when the
  governed fixture changes. Provider credentials and tenant/group mappings
  remain external Secret-backed runtime configuration.
- **Distributed control-state contract** — an immutable ConfigMap preserves
  the seven-row public authority/classification/replication inventory. Proxy
  and Identity receive the same validated `controlState.replicas` value; six
  controls remain mandatory and decoy catalog advertisement is the sole
  advisory projection.
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
  Its Secret must hold distinct Identity and Proxy token keys selected by
  `vault.identityTokenKey` and `vault.proxyTokenKey`.
- **No ingress / TLS termination.** Add an operator-controlled Ingress,
  Gateway, or service-mesh layer downstream of this chart. Chart-owned
  Services are fixed to ClusterIP; `NodePort` and `LoadBalancer` values
  fail rendering. Publish proxy 8443 through an mTLS-capable edge and
  select console peers by trust class. Operator TLS must terminate inside
  clavenar-console, so use end-to-end TLS passthrough or an SSH tunnel;
  forwarded client-certificate headers are not supported.
- **No HPA.** Add one against stateless services if you need it. Proxy,
  policy-engine, ledger, hil, and identity stay pinned to `replicas: 1` while
  their SQLite persistence is enabled.
- **No image build pipeline.** Default `imageRegistry` is
  `ghcr.io/clavenar`, expecting images named `clavenar-proxy`,
  `clavenar-brain`, etc., tagged with `appVersion`.
- **No demo-mint, no simulator.** These are demo-VPS artefacts —
  not part of the production sidecar deploy.

## Values reference

See `values.yaml` — every commented block doubles as documentation.
The top-level shape is:

```yaml
deploymentProfile: evaluation             # evaluation | production

ledgerCryptographicVerification:
  enabled: true                            # workload TLS also requires signed regulatory bundles
  tsaRequired: false                       # production requires true
  tsaUrl: https://freetsa.org/tsr          # RFC 3161 submission endpoint
  anchorIntervalSeconds: 3600

imageRegistry: ghcr.io/clavenar   # global registry override
imageTag: ""                             # global tag override → falls back to appVersion
imagePullPolicy: IfNotPresent
imagePullSecrets: []

nats:  { url: nats://nats:4222 }
controlState: { replicas: 1 }             # exact shared control KV replicas (1..5)
vault: { addr: "", tokenSecretName: "", identityTokenKey: identity-token, proxyTokenKey: proxy-token }

authSecrets:
  existingSecretName: ""               # empty: chart-managed <release>-shared-tokens
                                        # set: existing Secret with HIL + Brain cache keys
  rotationId: bootstrap-v1              # change explicitly to rotate and roll consumers

drainCapSecs: 30                         # CLAVENAR_GRACEFUL_DRAIN_SECS

probeDefaults:
  liveness:  { initialDelaySeconds: 5, periodSeconds: 10, timeoutSeconds: 2, failureThreshold: 3 }
  readiness: { initialDelaySeconds: 2, periodSeconds: 5,  timeoutSeconds: 2, failureThreshold: 3 }

services:
  proxy:
    grantJwksRefreshSeconds: 30       # required identity JWKS refresh cadence
    grantJwksMaxStalenessSeconds: 120 # presented grants fail 503 after this age
    grantJwksFetchTimeoutSeconds: 5   # per-refresh HTTP deadline
    extraEnv: []                      # health/backend/TLS/JWKS wiring is governed
  brain:
    port: 8081                       # workload-mTLS application listener
    healthPort: 9081                 # diagnostics only; not Service-published
    explainCallerSpiffe: spiffe://clavenar.local/service/policy-engine
    narrateCallerSpiffe: spiffe://clavenar.local/service/console
    explainRateLimitPerMinute: 20
    narrateRateLimitPerMinute: 60
    auxSpendBudgetMicroUsdPerHour: 5000000
    auxTimeoutMillis: 5000           # valid range 1..30000
    auxBodyLimitBytes: 16384         # valid range 1..1048576
    extraEnv: [{name: ANTHROPIC_API_KEY, value: mock-key}]
  policyEngine: { ... }
  ledger:
    replicas: 1                    # lift only with CLAVENAR_LEDGER_BACKEND=postgres
    requireTrustedProxy: false     # production requires true
    trustedProxySpiffe: ""         # production fixes exact website SPIFFE URI
  hil:          { ... }            # replicas: 1 (SQLite-pinned)
  identity:
    replicas: 1                    # lifecycle SQLite remains singleton
    replayReplicas: 1              # exact durable JetStream KV replicas (1..5)
  deepReview:   { ... }            # singleton; daily token budget is per-pod
  assurance:
    port: 8088                      # exact-console workload mTLS control
    healthPort: 9088                # plain /health + /readyz; ClusterIP-internal
    forensicSubject: clavenar.forensic
    forensicStream: clavenar-forensic # exact JetStream acknowledgement source
    requestTimeoutSecs: 30          # valid range 1..300
    runTimeoutSecs: 900             # valid range 1..3600; >= nested deadlines
    publishTimeoutSecs: 10          # valid range 1..60
  console:
    port: 8085                      # demo-only by default; operator mTLS when enabled
    demoPort: 9085                  # optional curated demo beside operator mTLS
    diagnosticsPort: 9185           # health/readiness/metrics; ClusterIP-internal
    operatorMtls: { enabled: false, publicTrustSecretName: "" }
    mutationOrigins: []             # exact HTTPS Admin mutation origins
    demo: { enabled: false }

persistence:
  proxy:   { enabled: true, size: 1Gi, ... } # durable server-execution receipts/outbox
  policyEngine: { enabled: true, size: 1Gi, existingClaim: "", ... }
  ledger:   { enabled: true, size: 5Gi, ... }
  hil:      { enabled: true, size: 1Gi, ... }
  identity: { enabled: true, size: 1Gi, ... }

workloadIdentity:
  enabled: true                         # required by the production profile
  size: 64Mi                            # one private-key/certificate state pair per PVC
  storageClass: ""                      # cluster default when empty
  accessMode: ReadWriteOnce             # one writer per workload Deployment

tlsBundle:
  secretName: ""                         # Required for any non-trivial deploy
  mountPath: /certs
  spiffeTrustDomain: clavenar.local      # governed exact-caller trust domain
  autoMint: false                        # evaluation only
  rotation:
    operation: reconcile                 # reconcile or explicit rotate
    generation: bootstrap-v1             # advance for every rotation
    reason: none                         # none, membership, or expiry
    expiryWindowSeconds: 2592000         # expiry rotation gate (30 days)
    overlapSeconds: 900                  # bounded two-public-root window
    rolloutTimeoutSeconds: 300           # readiness deadline per controller

networkPolicy:
  enabled: true                          # Baseline ingress isolation; requires a policy-capable CNI
  ledger:
    trustedProxy: { allowedPeers: [] }   # exact website selector; ledger mTLS only
  console:
    operatorMtls: { allowedPeers: [] }   # TLS-passthrough/operator access peers
    demo: { allowedPeers: [] }           # Public/demo reverse-proxy peers
  prometheusNamespaceLabel: ""           # Set to allow scrapes from a specific namespace

podDisruptionBudget:
  enabled: true                          # Only emits when services.<svc>.replicas > 1
```

Per-service `image.tag` overrides global `imageTag`, which falls back to
`Chart.appVersion`. Per-service `extraEnv` is reserved for names the chart does
not emit. Duplicate names, including attempts to shadow `NATS_URL`,
`CLAVENAR_GRACEFUL_DRAIN_SECS`, auto-wired endpoints, listener/auth settings,
or TLS/trust paths, fail rendering and schema validation.

The Brain caller and limit fields above are required in every official render.
The chart sets `CLAVENAR_BRAIN_REQUIRE_AUX_CONTROLS=true`, so the binary also
validates them before binding. Caller values are fixed to one complete SPIFFE
URI each; empty values, prefixes, lists, wrong schemes, and attempts to replace
the generated env through `extraEnv` fail Helm rendering. Rate and spend values
must be positive; timeout and body values additionally retain the Brain's
30-second and 1 MiB startup caps. The official chart consequently fixes
`tlsBundle.spiffeTrustDomain` to `clavenar.local` so auto-minted identities
cannot drift away from those canonical callers.

### Evaluation auto-mint trust rotation

`tlsBundle.autoMint` is evaluation-only, but its trust lifecycle is explicit.
On every install or upgrade a snapshot stage reads the exact
`clavenar.com/san-scheme` label and validates the complete Secret inventory.
The default `rotation.operation=reconcile` initializes an absent Secret,
performs a metadata-only migration of a valid legacy Secret, or preserves a
canonical stable Secret byte-for-byte. A generation change or membership
change under `reconcile` fails closed instead of silently replacing trust.

To rotate, advance the generation and select exactly one governed reason:

```bash
helm upgrade my-clavenar charts/clavenar --reuse-values \
  --set tlsBundle.rotation.operation=rotate \
  --set-string tlsBundle.rotation.generation=2026-07-rotation-1 \
  --set tlsBundle.rotation.reason=expiry
```

`expiry` is admitted only inside `expiryWindowSeconds`. `membership` requires
an actual additive membership change; removal of a still-live identity is
rejected. The hook validates a wholly fresh CA and leaf set, publishes old
leaves with both public roots, rolls every TLS consumer to Ready, publishes new
leaves under both roots, and only then retires the old live root. The active
Secret contains one signer at every phase. Superseded private material exists
only in memory for the hook lifetime; the retained history Secret contains
only the retired `ca.crt`. A rollout or deadline failure restores the prior
generation through the same dual-trust ordering.

Helm persists CLI value overrides. After a successful rotation, return the
operation to the ordinary posture while retaining the new generation:

```bash
helm upgrade my-clavenar charts/clavenar --reuse-values \
  --set tlsBundle.rotation.operation=reconcile \
  --set tlsBundle.rotation.reason=none
```

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
| Diagnostics `:9185` | Always | Internal readiness | Plain HTTP `/health`, `/readyz`, and `/metrics` only. Kubelet probes, reviewed dependency gates, and Prometheus annotations target this ClusterIP port. |

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
deploymentProfile: production

authSecrets:
  existingSecretName: clavenar-runtime-auth

tlsBundle:
  secretName: clavenar-workload-tls
  autoMint: false

services:
  ledger:
    requireTrustedProxy: true
    trustedProxySpiffe: spiffe://clavenar.local/service/website
  console:
    operatorMtls:
      enabled: true
      publicTrustSecretName: clavenar-operator-trust
    mutationOrigins:
      - https://console.example.com
    demo:
      enabled: false

networkPolicy:
  enabled: true
  ledger:
    trustedProxy:
      allowedPeers:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: clavenar-edge
          podSelector:
            matchLabels:
              app.kubernetes.io/name: clavenar-website
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

The ledger website selector follows the same exact-positive shape and must
contain exactly one peer in production. It is rendered as an ingress rule on
ledger `:8183` only. Unlike the console peers, it must include both the exact
`app.kubernetes.io/name=clavenar-website` pod label and an explicit
`kubernetes.io/metadata.name` namespace label. The website namespace must
differ from `.Release.Namespace` and `prometheusNamespaceLabel`, preventing
the website pod from also matching an in-release application rule or the
namespace-wide Prometheus exception on public `:8083`. Ledger also receives
`CLAVENAR_LEDGER_REQUIRE_TRUSTED_PROXY=true` and the exact canonical
`CLAVENAR_LEDGER_TRUSTED_PROXY_SPIFFE`; both variables are chart-governed and
cannot be replaced through `services.ledger.extraEnv`. Direct/public `:8083`
requests remain keyed by the socket peer and never trust forwarded addresses.

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
  --set vault.identityTokenKey=identity-token \
  --set vault.proxyTokenKey=proxy-token \
  --set networkPolicy.enabled=true

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
