#!/bin/sh
# Validate the current auto-mint bundle and, only for an absent install or an
# explicit governed rotation, mint one complete fresh candidate.

set -eu
umask 077

: "${SPIFFE_TRUST_DOMAIN:?must be set}"
: "${BUNDLE_SERVICES:?must be set}"
: "${RELEASE_NAME:?must be set}"
: "${PROXY_SERVER_ADDITIONAL_DNS_NAMES?must be set}"
: "${CONSOLE_ADDITIONAL_DNS_NAMES?must be set}"
: "${IDENTITY_ADDITIONAL_DNS_NAMES?must be set}"
: "${NATS_ADDITIONAL_DNS_NAMES?must be set}"
: "${TLS_ROTATION_OPERATION:?must be set}"
: "${TLS_ROTATION_GENERATION:?must be set}"
: "${TLS_ROTATION_REASON:?must be set}"
: "${EXPIRY_WINDOW_SECONDS:?must be set}"
CERT_VALIDITY_DAYS="${CERT_VALIDITY_DAYS:-365}"

STATE_DIR="${STATE_DIR:-/state}"
WORK_DIR="${WORK_DIR:-/work}"
PREVIOUS_DIR="$STATE_DIR/previous"
NEW_DIR="$WORK_DIR/new"

die() {
    echo "tls rotation mint: $*" >&2
    exit 1
}

printf '%s\n' "$TLS_ROTATION_GENERATION" \
    | grep -Eq '^[a-z0-9]([-a-z0-9.]{0,61}[a-z0-9])?$' \
    || die "generation is not canonical"
printf '%s\n' "$BUNDLE_SERVICES" \
    | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*( [a-z0-9]+(-[a-z0-9]+)*)*$' \
    || die "bundle membership is not canonical"
canonical_services="$(printf '%s\n' "$BUNDLE_SERVICES" | tr ' ' '\n' \
    | sort -u | tr '\n' ' ' | sed 's/ $//')"
[ "$(printf '%s\n' "$BUNDLE_SERVICES" | tr ' ' '\n' | wc -l)" \
    -eq "$(printf '%s\n' "$canonical_services" | tr ' ' '\n' | wc -l)" ] \
    || die "bundle membership contains duplicates"
BUNDLE_SERVICES="$canonical_services"
printf '%s\n' "$CERT_VALIDITY_DAYS" | grep -Eq '^[0-9]+$' \
    || die "certificate validity must be an integer day count"
[ "$CERT_VALIDITY_DAYS" -ge 1 ] && [ "$CERT_VALIDITY_DAYS" -le 3650 ] \
    || die "certificate validity must be between 1 and 3650 days"

validate_dns_names() {
    contract="$1"
    names="$2"
    [ -z "$names" ] || printf '%s\n' "$names" \
        | grep -Eq '^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?(\.[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?)*( [a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?(\.[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?)*)*$' \
        || die "$contract contains a noncanonical DNS name"
    [ -z "$names" ] && return
    [ "$(printf '%s\n' "$names" | tr ' ' '\n' | wc -l)" \
        -eq "$(printf '%s\n' "$names" | tr ' ' '\n' | sort -u | wc -l)" ] \
        || die "$contract contains duplicate DNS names"
}

dns_san_suffix() {
    names="$1"
    suffix=""
    for dns_name in $names; do
        suffix="${suffix},DNS:${dns_name}"
    done
    printf '%s' "$suffix"
}

validate_dns_names "Proxy server DNS contract" "$PROXY_SERVER_ADDITIONAL_DNS_NAMES"
validate_dns_names "Console DNS contract" "$CONSOLE_ADDITIONAL_DNS_NAMES"
validate_dns_names "Identity DNS contract" "$IDENTITY_ADDITIONAL_DNS_NAMES"
validate_dns_names "NATS DNS contract" "$NATS_ADDITIONAL_DNS_NAMES"
proxy_server_dns_suffix="$(dns_san_suffix "$PROXY_SERVER_ADDITIONAL_DNS_NAMES")"
console_dns_suffix="$(dns_san_suffix "$CONSOLE_ADDITIONAL_DNS_NAMES")"
identity_dns_suffix="$(dns_san_suffix "$IDENTITY_ADDITIONAL_DNS_NAMES")"
nats_dns_suffix="$(dns_san_suffix "$NATS_ADDITIONAL_DNS_NAMES")"

membership_sha() {
    printf 'sha256:%s' "$(printf '%s\n' "$1" | sha256sum | cut -d' ' -f1)"
}

certificate_public_digest() {
    openssl x509 -in "$1" -pubkey -noout 2>/dev/null \
        | openssl pkey -pubin -outform DER 2>/dev/null \
        | sha256sum | cut -d' ' -f1
}

private_public_digest() {
    openssl pkey -in "$1" -pubout -outform DER 2>/dev/null \
        | sha256sum | cut -d' ' -f1
}

certificate_digest() {
    printf 'sha256:%s' "$(openssl x509 -in "$1" -outform DER 2>/dev/null \
        | sha256sum | cut -d' ' -f1)"
}

bundle_digest() {
    directory="$1"
    (
        cd "$directory"
        find . -mindepth 1 -maxdepth 1 -type f \
            | sed 's#^./##' | sort \
            | while IFS= read -r name; do
                printf '%s %s\n' "$name" "$(sha256sum "$name" | cut -d' ' -f1)"
            done
    ) | sha256sum | awk '{printf "sha256:%s", $1}'
}

require_pair() {
    certificate="$1"
    private_key="$2"
    ca="$3"
    if [ ! -s "$certificate" ] || [ ! -s "$private_key" ]; then
        die "bundle has a missing certificate or private key"
    fi
    openssl verify -CAfile "$ca" "$certificate" >/dev/null 2>&1 \
        || die "bundle has a certificate outside its active CA"
    [ "$(certificate_public_digest "$certificate")" \
        = "$(private_public_digest "$private_key")" ] \
        || die "bundle has a certificate/private-key mismatch"
}

validate_bundle() {
    directory="$1"
    services="$2"
    validation_mode="${3:-target}"
    ca="$directory/ca.crt"
    if [ ! -s "$ca" ] || [ ! -s "$directory/ca.key" ]; then
        die "bundle has no complete CA pair"
    fi
    [ "$(grep -c '^-----BEGIN CERTIFICATE-----$' "$ca")" -eq 1 ] \
        || die "stable bundle trust must contain exactly one CA certificate"
    openssl verify -CAfile "$ca" "$ca" >/dev/null 2>&1 \
        || die "bundle CA certificate is invalid"
    [ "$(certificate_public_digest "$ca")" \
        = "$(private_public_digest "$directory/ca.key")" ] \
        || die "bundle CA signer does not match its certificate"
    openssl x509 -checkend 0 -noout -in "$ca" >/dev/null 2>&1 \
        || die "bundle CA is expired"

    require_pair "$directory/server.crt" "$directory/server.key" "$ca"
    server_sans="$(openssl x509 -in "$directory/server.crt" \
        -noout -ext subjectAltName 2>/dev/null | sed -n '2,$p' | tr -d '[:space:]')"
    base_server_sans="DNS:localhost,DNS:proxy,DNS:proxy.clavenar.local"
    expected_server_sans="${base_server_sans}${proxy_server_dns_suffix}"
    if [ "$validation_mode" = active-dns ]; then
        case "$server_sans" in
            "$base_server_sans") ;;
            "$base_server_sans",DNS:*)
                active_dns_names="$(printf '%s\n' "${server_sans#"$base_server_sans",DNS:}" | sed 's/,DNS:/ /g')"
                validate_dns_names "active Proxy server DNS contract" "$active_dns_names"
                ;;
            *) die "active Proxy server certificate SANs are invalid" ;;
        esac
        [ "$server_sans" = "$expected_server_sans" ] || san_contract_drift=1
    else
        [ "$server_sans" = "$expected_server_sans" ] \
            || die "Proxy server certificate SANs are not exact"
    fi
    require_pair "$directory/client.crt" "$directory/client.key" "$ca"

    expected_files="ca.crt ca.key client.crt client.key server.crt server.key"
    private_digests="$(private_public_digest "$directory/ca.key")
$(private_public_digest "$directory/client.key")
$(private_public_digest "$directory/server.key")"
    for service in $services; do
        certificate="$directory/service-${service}.crt"
        private_key="$directory/service-${service}.key"
        require_pair "$certificate" "$private_key" "$ca"
        actual_sans="$(openssl x509 -in "$certificate" -noout \
            -ext subjectAltName 2>/dev/null | sed -n '2,$p' | tr -d '[:space:]')"
        base_sans="URI:spiffe://${SPIFFE_TRUST_DOMAIN}/service/${service},DNS:${service},DNS:${RELEASE_NAME}-${service},DNS:localhost"
        expected_sans="$base_sans"
        if [ "$service" = console ]; then
            expected_sans="${expected_sans}${console_dns_suffix}"
        elif [ "$service" = identity ]; then
            expected_sans="${expected_sans}${identity_dns_suffix}"
        elif [ "$service" = nats ]; then
            expected_sans="${expected_sans}${nats_dns_suffix}"
        fi
        if [ "$validation_mode" = active-dns ]; then
            case "$actual_sans" in
                "$base_sans") ;;
                "$base_sans",DNS:*)
                    active_dns_names="$(printf '%s\n' "${actual_sans#"$base_sans",DNS:}" | sed 's/,DNS:/ /g')"
                    validate_dns_names "active workload DNS contract" "$active_dns_names"
                    ;;
                *) die "active workload certificate SANs are invalid" ;;
            esac
            [ "$actual_sans" = "$expected_sans" ] || san_contract_drift=1
        else
            [ "$actual_sans" = "$expected_sans" ] \
                || die "workload certificate SANs are not exact"
        fi
        digest="$(private_public_digest "$private_key")"
        printf '%s\n' "$private_digests" | grep -Fqx "$digest" \
            && die "bundle reuses a private identity"
        private_digests="${private_digests}
${digest}"
        expected_files="${expected_files} service-${service}.crt service-${service}.key"
    done
    actual_files="$(find "$directory" -mindepth 1 -maxdepth 1 -type f \
        | sed 's#.*/##' | sort | tr '\n' ' ' | sed 's/ $//')"
    # Word splitting is intentional: expected_files is a validated,
    # space-separated inventory assembled only from canonical service names.
    # shellcheck disable=SC2086
    canonical_expected="$(printf '%s\n' $expected_files | sort \
        | tr '\n' ' ' | sed 's/ $//')"
    [ "$actual_files" = "$canonical_expected" ] \
        || die "bundle contains missing or foreign files"
    for file in "$directory"/*; do
        [ "$(stat -c '%a' "$file")" = 600 ] \
            || die "staged bundle members must be owner-readable only"
    done
}

generate_bundle() {
    directory="$1"
    services="$2"
    [ ! -e "$directory" ] || die "candidate output already exists"
    mkdir "$directory"
    cd "$directory"
    openssl genrsa -out ca.key 2048 2>/dev/null
    openssl req -new -x509 -days "$CERT_VALIDITY_DAYS" -key ca.key -out ca.crt \
        -subj "/CN=AgentClavenarCA" 2>/dev/null
    openssl genrsa -out server.key 2048 2>/dev/null
    openssl req -new -key server.key -out server.csr \
        -subj "/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,DNS:proxy,DNS:proxy.clavenar.local${proxy_server_dns_suffix}" \
        2>/dev/null
    openssl x509 -req -days "$CERT_VALIDITY_DAYS" -in server.csr -CA ca.crt -CAkey ca.key \
        -CAcreateserial -copy_extensions copy -out server.crt 2>/dev/null
    openssl genrsa -out client.key 2048 2>/dev/null
    openssl req -new -key client.key -out client.csr \
        -subj "/CN=agent-001" 2>/dev/null
    openssl x509 -req -days "$CERT_VALIDITY_DAYS" -in client.csr -CA ca.crt -CAkey ca.key \
        -CAcreateserial -out client.crt 2>/dev/null
    for service in $services; do
        workload_dns_suffix=""
        if [ "$service" = console ]; then
            workload_dns_suffix="$console_dns_suffix"
        elif [ "$service" = identity ]; then
            workload_dns_suffix="$identity_dns_suffix"
        elif [ "$service" = nats ]; then
            workload_dns_suffix="$nats_dns_suffix"
        fi
        openssl genrsa -out "service-${service}.key" 2048 2>/dev/null
        openssl req -new -key "service-${service}.key" \
            -out "service-${service}.csr" -subj "/CN=clavenar-${service}" \
            -addext "subjectAltName=URI:spiffe://${SPIFFE_TRUST_DOMAIN}/service/${service},DNS:${service},DNS:${RELEASE_NAME}-${service},DNS:localhost${workload_dns_suffix}" \
            2>/dev/null
        openssl x509 -req -days "$CERT_VALIDITY_DAYS" -in "service-${service}.csr" \
            -CA ca.crt -CAkey ca.key -CAcreateserial -copy_extensions copy \
            -out "service-${service}.crt" 2>/dev/null
    done
    rm -f ./*.csr ca.srl
    cd "$WORK_DIR"
}

new_membership_sha="$(membership_sha "$BUNDLE_SERVICES")"
printf '%s\n' "$new_membership_sha" > "$STATE_DIR/new-membership-sha"

if [ "$(cat "$STATE_DIR/existence")" = present ]; then
    previous_services="$(cat "$STATE_DIR/previous-services")"
    san_contract_drift=0
    if [ "$TLS_ROTATION_OPERATION" = rotate ] \
        && [ "$TLS_ROTATION_REASON" = dns ]; then
        validate_bundle "$PREVIOUS_DIR" "$previous_services" active-dns
    else
        validate_bundle "$PREVIOUS_DIR" "$previous_services"
    fi
    previous_ca_sha="$(certificate_digest "$PREVIOUS_DIR/ca.crt")"
    previous_bundle_sha="$(bundle_digest "$PREVIOUS_DIR")"
    printf '%s\n' "$previous_ca_sha" > "$STATE_DIR/previous-ca-sha"
    printf '%s\n' "$previous_bundle_sha" > "$STATE_DIR/previous-bundle-sha"
    if [ "$(cat "$STATE_DIR/metadata-shape")" = canonical ]; then
        [ "$(cat "$STATE_DIR/recorded-ca-sha")" = "$previous_ca_sha" ] \
            || die "recorded CA digest does not match existing bytes"
        [ "$(cat "$STATE_DIR/recorded-bundle-sha")" = "$previous_bundle_sha" ] \
            || die "recorded bundle digest does not match existing bytes"
        previous_trust_sha="sha256:$(sha256sum "$PREVIOUS_DIR/ca.crt" | cut -d' ' -f1)"
        [ "$(cat "$STATE_DIR/recorded-trust-sha")" = "$previous_trust_sha" ] \
            || die "recorded trust digest does not match existing bytes"
    fi
fi

case "$TLS_ROTATION_OPERATION" in
    reconcile)
        [ "$TLS_ROTATION_REASON" = none ] \
            || die "reconcile requires reason none"
        if [ "$(cat "$STATE_DIR/existence")" = present ]; then
            if [ "$(cat "$STATE_DIR/metadata-shape")" = canonical ]; then
                [ "$(cat "$STATE_DIR/previous-generation")" \
                    = "$TLS_ROTATION_GENERATION" ] \
                    || die "generation changes require an explicit rotation"
            fi
            [ "$(cat "$STATE_DIR/previous-membership-sha")" \
                = "$new_membership_sha" ] \
                || die "membership changes require an explicit rotation"
            echo "Existing TLS bundle validated for exact no-op preservation"
            exit 0
        fi
        generate_bundle "$NEW_DIR" "$BUNDLE_SERVICES"
        ;;
    rotate)
        [ "$(cat "$STATE_DIR/existence")" = present ] \
            || die "rotation requires an existing stable bundle"
        [ "$(cat "$STATE_DIR/metadata-shape")" = canonical ] \
            || die "migrate legacy state with reconcile before rotation"
        [ "$(cat "$STATE_DIR/previous-generation")" \
            != "$TLS_ROTATION_GENERATION" ] \
            || die "rotation generation must advance"
        case "$TLS_ROTATION_REASON" in
            membership)
                [ "$(cat "$STATE_DIR/previous-membership-sha")" \
                    != "$new_membership_sha" ] \
                    || die "membership rotation requires changed membership"
                for service in $(cat "$STATE_DIR/previous-services"); do
                    printf '%s\n' "$BUNDLE_SERVICES" | tr ' ' '\n' \
                        | grep -Fqx "$service" \
                        || die "membership rotation may add identities but cannot remove a live identity"
                done
                ;;
            expiry)
                [ "$(cat "$STATE_DIR/previous-membership-sha")" \
                    = "$new_membership_sha" ] \
                    || die "expiry rotation cannot also change membership"
                expiry_due=0
                for certificate in "$PREVIOUS_DIR"/*.crt; do
                    if ! openssl x509 -checkend "$EXPIRY_WINDOW_SECONDS" \
                        -noout -in "$certificate" >/dev/null 2>&1; then
                        expiry_due=1
                    fi
                done
                if [ "$expiry_due" -eq 0 ]; then
                    die "expiry rotation is outside the configured renewal window"
                fi
                ;;
            dns)
                [ "$(cat "$STATE_DIR/previous-membership-sha")" \
                    = "$new_membership_sha" ] \
                    || die "DNS rotation cannot also change membership"
                [ "$san_contract_drift" -eq 1 ] \
                    || die "DNS rotation requires an actual SAN contract change"
                ;;
            *) die "rotation reason must be expiry, membership, or dns" ;;
        esac
        generate_bundle "$NEW_DIR" "$BUNDLE_SERVICES"
        ;;
    *) die "operation must be reconcile or rotate" ;;
esac

validate_bundle "$NEW_DIR" "$BUNDLE_SERVICES"
new_ca_sha="$(certificate_digest "$NEW_DIR/ca.crt")"
new_bundle_sha="$(bundle_digest "$NEW_DIR")"
printf '%s\n' "$new_ca_sha" > "$STATE_DIR/new-ca-sha"
printf '%s\n' "$new_bundle_sha" > "$STATE_DIR/new-bundle-sha"

if [ "$(cat "$STATE_DIR/existence")" = present ]; then
    [ "$new_ca_sha" != "$(cat "$STATE_DIR/previous-ca-sha")" ] \
        || die "fresh candidate reused the active CA"
    old_private_digests="$(find "$PREVIOUS_DIR" -maxdepth 1 -type f \
        -name '*.key' -exec sh -c '
            for key do
                openssl pkey -in "$key" -pubout -outform DER 2>/dev/null \
                    | sha256sum | cut -d" " -f1
            done
        ' sh {} +)"
    find "$NEW_DIR" -maxdepth 1 -type f -name '*.key' | sort \
        > "$STATE_DIR/new-private-keys"
    while IFS= read -r key; do
        new_digest="$(private_public_digest "$key")"
        printf '%s\n' "$old_private_digests" | grep -Fqx "$new_digest" \
            && die "fresh candidate reused a superseded private identity"
    done < "$STATE_DIR/new-private-keys"
    cat "$PREVIOUS_DIR/ca.crt" "$NEW_DIR/ca.crt" > "$STATE_DIR/dual-ca.crt"
fi

echo "Fresh TLS bundle candidate validated"
