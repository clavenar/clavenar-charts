# Security Policy

Agent Warden is a security product. We take vulnerability reports seriously
and aim to acknowledge every report within 72 hours.

## Reporting a vulnerability

Email **vanteguardlabs@gmail.com** with:

- A description of the issue and the impact you observed.
- Steps to reproduce. A minimal Helm values file or `helm template` invocation
  that reproduces the surface is appreciated but not required if the issue
  is structural.
- Affected chart version (from `Chart.yaml:version`) and the specific
  template path (e.g. `charts/warden/templates/networkpolicy.yaml`).
- Whether you would like public credit in the disclosure announcement.

PGP/GPG: not yet available. If you need an encrypted channel, mention it
in your initial email and we will arrange one.

## Scope

In scope:

- Every template and helper under `charts/warden/` — Deployment shapes,
  Service definitions, NetworkPolicy rules, PodDisruptionBudget logic,
  the `warden.serviceFullname` helper kebab-casing, the per-pod secret
  projection that isolates one service's private key from another.
- Default `values.yaml` knobs that ship with the chart — anything an
  out-of-the-box `helm install` produces.
- The chart's CI rendering matrix (`.github/workflows/ci.yml`) and the
  kubeconform schema validation step.

Out of scope:

- Bugs in the per-service runtime binaries (proxy, brain, policy-engine,
  ledger, hil, identity, deep-review, console). Report those against
  the relevant `warden-<service>` repository's `SECURITY.md`.
- Bugs in upstream dependencies operators bring themselves — NATS,
  Vault, cert-manager, the container runtime, the k8s control plane.
- Findings against demo / simulator components when wired into a
  chart-deployed cluster — those tools' admin surfaces are intentionally
  unauthenticated on loopback only.
- Self-XSS, clickjacking, or other browser-side findings against the
  console (covered in `warden-console/SECURITY.md`).

## Safe harbor

We will not pursue civil or criminal action against researchers who:

- Make a good-faith effort to avoid privacy violations, destruction of
  data, and interruption or degradation of our services.
- Only interact with accounts they own or with explicit permission of
  the account holder.
- Give us reasonable time to respond before disclosing publicly.
- Do not exploit a security issue beyond what is necessary to confirm
  it.

## Response targets

- **72 hours**: acknowledgement of the report.
- **7 days**: triage outcome (accepted / duplicate / out-of-scope) and a
  CVE assignment plan if applicable.
- **90 days**: public disclosure, coordinated with the reporter.

We may extend the disclosure window for complex issues that require a
coordinated multi-repo fix; we will tell you in advance and explain why.
