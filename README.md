# warden-charts

Helm chart for deploying [Agent Warden](https://github.com/vanteguardlabs)
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
charts/warden/        # the chart — see charts/warden/README.md for the full
                     # quickstart, values reference, and per-service knobs
.github/workflows/    # helm lint + template + kubeconform schema check
SECURITY.md           # vulnerability reporting policy
```

## Quick install

```bash
helm install my-warden charts/warden \
  --namespace warden --create-namespace \
  --set nats.url=nats://my-nats:4222 \
  --set tlsBundle.secretName=warden-certs
```

See `charts/warden/README.md` for the full quickstart, the `values.yaml`
reference, mTLS cert provisioning, the SQLite-on-shared-PVC constraints,
and how to flip the ledger to Postgres mode.

## Publishing images to GHCR

Image versions live in `VERSION` at this repo's root — a single semver
line, the source of truth for the tag pushed to
`ghcr.io/vanteguardlabs/<service>`. `charts/warden/Chart.yaml`
`appVersion` mirrors this file so a fresh `helm install` pulls tags
that actually exist on GHCR. The version is independent of
`warden-specs/VERSION` (which tracks the demo VPS deploy, not the
chart's published image set).

`scripts/push-images.sh` builds all 8 services from their sibling
repos under `../warden-<name>/`, pushes both `:<VERSION>` and
`:latest` to GHCR, then bumps the VERSION patch + `Chart.appVersion`
atomically and auto-commits the result.

```bash
# One-time: log root's docker into ghcr.io (the script uses sudo -n docker)
echo "$GH_PAT" | sudo -n docker login ghcr.io -u vanteguardlabs --password-stdin

# Full publish — builds 8 images, pushes 16 tags, bumps VERSION
./scripts/push-images.sh

# Subset / iteration (implies --no-bump)
./scripts/push-images.sh --only=warden-proxy,warden-brain --allow-dirty

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

- [warden-specs](https://github.com/vanteguardlabs/warden-specs) — the
  wire-contract source of truth that every service in the chart honors.
- The per-service repositories under
  `github.com/vanteguardlabs/warden-<name>` — Dockerfiles, source, and
  per-component SECURITY.md.

## Not yet shipped

Pure-Terraform modules for AWS / GCP / Azure are on the roadmap but not
in this repository today. A Helm chart deployed via your existing IaC
(Terraform `helm_release`, Pulumi, Argo CD, etc.) is the supported path.

## License

Apache-2.0.
