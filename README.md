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

The public `clavenar.dependency-readiness/v1` contract is packaged byte-for-byte
in the chart. It drives distinct liveness/readiness probes, bounded dependency
init gates, runtime dependency URLs, internal diagnostics Service ports, and
matching NetworkPolicy callers. CI verifies default, bundled, and production
renders against the public Specs source.

`deploymentProfile` is explicit. The backwards-compatible `evaluation`
default keeps the existing render surface. `production` fails Helm rendering
unless NetworkPolicy, externally managed workload TLS, operator-managed HIL
auth, native console operator mTLS with separate public trust, and the exact
website-to-ledger trusted-proxy mTLS boundary are all configured together.
This profile is a chart render and configuration gate, not production-readiness
certification. Operators must still validate the deployed service images,
external PKI and CNI enforcement, release artifacts, persistence, backup and
disaster recovery, and runtime behavior in their own environment.

The optional execution gateway is excluded from default renders and explicitly
forbidden by `deploymentProfile=production` until WP-13 closes. Evaluation
opt-in requires workload TLS and NetworkPolicy: Proxy reaches Exec only over
mutual TLS as the exact `service/proxy` SPIFFE identity, the Service publishes
only the authority port, and probes use a separate health-only listener. Its
image may use an exact digest or a unique non-`latest` evaluation build tag,
with digest taking precedence; the byte-exact structured-command policy is
mounted read-only, shell strings are absent, scratch is bounded, and egress
defaults denied except cluster DNS and the exact in-cluster fallback peer.
Fetch connections validate the complete DNS answer set, pin one deterministic
public address while retaining hostname identity, and repeat the exact
allowlist/resolve/validate/pin sequence for at most five manual redirects.

The default Proxy PVC retains the durable server-execution intent, exact
result, and forensic outbox across pod restarts. Because that store is SQLite,
the chart keeps Proxy at one replica while `persistence.proxy.enabled=true`.

The raw chart defaults to a curated `demo-only` router with no operator or
Admin authority; a valid demo cookie carries only a prefix-scoped demo
Viewer. Customer installation selects the full `webauthn` router on `:8085`
for localhost port-forward access and one-use Admin passkey enrollment. The
hardened alternative terminates operator mTLS inside the console process,
maps only exact registered certificates to roles, and keeps demo (`:9085`)
and diagnostics (`:9185`) on separate listeners and NetworkPolicy trust
classes.

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
the chart manages the upgrade- and uninstall-stable `<release>-shared-tokens` Secret; production operators can
set `authSecrets.existingSecretName` to a Secret reconciled by their own secret
manager instead. `authSecrets.rotationId` is the non-secret rollout generation:
the same value preserves chart-managed keys, while a new value rotates those
keys and rolls every consumer. External-secret operators update the complete
Secret first and advance the identifier in the controlled release.
The packaged bundled evaluation values also generate HIL's dedicated payload
encryption key in that shared Secret so a fresh cluster needs no operator
bootstrap. Production forbids this evaluation convenience and keeps the HIL
key in its separately managed Secret.
They additionally mount public evaluation-only OIDC and attestation
verification keys, create an Ed25519 Vault Transit authority, and use the
stable `identity` DNS alias required by renewed workload certificates. No
private OIDC or attestation signing key is packaged.

NATS and Vault are not bundled by default. Operators can bring their own or
enable the evaluation-only subcharts. Bundled NATS uses durable file-backed
JetStream, exact certificate-to-user `verify_and_map` authorization generated
from the public `clavenar.nats-authorization/v1` fixture, and a NetworkPolicy
admitting only declared in-chart clients plus, when explicitly selected, the
canonical demo-mint pod in its external namespace on client port `:4222`.
That peer never reaches monitoring `:8222`. Production external-broker mode renders only
when the operator explicitly declares ownership of that same authorization
contract and durable JetStream storage.

Evaluation auto-mint upgrades preserve a validated TLS Secret byte-for-byte.
Trust replacement requires an explicit generation-bound Helm rotation, uses a
bounded two-public-root rollout with readiness gates and rollback, retains only
the retired public CA as history, and rejects implicit or malformed changes.
See the chart README for the rotation values and operator sequence.

Persistence-enabled SQLite workloads also use explicit `Recreate` rollouts.
Chart 0.25.0 adds verified online pre-upgrade backups and zero-writer,
digest-checked pre-rollback restoration for Ledger, HIL, Identity, Policy
Engine, and Proxy. Establish 0.25.0 or newer as the rollback source revision;
older revisions cannot contain the restore hook. PostgreSQL Ledger remains
outside this SQLite upgrade transaction.

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
helm pull oci://ghcr.io/clavenar/charts/clavenar \
  --version 0.38.1 --untar
curl -fsSLO \
  https://github.com/clavenar/clavenar-charts/releases/download/v0.38.1/clavenar-images-1.249.1.yaml
helm install my-clavenar ./clavenar \
  --namespace clavenar --create-namespace \
  --wait --wait-for-jobs --timeout 10m \
  -f ./clavenar/examples/values-bundled.yaml \
  -f ./clavenar-images-1.249.1.yaml
```

This evaluation-only path bundles NATS, dev-mode Vault, and auto-minted
workload TLS. The published values file disables the separately built optional
execution gateway, so every referenced Clavenar image belongs to the exact
protected public release.

Chart-created PVCs carry Helm's `keep` resource policy. A Helm uninstall
therefore removes the release workloads but retains persistent data and the
namespace. The checksum-verifying `https://clavenar.ai/uninstall.sh` wrapper
adds ownership checks and requires a separate, explicit confirmation before it
deletes retained data.

The selected authentication Secret must already contain `hil-session-key` and
`hil-decide-token`. Omit `authSecrets.existingSecretName` to retain the
backwards-compatible chart-generated Secret.
Production also requires a public-only `attestationTrustAnchors` Secret; keep
the corresponding evidence-signing private key with the cluster attester,
outside every Clavenar pod. Its signed measurement registry requires the
external Vault address and a Secret containing distinct, scoped Identity and
Proxy token keys; production projects each key as a file and refuses bundled
dev Vault.
An optional public-only `tpm2AttestationTrust` Secret adds pinned TPM 2.0
attestation keys and qualified names. When selected, Proxy and Identity use the
combined `identity-k8s-key-bound+tpm2-quote` posture; Kubernetes verification
remains mandatory and no TPM private key is mounted.

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
and the staged single-replica PostgreSQL Ledger mode.

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

After acceptance, **Protected public distribution** publishes this exact chart
and a matching `clavenar-images-<stack-version>.yaml` values file. The values
file contains all ten unique immutable image digests from the signed 11-subject
graph (Simulator and upstream-stub intentionally share one image). The chart,
values file, and exact images are anonymously readable; supported installs use
the versioned OCI chart plus its matching digest values file.

`VERSION` and `Chart.appVersion` remain frozen at the last legacy chart image
set for tag-only compatibility. Supported publication, install, upgrade,
rollback, and recovery consume exact digests from the signed BOM through the
release-specific values file. Do not advance either value merely because a
protected artifact graph was staged.

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
