# clavenar-charts

Helm chart for deploying [Clavenar](https://github.com/clavenar)
as a sidecar control plane in your Kubernetes cluster. The chart deploys
the nine-service stack — **proxy, brain, policy-engine, ledger, hil,
identity, deep-review, assurance, console** — as Deployments + Services with
`/health` and `/readyz` probes, PVCs for the SQLite-backed services, a
default-deny NetworkPolicy perimeter, and PodDisruptionBudgets where they
make sense.

The governed inventory at `charts/clavenar/listeners.yaml` records every
listener, authentication boundary, caller, and Service publication. CI
checks default, TLS, production, optional-listener, and bundled-subchart
renders against it.

`deploymentProfile` is explicit. The backwards-compatible `evaluation`
default keeps the existing render surface. `production` fails Helm rendering
unless NetworkPolicy, externally managed workload TLS, operator-managed HIL
auth, native console operator mTLS with separate public trust, and the exact
website-to-ledger trusted-proxy mTLS boundary are all configured together.
This profile is a chart render and configuration gate, not production-readiness
certification. Operators must still validate the deployed service images,
external PKI and CNI enforcement, release artifacts, persistence, backup and
disaster recovery, and runtime behavior in their own environment.

The default Proxy PVC retains the durable server-execution intent, exact
result, and forensic outbox across pod restarts. Because that store is SQLite,
the chart keeps Proxy at one replica while `persistence.proxy.enabled=true`.

The console defaults to a curated `demo-only` router with no operator or
Admin authority; a valid demo cookie carries only a prefix-scoped demo
Viewer. Its optional operator path terminates mTLS
inside the console process on `:8085`, maps only exact registered operator
certificates to roles, and keeps demo (`:9085`) and diagnostics (`:9185`)
on separate listeners and NetworkPolicy trust classes.

Assurance control is separately fail-closed: `:8088` requires workload mTLS
and the exact console SPIFFE identity, while plain `:9088` exposes only
side-effect-free health and readiness routes. Demo visitors cannot trigger
assurance runs. Chart-governed request, whole-run, and publish deadlines bound
execution, and completion advances only after the configured exact JetStream
forensic stream acknowledges the result.

Brain's auxiliary routes share its workload-mTLS application
listener on `:8081`, never the plain diagnostics listener on `:9081`.
`/explain-pattern` accepts only the exact policy-engine SPIFFE identity;
`/narrate-decision` and `/model-snapshot` accept only the exact console
identity. The chart renders strict 16 KiB bodies, independent 20/60-per-minute
request gates, a shared 5,000,000 micro-USD/hour conservative spend budget,
and a 5-second whole-provider deadline. Policy and console always receive the
HTTPS application URL; rejected or unavailable auxiliary calls fail soft at
those consumers.

The chart does not auto-mint a demo-session signing key or token issuer. A
fresh install therefore serves the anonymous `/demo` preview safely, while
signed demo routes stay closed until an operator supplies one dedicated key to
console, HIL, and ledger as documented in the chart README.

HIL's session and decision credentials are always rendered into Deployments as
Kubernetes `secretKeyRef`s, never literal values. Brain receives its cache-HMAC
key only as a mode-0440 Secret file and refuses to bind without it. By default
the chart manages the upgrade-stable `<release>-shared-tokens` Secret; production operators can
set `authSecrets.existingSecretName` to a Secret reconciled by their own secret
manager instead. `authSecrets.rotationId` is the non-secret rollout generation:
the same value preserves chart-managed keys, while a new value rotates those
keys and rolls every consumer. External-secret operators update the complete
Secret first and advance the identifier in the controlled release.

NATS and Vault are not bundled by default. Operators can bring their own or
enable the evaluation-only subcharts. Bundled NATS uses durable file-backed
JetStream, exact certificate-to-user `verify_and_map` authorization generated
from the public `clavenar.nats-authorization/v1` fixture, and a NetworkPolicy
admitting only declared clients. Production external-broker mode renders only
when the operator explicitly declares ownership of that same authorization
contract and durable JetStream storage.

Evaluation auto-mint upgrades preserve a validated TLS Secret byte-for-byte.
Trust replacement requires an explicit generation-bound Helm rotation, uses a
bounded two-public-root rollout with readiness gates and rollback, retains only
the retired public CA as history, and rejects implicit or malformed changes.
See the chart README for the rotation values and operator sequence.

Sequence diagrams for the six primary flows — `helm install` render +
apply, pod boot under `tlsBundle.secretName`, cross-service backend URL
wiring under TLS, Prometheus scrape + Grafana sidecar discovery, the
alert fan-out via Alertmanager, and the NetworkPolicy ingress check —
plus a chart render decision tree, live in
[`docs/SEQUENCES.md`](docs/SEQUENCES.md).

## Layout

```
charts/clavenar/        # the chart — see charts/clavenar/README.md for the full
                     # quickstart, values reference, and per-service knobs
lab/                  # optional Claude Code agent pod manifests for an
                     # in-cluster end-to-end demo (proxy → brain → policy →
                     # hil → ledger). See lab/README.md.
.github/workflows/    # helm lint + template + kubeconform schema check
SECURITY.md           # vulnerability reporting policy
```

## Compose-native dev (clavenar-e2e)

If you don't have a Kubernetes cluster handy, the compose-native
stack at [`clavenar-e2e`](https://github.com/clavenar/clavenar-e2e)
boots the same nine services under `docker compose --profile stack`
on a single host. Same wire contracts, same TECH_SPEC.md, isolated
prod / dev environments under `prod/` and `dev/`. That repo also
hosts the MANUAL_TESTS.md scenarios and `bootstrap.sh` / `deploy.sh`
operator runners.

The Helm chart in this repo and the compose stack in clavenar-e2e
are independent deploy paths — pick whichever matches your target.
The lab manifests under `lab/` are the Kubernetes counterpart to
clavenar-e2e's compose-native end-to-end test.

## Quick install

The default command below uses `deploymentProfile=evaluation`. For a
production render, copy and customize `tests/values-production.yaml`; it is
also exercised by CI as the canonical fail-closed profile.

```bash
helm install my-clavenar charts/clavenar \
  --namespace clavenar --create-namespace \
  --set nats.url=nats://my-nats:4222 \
  --set vault.addr=https://vault.internal:8200 \
  --set vault.tokenSecretName=clavenar-vault-token \
  --set vault.identityTokenKey=identity-token \
  --set vault.proxyTokenKey=proxy-token \
  --set authSecrets.existingSecretName=clavenar-runtime-auth \
  --set attestationTrustAnchors.secretName=clavenar-attestation-trust \
  --set tlsBundle.secretName=clavenar-certs
```

The selected authentication Secret must already contain `hil-session-key` and
`hil-decide-token`. Omit `authSecrets.existingSecretName` to retain the
backwards-compatible chart-generated Secret.
Production also requires a public-only `attestationTrustAnchors` Secret; keep
the corresponding evidence-signing private key with the cluster attester,
outside every Clavenar pod. Its signed measurement registry requires the
external Vault address and a Secret containing distinct, scoped Identity and
Proxy token keys; production projects each key as a file and refuses bundled
dev Vault.

That default keeps console ingress denied and serves only the safe demo
router if you port-forward it. Enabling operator mTLS additionally requires
`services.console.operatorMtls.enabled=true` and a pre-existing
`publicTrustSecretName` Secret containing the dedicated public operator CA
(`ca.crt`) plus the sanitized exact identity registry (`operators.json`).
List every exact HTTPS browser origin allowed to submit simulator or assurance
controls in `services.console.mutationOrigins`. Private operator authority and
leaf keys never belong in the runtime Secret.

Production additionally requires ledger to honor forwarded client addresses
only from `spiffe://clavenar.local/service/website`. An explicit positive
website workload selector is admitted to ledger's mTLS port `8183` only. It
must select `app.kubernetes.io/name=clavenar-website` in an explicitly named
namespace distinct from both the Helm release and Prometheus namespaces, so it
cannot inherit an in-release or namespace-wide public-read rule.

Every production service also has a single-valued rendered environment:
`extraEnv` cannot duplicate chart-owned authentication, TLS/trust paths,
listener addresses or ports, caller identities, backend endpoints, or common
NATS/Vault settings. Helm refuses such entries instead of relying on
Kubernetes' env-list ordering.

See `charts/clavenar/README.md` for the full quickstart, the `values.yaml`
reference, mTLS cert provisioning, the SQLite-on-shared-PVC constraints,
and how to flip the ledger to Postgres mode.

## Publishing release artifacts

The local image publisher is permanently disabled. It could overwrite current
semantic component tags, publish mutable `latest` tags, and leave a release
partially visible. `scripts/push-images.sh` now fails closed for every
invocation and must not regain `--only`, `--allow-dirty`, or `--no-bump` paths.

Release/Security operators dispatch **Protected immutable stack release** from
`clavenar-e2e` at an exact committed internal release version. That workflow
derives the 11 deployment image subjects and every BuildKit named context from
the rendered Compose graph, stages all images, packages, SBOMs, and provenance
as private digest-addressed OCI objects, verifies the complete signed stack
BOM, then creates one semantic stack-release reference. It never creates
component semantic or `latest` tags, and refuses to overwrite the stack
reference.

`VERSION` and `Chart.appVersion` remain frozen at the last legacy chart image
set until WP-14.5 changes installation, upgrade, rollback, and recovery to
consume image digests from the signed BOM. Do not advance either value merely
because a protected artifact graph was staged.

Images are built for `linux/amd64` only in v1; multi-arch via
`docker buildx` is a deferred follow-up.

## Related repositories

- [clavenar-specs](https://github.com/clavenar/clavenar-specs) — the
  wire-contract source of truth that every service in the chart honors.
- The per-service repositories under
  `github.com/clavenar/clavenar-<name>` — Dockerfiles, source, and
  per-component SECURITY.md.

## Not yet shipped

Pure-Terraform modules for AWS / GCP / Azure are on the roadmap but not
in this repository today. A Helm chart deployed via your existing IaC
(Terraform `helm_release`, Pulumi, Argo CD, etc.) is the supported path.

## License

Apache-2.0.
