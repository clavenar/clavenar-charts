# Lab — Claude Code agent pod through warden-proxy

Optional scaffolding to drop a real **Claude Code CLI** into the same
Kubernetes cluster as the warden helm release, with every MCP tool
call routed through warden-proxy. End-to-end demo of the security
pipeline: agent → mTLS → proxy → Brain → Policy → (HIL?) → ledger →
echo upstream → response.

This is lab-only. Production deploys run their agents externally and
point them at the proxy from outside the cluster; the chart itself
does not deploy this pod.

## Lab posture vs. warden-exec

As of chart 0.2.x, `exec.enabled=true` in `tests/values-bundled.yaml`
puts `warden-exec` between the proxy and the upstream-stub. Two lab
manifests together close the agent's escape hatches:

- `mcp-config-cm.yaml` (mounted at `~/.claude.json`) registers the
  `warden` MCP server — the only MCP path out of the pod.
- `claude-code-managed-settings-cm.yaml` (mounted at
  `/etc/claude-code/managed-settings.json`) denylists every Claude
  Code built-in (`Bash`, `Read`, `Write`, `Edit`, `WebFetch`,
  `Glob`, `Grep`, `NotebookEdit`) at the **managed-settings** level
  with `allowManagedPermissionRulesOnly: true`. Managed settings sit
  at the top of Claude Code's precedence chain — the agent cannot
  re-enable a tool by editing `~/.claude/settings.json` or
  `.claude/settings.local.json` because both are lower precedence.
  Putting deny rules in `~/.claude.json` does **not** work; that
  file does not feed the permission system.

`agent-pod.yaml` also mounts the shared workspace PVC at `/workspace`
so an operator can `kubectl exec -- ls /workspace` alongside the
agent. Net result: every shell command and file op the agent runs
flows through Brain + Policy + HIL + ledger. Inspect with:

```bash
kubectl -n warden logs deploy/<release>-exec --tail=200 | grep tools/call
kubectl -n warden exec -it claude-code-agent -- ls /workspace
```

To run without the gateway (raw Claude Code + warden as one MCP
among many), set `exec.enabled=false` and skip applying
`claude-code-managed-settings-cm.yaml`.

## Prerequisites

1. **A warden release with the bundled upstream stub.** The chart must
   be installed with `upstreamStub.enabled=true` and
   `agentVaultSeed.enabled=true` (both are flipped on automatically in
   `tests/values-bundled.yaml`). Without these, the proxy forwards to
   a non-existent `localhost:9000/mcp` and returns 500 on every tool
   call. Verify with:
   ```bash
   kubectl -n warden get pods | grep upstream-stub
   kubectl -n warden get jobs | grep vault-agent-seed
   ```
   Both should be `Running` / `Completed`.

2. **An Anthropic API key Secret.** The agent pod reads
   `ANTHROPIC_API_KEY` from a Secret named `claude-code-agent-anthropic`.
   Create it before applying the pod manifest:
   ```bash
   kubectl -n warden create secret generic claude-code-agent-anthropic \
     --from-literal=ANTHROPIC_API_KEY=sk-ant-...
   ```
   `anthropic-secret.example.yaml` documents the shape but **must not
   be applied** as-is.

3. **The agent image, built + reachable.** Run:
   ```bash
   lab/scripts/build-agent-image.sh        # local build
   lab/scripts/build-agent-image.sh --push # build + push to GHCR
   ```
   The script needs the `warden-ctl` and `warden-sdk` sibling repos
   checked out next to `warden-charts/`. It tags as
   `ghcr.io/vanteguardlabs/warden-claude-code-agent:<chart-appVersion>`
   plus `:latest`. For a local kind cluster, `kind load
   docker-image` it directly without pushing.

## Apply

The ConfigMap dials the proxy as bare `https://proxy:8443/mcp`. The
chart's auto-mint script stamps the proxy cert SAN as `DNS:proxy,
DNS:proxy.warden.local, DNS:localhost` (no `<release>-proxy` entry),
so the bridge has to present `proxy` as the TLS SNI for the handshake
to validate. The `proxyAlias.enabled=true` flag in the chart emits an
ExternalName Service named `proxy` that CNAMEs to the real
`<release>-proxy` — DNS resolves, SNI matches, handshake passes.
`tests/values-bundled.yaml` ships with this flag on.

```bash
# Only edit manifests/agent-pod.yaml if your release set a different
# tlsBundle.secretName than `smoke-tls`.

kubectl -n warden apply -f lab/manifests/mcp-config-cm.yaml
# Only when exec.enabled=true on the release. Skip otherwise.
kubectl -n warden apply -f lab/manifests/claude-code-managed-settings-cm.yaml
kubectl -n warden apply -f lab/manifests/agent-pod.yaml

kubectl -n warden wait --for=condition=Ready pod/claude-code-agent --timeout=120s
```

The alias Service is a lab convenience. The right long-term fix is a
chart-side patch to the auto-mint script that also stamps
`DNS:<release>-proxy` (and ideally `DNS:proxy.<namespace>.svc.cluster.local`)
into the SAN list — at which point `proxyAlias.enabled` becomes
unnecessary.

## Smoke

```bash
# Confirm Claude Code sees the warden MCP server.
kubectl -n warden exec -it claude-code-agent -- claude mcp list
# Expect: `warden` listed as an active server.

# Interactive session — fire any tool. The upstream-stub echoes back,
# but the request still hits Brain + Policy + ledger.
kubectl -n warden exec -it claude-code-agent -- claude

# Inspect the ledger row (port-forward in a second terminal).
kubectl -n warden port-forward svc/<release>-ledger 8083:8083 &
curl -s http://localhost:8083/audit/agent-001 | jq '.[0]'
curl -s http://localhost:8083/verify
```

The proxy logs identify every inbound call by agent_id from the cert's
CN — see `repos/warden-ctl/docs/clients/claude-code.md` for the
canonical bridge config and troubleshooting matrix.

## BYO Vault (no chart-managed seed)

`agentVaultSeed.enabled=true` only works alongside
`vault.bundled.enabled=true`. With an externally-managed Vault, run the
seed manually before applying the agent pod:

```bash
curl -H "X-Vault-Token: $VAULT_TOKEN" -X POST \
  "${VAULT_ADDR}/v1/secret/data/agents/agent-001" \
  -d '{"data":{"api_key":"stub-key"}}'
```

Without this entry, the proxy returns `403 No credentials found for
agent agent-001` on every request.

## Teardown

```bash
kubectl -n warden delete -f lab/manifests/agent-pod.yaml
kubectl -n warden delete -f lab/manifests/mcp-config-cm.yaml
kubectl -n warden delete -f lab/manifests/claude-code-managed-settings-cm.yaml --ignore-not-found
kubectl -n warden delete secret claude-code-agent-anthropic
```

The chart itself is untouched — `helm uninstall <release>` cleans the
warden services + the bundled upstream-stub + the vault seed Job.

## Limitations

- **Shared identity.** Every pod uses the auto-mint starter cert
  (CN `agent-001`). The ledger conflates runs from multiple lab pods.
  Per-pod SVIDs (via `warden-identity` `POST /agents` + `/svid`) are a
  follow-up.
- **Claude Code version floats.** The Dockerfile installs
  `@anthropic-ai/claude-code@latest`; pin once the lab flow is stable.
- **Image size.** ~600 MB compressed (Node + Rust runtime). Lab infra,
  not the security pipeline — not a concern for production.
- **No multi-arch.** Image builds `linux/amd64` only. ARM nodes need a
  `docker buildx` multi-arch follow-up.
