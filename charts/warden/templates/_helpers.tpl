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
  value: "http://{{ $rel }}-hil:8084"
- name: WARDEN_IDENTITY_URL
  value: "http://{{ $rel }}-identity:8086"
{{- if $tlsOn }}
# Outbound mTLS (B7 v1.x+2 sessions 3-4) — proxy presents service-proxy
# cert on brain + policy hops; sessions 5-6 extend to hil / identity /
# ledger as their receive sides flip.
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
# /readyz + /metrics move to the plain-HTTP health port. Today only
# warden-proxy calls /evaluate; warden-console flips to mTLS on the
# /policies/* management surface in session 5 (add its prefix here
# when that lands).
- name: WARDEN_POLICY_TLS_DIR
  value: {{ $mount | quote }}
- name: WARDEN_POLICY_ALLOWED_CALLERS
  value: "spiffe://warden.local/service/proxy"
- name: WARDEN_POLICY_HEALTH_ADDR
  value: "0.0.0.0:9082"
{{- end }}
{{- end }}
{{- if eq $name "console" }}
- name: WARDEN_CONSOLE_LEDGER_URL
  value: "http://{{ $rel }}-ledger:8083"
- name: WARDEN_CONSOLE_HIL_URL
  value: "http://{{ $rel }}-hil:8084"
- name: WARDEN_CONSOLE_POLICY_ENGINE_URL
  value: "http://{{ $rel }}-policy-engine:8082"
- name: WARDEN_CONSOLE_IDENTITY_URL
  value: "http://{{ $rel }}-identity:8086"
{{- end }}
{{- if eq $name "deepReview" }}
- name: WARDEN_DEEP_REVIEW_LEDGER_URL
  value: "http://{{ $rel }}-ledger:8083"
{{- end }}
{{- if eq $name "identity" }}
- name: WARDEN_IDENTITY_CA_DIR
  value: {{ $mount | quote }}
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
