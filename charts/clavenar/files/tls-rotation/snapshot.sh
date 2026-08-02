#!/bin/sh
# Snapshot and structurally validate the current auto-mint Secret without
# printing any certificate or private-key bytes.

set -eu
umask 077

: "${TLS_SECRET_NAME:?must be set}"
: "${POD_NAMESPACE:?must be set}"
: "${EXPECTED_SAN_SCHEME:?must be set}"

STATE_DIR="${STATE_DIR:-/state}"
mkdir -p "$STATE_DIR/previous"

die() {
    echo "tls rotation snapshot: $*" >&2
    exit 1
}

if ! kubectl -n "$POD_NAMESPACE" get secret "$TLS_SECRET_NAME" >/dev/null 2>&1; then
    printf 'absent\n' > "$STATE_DIR/existence"
    exit 0
fi

printf 'present\n' > "$STATE_DIR/existence"
kubectl -n "$POD_NAMESPACE" get secret "$TLS_SECRET_NAME" -o json \
    > "$STATE_DIR/secret.json"

jq -e '.type == "Opaque" and (.data | type == "object")' \
    "$STATE_DIR/secret.json" >/dev/null \
    || die "existing Secret must be one complete Opaque bundle"

# The prior hook used two backslashes in a kubectl JSONPath expression, so it
# never read this valid qualified label and silently regenerated trust. Read
# the exact map key with jq; do not reintroduce JSONPath escaping here.
existing_scheme="$(jq -r '.metadata.labels["clavenar.com/san-scheme"] // ""' \
    "$STATE_DIR/secret.json")"
[ "$existing_scheme" = "$EXPECTED_SAN_SCHEME" ] \
    || die "existing Secret has a missing or unsupported SAN-scheme label"

jq -r '.data | keys[]' "$STATE_DIR/secret.json" | sort \
    > "$STATE_DIR/actual-keys"
jq -r '.data | keys[] | select(startswith("service-") and endswith(".crt"))
    | sub("^service-"; "") | sub("\\.crt$"; "")' \
    "$STATE_DIR/secret.json" | sort > "$STATE_DIR/previous-services-lines"

[ -s "$STATE_DIR/previous-services-lines" ] \
    || die "existing Secret has no workload identity membership"
while IFS= read -r service; do
    printf '%s\n' "$service" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' \
        || die "existing Secret has a noncanonical workload identity name"
done < "$STATE_DIR/previous-services-lines"

previous_services="$(tr '\n' ' ' < "$STATE_DIR/previous-services-lines" | sed 's/ $//')"
printf '%s\n' "$previous_services" > "$STATE_DIR/previous-services"
previous_membership_sha="sha256:$(printf '%s\n' "$previous_services" \
    | sha256sum | cut -d' ' -f1)"
printf '%s\n' "$previous_membership_sha" > "$STATE_DIR/previous-membership-sha"

{
    printf '%s\n' ca.crt ca.key client.crt client.key server.crt server.key
    while IFS= read -r service; do
        printf 'service-%s.crt\nservice-%s.key\n' "$service" "$service"
    done < "$STATE_DIR/previous-services-lines"
} | sort > "$STATE_DIR/expected-keys"

[ "$(cat "$STATE_DIR/actual-keys")" = "$(cat "$STATE_DIR/expected-keys")" ] \
    || die "existing Secret contains missing or foreign bundle members"

while IFS= read -r key; do
    jq -er --arg key "$key" '.data[$key]' "$STATE_DIR/secret.json" \
        | base64 -d > "$STATE_DIR/previous/$key" \
        || die "existing Secret contains invalid encoded bundle data"
done < "$STATE_DIR/expected-keys"

annotation() {
    jq -r --arg key "$1" '.metadata.annotations[$key] // ""' \
        "$STATE_DIR/secret.json"
}

state_version="$(annotation clavenar.com/tls-state-version)"
state="$(annotation clavenar.com/tls-state)"
generation="$(annotation clavenar.com/tls-generation)"
recorded_membership_sha="$(annotation clavenar.com/tls-membership-sha256)"
recorded_ca_sha="$(annotation clavenar.com/tls-ca-sha256)"
recorded_bundle_sha="$(annotation clavenar.com/tls-bundle-sha256)"
recorded_trust_sha="$(annotation clavenar.com/tls-trust-sha256)"
previous_ca_sha="$(annotation clavenar.com/tls-previous-ca-sha256)"
overlap_deadline="$(annotation clavenar.com/tls-overlap-deadline-epoch)"
rotation_reason="$(annotation clavenar.com/tls-rotation-reason)"
readiness="$(annotation clavenar.com/tls-readiness)"
ready_generation="$(annotation clavenar.com/tls-ready-generation)"
rollback_generation="$(annotation clavenar.com/tls-rollback-generation)"
rollback_available="$(annotation clavenar.com/tls-rollback-available)"

if [ -z "$state_version" ]; then
    if [ -n "$state" ] || [ -n "$generation" ] \
        || [ -n "$recorded_membership_sha" ] \
        || [ -n "$recorded_ca_sha" ] || [ -n "$recorded_bundle_sha" ] \
        || [ -n "$recorded_trust_sha" ] || [ -n "$previous_ca_sha" ] \
        || [ -n "$overlap_deadline" ] || [ -n "$rotation_reason" ] \
        || [ -n "$readiness" ] || [ -n "$ready_generation" ] \
        || [ -n "$rollback_generation" ] \
        || [ -n "$rollback_available" ]; then
        die "existing Secret has partial trust-state metadata"
    fi
    printf 'legacy\n' > "$STATE_DIR/metadata-shape"
else
    if [ "$state_version" != "1" ] || [ "$state" != "stable" ]; then
        die "existing Secret is not in canonical stable state"
    fi
    printf '%s\n' "$generation" \
        | grep -Eq '^[a-z0-9]([-a-z0-9.]{0,61}[a-z0-9])?$' \
        || die "existing Secret has an invalid generation"
    [ "$recorded_membership_sha" = "$previous_membership_sha" ] \
        || die "existing Secret membership metadata does not match its keys"
    printf '%s\n' "$recorded_ca_sha" "$recorded_bundle_sha" \
        "$recorded_trust_sha" \
        | grep -Eqv '^sha256:[0-9a-f]{64}$' \
        && die "existing Secret has an invalid recorded digest"
    printf '%s\n' "$previous_ca_sha" \
        | grep -Eq '^(none|sha256:[0-9a-f]{64})$' \
        || die "existing Secret has invalid prior-CA lineage"
    [ "$overlap_deadline" = 0 ] \
        || die "stable Secret has a live overlap deadline"
    printf '%s\n' "$rotation_reason" | grep -Eq '^(none|membership|expiry|dns)$' \
        || die "stable Secret has an invalid rotation reason"
    [ "$readiness" = ready ] && [ "$ready_generation" = "$generation" ] \
        || die "stable Secret lacks exact generation readiness"
    printf '%s\n' "$rollback_generation" \
        | grep -Eq '^(none|[a-z0-9]([-a-z0-9.]{0,61}[a-z0-9])?)$' \
        || die "stable Secret has an invalid rollback generation"
    [ "$rollback_available" = false ] \
        || die "stable Secret ambiguously advertises rollback availability"
    printf 'canonical\n' > "$STATE_DIR/metadata-shape"
    printf '%s\n' "$generation" > "$STATE_DIR/previous-generation"
    printf '%s\n' "$recorded_ca_sha" > "$STATE_DIR/recorded-ca-sha"
    printf '%s\n' "$recorded_bundle_sha" > "$STATE_DIR/recorded-bundle-sha"
    printf '%s\n' "$recorded_trust_sha" > "$STATE_DIR/recorded-trust-sha"
fi

echo "Existing TLS Secret snapshot validated"
