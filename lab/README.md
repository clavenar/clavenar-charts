# Lab — Claude Code agent pod through warden-proxy

Optional scaffolding to drop a real **Claude Code CLI** into the same
Kubernetes cluster as the warden helm release, with every MCP tool
call routed through warden-proxy. End-to-end demo of the security
pipeline: agent → mTLS → proxy → Brain → Policy → (HIL?) → ledger →
echo upstream → response.

This is lab-only. Production deploys run their agents externally and
point them at the proxy from outside the cluster; the chart itself
does not deploy this pod.

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

```bash
# 1. Adjust manifests/mcp-config-cm.yaml so the proxy URL matches your
#    release name. Default is `my-warden`; change to whatever you used
#    for `helm install <release> ...`.
sed -i 's|my-warden-proxy|<your-release>-proxy|' \
  lab/manifests/mcp-config-cm.yaml

# 2. Same for the smoke-tls Secret name in manifests/agent-pod.yaml if
#    your release set a different tlsBundle.secretName.

# 3. Apply.
kubectl -n warden apply -f lab/manifests/mcp-config-cm.yaml
kubectl -n warden apply -f lab/manifests/agent-pod.yaml

kubectl -n warden wait --for=condition=Ready pod/claude-code-agent --timeout=120s
```

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
