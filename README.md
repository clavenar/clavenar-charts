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
