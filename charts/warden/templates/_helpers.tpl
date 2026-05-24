{{/*
Helpers for the Warden chart. Image-tag fallback chain:
  services.<svc>.image.tag → .Values.imageTag → .Chart.AppVersion
*/}}

{{/* Release name only — chart-name suffix would yield names like
`my-warden-warden-config`. Override via .Values.fullnameOverride. */}}
{{- define "warden.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Per-service fullname: <release>-<service>. The values key is
camelCase to form a valid Go-template path; k8s object names need
RFC-1123 lowercase, so we kebabcase here. */}}
{{- define "warden.serviceFullname" -}}
{{- $ctx := .ctx -}}
{{- $service := .service | kebabcase -}}
{{- printf "%s-%s" $ctx.Release.Name $service | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* app.kubernetes.io/component differentiates services. Kebabcased
to stay consistent with metadata.name. */}}
{{- define "warden.selectorLabels" -}}
app.kubernetes.io/name: {{ .ctx.Chart.Name }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .service | kebabcase }}
{{- end -}}

{{/* Common labels applied to every object. */}}
{{- define "warden.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .ctx.Chart.Name .ctx.Chart.Version | replace "+" "_" }}
{{ include "warden.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
app.kubernetes.io/version: {{ .ctx.Chart.AppVersion | quote }}
{{- end -}}

{{/* Resolve the image reference for a service. */}}
{{- define "warden.imageRef" -}}
{{- $ctx := .ctx -}}
{{- $svcCfg := .svcCfg -}}
{{- $registry := $ctx.Values.imageRegistry -}}
{{- $repo := $svcCfg.image.repository -}}
{{- $tag := default $ctx.Values.imageTag $svcCfg.image.tag -}}
{{- if not $tag -}}{{- $tag = $ctx.Chart.AppVersion -}}{{- end -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{/* terminationGracePeriodSeconds = drain cap + 5s safety margin. */}}
{{- define "warden.terminationGrace" -}}
{{- add (int .Values.drainCapSecs) 5 -}}
{{- end -}}

{{/* NATS URL: bundled mode forces the in-cluster service DNS; BYO
mode honors the operator-supplied .Values.nats.url. The upstream
nats-io/nats subchart names its Service `<release>-nats` so the
helper composes that directly. Scheme flips to `tls://` when the
auto-mint bundle is in use — warden clients then read
NATS_TLS_{CERT,KEY,CA}_PATH and require TLS on the wire (see
B7.5 / nats_tls.rs in warden-proxy). Guard fails the render if the
bundled NATS subchart hasn't been told to terminate TLS itself — the
default would otherwise crash every client with `InvalidContentType`
(plaintext NATS server, TLS-only clients). */}}
{{- define "warden.natsUrl" -}}
{{- if .Values.nats.bundled.enabled -}}
{{- $tlsOn := not (empty .Values.tlsBundle.secretName) -}}
{{- $natsTlsOn := and (hasKey .Values "nats") (hasKey .Values.nats "config") (hasKey .Values.nats.config "nats") (hasKey .Values.nats.config.nats "tls") .Values.nats.config.nats.tls.enabled -}}
{{- if and $tlsOn (not $natsTlsOn) -}}
{{- fail "tlsBundle.secretName is set + nats.bundled.enabled is true, but nats.config.nats.tls.enabled is false — bundled NATS would listen plaintext while warden clients dial TLS (InvalidContentType crash). Mirror tests/values-bundled.yaml's nats.config.nats.tls + nats.tlsCA blocks." -}}
{{- end -}}
{{- $scheme := ternary "tls" "nats" $tlsOn -}}
{{ $scheme }}://{{ .Release.Name }}-nats:4222
{{- else -}}
{{ .Values.nats.url }}
{{- end -}}
{{- end -}}

{{/* VAULT_ADDR: bundled mode points at the in-cluster service;
BYO mode honors .Values.vault.addr (empty string disables Vault
wiring entirely — configmap.yaml gates the env emission on this). */}}
{{- define "warden.vaultAddr" -}}
{{- if .Values.vault.bundled.enabled -}}
http://{{ .Release.Name }}-vault:8200
{{- else -}}
{{ .Values.vault.addr }}
{{- end -}}
{{- end -}}

{{/* k8s Secret name holding the Vault token (key `token`). Bundled
mode autogenerates `<release>-vault-token`; BYO mode honors
.Values.vault.tokenSecretName. */}}
{{- define "warden.vaultTokenSecretName" -}}
{{- if .Values.vault.bundled.enabled -}}
{{ .Release.Name }}-vault-token
{{- else -}}
{{ .Values.vault.tokenSecretName }}
{{- end -}}
{{- end -}}

{{/* Workload names that need a per-service cert. Always includes
the 8 in-chart warden services from .Values.tlsBundle.bundleServices;
"nats" is appended when nats.bundled.enabled so the bundled NATS
StatefulSet (which mounts the same Secret for TLS) finds its own
keypair. Emits a space-separated list — consumed by the auto-mint
Job's env. */}}
{{- define "warden.bundleServices" -}}
{{- $services := default (list) .Values.tlsBundle.bundleServices -}}
{{- if .Values.nats.bundled.enabled -}}
{{- $services = append $services "nats" -}}
{{- end -}}
{{ join " " $services }}
{{- end -}}

{{/* Shared NATS + drain-cap envs, then per-component back-end URLs,
then per-service extraEnv. Pass `service` so the back-end-URL helper
knows the component. */}}
{{- define "warden.commonEnv" -}}
- name: NATS_URL
  valueFrom:
    configMapKeyRef:
      name: {{ include "warden.fullname" .ctx }}-config
      key: NATS_URL
- name: WARDEN_GRACEFUL_DRAIN_SECS
  valueFrom:
    configMapKeyRef:
      name: {{ include "warden.fullname" .ctx }}-config
      key: WARDEN_GRACEFUL_DRAIN_SECS
{{- include "warden.backendEnvs" (dict "ctx" .ctx "service" .service) }}
{{- with .svcCfg.extraEnv }}
{{- toYaml . | nindent 0 }}
{{- end }}
{{- end -}}

{{/* Backend URL env vars wired by component. The compose stack pins
these explicitly per service; the chart computes the same shape so
adding a service mesh that renames Services only needs the relevant
`values.<svc>.extraEnv` override (later entries shadow earlier ones
in the same env list).

Proxy → brain + policy + hil + identity
Console → ledger + hil + policy-engine + identity
Deep-review → ledger
Identity → CA dir (cert mount lives at tlsBundle.mountPath, fixed /certs) */}}
{{- define "warden.backendEnvs" -}}
{{- $rel := .ctx.Release.Name -}}
{{- $name := .service -}}
{{- $tls := .ctx.Values.tlsBundle.secretName -}}
{{- $mount := .ctx.Values.tlsBundle.mountPath -}}
{{- $tlsOn := not (empty $tls) -}}
{{- $brainScheme := ternary "https" "http" $tlsOn -}}
{{- $policyScheme := ternary "https" "http" $tlsOn -}}
{{- if eq $name "proxy" }}
- name: WARDEN_BRAIN_URL
  value: "{{ $brainScheme }}://{{ $rel }}-brain:8081/inspect"
- name: WARDEN_POLICY_URL
  value: "{{ $policyScheme }}://{{ $rel }}-policy-engine:8082/evaluate"
- name: WARDEN_HIL_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ $rel }}-hil:8084"
- name: WARDEN_IDENTITY_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ $rel }}-identity:{{ ternary "8186" "8086" $tlsOn }}"
{{- if .ctx.Values.upstreamStub.enabled }}
# Bundled echo-MCP target. Opt-in via upstreamStub.enabled. Operator
# extraEnv setting WARDEN_UPSTREAM_URL still wins — Kubernetes applies
# duplicate env entries last-write-wins and warden.commonEnv emits
# .svcCfg.extraEnv AFTER this block. Production deploys leave
# upstreamStub off and set WARDEN_UPSTREAM_URL via
# services.proxy.extraEnv pointing at a real MCP server.
- name: WARDEN_UPSTREAM_URL
  value: "http://{{ $rel }}-upstream-stub:{{ .ctx.Values.upstreamStub.port }}/mcp"
{{- end }}
{{- if $tlsOn }}
# Outbound mTLS (B7 v1.x+2 sessions 3-6) — service-proxy cert covers
# brain, policy, hil, identity, and the HIL poll path. One bundle, four
# downstream listeners.
- name: WARDEN_PROXY_OUTBOUND_CERT_PATH
  value: "{{ $mount }}/service-proxy.crt"
- name: WARDEN_PROXY_OUTBOUND_KEY_PATH
  value: "{{ $mount }}/service-proxy.key"
- name: WARDEN_PROXY_OUTBOUND_CA_PATH
  value: "{{ $mount }}/ca.crt"
{{- end }}
{{- end }}
{{- if eq $name "brain" }}
{{- if $tlsOn }}
# mTLS receive (B7 v1.x+2 session 3). Bundle mounted → brain binds
# rustls + SPIFFE-URI allowlist on the application port; /health +
# /readyz + /metrics move to the plain-HTTP health port so kubelet
# probes don't need a client cert.
- name: WARDEN_BRAIN_TLS_DIR
  value: {{ $mount | quote }}
- name: WARDEN_BRAIN_ALLOWED_CALLERS
  value: "spiffe://warden.local/service/proxy"
- name: WARDEN_BRAIN_HEALTH_ADDR
  value: "0.0.0.0:9081"
{{- end }}
{{- end }}
{{- if eq $name "policyEngine" }}
{{- if $tlsOn }}
# mTLS receive (B7 v1.x+2 session 4). Bundle mounted → engine binds
# rustls + SPIFFE-URI allowlist on the application port; /health +
# /readyz + /metrics move to the plain-HTTP health port. Session 5
# adds console to the allowlist for /policies/* CRUD.
- name: WARDEN_POLICY_TLS_DIR
  value: {{ $mount | quote }}
- name: WARDEN_POLICY_ALLOWED_CALLERS
  value: "spiffe://warden.local/service/proxy,spiffe://warden.local/service/console"
- name: WARDEN_POLICY_HEALTH_ADDR
  value: "0.0.0.0:9082"
{{- end }}
{{- end }}
{{- if eq $name "hil" }}
{{- if $tlsOn }}
# mTLS receive (B7 v1.x+2 session 6). Single-mode listener — port 8084
# becomes rustls when the bundle is mounted; only callers presenting a
# workload cert from the allowlist are accepted. Health + /metrics move
# to `services.hil.healthPort` (default 9084) so kubelet + Prometheus
# can reach the plain-HTTP surface without a client cert.
- name: WARDEN_HIL_TLS_DIR
  value: {{ $mount | quote }}
- name: WARDEN_HIL_ALLOWED_CALLERS
  value: "spiffe://warden.local/service/proxy,spiffe://warden.local/service/console,spiffe://warden.local/service/simulator"
- name: WARDEN_HIL_HEALTH_ADDR
  value: "0.0.0.0:9084"
{{- end }}
{{- end }}
{{- if eq $name "identity" }}
{{- if $tlsOn }}
# mTLS receive (B7 v1.x+2 session 6). Dual-listener:
#   * plain HTTP on `services.identity.port` (default 8086) — public
#     subset (`/stats`, `/jwks.json`, `/.well-known/spiffe-bundle`,
#     health). Internal routes are STRIPPED on this port.
#   * mTLS on `services.identity.mtlsPort` (default 8186) — full surface
#     including `/svid`, `/grant`, `/revoke`, `/sign`, `/actor-token*`,
#     `/agents*`. SPIFFE allowlist gates every internal route.
#
# Service template emits a second port (`name: mtls`) alongside
# `http` when tlsBundle.secretName is set, so the chart-wired
# WARDEN_CONSOLE_IDENTITY_URL=https://<release>-identity:8186 resolves
# without any per-release manifest tweak.
- name: WARDEN_IDENTITY_TLS_DIR
  value: {{ $mount | quote }}
- name: WARDEN_IDENTITY_ALLOWED_CALLERS
  value: "spiffe://warden.local/service/proxy,spiffe://warden.local/service/console,spiffe://warden.local/service/simulator"
- name: WARDEN_IDENTITY_MTLS_ADDR
  value: "0.0.0.0:8186"
{{- end }}
{{- end }}
{{- if eq $name "ledger" }}
{{- if $tlsOn }}
# mTLS receive (B7 v1.x+2 session 5). Bundle mounted → ledger runs
# TWO listeners. Plain HTTP on `port` (default 8083) serves the public
# `/verify` + `/audit/{agent_id}*` read surface + `/health` + `/metrics`
# (kubelet + Ingress reach this without a client cert). mTLS on
# `mtlsPort` (default 8183) serves the full router; the internal write
# + console-only read subset (`/log`, `/audit/correlation/*`,
# `/stream/audit`, `/export*`, `/agents`) is SPIFFE-gated by the
# allowlist. The plain HTTP router STRIPS those routes so a cluster-
# network attacker cannot bypass mTLS by hitting `port` directly.
# Service template emits a second port (`name: mtls`) alongside
# `http` when tlsBundle.secretName is set so in-cluster clients can
# dial WARDEN_CONSOLE_LEDGER_URL=https://<release>-ledger:8183 by
# Service DNS.
- name: WARDEN_LEDGER_TLS_DIR
  value: {{ $mount | quote }}
- name: WARDEN_LEDGER_ALLOWED_CALLERS
  value: "spiffe://warden.local/service/proxy,spiffe://warden.local/service/console,spiffe://warden.local/service/deep-review"
- name: WARDEN_LEDGER_MTLS_ADDR
  value: "0.0.0.0:8183"
{{- end }}
{{- end }}
{{- if eq $name "console" }}
# Console → backend hops (B7 v1.x+2 sessions 5-6). All four hops flip
# to https when the bundle is mounted: ledger on :8183 (mTLS listener),
# policy-engine on :8082 (single-port mTLS), hil on :8084 (single-mode
# mTLS), identity on :8186 (mTLS listener).
- name: WARDEN_CONSOLE_LEDGER_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ $rel }}-ledger:{{ ternary "8183" "8083" $tlsOn }}"
- name: WARDEN_CONSOLE_HIL_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ $rel }}-hil:8084"
- name: WARDEN_CONSOLE_POLICY_ENGINE_URL
  value: "{{ $policyScheme }}://{{ $rel }}-policy-engine:8082"
- name: WARDEN_CONSOLE_IDENTITY_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ $rel }}-identity:{{ ternary "8186" "8086" $tlsOn }}"
{{- if $tlsOn }}
# Outbound mTLS — same cert bundle the proxy uses. One
# `service-console` identity authenticates every backend hop.
- name: WARDEN_CONSOLE_OUTBOUND_CERT_PATH
  value: "{{ $mount }}/service-console.crt"
- name: WARDEN_CONSOLE_OUTBOUND_KEY_PATH
  value: "{{ $mount }}/service-console.key"
- name: WARDEN_CONSOLE_OUTBOUND_CA_PATH
  value: "{{ $mount }}/ca.crt"
{{- end }}
{{- end }}
{{- if eq $name "deepReview" }}
- name: WARDEN_DEEP_REVIEW_LEDGER_URL
  value: "http://{{ $rel }}-ledger:8083"
# Deep-review is the only service that namespaces its NATS URL with
# the service prefix — every other warden binary reads bare NATS_URL.
# Mirror the helper-computed value (tls:// vs nats://) into the
# service-prefixed name so the bundled/mTLS path works without a
# deep-review code change.
- name: WARDEN_DEEP_REVIEW_NATS_URL
  valueFrom:
    configMapKeyRef:
      name: {{ include "warden.fullname" .ctx }}-config
      key: NATS_URL
{{- end }}
{{- if eq $name "identity" }}
- name: WARDEN_IDENTITY_CA_DIR
  value: {{ $mount | quote }}
{{- end }}
{{/* NATS mTLS (B7.5 v1.x+3). When tlsBundle is set, every service that
connects to NATS authenticates with its workload cert. Helm currently
doesn't enumerate demo-mint or simulator; this fires for the six
in-chart NATS-connecting services. The NATS server itself ships a
service-nats workload cert via the same bundle but is consumed by the
compose / chart-external NATS deployment. */}}
{{- if and $tlsOn (has $name (list "proxy" "ledger" "hil" "identity" "policyEngine" "deepReview")) }}
- name: NATS_TLS_CERT_PATH
  value: "{{ $mount }}/service-{{ $name | kebabcase }}.crt"
- name: NATS_TLS_KEY_PATH
  value: "{{ $mount }}/service-{{ $name | kebabcase }}.key"
- name: NATS_TLS_CA_PATH
  value: "{{ $mount }}/ca.crt"
{{- end }}
{{- end -}}

{{/* Pod-level Prometheus scrape annotations. Port fallback chain:
.metrics.port → .healthPort → .port. The healthPort step matters under
mTLS: services like brain + policy-engine flip their app port to TLS
when the bundle is mounted, and Prometheus scrapes without a client
cert; routing the scrape at healthPort keeps the plain-HTTP /metrics
endpoint reachable. */}}
{{- define "warden.metricsAnnotations" -}}
{{- $svcCfg := .svcCfg -}}
{{- $metrics := default dict $svcCfg.metrics -}}
{{- if $metrics.enabled -}}
{{- $port := default $svcCfg.port (default $svcCfg.healthPort $metrics.port) -}}
{{- $path := default "/metrics" $metrics.path }}
prometheus.io/scrape: "true"
prometheus.io/path: {{ $path | quote }}
prometheus.io/port: {{ $port | quote }}
{{- with $metrics.extraAnnotations }}
{{- toYaml . | nindent 0 }}
{{- end }}
{{- end -}}
{{- end -}}

{{/* `kind` is "liveness"/"readiness". Port fallback chain:
.probes.port → .healthPort → .port. Same rationale as the metrics
helper above — kubelet probes don't carry a client cert, so they have
to land on the plain-HTTP health port under mTLS mode. */}}
{{- define "warden.probe" -}}
{{- $ctx := .ctx -}}
{{- $svcCfg := .svcCfg -}}
{{- $kind := .kind -}}
{{- $defaults := index $ctx.Values.probeDefaults $kind -}}
{{- $probePort := default $svcCfg.port (default $svcCfg.healthPort $svcCfg.probes.port) -}}
initialDelaySeconds: {{ $defaults.initialDelaySeconds }}
periodSeconds: {{ $defaults.periodSeconds }}
timeoutSeconds: {{ $defaults.timeoutSeconds }}
failureThreshold: {{ $defaults.failureThreshold }}
{{- if eq $svcCfg.probes.type "httpGet" }}
httpGet:
  path: {{ if eq $kind "liveness" }}{{ $svcCfg.probes.health }}{{ else }}{{ $svcCfg.probes.ready }}{{ end }}
  port: {{ $probePort }}
{{- else if eq $svcCfg.probes.type "tcpSocket" }}
tcpSocket:
  port: {{ $probePort }}
{{- end }}
{{- end -}}
