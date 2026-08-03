{{/*
Helpers for the Clavenar chart. Image-tag fallback chain:
  services.<svc>.image.tag → .Values.imageTag → .Chart.AppVersion
*/}}

{{/* Release name only — chart-name suffix would yield names like
`my-clavenar-clavenar-config`. Override via .Values.fullnameOverride. */}}
{{- define "clavenar.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Content-address immutable workload policy so kubelet never reuses a
stale projection after Helm replaces the bundle. */}}
{{- define "clavenar.workloadCapabilityConfigMapName" -}}
{{- $base := include "clavenar.fullname" . | trunc 28 | trimSuffix "-" -}}
{{- $digest := .Files.Get "files/workload-capability-bundle.json" | sha256sum | trunc 12 -}}
{{- printf "%s-workload-capabilities-%s" $base $digest -}}
{{- end -}}

{{/* Shared authentication Secret. Preserve the historical
`<release>-shared-tokens` default while allowing production operators to
reference a Secret managed outside this release. */}}
{{- define "clavenar.authSecretName" -}}
{{- default (printf "%s-shared-tokens" .Release.Name) .Values.authSecrets.existingSecretName -}}
{{- end -}}

{{/* HIL exact-payload encryption remains a dedicated operator-owned Secret
unless the bundled evaluation recipe explicitly opts into the upgrade-stable
chart-managed key. Production validation forbids the managed form. */}}
{{- define "clavenar.hilPayloadSecretName" -}}
{{- if .Values.hilPayloadProtection.managedEvaluation -}}
{{- include "clavenar.authSecretName" . -}}
{{- else -}}
{{- required "hilPayloadProtection.secretName is required" .Values.hilPayloadProtection.secretName -}}
{{- end -}}
{{- end -}}

{{/* Chart-owned public verification material for the evaluation quickstart.
No private signing key is present in this ConfigMap. */}}
{{- define "clavenar.evaluationPublicTrustName" -}}
{{- printf "%s-evaluation-public-trust" .Release.Name -}}
{{- end -}}

{{/* Stable TLS hostname for the workload-SVID authority. Renewed Identity
certificates deliberately carry DNS:identity, independent of release name. */}}
{{- define "clavenar.identityHost" -}}
{{- if and .Values.identityAlias.enabled (not (empty .Values.tlsBundle.secretName)) .Values.workloadIdentity.enabled -}}identity{{- else -}}{{ .Release.Name }}-identity{{- end -}}
{{- end -}}

{{/* Stable application hostname for a managed workload-SVID server. Renewed
certificates deliberately carry the bare service DNS name, so mTLS callers use
the matching ExternalName alias after workload identity is enabled. */}}
{{- define "clavenar.workloadHost" -}}
{{- if and (not (empty .ctx.Values.tlsBundle.secretName)) .ctx.Values.workloadIdentity.enabled -}}
{{- .service | kebabcase -}}
{{- else -}}
{{- printf "%s-%s" .ctx.Release.Name (.service | kebabcase) -}}
{{- end -}}
{{- end -}}

{{/* Per-service fullname: <release>-<service>. The values key is
camelCase to form a valid Go-template path; k8s object names need
RFC-1123 lowercase, so we kebabcase here. */}}
{{- define "clavenar.serviceFullname" -}}
{{- $ctx := .ctx -}}
{{- $service := .service | kebabcase -}}
{{- printf "%s-%s" $ctx.Release.Name $service | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* app.kubernetes.io/component differentiates services. Kebabcased
to stay consistent with metadata.name. */}}
{{- define "clavenar.selectorLabels" -}}
app.kubernetes.io/name: {{ .ctx.Chart.Name }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .service | kebabcase }}
{{- end -}}

{{/* Common labels applied to every object. */}}
{{- define "clavenar.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .ctx.Chart.Name .ctx.Chart.Version | replace "+" "_" }}
{{ include "clavenar.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
app.kubernetes.io/version: {{ .ctx.Chart.AppVersion | quote }}
{{- end -}}

{{/* Resolve the image reference for a service. */}}
{{- define "clavenar.imageRef" -}}
{{- $ctx := .ctx -}}
{{- $svcCfg := .svcCfg -}}
{{- $registry := $ctx.Values.imageRegistry -}}
{{- $repo := $svcCfg.image.repository -}}
{{- $digest := default "" $svcCfg.image.digest -}}
{{- if $digest -}}
{{- if not (regexMatch "^sha256:[a-f0-9]{64}$" $digest) -}}
{{- fail (printf "service image digest must be sha256:<64 lowercase hex>, got %q" $digest) -}}
{{- end -}}
{{- if $registry -}}
{{- printf "%s/%s@%s" $registry $repo $digest -}}
{{- else -}}
{{- printf "%s@%s" $repo $digest -}}
{{- end -}}
{{- else -}}
{{- $tag := default $ctx.Values.imageTag $svcCfg.image.tag -}}
{{- if not $tag -}}{{- $tag = $ctx.Chart.AppVersion -}}{{- end -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* terminationGracePeriodSeconds = drain cap + 5s safety margin. */}}
{{- define "clavenar.terminationGrace" -}}
{{- add (int .Values.drainCapSecs) 5 -}}
{{- end -}}

{{/* NATS URL: bundled mode forces the in-cluster service DNS; BYO
mode honors the operator-supplied .Values.nats.url. The upstream
nats-io/nats subchart names its Service `<release>-nats` so the
helper composes that directly. Scheme flips to `tls://` when the
auto-mint bundle is in use — clavenar clients then read
NATS_TLS_{CERT,KEY,CA}_PATH and require TLS on the wire (see
B7.5 / nats_tls.rs in clavenar-proxy). Guard fails the render if the
bundled NATS subchart hasn't been told to terminate TLS itself — the
default would otherwise crash every client with `InvalidContentType`
(plaintext NATS server, TLS-only clients). */}}
{{- define "clavenar.natsUrl" -}}
{{- if .Values.nats.bundled.enabled -}}
{{- $tlsOn := not (empty .Values.tlsBundle.secretName) -}}
{{- $natsTlsOn := and (hasKey .Values "nats") (hasKey .Values.nats "config") (hasKey .Values.nats.config "nats") (hasKey .Values.nats.config.nats "tls") .Values.nats.config.nats.tls.enabled -}}
{{- if and $tlsOn (not $natsTlsOn) -}}
{{- fail "tlsBundle.secretName is set + nats.bundled.enabled is true, but nats.config.nats.tls.enabled is false — bundled NATS would listen plaintext while clavenar clients dial TLS (InvalidContentType crash). Mirror tests/values-bundled.yaml's nats.config.nats.tls + nats.tlsCA blocks." -}}
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
{{- define "clavenar.vaultAddr" -}}
{{- if .Values.vault.bundled.enabled -}}
http://{{ .Release.Name }}-vault:8200
{{- else -}}
{{ .Values.vault.addr }}
{{- end -}}
{{- end -}}

{{/* k8s Secret holding disjoint Identity and Proxy Vault token keys. Bundled
mode autogenerates `<release>-vault-token`; BYO mode honors
.Values.vault.tokenSecretName. */}}
{{- define "clavenar.vaultTokenSecretName" -}}
{{- if .Values.vault.bundled.enabled -}}
{{ .Release.Name }}-vault-token
{{- else -}}
{{ .Values.vault.tokenSecretName }}
{{- end -}}
{{- end -}}

{{/* Workload names that need a per-service cert. Values include the 9
in-chart services and external website/demo-mint/simulator peers; "exec" and
"nats" are appended when enabled. Emits a space-separated list consumed by
the auto-mint Job. */}}
{{- define "clavenar.bundleServices" -}}
{{- $services := default (list) .Values.tlsBundle.bundleServices -}}
{{- if .Values.exec.enabled -}}
{{- $services = append $services "exec" -}}
{{- end -}}
{{- if .Values.nats.bundled.enabled -}}
{{- $services = append $services "nats" -}}
{{- end -}}
{{ join " " $services }}
{{- end -}}

{{/* Stable identity-layout marker. Change only for an incompatible SAN or
Secret data shape; rotation generation is deliberately independent. */}}
{{- define "clavenar.tlsSanScheme" -}}
release-prefixed-v4-additional-dns
{{- end -}}

{{/* Enabled TLS consumers coordinated by the explicit rotation hook. */}}
{{- define "clavenar.tlsRolloutDeployments" -}}
{{- $deployments := list -}}
{{- if .Values.tlsBundle.secretName -}}
{{- range $name, $cfg := .Values.services -}}
{{- if $cfg.enabled -}}
{{- $deployments = append $deployments ($name | kebabcase) -}}
{{- end -}}
{{- end -}}
{{- if .Values.exec.enabled -}}
{{- $deployments = append $deployments "exec" -}}
{{- end -}}
{{- end -}}
{{ join " " $deployments }}
{{- end -}}

{{- define "clavenar.tlsRolloutStatefulSets" -}}
{{- if and .Values.tlsBundle.secretName .Values.nats.bundled.enabled -}}nats{{- end -}}
{{- end -}}

{{/*
Reject authentication material that would be rendered literally through
services.<name>.extraEnv. HIL's session and decision variables are chart-owned
and already emitted as secretKeyRef entries, so allowing duplicates would both
leak a value into the Pod spec and make precedence ambiguous. Optional shared
demo-session variables remain configurable, but only through an explicit
secretKeyRef.
*/}}
{{- define "clavenar.validateAuthExtraEnv" -}}
{{- $service := .service -}}
{{- range $entry := default (list) .svcCfg.extraEnv -}}
{{- $name := default "" $entry.name -}}
{{- if and (eq $service "hil") (or (eq $name "CLAVENAR_HIL_SESSION_KEY") (eq $name "CLAVENAR_HIL_DECIDE_TOKEN") (eq $name "CLAVENAR_HIL_BOOTSTRAP_TOKEN")) -}}
{{- fail (printf "services.%s.extraEnv must not override chart-owned authentication variable %s; use authSecrets.existingSecretName" $service $name) -}}
{{- end -}}
{{- if and (eq $service "identity") (or (eq $name "CLAVENAR_IDENTITY_OIDC_HS256_KEY") (regexMatch "^CLAVENAR_IDENTITY_OIDC_TENANT_.*_HS256_KEY$" $name)) -}}
{{- fail (printf "services.%s.extraEnv authentication variable %s is a symmetric OIDC signing key; official chart deployments require an RS256 JWKS file" $service $name) -}}
{{- end -}}
{{- $demoAuth := or
      (and (eq $service "console") (eq $name "CLAVENAR_CONSOLE_DEMO_SESSION_HS256"))
      (and (eq $service "hil") (eq $name "CLAVENAR_HIL_DEMO_SESSION_HS256"))
      (and (eq $service "ledger") (eq $name "CLAVENAR_LEDGER_DEMO_SESSION_HS256")) -}}
{{- if $demoAuth -}}
{{- $valueFrom := default (dict) $entry.valueFrom -}}
{{- $secretKeyRef := default (dict) $valueFrom.secretKeyRef -}}
{{- if or (hasKey $entry "value") (empty $secretKeyRef.name) (empty $secretKeyRef.key) -}}
{{- fail (printf "services.%s.extraEnv authentication variable %s requires valueFrom.secretKeyRef with non-empty name and key; literal values are forbidden" $service $name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Reject extraEnv entries that duplicate chart-emitted transport, listener,
authentication, caller, or trust settings. Kubernetes accepts duplicate env
names and resolves them by list order, which would turn these chart-owned
boundaries into last-write-wins configuration. Also reject duplicate names
inside extraEnv itself so every rendered container has one unambiguous value
per environment variable.
*/}}
{{- define "clavenar.validateGovernedExtraEnv" -}}
{{- $ctx := .ctx -}}
{{- $service := .service -}}
{{- $common := list
      "NATS_URL"
      "NATS_INBOX_PREFIX"
      "CLAVENAR_GRACEFUL_DRAIN_SECS" -}}
{{- $byService := dict
      "proxy" (list
        "CLAVENAR_CONTROL_STATE_REPLICAS"
        "CLAVENAR_PROXY_QUOTA_GATE_ENABLED"
        "CLAVENAR_PROXY_QUOTA_CACHE_TTL_SECS"
        "CLAVENAR_CONTROL_STATE_SNAPSHOT_PATH"
        "CLAVENAR_RUNTIME_ENVIRONMENT"
        "CLAVENAR_ATTESTATION_PROVIDER"
        "CLAVENAR_PROXY_HEALTH_ADDR"
        "CLAVENAR_BRAIN_URL"
        "CLAVENAR_POLICY_URL"
        "CLAVENAR_HIL_URL"
        "CLAVENAR_LEDGER_URL"
        "CLAVENAR_IDENTITY_URL"
        "CLAVENAR_BRAIN_READINESS_URL"
        "CLAVENAR_POLICY_READINESS_URL"
        "CLAVENAR_HIL_READINESS_URL"
        "CLAVENAR_LEDGER_READINESS_URL"
        "CLAVENAR_IDENTITY_READINESS_URL"
        "CLAVENAR_UPSTREAM_READINESS_URL"
        "CLAVENAR_PROXY_GRANT_JWKS_URL"
        "CLAVENAR_PROXY_GRANT_JWKS_REFRESH_SECS"
        "CLAVENAR_PROXY_GRANT_JWKS_MAX_STALENESS_SECS"
        "CLAVENAR_PROXY_GRANT_JWKS_FETCH_TIMEOUT_SECS"
        "CLAVENAR_PROXY_SERVER_EXECUTION_DB"
        "CLAVENAR_PROXY_OUTBOUND_CERT_PATH"
        "CLAVENAR_PROXY_OUTBOUND_KEY_PATH"
        "CLAVENAR_PROXY_OUTBOUND_CA_PATH"
        "CLAVENAR_PROXY_SPIFFE"
        "CLAVENAR_PROXY_EXEC_UPSTREAM_MTLS"
        "NATS_TLS_CERT_PATH"
        "NATS_TLS_KEY_PATH"
        "NATS_TLS_CA_PATH"
        "VAULT_ADDR"
        "VAULT_TOKEN_FILE")
      "brain" (list
        "CLAVENAR_BRAIN_TLS_DIR"
        "CLAVENAR_BRAIN_ALLOWED_CALLERS"
        "CLAVENAR_BRAIN_HEALTH_ADDR"
        "CLAVENAR_BRAIN_PLAIN_ADDR"
        "CLAVENAR_BRAIN_REQUIRE_AUX_CONTROLS"
        "CLAVENAR_BRAIN_EXPLAIN_CALLER_SPIFFE"
        "CLAVENAR_BRAIN_NARRATE_CALLER_SPIFFE"
        "CLAVENAR_BRAIN_EXPLAIN_RATE_LIMIT_PER_MINUTE"
        "CLAVENAR_BRAIN_NARRATE_RATE_LIMIT_PER_MINUTE"
        "CLAVENAR_BRAIN_AUX_SPEND_BUDGET_MICRO_USD_PER_HOUR"
        "CLAVENAR_BRAIN_AUX_TIMEOUT_MILLIS"
        "CLAVENAR_BRAIN_AUX_BODY_LIMIT_BYTES"
        "CLAVENAR_BRAIN_CACHE_HMAC_KEY_FILE"
        "CLAVENAR_BRAIN_REQUIRE_CACHE_HMAC_KEY")
      "policyEngine" (list
        "CLAVENAR_POLICY_DB"
        "CLAVENAR_POLICY_ENGINE_BRAIN_URL"
        "CLAVENAR_POLICY_EXPECTED_PEER_SPIFFE"
        "CLAVENAR_POLICY_TLS_DIR"
        "CLAVENAR_POLICY_ALLOWED_CALLERS"
        "CLAVENAR_POLICY_HEALTH_ADDR"
        "NATS_TLS_CERT_PATH"
        "NATS_TLS_KEY_PATH"
        "NATS_TLS_CA_PATH")
      "ledger" (list
        "CLAVENAR_LEDGER_BACKEND"
        "CLAVENAR_LEDGER_DB"
        "CLAVENAR_LEDGER_PG_URL"
        "CLAVENAR_LEDGER_PG_DSN"
        "CLAVENAR_LEDGER_PG_TLS_CA_FILE"
        "CLAVENAR_LEDGER_PG_ALLOW_INSECURE_TEST_ONLY"
        "CLAVENAR_LEDGER_ALLOWED_CALLERS"
        "CLAVENAR_LEDGER_TLS_DIR"
        "CLAVENAR_LEDGER_MTLS_ADDR"
        "CLAVENAR_LEDGER_REQUIRE_TRUSTED_PROXY"
        "CLAVENAR_LEDGER_TRUSTED_PROXY_SPIFFE"
        "CLAVENAR_LEDGER_REGULATORY_SIGNING_REQUIRED"
        "CLAVENAR_LEDGER_CRYPTOGRAPHIC_VERIFICATION_REQUIRED"
        "CLAVENAR_LEDGER_TSA_REQUIRED"
        "CLAVENAR_LEDGER_TSA_CA_FILE"
        "CLAVENAR_LEDGER_TSA_SIGNER_CERT_FILE"
        "CLAVENAR_LEDGER_ANCHOR_INTERVAL_SECS"
        "CLAVENAR_LEDGER_ANCHOR_TSA_URL"
        "CLAVENAR_IDENTITY_URL"
        "CLAVENAR_LEDGER_SPIFFE"
        "NATS_TLS_CERT_PATH"
        "NATS_TLS_KEY_PATH"
        "NATS_TLS_CA_PATH")
      "hil" (list
        "CLAVENAR_HIL_TLS_DIR"
        "CLAVENAR_HIL_ALLOWED_CALLERS"
        "CLAVENAR_HIL_HEALTH_ADDR"
        "CLAVENAR_HIL_DECIDE_TOKEN"
        "CLAVENAR_HIL_SESSION_KEY"
        "CLAVENAR_HIL_BOOTSTRAP_TOKEN"
        "CLAVENAR_HIL_DEPLOYMENT_ID"
        "CLAVENAR_HIL_SIMULATOR_TENANT"
        "CLAVENAR_HIL_WEBAUTHN_TTL_SECS"
        "CLAVENAR_HIL_WEBAUTHN_ATTEMPT_LIMIT"
        "CLAVENAR_HIL_WEBAUTHN_RATE_WINDOW_SECS"
        "CLAVENAR_HIL_WEBAUTHN_SUBJECT_START_LIMIT"
        "CLAVENAR_HIL_WEBAUTHN_TENANT_START_LIMIT"
        "CLAVENAR_HIL_WEBAUTHN_SOURCE_START_LIMIT"
        "CLAVENAR_HIL_WEBAUTHN_DEPLOYMENT_START_LIMIT"
        "CLAVENAR_HIL_WEBAUTHN_SUBJECT_PENDING_LIMIT"
        "CLAVENAR_HIL_WEBAUTHN_TENANT_PENDING_LIMIT"
        "CLAVENAR_HIL_WEBAUTHN_SOURCE_PENDING_LIMIT"
        "CLAVENAR_HIL_WEBAUTHN_DEPLOYMENT_PENDING_LIMIT"
        "CLAVENAR_HIL_NOTIFICATION_MODE"
        "CLAVENAR_HIL_NOTIFICATION_TIMEOUT_SECS"
        "CLAVENAR_HIL_WEBHOOK_URL"
        "CLAVENAR_HIL_WEBHOOK_BEARER_TOKEN_FILE"
        "NATS_TLS_CERT_PATH"
        "NATS_TLS_KEY_PATH"
        "NATS_TLS_CA_PATH")
      "identity" (list
        "CLAVENAR_CONTROL_STATE_REPLICAS"
        "CLAVENAR_RUNTIME_ENVIRONMENT"
        "CLAVENAR_ATTESTATION_PROVIDER"
        "CLAVENAR_IDENTITY_TLS_DIR"
        "CLAVENAR_IDENTITY_ALLOWED_CALLERS"
        "CLAVENAR_IDENTITY_MTLS_ADDR"
        "CLAVENAR_IDENTITY_CA_DIR"
        "CLAVENAR_IDENTITY_REPLAY_REPLICAS"
        "CLAVENAR_IDENTITY_WORKLOAD_ALLOWED_CALLERS"
        "CLAVENAR_ATTESTATION_TRUST_ANCHORS_FILE"
        "CLAVENAR_ATTESTATION_BOOTSTRAP_APPROVAL_FILE"
        "CLAVENAR_IDENTITY_CAPABILITIES_TOML"
        "NATS_TLS_CERT_PATH"
        "NATS_TLS_KEY_PATH"
        "NATS_TLS_CA_PATH"
        "VAULT_ADDR"
        "VAULT_TOKEN_FILE")
      "deepReview" (list
        "CLAVENAR_DEEP_REVIEW_LEDGER_URL"
        "CLAVENAR_DEEP_REVIEW_NATS_URL"
        "NATS_TLS_CERT_PATH"
        "NATS_TLS_KEY_PATH"
        "NATS_TLS_CA_PATH")
      "assurance" (list
        "CLAVENAR_ASSURANCE_PROXY_URL"
        "CLAVENAR_ASSURANCE_PROXY_READINESS_URL"
        "CLAVENAR_ASSURANCE_NATS_URL"
        "CLAVENAR_ASSURANCE_ADMIN_PORT"
        "CLAVENAR_ASSURANCE_DIAGNOSTICS_PORT"
        "CLAVENAR_ASSURANCE_TLS_DIR"
        "CLAVENAR_ASSURANCE_ALLOWED_CALLERS"
        "CLAVENAR_ASSURANCE_FORENSIC_SUBJECT"
        "CLAVENAR_ASSURANCE_FORENSIC_STREAM"
        "CLAVENAR_ASSURANCE_REQUEST_TIMEOUT_SECS"
        "CLAVENAR_ASSURANCE_RUN_TIMEOUT_SECS"
        "CLAVENAR_ASSURANCE_PUBLISH_TIMEOUT_SECS"
        "CLAVENAR_ASSURANCE_CERT_DIR"
        "NATS_TLS_CERT_PATH"
        "NATS_TLS_KEY_PATH"
        "NATS_TLS_CA_PATH")
      "console" (list
        "CLAVENAR_CONSOLE_AUTH"
        "CLAVENAR_CONSOLE_BIND"
        "CLAVENAR_CONSOLE_PORT"
        "CLAVENAR_CONSOLE_DEMO_ADDR"
        "CLAVENAR_CONSOLE_DIAGNOSTICS_ADDR"
        "CLAVENAR_CONSOLE_OPERATOR_TLS_CERT_PATH"
        "CLAVENAR_CONSOLE_OPERATOR_TLS_KEY_PATH"
        "CLAVENAR_CONSOLE_OPERATOR_CLIENT_CA_PATH"
        "CLAVENAR_CONSOLE_OPERATOR_IDENTITIES_PATH"
        "CLAVENAR_CONSOLE_AUTH_RATE_LIMIT_MAX"
        "CLAVENAR_CONSOLE_AUTH_RATE_LIMIT_WINDOW_SECS"
        "CLAVENAR_CONSOLE_RELEASE_VERSION"
        "CLAVENAR_CONSOLE_MUTATION_ORIGINS"
        "CLAVENAR_CONSOLE_BRAIN_URL"
        "CLAVENAR_CONSOLE_LEDGER_URL"
        "CLAVENAR_CONSOLE_HIL_URL"
        "CLAVENAR_CONSOLE_POLICY_ENGINE_URL"
        "CLAVENAR_CONSOLE_IDENTITY_URL"
        "CLAVENAR_ASSURANCE_URL"
        "CLAVENAR_CONSOLE_LEDGER_READINESS_URL"
        "CLAVENAR_CONSOLE_HIL_READINESS_URL"
        "CLAVENAR_CONSOLE_POLICY_READINESS_URL"
        "CLAVENAR_CONSOLE_IDENTITY_READINESS_URL"
        "CLAVENAR_CONSOLE_BRAIN_READINESS_URL"
        "CLAVENAR_CONSOLE_ASSURANCE_READINESS_URL"
        "CLAVENAR_CONSOLE_SIMULATOR_READINESS_URL"
        "CLAVENAR_CONSOLE_TLS_DIR"
        "CLAVENAR_CONSOLE_OUTBOUND_CERT_PATH"
        "CLAVENAR_CONSOLE_OUTBOUND_KEY_PATH"
        "CLAVENAR_CONSOLE_OUTBOUND_CA_PATH"
        "CLAVENAR_HIL_DECIDE_TOKEN"
        "CLAVENAR_CONSOLE_ALLOW_DISABLED_NETWORK") -}}
{{- $governed := concat $common (default (list) (get $byService $service)) -}}
{{- if has $service (list "ledger" "policyEngine" "hil" "identity") -}}
{{- $governed = concat $governed (list
      "CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE"
      "CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE_SHA256"
      "CLAVENAR_ENDPOINT_CAPABILITY_MATRIX_SHA256") -}}
{{- end -}}
{{- if and (eq $service "proxy") (or $ctx.Values.exec.enabled $ctx.Values.upstreamStub.enabled) -}}
{{- $governed = append $governed "CLAVENAR_UPSTREAM_URL" -}}
{{- end -}}
{{- if and (eq $service "identity") $ctx.Values.evaluationPublicTrust.enabled -}}
{{- $governed = concat $governed (list
      "CLAVENAR_IDENTITY_OIDC_TENANT_EVALUATION_ISSUER"
      "CLAVENAR_IDENTITY_OIDC_TENANT_EVALUATION_AUDIENCE"
      "CLAVENAR_IDENTITY_OIDC_TENANT_EVALUATION_RS256_JWKS_FILE"
      "CLAVENAR_IDENTITY_REQUIRE_ASYMMETRIC_OIDC") -}}
{{- end -}}
{{- $seen := dict -}}
{{- range $index, $entry := default (list) .svcCfg.extraEnv -}}
{{- $name := default "" $entry.name -}}
{{- if and $name (hasKey $seen $name) -}}
{{- fail (printf "services.%s.extraEnv[%d].name=%s duplicates an earlier extraEnv entry" $service $index $name) -}}
{{- end -}}
{{- if $name -}}{{- $_ := set $seen $name true -}}{{- end -}}
{{- if has $name $governed -}}
{{- fail (printf "services.%s.extraEnv[%d].name=%s duplicates a chart-governed environment variable" $service $index $name) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Shared NATS + drain-cap envs, then per-component back-end URLs,
then per-service extraEnv. Pass `service` so the back-end-URL helper
knows the component. */}}
{{- define "clavenar.commonEnv" -}}
{{- if has .service (list "proxy" "policyEngine" "ledger" "hil" "identity" "deepReview" "assurance") }}
- name: NATS_URL
  valueFrom:
    configMapKeyRef:
      name: {{ include "clavenar.fullname" .ctx }}-config
      key: NATS_URL
- name: NATS_INBOX_PREFIX
  value: "_INBOX.clavenar.{{ .service | kebabcase }}"
{{- end }}
- name: CLAVENAR_GRACEFUL_DRAIN_SECS
  valueFrom:
    configMapKeyRef:
      name: {{ include "clavenar.fullname" .ctx }}-config
      key: CLAVENAR_GRACEFUL_DRAIN_SECS
{{- include "clavenar.backendEnvs" (dict "ctx" .ctx "service" .service) }}
{{- with .svcCfg.extraEnv }}
{{- toYaml . | nindent 0 }}
{{- end }}
{{- end -}}

{{/* Backend URL env vars wired by component. The compose stack pins
these explicitly per service; the chart computes the official in-release
topology and rejects duplicate extraEnv entries so the security boundary is
never decided by Kubernetes list ordering.

Proxy → brain + policy + hil + identity
Policy-engine → brain auxiliary explanation listener
Console → brain + ledger + hil + policy-engine + identity
Deep-review → ledger
Identity → CA dir (cert mount lives at tlsBundle.mountPath, fixed /certs) */}}
{{- define "clavenar.backendEnvs" -}}
{{- $rel := .ctx.Release.Name -}}
{{- $name := .service -}}
{{- $tls := .ctx.Values.tlsBundle.secretName -}}
{{- $mount := .ctx.Values.tlsBundle.mountPath -}}
{{- $tlsOn := not (empty $tls) -}}
{{- $identityHost := include "clavenar.identityHost" .ctx -}}
{{- $proxyHost := include "clavenar.workloadHost" (dict "ctx" .ctx "service" "proxy") -}}
{{- $brainHost := include "clavenar.workloadHost" (dict "ctx" .ctx "service" "brain") -}}
{{- $policyHost := include "clavenar.workloadHost" (dict "ctx" .ctx "service" "policy-engine") -}}
{{- $ledgerHost := include "clavenar.workloadHost" (dict "ctx" .ctx "service" "ledger") -}}
{{- $hilHost := include "clavenar.workloadHost" (dict "ctx" .ctx "service" "hil") -}}
{{- $assuranceHost := include "clavenar.workloadHost" (dict "ctx" .ctx "service" "assurance") -}}
{{- $brainScheme := ternary "https" "http" $tlsOn -}}
{{- $policyScheme := ternary "https" "http" $tlsOn -}}
{{- $managedWorkload := and $tlsOn .ctx.Values.workloadIdentity.enabled (has $name (list "proxy" "brain" "policyEngine" "ledger" "hil" "identity" "console")) -}}
{{ if $managedWorkload }}
{{ $prefix := get (dict "proxy" "PROXY" "brain" "BRAIN" "policyEngine" "POLICY" "ledger" "LEDGER" "hil" "HIL" "identity" "IDENTITY" "console" "CONSOLE") $name -}}
{{- $expected := get (dict
      "proxy" "spiffe://clavenar.local/service/brain,spiffe://clavenar.local/service/policy-engine,spiffe://clavenar.local/service/hil,spiffe://clavenar.local/service/identity,spiffe://clavenar.local/service/ledger"
      "brain" "spiffe://clavenar.local/service/identity"
      "policyEngine" "spiffe://clavenar.local/service/identity,spiffe://clavenar.local/service/brain"
      "ledger" "spiffe://clavenar.local/service/identity"
      "hil" "spiffe://clavenar.local/service/identity"
      "identity" "spiffe://clavenar.local/service/identity"
      "console" "spiffe://clavenar.local/service/identity,spiffe://clavenar.local/service/ledger,spiffe://clavenar.local/service/hil,spiffe://clavenar.local/service/policy-engine,spiffe://clavenar.local/service/simulator,spiffe://clavenar.local/service/assurance,spiffe://clavenar.local/service/brain") $name -}}
{{- if and (eq $name "proxy") .ctx.Values.exec.enabled -}}
{{- $expected = printf "%s,spiffe://clavenar.local/service/exec" $expected -}}
{{- end -}}
# Managed workload identity: durable caller-held key, exact-current renewal,
# and strict peer SPIFFE verification. The Identity Service publishes not-ready
# endpoints so its first self-enrollment cannot deadlock on readiness.
- name: {{ printf "CLAVENAR_%s_WORKLOAD_REFRESH_URL" $prefix }}
  value: "https://{{ $identityHost }}:8186/workload-svid"
- name: {{ printf "CLAVENAR_%s_WORKLOAD_STATE_DIR" $prefix }}
  value: "/var/lib/clavenar-workload-identity"
- name: {{ printf "CLAVENAR_%s_EXPECTED_PEER_SPIFFE" $prefix }}
  value: {{ $expected | quote }}
{{- if eq $name "identity" }}
# Identity is the workload-SVID authority for every chart-managed caller.
# This exact allowlist is chart-owned so a fresh install can perform the
# first self-enrollment without an unsafe wildcard.
- name: CLAVENAR_IDENTITY_WORKLOAD_ALLOWED_CALLERS
  value: "spiffe://clavenar.local/service/proxy,spiffe://clavenar.local/service/brain,spiffe://clavenar.local/service/policy-engine,spiffe://clavenar.local/service/ledger,spiffe://clavenar.local/service/hil,spiffe://clavenar.local/service/identity,spiffe://clavenar.local/service/console,spiffe://clavenar.local/service/simulator"
{{- end }}
{{ end }}{{ "\n" -}}
{{- if has $name (list "proxy" "identity") }}
# Attestation provider posture is chart-owned. Evaluation may explicitly use
# the deterministic Proxy mock; production selects only Identity's real
# verifier and both binaries enforce it before listener bind. A separately
# projected TPM registry adds tpm2-quote without replacing k8s-key-bound.
- name: CLAVENAR_RUNTIME_ENVIRONMENT
  value: {{ ternary "production" "development" (eq .ctx.Values.deploymentProfile "production") | quote }}
- name: CLAVENAR_ATTESTATION_PROVIDER
  value: {{ ternary "identity-k8s-key-bound+tpm2-quote" (ternary "identity-k8s-key-bound" (ternary "mock" "identity-k8s-key-bound" (eq $name "proxy")) (eq .ctx.Values.deploymentProfile "production")) (not (empty .ctx.Values.tpm2AttestationTrust.secretName)) | quote }}
# Retained JetStream control projections use one exact replica contract across
# their Identity writer and Proxy reader/CAS writer.
- name: CLAVENAR_CONTROL_STATE_REPLICAS
  value: {{ .ctx.Values.controlState.replicas | quote }}
{{- end }}
{{- if and $tlsOn (has $name (list "ledger" "policyEngine" "hil" "identity")) }}
{{- $capabilityBundle := .ctx.Files.Get "files/workload-capability-bundle.json" -}}
{{- $capabilityDocument := fromJson $capabilityBundle -}}
# All four application mTLS listeners consume the byte-identical generated
# policy. The file digest and its canonical matrix binding are startup gates.
- name: CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE
  value: "/etc/clavenar/workload-capability-bundle.json"
- name: CLAVENAR_WORKLOAD_CAPABILITY_BUNDLE_SHA256
  value: "sha256:{{ sha256sum $capabilityBundle }}"
- name: CLAVENAR_ENDPOINT_CAPABILITY_MATRIX_SHA256
  value: {{ required "generated capability bundle requires matrixSha256" $capabilityDocument.matrixSha256 | quote }}
{{- end }}
{{- if eq $name "proxy" }}
- name: CLAVENAR_PROXY_HEALTH_ADDR
  value: "0.0.0.0:8080"
- name: CLAVENAR_PROXY_QUOTA_GATE_ENABLED
  value: "true"
- name: CLAVENAR_PROXY_QUOTA_CACHE_TTL_SECS
  value: "300"
- name: CLAVENAR_BRAIN_URL
  value: "{{ $brainScheme }}://{{ $brainHost }}:8081/inspect"
- name: CLAVENAR_POLICY_URL
  value: "{{ $policyScheme }}://{{ $policyHost }}:8082/evaluate"
- name: CLAVENAR_HIL_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ $hilHost }}:8084"
- name: CLAVENAR_LEDGER_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ $ledgerHost }}:{{ ternary "8183" "8083" $tlsOn }}"
- name: CLAVENAR_IDENTITY_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ ternary $identityHost (printf "%s-identity" $rel) $tlsOn }}:{{ ternary "8186" "8086" $tlsOn }}"
- name: CLAVENAR_BRAIN_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "brain") | quote }}
- name: CLAVENAR_POLICY_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "policy-engine") | quote }}
- name: CLAVENAR_HIL_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "hil") | quote }}
- name: CLAVENAR_LEDGER_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "ledger") | quote }}
- name: CLAVENAR_IDENTITY_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "identity") | quote }}
- name: CLAVENAR_UPSTREAM_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "upstream-stub") | quote }}
# JWKS is public verification material on identity's plain public listener,
# even when authority-bearing identity operations move to workload mTLS.
- name: CLAVENAR_PROXY_GRANT_JWKS_URL
  value: "http://{{ $rel }}-identity:8086/jwks.json"
- name: CLAVENAR_PROXY_GRANT_JWKS_REFRESH_SECS
  value: {{ .ctx.Values.services.proxy.grantJwksRefreshSeconds | quote }}
- name: CLAVENAR_PROXY_GRANT_JWKS_MAX_STALENESS_SECS
  value: {{ .ctx.Values.services.proxy.grantJwksMaxStalenessSeconds | quote }}
- name: CLAVENAR_PROXY_GRANT_JWKS_FETCH_TIMEOUT_SECS
  value: {{ .ctx.Values.services.proxy.grantJwksFetchTimeoutSeconds | quote }}
- name: CLAVENAR_PROXY_SERVER_EXECUTION_DB
  value: "/var/lib/clavenar/server-execution.db"
{{- if .ctx.Values.exec.enabled }}
# Execution-gateway in front of any upstream. clavenar-exec runs the
# Claude-Code-built-in-parity tools locally and forwards everything
# else to its CLAVENAR_EXEC_FALLBACK_URL (typically the upstream-stub
# Service). Takes precedence over the bare upstreamStub wiring below.
- name: CLAVENAR_UPSTREAM_URL
  value: "https://{{ $rel }}-exec:{{ .ctx.Values.exec.port }}/mcp"
- name: CLAVENAR_PROXY_EXEC_UPSTREAM_MTLS
  value: "true"
{{- else if .ctx.Values.upstreamStub.enabled }}
# Bundled echo-MCP target. Opt-in via upstreamStub.enabled. While the chart
# emits this target, a duplicate services.proxy.extraEnv entry is rejected.
# Production deploys leave upstreamStub off and may set CLAVENAR_UPSTREAM_URL
# through services.proxy.extraEnv to point at a real MCP server.
- name: CLAVENAR_UPSTREAM_URL
  value: "http://{{ $rel }}-upstream-stub:{{ .ctx.Values.upstreamStub.port }}/mcp"
{{- end }}
{{- if $tlsOn }}
# Outbound mTLS (B7 v1.x+2 sessions 3-6) — service-proxy cert covers
# brain, policy, hil, identity, ledger, and the HIL poll / exact-receipt
# paths. One bundle, five downstream listeners.
- name: CLAVENAR_PROXY_OUTBOUND_CERT_PATH
  value: "{{ $mount }}/service-proxy.crt"
- name: CLAVENAR_PROXY_OUTBOUND_KEY_PATH
  value: "{{ $mount }}/service-proxy.key"
- name: CLAVENAR_PROXY_OUTBOUND_CA_PATH
  value: "{{ $mount }}/ca.crt"
# Identity signing and A2A are a both-or-neither contract: the chart-owned
# Identity URL above must be paired with the exact workload caller identity.
- name: CLAVENAR_PROXY_SPIFFE
  value: "spiffe://{{ .ctx.Values.tlsBundle.spiffeTrustDomain }}/service/proxy"
{{- end }}
{{- end }}
{{- if eq $name "brain" }}
# Brain auxiliary provider operations are a strict chart-owned contract.
# The process validates every value before binding. Exact caller identities
# are single complete SPIFFE URIs; they never inherit the broader inspect
# prefix allowlist below.
- name: CLAVENAR_BRAIN_REQUIRE_AUX_CONTROLS
  value: "true"
- name: CLAVENAR_BRAIN_EXPLAIN_CALLER_SPIFFE
  value: {{ .ctx.Values.services.brain.explainCallerSpiffe | quote }}
- name: CLAVENAR_BRAIN_NARRATE_CALLER_SPIFFE
  value: {{ .ctx.Values.services.brain.narrateCallerSpiffe | quote }}
- name: CLAVENAR_BRAIN_EXPLAIN_RATE_LIMIT_PER_MINUTE
  value: {{ printf "%d" (int .ctx.Values.services.brain.explainRateLimitPerMinute) | quote }}
- name: CLAVENAR_BRAIN_NARRATE_RATE_LIMIT_PER_MINUTE
  value: {{ printf "%d" (int .ctx.Values.services.brain.narrateRateLimitPerMinute) | quote }}
- name: CLAVENAR_BRAIN_AUX_SPEND_BUDGET_MICRO_USD_PER_HOUR
  value: {{ printf "%d" (int .ctx.Values.services.brain.auxSpendBudgetMicroUsdPerHour) | quote }}
- name: CLAVENAR_BRAIN_AUX_TIMEOUT_MILLIS
  value: {{ printf "%d" (int .ctx.Values.services.brain.auxTimeoutMillis) | quote }}
- name: CLAVENAR_BRAIN_AUX_BODY_LIMIT_BYTES
  value: {{ printf "%d" (int .ctx.Values.services.brain.auxBodyLimitBytes) | quote }}
- name: CLAVENAR_BRAIN_HEALTH_ADDR
  value: "0.0.0.0:9081"
{{- if $tlsOn }}
# mTLS receive (B7 v1.x+2 session 3). Bundle mounted → brain binds
# rustls + generated route capabilities on the application port; /health +
# /readyz + /metrics move to the plain-HTTP health port so kubelet
# probes don't need a client cert.
- name: CLAVENAR_BRAIN_TLS_DIR
  value: {{ $mount | quote }}
- name: CLAVENAR_BRAIN_ALLOWED_CALLERS
  value: "spiffe://clavenar.local/service/proxy"
{{- end }}
{{- end }}
{{- if eq $name "ledger" }}
{{- if .ctx.Values.services.ledger.postgres.enabled }}
# Staged Ledger-only PostgreSQL topology. The DSN is secret-backed and the
# process forces verified TLS against this exact projected private CA.
- name: CLAVENAR_LEDGER_BACKEND
  value: "postgres"
- name: CLAVENAR_LEDGER_PG_URL
  valueFrom:
    secretKeyRef:
      name: {{ .ctx.Values.services.ledger.postgres.dsnSecretName }}
      key: {{ .ctx.Values.services.ledger.postgres.dsnSecretKey }}
- name: CLAVENAR_LEDGER_PG_TLS_CA_FILE
  value: "/etc/clavenar/postgres/ca.crt"
{{- else }}
- name: CLAVENAR_LEDGER_BACKEND
  value: "sqlite"
- name: CLAVENAR_LEDGER_DB
  value: "/var/lib/clavenar/ledger.db"
{{- end }}
{{- end }}
{{- if eq $name "policyEngine" }}
# Policy governance state is always written to the exact retained mount. The
# path remains explicit when persistence is disabled for a disposable render.
- name: CLAVENAR_POLICY_DB
  value: "/var/lib/clavenar-policy-engine/policies.db"
- name: CLAVENAR_POLICY_HEALTH_ADDR
  value: "0.0.0.0:9082"
# Policy mining explanations use only Brain's workload-mTLS application
# listener. The workload client additionally pins the exact Brain SPIFFE URI;
# there is no plaintext health-listener fallback.
- name: CLAVENAR_POLICY_ENGINE_BRAIN_URL
  value: "https://{{ $brainHost }}:8081"
{{- if not $tlsOn }}
- name: CLAVENAR_POLICY_EXPECTED_PEER_SPIFFE
  value: "spiffe://clavenar.local/service/identity,spiffe://clavenar.local/service/brain"
{{- end }}
{{- if $tlsOn }}
# mTLS receive (B7 v1.x+2 session 4). Bundle mounted → engine binds
# rustls + SPIFFE-URI allowlist on the application port; /health +
# /readyz + /metrics move to the plain-HTTP health port. Session 5
# adds route-specific generated capabilities for policy management.
- name: CLAVENAR_POLICY_TLS_DIR
  value: {{ $mount | quote }}
{{- end }}
{{- end }}
{{- if eq $name "hil" }}
- name: CLAVENAR_HIL_HEALTH_ADDR
  value: "0.0.0.0:9084"
{{- if $tlsOn }}
# mTLS receive (B7 v1.x+2 session 6). Port 8084 becomes rustls when the
# bundle is mounted. Its application branch additionally enforces the
# generated route capabilities; the four operational routes remain merged on 8084
# outside that route middleware and are also served on
# `services.hil.healthPort` (default 9084), so kubelet
# + Prometheus can use plain HTTP without a client cert.
- name: CLAVENAR_HIL_TLS_DIR
  value: {{ $mount | quote }}
{{- end }}
{{- end }}
{{- if eq $name "identity" }}
# Exact replica contract for the shared durable actor-token replay KV bucket.
# Identity validates every other governed bucket property at runtime and
# returns replay_store_unavailable instead of falling back to local SQLite.
- name: CLAVENAR_IDENTITY_REPLAY_REPLICAS
  value: {{ .ctx.Values.services.identity.replayReplicas | quote }}
{{- if $tlsOn }}
# mTLS receive (B7 v1.x+2 session 6). Dual-listener:
#   * plain HTTP on `services.identity.port` (default 8086) — public
#     subset (`/stats`, `/jwks.json`, `/.well-known/spiffe-bundle`,
#     health). Internal routes are STRIPPED on this port.
#   * mTLS on `services.identity.mtlsPort` (default 8186) — full surface
#     including durable CSR-bound `/svid` (one-use Simulator bootstrap,
#     then exact current-agent-SVID renewal; private keys stay caller-side),
#     `/grant`, `/revoke`, `/sign`, `/actor-token*`,
#     `/agents*`. Generated capabilities gate every internal route.
#
# Service template emits a second port (`name: mtls`) alongside
# `http` when tlsBundle.secretName is set, so the chart-wired
# CLAVENAR_CONSOLE_IDENTITY_URL=https://identity:8186 resolves through the
# stable workload alias without any per-release manifest tweak.
- name: CLAVENAR_IDENTITY_TLS_DIR
  value: {{ $mount | quote }}
- name: CLAVENAR_IDENTITY_MTLS_ADDR
  value: "0.0.0.0:8186"
{{- end }}
{{- end }}
{{- if eq $name "ledger" }}
# Forwarded client addresses are a chart-governed, fail-closed contract. The
# evaluation default leaves enforcement off; production validation below the
# values layer requires true plus the canonical website workload identity.
- name: CLAVENAR_LEDGER_REQUIRE_TRUSTED_PROXY
  value: {{ ternary "true" "false" .ctx.Values.services.ledger.requireTrustedProxy | quote }}
{{- if .ctx.Values.services.ledger.trustedProxySpiffe }}
- name: CLAVENAR_LEDGER_TRUSTED_PROXY_SPIFFE
  value: {{ .ctx.Values.services.ledger.trustedProxySpiffe | quote }}
{{- end }}
{{- if $tlsOn }}
# mTLS receive (B7 v1.x+2 session 5). Bundle mounted → ledger runs
# TWO listeners. Plain HTTP on `port` (default 8083) serves the public
# `/verify`, `/`, `/health`, `/readyz`, and `/metrics` surface (kubelet
# + internal readers reach this without a client cert). mTLS on
# `mtlsPort` (default 8183) serves the full router; the internal write
# + console-only read subset (the exact routes are governed by
# `listeners.yaml`) is SPIFFE-gated by generated route capabilities. The plain
# HTTP router STRIPS those routes so a cluster-
# network attacker cannot bypass mTLS by hitting `port` directly.
# Service template emits a second port (`name: mtls`) alongside
# `http` when tlsBundle.secretName is set so in-cluster clients can
# dial CLAVENAR_CONSOLE_LEDGER_URL=https://ledger:8183 through the stable
# workload alias.
- name: CLAVENAR_LEDGER_TLS_DIR
  value: {{ $mount | quote }}
- name: CLAVENAR_LEDGER_MTLS_ADDR
  value: "0.0.0.0:8183"
{{- if .ctx.Values.ledgerCryptographicVerification.enabled }}
# The verifier uses Ledger's hot-reloaded caller SVID to fetch the exact
# bounded-fresh historical key lineage from Identity. Timestamp proofs are
# independently verified against the pinned chart trust projection.
- name: CLAVENAR_LEDGER_CRYPTOGRAPHIC_VERIFICATION_REQUIRED
  value: "true"
- name: CLAVENAR_LEDGER_REGULATORY_SIGNING_REQUIRED
  value: "true"
- name: CLAVENAR_LEDGER_TSA_REQUIRED
  value: {{ ternary "true" "false" .ctx.Values.ledgerCryptographicVerification.tsaRequired | quote }}
- name: CLAVENAR_LEDGER_TSA_CA_FILE
  value: "/etc/clavenar/tsa/freetsa-ca.pem"
- name: CLAVENAR_LEDGER_TSA_SIGNER_CERT_FILE
  value: "/etc/clavenar/tsa/freetsa-tsa.crt"
- name: CLAVENAR_LEDGER_ANCHOR_INTERVAL_SECS
  value: {{ .ctx.Values.ledgerCryptographicVerification.anchorIntervalSeconds | quote }}
- name: CLAVENAR_LEDGER_ANCHOR_TSA_URL
  value: {{ .ctx.Values.ledgerCryptographicVerification.tsaUrl | quote }}
- name: CLAVENAR_IDENTITY_URL
  value: "https://{{ $identityHost }}:8186"
- name: CLAVENAR_LEDGER_SPIFFE
  value: "spiffe://{{ .ctx.Values.tlsBundle.spiffeTrustDomain }}/service/ledger"
{{- end }}
{{- end }}
{{- end }}
{{- if eq $name "console" }}
# Console ingress trust classes (WP-01.2). The chart derives these
# values instead of accepting extraEnv overrides so the process binds
# exactly the ports governed by listeners.yaml and NetworkPolicy.
- name: CLAVENAR_CONSOLE_AUTH
  value: {{ ternary "operator-mtls" "demo-only" .ctx.Values.services.console.operatorMtls.enabled | quote }}
- name: CLAVENAR_CONSOLE_BIND
  value: "0.0.0.0"
- name: CLAVENAR_CONSOLE_PORT
  value: {{ .ctx.Values.services.console.port | quote }}
- name: CLAVENAR_CONSOLE_DIAGNOSTICS_ADDR
  value: "0.0.0.0:{{ .ctx.Values.services.console.diagnosticsPort }}"
- name: CLAVENAR_CONSOLE_AUTH_RATE_LIMIT_MAX
  value: "10"
- name: CLAVENAR_CONSOLE_AUTH_RATE_LIMIT_WINDOW_SECS
  value: "60"
- name: CLAVENAR_CONSOLE_RELEASE_VERSION
  value: {{ .ctx.Chart.AppVersion | quote }}
{{- if .ctx.Values.services.console.operatorMtls.enabled }}
# TLS terminates inside clavenar-console. The server certificate comes
# from the workload bundle; client trust and the exact identity/role
# registry come from the separately projected public operator Secret.
- name: CLAVENAR_CONSOLE_OPERATOR_TLS_CERT_PATH
  value: "/certs/service-console.crt"
- name: CLAVENAR_CONSOLE_OPERATOR_TLS_KEY_PATH
  value: "/certs/service-console.key"
- name: CLAVENAR_CONSOLE_OPERATOR_CLIENT_CA_PATH
  value: "/operator-trust/ca.crt"
- name: CLAVENAR_CONSOLE_OPERATOR_IDENTITIES_PATH
  value: "/operator-trust/operators.json"
- name: CLAVENAR_CONSOLE_MUTATION_ORIGINS
  value: {{ join "," .ctx.Values.services.console.mutationOrigins | quote }}
{{- if .ctx.Values.services.console.demo.enabled }}
- name: CLAVENAR_CONSOLE_DEMO_ADDR
  value: "0.0.0.0:{{ .ctx.Values.services.console.demoPort }}"
{{- end }}
{{- end }}
# Console → backend hops (B7 v1.x+2 sessions 5-6). All four hops flip
# to https when the bundle is mounted: ledger on :8183 (mTLS listener),
# policy-engine on :8082 (single-port mTLS), hil on :8084 (single-mode
# mTLS), identity on :8186 (mTLS listener).
- name: CLAVENAR_CONSOLE_LEDGER_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ $ledgerHost }}:{{ ternary "8183" "8083" $tlsOn }}"
- name: CLAVENAR_CONSOLE_HIL_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ $hilHost }}:8084"
- name: CLAVENAR_CONSOLE_POLICY_ENGINE_URL
  value: "{{ $policyScheme }}://{{ $policyHost }}:8082"
- name: CLAVENAR_CONSOLE_IDENTITY_URL
  value: "{{ ternary "https" "http" $tlsOn }}://{{ ternary $identityHost (printf "%s-identity" $rel) $tlsOn }}:{{ ternary "8186" "8086" $tlsOn }}"
# Narration and model-snapshot reads never use Brain's plaintext diagnostics
# listener. Without workload TLS these optional operations fail soft.
- name: CLAVENAR_CONSOLE_BRAIN_URL
  value: "https://{{ $brainHost }}:8081"
- name: CLAVENAR_CONSOLE_LEDGER_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "ledger") | quote }}
- name: CLAVENAR_CONSOLE_HIL_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "hil") | quote }}
- name: CLAVENAR_CONSOLE_POLICY_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "policy-engine") | quote }}
- name: CLAVENAR_CONSOLE_IDENTITY_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "identity") | quote }}
- name: CLAVENAR_CONSOLE_BRAIN_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "brain") | quote }}
- name: CLAVENAR_CONSOLE_ASSURANCE_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "assurance") | quote }}
{{- if .ctx.Values.services.console.simulatorReadinessUrl }}
- name: CLAVENAR_CONSOLE_SIMULATOR_READINESS_URL
  value: {{ .ctx.Values.services.console.simulatorReadinessUrl | quote }}
{{- end }}
{{- if and $tlsOn .ctx.Values.services.console.operatorMtls.enabled .ctx.Values.services.assurance.enabled }}
- name: CLAVENAR_ASSURANCE_URL
  value: "https://{{ $assuranceHost }}:8088"
{{- end }}
{{- if $tlsOn }}
# Outbound mTLS — same cert bundle the proxy uses. One
# `service-console` identity authenticates every backend hop.
- name: CLAVENAR_CONSOLE_TLS_DIR
  value: {{ $mount | quote }}
- name: CLAVENAR_CONSOLE_OUTBOUND_CERT_PATH
  value: "{{ $mount }}/service-console.crt"
- name: CLAVENAR_CONSOLE_OUTBOUND_KEY_PATH
  value: "{{ $mount }}/service-console.key"
- name: CLAVENAR_CONSOLE_OUTBOUND_CA_PATH
  value: "{{ $mount }}/ca.crt"
{{- end }}
{{- end }}
{{- if eq $name "assurance" }}
- name: CLAVENAR_ASSURANCE_PROXY_URL
  value: "https://{{ $proxyHost }}:8443/mcp"
- name: CLAVENAR_ASSURANCE_PROXY_READINESS_URL
  value: {{ include "clavenar.dependencyReadinessUrl" (dict "ctx" .ctx "service" "proxy") | quote }}
# Assurance namespaces its NATS URL like deep-review does — mirror the
# helper-computed value into the service-prefixed name.
- name: CLAVENAR_ASSURANCE_NATS_URL
  valueFrom:
    configMapKeyRef:
      name: {{ include "clavenar.fullname" .ctx }}-config
      key: NATS_URL
# The receive-side boundary is chart-governed even when an operator has
# not supplied tlsBundle.secretName yet. In that incomplete posture the
# process fails closed while loading /certs; it never opens 8088 as HTTP.
- name: CLAVENAR_ASSURANCE_ADMIN_PORT
  value: "8088"
- name: CLAVENAR_ASSURANCE_TLS_DIR
  value: {{ $mount | quote }}
- name: CLAVENAR_ASSURANCE_ALLOWED_CALLERS
  value: "spiffe://clavenar.local/service/console"
- name: CLAVENAR_ASSURANCE_DIAGNOSTICS_PORT
  value: "9088"
- name: CLAVENAR_ASSURANCE_FORENSIC_SUBJECT
  value: {{ .ctx.Values.services.assurance.forensicSubject | quote }}
- name: CLAVENAR_ASSURANCE_FORENSIC_STREAM
  value: {{ .ctx.Values.services.assurance.forensicStream | quote }}
- name: CLAVENAR_ASSURANCE_REQUEST_TIMEOUT_SECS
  value: {{ .ctx.Values.services.assurance.requestTimeoutSecs | quote }}
- name: CLAVENAR_ASSURANCE_RUN_TIMEOUT_SECS
  value: {{ .ctx.Values.services.assurance.runTimeoutSecs | quote }}
- name: CLAVENAR_ASSURANCE_PUBLISH_TIMEOUT_SECS
  value: {{ .ctx.Values.services.assurance.publishTimeoutSecs | quote }}
{{- if $tlsOn }}
# NATS and the receive-side control listener use Assurance's one exact
# workload identity. Generic agent credentials never reach the broker.
- name: CLAVENAR_ASSURANCE_CERT_DIR
  value: {{ $mount | quote }}
- name: NATS_TLS_CERT_PATH
  value: "{{ $mount }}/service-assurance.crt"
- name: NATS_TLS_KEY_PATH
  value: "{{ $mount }}/service-assurance.key"
- name: NATS_TLS_CA_PATH
  value: "{{ $mount }}/ca.crt"
{{- end }}
{{- end }}
{{- if eq $name "deepReview" }}
- name: CLAVENAR_DEEP_REVIEW_LEDGER_URL
  value: "http://{{ $rel }}-ledger:8083"
# Deep-review is the only service that namespaces its NATS URL with
# the service prefix — every other clavenar binary reads bare NATS_URL.
# Mirror the helper-computed value (tls:// vs nats://) into the
# service-prefixed name so the bundled/mTLS path works without a
# deep-review code change.
- name: CLAVENAR_DEEP_REVIEW_NATS_URL
  valueFrom:
    configMapKeyRef:
      name: {{ include "clavenar.fullname" .ctx }}-config
      key: NATS_URL
{{- end }}
{{- if eq $name "identity" }}
- name: CLAVENAR_IDENTITY_CA_DIR
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
{{- define "clavenar.metricsAnnotations" -}}
{{- $svcCfg := .svcCfg -}}
{{- $metrics := default dict $svcCfg.metrics -}}
{{- if $metrics.enabled -}}
{{- $port := $svcCfg.port -}}
{{- if $svcCfg.healthPort -}}
{{- $port = $svcCfg.healthPort -}}
{{- end -}}
{{- if $metrics.port -}}{{- $port = $metrics.port -}}{{- end -}}
{{- $path := default "/metrics" $metrics.path }}
prometheus.io/scrape: "true"
prometheus.io/path: {{ $path | quote }}
prometheus.io/port: {{ $port | quote }}
{{- with $metrics.extraAnnotations }}
{{- toYaml . | nindent 0 }}
{{- end }}
{{- end -}}
{{- end -}}

{{/*
Return the in-cluster, plaintext readiness endpoint for one reviewed
dependency. Application diagnostics remain side-effect-free and are exposed
only through ClusterIP ports plus exact NetworkPolicy callers. External NATS,
Vault, and upstream operators must provide the corresponding explicit URL;
bundled infrastructure derives release-local Service DNS.
*/}}
{{- define "clavenar.dependencyReadinessUrl" -}}
{{- $ctx := .ctx -}}
{{- $service := .service -}}
{{- $rel := $ctx.Release.Name -}}
{{- if eq $service "nats" -}}
{{- if $ctx.Values.nats.bundled.enabled -}}
http://{{ $rel }}-nats:8222/healthz?js-enabled-only=true
{{- else -}}
{{ required "nats.readinessUrl is required for dependency startup gates" $ctx.Values.nats.readinessUrl }}
{{- end -}}
{{- else if eq $service "vault" -}}
{{- if $ctx.Values.vault.bundled.enabled -}}
http://{{ $rel }}-vault:8200/v1/sys/health
{{- else -}}
{{ required "vault.readinessUrl is required for dependency startup gates" $ctx.Values.vault.readinessUrl }}
{{- end -}}
{{- else if eq $service "upstream-stub" -}}
{{- if $ctx.Values.upstreamStub.enabled -}}
http://{{ $rel }}-upstream-stub:{{ $ctx.Values.upstreamStub.port }}/readyz
{{- else -}}
{{ required "upstreamStub.readinessUrl is required when the bundled upstream is disabled" $ctx.Values.upstreamStub.readinessUrl }}
{{- end -}}
{{- else if eq $service "identity" -}}
http://{{ $rel }}-identity:{{ $ctx.Values.services.identity.port }}/readyz
{{- else if eq $service "brain" -}}
http://{{ $rel }}-brain:{{ $ctx.Values.services.brain.healthPort }}/readyz
{{- else if eq $service "policy-engine" -}}
http://{{ $rel }}-policy-engine:{{ $ctx.Values.services.policyEngine.healthPort }}/readyz
{{- else if eq $service "ledger" -}}
http://{{ $rel }}-ledger:{{ $ctx.Values.services.ledger.port }}/readyz
{{- else if eq $service "hil" -}}
http://{{ $rel }}-hil:{{ $ctx.Values.services.hil.healthPort }}/readyz
{{- else if eq $service "proxy" -}}
http://{{ $rel }}-proxy:{{ $ctx.Values.services.proxy.healthPort }}/readyz
{{- else if eq $service "assurance" -}}
http://{{ $rel }}-assurance:{{ $ctx.Values.services.assurance.healthPort }}/readyz
{{- else -}}
{{- fail (printf "unknown readiness dependency %s" $service) -}}
{{- end -}}
{{- end -}}

{{/*
Project the public Helm startup graph. One URL is emitted per line for the
bounded BusyBox init gate in services.yaml.
*/}}
{{- define "clavenar.readinessTargets" -}}
{{- $dependencies := dict
      "identity" (list "nats" "vault")
      "brain" (list "identity")
      "policyEngine" (list "nats" "identity")
      "ledger" (list "nats" "identity")
      "hil" (list "nats" "identity")
      "deepReview" (list "nats")
      "proxy" (list "nats" "vault" "brain" "policy-engine" "hil" "ledger" "identity" "upstream-stub")
      "assurance" (list "nats" "proxy")
      "console" (list "brain" "policy-engine" "ledger" "hil" "identity" "assurance") -}}
{{- range $dependency := default (list) (get $dependencies .service) }}
{{ include "clavenar.dependencyReadinessUrl" (dict "ctx" $.ctx "service" $dependency) }}
{{- end }}
{{- end -}}

{{/* `kind` is "liveness"/"readiness". Port fallback chain:
.probes.port → .healthPort → .port. Same rationale as the metrics
helper above — kubelet probes don't carry a client cert, so they have
to land on the plain-HTTP health port under mTLS mode. */}}
{{- define "clavenar.probe" -}}
{{- $ctx := .ctx -}}
{{- $svcCfg := .svcCfg -}}
{{- $kind := .kind -}}
{{- $defaults := index $ctx.Values.probeDefaults $kind -}}
{{- $probePort := $svcCfg.port -}}
{{- if $svcCfg.healthPort -}}
{{- $probePort = $svcCfg.healthPort -}}
{{- end -}}
{{- if $svcCfg.probes.port -}}{{- $probePort = $svcCfg.probes.port -}}{{- end -}}
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

{{/* Effective Prometheus ingress port. Must stay in lockstep with the
metrics annotation helper above. */}}
{{- define "clavenar.metricsPort" -}}
{{- $port := .svcCfg.port -}}
{{- if .svcCfg.healthPort -}}
{{- $port = .svcCfg.healthPort -}}
{{- end -}}
{{- if (default dict .svcCfg.metrics).port -}}
{{- $port = .svcCfg.metrics.port -}}
{{- end -}}
{{- $port -}}
{{- end -}}
