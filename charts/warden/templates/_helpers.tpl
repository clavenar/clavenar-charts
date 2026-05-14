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
Identity → CA dir (cert mount lives at proxyTls.mountPath, fixed /certs) */}}
{{- define "warden.backendEnvs" -}}
{{- $rel := .ctx.Release.Name -}}
{{- $name := .service -}}
{{- if eq $name "proxy" }}
- name: WARDEN_BRAIN_URL
  value: "http://{{ $rel }}-brain:8081/inspect"
- name: WARDEN_POLICY_URL
  value: "http://{{ $rel }}-policy-engine:8082/evaluate"
- name: WARDEN_HIL_URL
  value: "http://{{ $rel }}-hil:8084"
- name: WARDEN_IDENTITY_URL
  value: "http://{{ $rel }}-identity:8086"
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
  value: {{ .ctx.Values.proxyTls.mountPath | quote }}
{{- end }}
{{- end -}}

{{/* Pod-level Prometheus scrape annotations. Port fallback:
.metrics.port (proxy uses healthPort 8080) → .port. */}}
{{- define "warden.metricsAnnotations" -}}
{{- $svcCfg := .svcCfg -}}
{{- $metrics := default dict $svcCfg.metrics -}}
{{- if $metrics.enabled -}}
{{- $port := default $svcCfg.port $metrics.port -}}
{{- $path := default "/metrics" $metrics.path }}
prometheus.io/scrape: "true"
prometheus.io/path: {{ $path | quote }}
prometheus.io/port: {{ $port | quote }}
{{- with $metrics.extraAnnotations }}
{{- toYaml . | nindent 0 }}
{{- end }}
{{- end -}}
{{- end -}}

{{/* `kind` is "liveness"/"readiness". Port fallback: .probes.port
(proxy targets healthPort) → .port. */}}
{{- define "warden.probe" -}}
{{- $ctx := .ctx -}}
{{- $svcCfg := .svcCfg -}}
{{- $kind := .kind -}}
{{- $defaults := index $ctx.Values.probeDefaults $kind -}}
{{- $probePort := default $svcCfg.port $svcCfg.probes.port -}}
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
