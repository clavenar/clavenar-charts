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
checks default, TLS, optional-listener, and bundled-subchart renders
against it.

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
Kubernetes `secretKeyRef`s, never literal values. By default the chart manages
the upgrade-stable `<release>-shared-tokens` Secret; production operators can
set `authSecrets.existingSecretName` to a Secret reconciled by their own secret
manager instead.

NATS and Vault are not bundled by default. Operators can bring their own or
enable the evaluation-only subcharts.

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

```bash
helm install my-clavenar charts/clavenar \
  --namespace clavenar --create-namespace \
  --set nats.url=nats://my-nats:4222 \
  --set authSecrets.existingSecretName=clavenar-runtime-auth \
  --set tlsBundle.secretName=clavenar-certs
```

The selected authentication Secret must already contain `hil-session-key` and
`hil-decide-token`. Omit `authSecrets.existingSecretName` to retain the
backwards-compatible chart-generated Secret.

That default keeps console ingress denied and serves only the safe demo
router if you port-forward it. Enabling operator mTLS additionally requires
`services.console.operatorMtls.enabled=true` and a pre-existing
`publicTrustSecretName` Secret containing the dedicated public operator CA
(`ca.crt`) plus the sanitized exact identity registry (`operators.json`).
List every exact HTTPS browser origin allowed to submit simulator or assurance
controls in `services.console.mutationOrigins`. Private operator authority and
leaf keys never belong in the runtime Secret.

See `charts/clavenar/README.md` for the full quickstart, the `values.yaml`
reference, mTLS cert provisioning, the SQLite-on-shared-PVC constraints,
and how to flip the ledger to Postgres mode.

## Publishing images to GHCR

Image versions are tracked by `VERSION` at this repo's root — a single
semver line that reflects the **latest image set already published to**
`ghcr.io/clavenar/<service>`. `charts/clavenar/Chart.yaml`
`appVersion` mirrors `VERSION` so a fresh `helm install` pulls tags
that actually exist on GHCR. Independent of `clavenar-internal-specs/VERSION`
(which tracks the demo VPS deploy, not the chart's published image
set).

`scripts/push-images.sh` reads `VERSION`, computes the next patch as
the target, builds all 11 services from their sibling repos under
`../clavenar-<name>/`, pushes both `:<target>` and `:latest` to GHCR,
then writes the new tag back into `VERSION` + `Chart.appVersion`
atomically and auto-commits. Failed pushes leave `VERSION` untouched
— it always reflects what is actually live on GHCR.

```bash
# One-time: log root's docker into ghcr.io (the script uses sudo -n docker)
echo "$GH_PAT" | sudo -n docker login ghcr.io -u vanteguardlabs --password-stdin

# Full publish — builds 11 images, pushes 22 tags, bumps VERSION
./scripts/push-images.sh

# Subset / iteration (implies --no-bump)
./scripts/push-images.sh --only=clavenar-proxy,clavenar-brain --allow-dirty

# What would happen?
./scripts/push-images.sh --dry-run
```

`$GH_PAT` is a classic personal access token with `write:packages` +
`read:packages` scopes.

**First-time visibility flip (one-time per service).** GHCR's REST API
does not expose package-visibility mutation — new packages land as
`private`. After the first push, click through the UI for each of the
11 packages at:

  `https://github.com/users/vanteguardlabs/packages/container/<service>/settings`

  Scroll to **Danger Zone** → **Change visibility** → **Public** →
  type the package name to confirm.

Subsequent pushes inherit the existing visibility, so this is a
one-shot per package.

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
