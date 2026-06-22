# clavenar-charts

Helm chart for deploying [Clavenar](https://github.com/clavenar)
as a sidecar control plane in your Kubernetes cluster. The chart deploys
the eight-service stack — **proxy, brain, policy-engine, ledger, hil,
identity, deep-review, console** — as Deployments + Services with
`/health` and `/readyz` probes, PVCs for the SQLite-backed services, an
optional NetworkPolicy perimeter, and PodDisruptionBudgets where they
make sense.

NATS and Vault are not bundled. Operators bring their own.

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
boots the same eight services under `docker compose --profile stack`
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
  --set tlsBundle.secretName=clavenar-certs
```

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
the target, builds all 8 services from their sibling repos under
`../clavenar-<name>/`, pushes both `:<target>` and `:latest` to GHCR,
then writes the new tag back into `VERSION` + `Chart.appVersion`
atomically and auto-commits. Failed pushes leave `VERSION` untouched
— it always reflects what is actually live on GHCR.

```bash
# One-time: log root's docker into ghcr.io (the script uses sudo -n docker)
echo "$GH_PAT" | sudo -n docker login ghcr.io -u vanteguardlabs --password-stdin

# Full publish — builds 10 images, pushes 20 tags, bumps VERSION
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
8 packages at:

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
