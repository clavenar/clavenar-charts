#!/bin/sh
# Apply an initialized bundle or perform one guarded dual-trust transaction.
# The target Secret contains exactly one signer/leaf generation at every phase;
# the prior private bundle exists only in this hook's memory-backed emptyDir.

set -eu
umask 077

: "${TLS_SECRET_NAME:?must be set}"
: "${POD_NAMESPACE:?must be set}"
: "${EXPECTED_SAN_SCHEME:?must be set}"
: "${BUNDLE_SERVICES:?must be set}"
: "${RELEASE_NAME:?must be set}"
: "${TLS_ROTATION_OPERATION:?must be set}"
: "${TLS_ROTATION_GENERATION:?must be set}"
: "${TLS_ROTATION_REASON:?must be set}"
: "${OVERLAP_SECONDS:?must be set}"
: "${ROLLOUT_TIMEOUT_SECONDS:?must be set}"

STATE_DIR="${STATE_DIR:-/state}"
WORK_DIR="${WORK_DIR:-/work}"
PREVIOUS_DIR="$STATE_DIR/previous"
NEW_DIR="$WORK_DIR/new"
rollback_enabled=0
rotation_deadline=0
rollback_generation=none

die() {
    echo "tls rotation apply: $*" >&2
    exit 1
}

annotate_secret() {
    generation="$1"
    state="$2"
    membership_sha="$3"
    ca_sha="$4"
    bundle_sha="$5"
    trust_sha="$6"
    previous_ca_sha="$7"
    deadline="$8"
    readiness="$9"
    shift 9
    ready_generation="$1"
    rollback_available="$2"
    kubectl -n "$POD_NAMESPACE" label --overwrite secret "$TLS_SECRET_NAME" \
        "clavenar.com/san-scheme=${EXPECTED_SAN_SCHEME}" >/dev/null
    kubectl -n "$POD_NAMESPACE" annotate --overwrite secret "$TLS_SECRET_NAME" \
        "clavenar.com/tls-state-version=1" \
        "clavenar.com/tls-state=${state}" \
        "clavenar.com/tls-generation=${generation}" \
        "clavenar.com/tls-membership-sha256=${membership_sha}" \
        "clavenar.com/tls-ca-sha256=${ca_sha}" \
        "clavenar.com/tls-bundle-sha256=${bundle_sha}" \
        "clavenar.com/tls-trust-sha256=${trust_sha}" \
        "clavenar.com/tls-previous-ca-sha256=${previous_ca_sha}" \
        "clavenar.com/tls-overlap-deadline-epoch=${deadline}" \
        "clavenar.com/tls-rotation-reason=${TLS_ROTATION_REASON}" \
        "clavenar.com/tls-readiness=${readiness}" \
        "clavenar.com/tls-ready-generation=${ready_generation}" \
        "clavenar.com/tls-rollback-generation=${rollback_generation}" \
        "clavenar.com/tls-rollback-available=${rollback_available}" >/dev/null
}

write_bundle() {
    directory="$1"
    ca_file="$2"
    services="$3"
    generation="$4"
    state="$5"
    membership_sha="$6"
    ca_sha="$7"
    bundle_sha="$8"
    previous_ca_sha="$9"
    shift 9
    deadline="$1"

    set -- \
        "--from-file=ca.crt=${ca_file}" \
        "--from-file=ca.key=${directory}/ca.key" \
        "--from-file=server.crt=${directory}/server.crt" \
        "--from-file=server.key=${directory}/server.key" \
        "--from-file=client.crt=${directory}/client.crt" \
        "--from-file=client.key=${directory}/client.key"
    for service in $services; do
        set -- "$@" \
            "--from-file=service-${service}.crt=${directory}/service-${service}.crt" \
            "--from-file=service-${service}.key=${directory}/service-${service}.key"
    done
    kubectl -n "$POD_NAMESPACE" create secret generic "$TLS_SECRET_NAME" \
        "$@" --dry-run=client -o yaml \
        | kubectl -n "$POD_NAMESPACE" apply -f - >/dev/null
    trust_sha="sha256:$(sha256sum "$ca_file" | cut -d' ' -f1)"
    rollback_available=false
    [ "$rollback_enabled" -eq 1 ] && rollback_available=true
    annotate_secret "$generation" "$state" "$membership_sha" "$ca_sha" \
        "$bundle_sha" "$trust_sha" "$previous_ca_sha" "$deadline" \
        pending none "$rollback_available"
}

rollout_digest() {
    bundle_sha="$1"
    ca_file="$2"
    trust_sha="sha256:$(sha256sum "$ca_file" | cut -d' ' -f1)"
    printf 'sha256:%s' "$(printf '%s\n%s\n' "$bundle_sha" "$trust_sha" \
        | sha256sum | cut -d' ' -f1)"
}

record_ready() {
    token="$1"
    kubectl -n "$POD_NAMESPACE" annotate --overwrite secret "$TLS_SECRET_NAME" \
        "clavenar.com/tls-readiness=ready" \
        "clavenar.com/tls-ready-generation=${token}" >/dev/null
}

ensure_overlap_open() {
    [ "$(date +%s)" -lt "$rotation_deadline" ] \
        || die "dual-trust overlap deadline expired before rollout completed"
}

rollout_generation() {
    token="$1"
    digest="$2"
    rollout_status=0
    for service in ${ROLLOUT_DEPLOYMENTS:-}; do
        deployment="${RELEASE_NAME}-${service}"
        if kubectl -n "$POD_NAMESPACE" get deployment "$deployment" \
            >/dev/null 2>&1; then
            patch="{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"clavenar.io/tls-generation\":\"${token}\",\"clavenar.io/tls-secret-digest\":\"${digest}\"}}}}}"
            kubectl -n "$POD_NAMESPACE" patch deployment "$deployment" \
                --type merge -p "$patch" >/dev/null || rollout_status=1
            kubectl -n "$POD_NAMESPACE" rollout status deployment "$deployment" \
                "--timeout=${ROLLOUT_TIMEOUT_SECONDS}s" >/dev/null \
                || rollout_status=1
        fi
    done
    for service in ${ROLLOUT_STATEFULSETS:-}; do
        statefulset="${RELEASE_NAME}-${service}"
        if kubectl -n "$POD_NAMESPACE" get statefulset "$statefulset" \
            >/dev/null 2>&1; then
            patch="{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"clavenar.io/tls-generation\":\"${token}\",\"clavenar.io/tls-secret-digest\":\"${digest}\"}}}}}"
            kubectl -n "$POD_NAMESPACE" patch statefulset "$statefulset" \
                --type merge -p "$patch" >/dev/null || rollout_status=1
            kubectl -n "$POD_NAMESPACE" rollout status statefulset "$statefulset" \
                "--timeout=${ROLLOUT_TIMEOUT_SECONDS}s" >/dev/null \
                || rollout_status=1
        fi
    done
    [ "$rollout_status" -eq 0 ] || return 1
    record_ready "$token"
}

rollback_rotation() {
    set +e
    echo "Rotation did not retire cleanly; restoring the pre-retirement generation" >&2
    previous_services="$(cat "$STATE_DIR/previous-services")"
    previous_generation="$(cat "$STATE_DIR/previous-generation")"
    previous_membership_sha="$(cat "$STATE_DIR/previous-membership-sha")"
    previous_ca_sha="$(cat "$STATE_DIR/previous-ca-sha")"
    previous_bundle_sha="$(cat "$STATE_DIR/previous-bundle-sha")"
    new_ca_sha="$(cat "$STATE_DIR/new-ca-sha")"
    new_bundle_sha="$(cat "$STATE_DIR/new-bundle-sha")"
    new_membership_sha="$(cat "$STATE_DIR/new-membership-sha")"
    rollback_generation="$previous_generation"

    if [ "$TLS_ROTATION_REASON" = dns ]; then
        # DNS publication changes keep the active CA. Restore the exact prior
        # leaf bundle and roll it back without introducing a second root.
        rollback_enabled=0
        write_bundle "$PREVIOUS_DIR" "$PREVIOUS_DIR/ca.crt" \
            "$previous_services" "$previous_generation" stable \
            "$previous_membership_sha" "$previous_ca_sha" \
            "$previous_bundle_sha" none 0
        rollout_generation "$previous_generation" \
            "$(rollout_digest "$previous_bundle_sha" "$PREVIOUS_DIR/ca.crt")"
        return 0
    fi

    # First restore dual trust with the new leaves, then dual trust with the
    # old leaves, and only then old-only trust. This ordering keeps every mixed
    # pod pair mutually authenticated during rollback.
    write_bundle "$NEW_DIR" "$STATE_DIR/dual-ca.crt" "$BUNDLE_SERVICES" \
        "$TLS_ROTATION_GENERATION" rollback-new-dual "$new_membership_sha" \
        "$new_ca_sha" "$new_bundle_sha" "$previous_ca_sha" "$rotation_deadline"
    rollout_generation "${TLS_ROTATION_GENERATION}-rollback-new-dual" \
        "$(rollout_digest "$new_bundle_sha" "$STATE_DIR/dual-ca.crt")"
    write_bundle "$PREVIOUS_DIR" "$STATE_DIR/dual-ca.crt" "$previous_services" \
        "$previous_generation" rollback-old-dual "$previous_membership_sha" \
        "$previous_ca_sha" "$previous_bundle_sha" "$new_ca_sha" "$rotation_deadline"
    rollout_generation "${previous_generation}-rollback-old-dual" \
        "$(rollout_digest "$previous_bundle_sha" "$STATE_DIR/dual-ca.crt")"
    rollback_enabled=0
    write_bundle "$PREVIOUS_DIR" "$PREVIOUS_DIR/ca.crt" "$previous_services" \
        "$previous_generation" stable "$previous_membership_sha" \
        "$previous_ca_sha" "$previous_bundle_sha" none 0
    rollout_generation "$previous_generation" \
        "$(rollout_digest "$previous_bundle_sha" "$PREVIOUS_DIR/ca.crt")"
    set -e
}

on_exit() {
    status=$?
    trap - 0 HUP INT TERM
    if [ "$status" -ne 0 ] && [ "$rollback_enabled" -eq 1 ]; then
        rollback_rotation
    fi
    exit "$status"
}
trap on_exit 0
trap 'exit 1' HUP INT TERM

if [ "$TLS_ROTATION_OPERATION" = reconcile ]; then
    if [ "$(cat "$STATE_DIR/existence")" = absent ]; then
        write_bundle "$NEW_DIR" "$NEW_DIR/ca.crt" "$BUNDLE_SERVICES" \
            "$TLS_ROTATION_GENERATION" stable \
            "$(cat "$STATE_DIR/new-membership-sha")" \
            "$(cat "$STATE_DIR/new-ca-sha")" \
            "$(cat "$STATE_DIR/new-bundle-sha")" none 0
        rollout_generation "$TLS_ROTATION_GENERATION" \
            "$(rollout_digest "$(cat "$STATE_DIR/new-bundle-sha")" "$NEW_DIR/ca.crt")"
        echo "Initialized stable TLS generation ${TLS_ROTATION_GENERATION}"
        exit 0
    fi
    if [ "$(cat "$STATE_DIR/metadata-shape")" = legacy ]; then
        # Metadata-only migration: certificate, signer, leaf, and Secret data
        # bytes remain untouched.
        trust_sha="sha256:$(sha256sum "$PREVIOUS_DIR/ca.crt" | cut -d' ' -f1)"
        annotate_secret "$TLS_ROTATION_GENERATION" stable \
            "$(cat "$STATE_DIR/previous-membership-sha")" \
            "$(cat "$STATE_DIR/previous-ca-sha")" \
            "$(cat "$STATE_DIR/previous-bundle-sha")" "$trust_sha" none 0 \
            ready "$TLS_ROTATION_GENERATION" false
        echo "Migrated legacy SAN-scheme state without replacing Secret data"
    else
        echo "Stable TLS generation ${TLS_ROTATION_GENERATION} preserved exactly"
    fi
    exit 0
fi

previous_services="$(cat "$STATE_DIR/previous-services")"
previous_generation="$(cat "$STATE_DIR/previous-generation")"
previous_membership_sha="$(cat "$STATE_DIR/previous-membership-sha")"
previous_ca_sha="$(cat "$STATE_DIR/previous-ca-sha")"
previous_bundle_sha="$(cat "$STATE_DIR/previous-bundle-sha")"
new_membership_sha="$(cat "$STATE_DIR/new-membership-sha")"
new_ca_sha="$(cat "$STATE_DIR/new-ca-sha")"
new_bundle_sha="$(cat "$STATE_DIR/new-bundle-sha")"
rotation_deadline=$(($(date +%s) + OVERLAP_SECONDS))
rollback_enabled=1
rollback_generation="$previous_generation"

if [ "$TLS_ROTATION_REASON" = dns ]; then
    # A DNS contract change affects only leaf identities. Keeping the active
    # CA avoids invalidating workload state bound to that trust anchor.
    write_bundle "$NEW_DIR" "$NEW_DIR/ca.crt" "$BUNDLE_SERVICES" \
        "$TLS_ROTATION_GENERATION" rotating-dns "$new_membership_sha" \
        "$new_ca_sha" "$new_bundle_sha" none 0
    rollout_generation "$TLS_ROTATION_GENERATION" \
        "$(rollout_digest "$new_bundle_sha" "$NEW_DIR/ca.crt")"
    rollback_enabled=0
    rollback_generation=none
    trust_sha="sha256:$(sha256sum "$NEW_DIR/ca.crt" | cut -d' ' -f1)"
    annotate_secret "$TLS_ROTATION_GENERATION" stable "$new_membership_sha" \
        "$new_ca_sha" "$new_bundle_sha" "$trust_sha" none 0 \
        ready "$TLS_ROTATION_GENERATION" false
    echo "TLS generation ${TLS_ROTATION_GENERATION} applied DNS leaf updates under the active CA"
    exit 0
fi

write_bundle "$PREVIOUS_DIR" "$STATE_DIR/dual-ca.crt" "$previous_services" \
    "$previous_generation" overlap-old "$previous_membership_sha" \
    "$previous_ca_sha" "$previous_bundle_sha" "$new_ca_sha" "$rotation_deadline"
rollout_generation "${TLS_ROTATION_GENERATION}-overlap-old" \
    "$(rollout_digest "$previous_bundle_sha" "$STATE_DIR/dual-ca.crt")"
ensure_overlap_open

write_bundle "$NEW_DIR" "$STATE_DIR/dual-ca.crt" "$BUNDLE_SERVICES" \
    "$TLS_ROTATION_GENERATION" overlap-new "$new_membership_sha" \
    "$new_ca_sha" "$new_bundle_sha" "$previous_ca_sha" "$rotation_deadline"
rollout_generation "${TLS_ROTATION_GENERATION}-overlap-new" \
    "$(rollout_digest "$new_bundle_sha" "$STATE_DIR/dual-ca.crt")"
ensure_overlap_open

# Retirement is the explicit rollback boundary: every governed workload has
# reported Ready with the new leaf while both public roots are trusted.
write_bundle "$NEW_DIR" "$NEW_DIR/ca.crt" "$BUNDLE_SERVICES" \
    "$TLS_ROTATION_GENERATION" retiring "$new_membership_sha" \
    "$new_ca_sha" "$new_bundle_sha" "$previous_ca_sha" 0
rollout_generation "$TLS_ROTATION_GENERATION" \
    "$(rollout_digest "$new_bundle_sha" "$NEW_DIR/ca.crt")"

history_suffix="$(printf '%s' "$previous_ca_sha" | sed 's/^sha256://' | cut -c1-16)"
successor_suffix="$(printf '%s' "$new_ca_sha" | sed 's/^sha256://' | cut -c1-16)"
history_prefix="$(printf '%s' "$TLS_SECRET_NAME" | cut -c1-211)"
history_name="${history_prefix}-history-${history_suffix}-${successor_suffix}"
kubectl -n "$POD_NAMESPACE" get secret "$history_name" >/dev/null 2>&1 \
    && die "historical public CA record already exists"
kubectl -n "$POD_NAMESPACE" create secret generic "$history_name" \
    "--from-file=ca.crt=${PREVIOUS_DIR}/ca.crt" \
    --dry-run=client -o json \
    | jq --arg retired_ca "$previous_ca_sha" \
        --arg successor_ca "$new_ca_sha" \
        --arg retired_generation "$previous_generation" \
        --arg successor_generation "$TLS_ROTATION_GENERATION" '
        .metadata.labels["clavenar.com/trust-history"] = "true"
        | .metadata.annotations = {
            "clavenar.com/tls-state-version": "1",
            "clavenar.com/tls-retired-ca-sha256": $retired_ca,
            "clavenar.com/tls-successor-ca-sha256": $successor_ca,
            "clavenar.com/tls-retired-generation": $retired_generation,
            "clavenar.com/tls-successor-generation": $successor_generation
          }' \
    | kubectl -n "$POD_NAMESPACE" create -f - >/dev/null

trust_sha="sha256:$(sha256sum "$NEW_DIR/ca.crt" | cut -d' ' -f1)"
annotate_secret "$TLS_ROTATION_GENERATION" stable "$new_membership_sha" \
    "$new_ca_sha" "$new_bundle_sha" "$trust_sha" "$previous_ca_sha" 0 \
    ready "$TLS_ROTATION_GENERATION" false
rollback_enabled=0
echo "TLS generation ${TLS_ROTATION_GENERATION} retired prior live trust successfully"
