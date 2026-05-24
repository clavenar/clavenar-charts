#!/bin/bash
set -euo pipefail

# Build and push all 8 warden service images to ghcr.io/vanteguardlabs.
# VERSION holds the latest tag already published; the script bumps to
# next patch, builds + pushes under that, then writes the new value
# back to VERSION + Chart.appVersion. Failed pushes leave VERSION
# untouched so it always reflects what's actually live on GHCR.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHART_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE_ROOT="$(cd "$CHART_REPO/.." && pwd)"

REGISTRY="ghcr.io/vanteguardlabs"

SERVICES=(
    "warden-proxy"
    "warden-brain"
    "warden-policy-engine"
    "warden-ledger"
    "warden-hil"
    "warden-console"
    "warden-deep-review"
    "warden-identity"
    # warden-simulator ships the warden-upstream-stub binary the chart's
    # bundled-lab upstreamStub Deployment runs. Operators enabling
    # upstreamStub.enabled need this image at the chart's appVersion.
    "warden-simulator"
)

# Seven of the nine Dockerfiles `COPY --from=<name>` source from sibling
# library repos via BuildKit named contexts. Resolves them to the local
# checkouts under WORKSPACE_ROOT — without these flags docker tries to
# pull `docker.io/library/<name>` and fails.
declare -A EXTRA_CONTEXTS=(
    [warden-proxy]="warden-sandbox"
    [warden-brain]="warden-workload-identity"
    [warden-policy-engine]="warden-workload-identity"
    [warden-ledger]="warden-workload-identity"
    [warden-console]="warden-sdk warden-workload-identity"
    [warden-identity]="warden-workload-identity"
    [warden-simulator]="warden-workload-identity"
)

ALLOW_DIRTY=0
NO_BUMP=0
DRY_RUN=0
ONLY=""

usage() {
    cat <<'EOF'
push-images.sh — build + push all warden service images to GHCR

Usage:
  push-images.sh [--only=svc1,svc2] [--allow-dirty] [--no-bump] [--dry-run]

VERSION holds the latest image set already published to GHCR. The
script computes the next patch as the target, builds each sibling
repo's Dockerfile, pushes ghcr.io/vanteguardlabs/<service>:<target>
and :latest, then writes <target> into VERSION + Chart.appVersion.

Flags:
  --only=<csv>     Subset of the 8 services. Implies --no-bump
                   (re-pushes the current VERSION tag for those svcs).
  --allow-dirty    Skip the "sibling repos on main + clean" preflight.
  --no-bump        Re-push the current VERSION tag; do not bump or commit.
  --dry-run        Print commands without executing.
  --help           This usage message.

Preflight: docker must be authenticated to ghcr.io
  echo "$GH_PAT" | sudo -n docker login ghcr.io -u vanteguardlabs --password-stdin
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --only=*) ONLY="${1#--only=}"; NO_BUMP=1; shift ;;
        --allow-dirty) ALLOW_DIRTY=1; shift ;;
        --no-bump) NO_BUMP=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
    esac
done

# Resolve effective target list.
TARGETS=()
if [ -z "$ONLY" ]; then
    TARGETS=("${SERVICES[@]}")
else
    IFS=',' read -ra requested <<<"$ONLY"
    for r in "${requested[@]}"; do
        found=0
        for s in "${SERVICES[@]}"; do
            if [ "$s" = "$r" ]; then
                TARGETS+=("$s")
                found=1
                break
            fi
        done
        if [ "$found" -eq 0 ]; then
            echo "unknown service: $r" >&2
            echo "valid: ${SERVICES[*]}" >&2
            exit 2
        fi
    done
fi

VERSION_FILE="$CHART_REPO/VERSION"
CHART_FILE="$CHART_REPO/charts/warden/Chart.yaml"

[ -f "$VERSION_FILE" ] || { echo "VERSION missing at $VERSION_FILE" >&2; exit 1; }
[ -f "$CHART_FILE" ] || { echo "Chart.yaml missing at $CHART_FILE" >&2; exit 1; }

PUBLISHED="$(tr -d '[:space:]' < "$VERSION_FILE")"
case "$PUBLISHED" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) echo "VERSION not semver (got '$PUBLISHED')" >&2; exit 1 ;;
esac

# Chart.appVersion must equal VERSION going in — both track the latest
# published image set; drift means a manual edit the operator must
# resolve before pushing.
chart_app_version="$(grep -E '^appVersion:' "$CHART_FILE" \
    | sed -E 's/^appVersion:[[:space:]]*"?([^"[:space:]]+)"?[[:space:]]*$/\1/')"
if [ "$chart_app_version" != "$PUBLISHED" ]; then
    echo "Chart.yaml appVersion ($chart_app_version) differs from VERSION ($PUBLISHED)." >&2
    echo "Resolve the manual edit before running this script." >&2
    exit 1
fi

# Compute target tag — what we're about to push. --no-bump (and --only,
# which implies --no-bump) re-pushes the current VERSION tag.
if [ "$NO_BUMP" -eq 1 ]; then
    TARGET="$PUBLISHED"
else
    IFS='.' read -r t_major t_minor t_patch <<<"$PUBLISHED"
    TARGET="${t_major}.${t_minor}.$((t_patch + 1))"
fi

# docker auth preflight — script runs `sudo -n docker …` so the
# authoritative config is /root/.docker/config.json.
docker_auth_ok() {
    sudo -n cat /root/.docker/config.json 2>/dev/null | grep -q '"ghcr.io"'
}

if ! docker_auth_ok; then
    cat >&2 <<'EOF'
no ghcr.io credentials found in /root/.docker/config.json.
authenticate first:
  echo "$GH_PAT" | sudo -n docker login ghcr.io -u vanteguardlabs --password-stdin
EOF
    exit 1
fi

# Sibling repo preflight — each target must be on main with a clean
# working tree, so published images correspond to an actual commit.
check_sibling_clean() {
    violators=()
    targets_plus_aux=("${TARGETS[@]}")
    # Only enforce on aux repos actually pulled in by the selected
    # targets (so --only doesn't trip on an aux dir it never touches).
    for svc in "${TARGETS[@]}"; do
        for ctx in ${EXTRA_CONTEXTS[$svc]:-}; do
            targets_plus_aux+=("$ctx")
        done
    done
    for repo_name in "${targets_plus_aux[@]}"; do
        repo="$WORKSPACE_ROOT/$repo_name"
        if [ ! -d "$repo" ]; then
            echo "missing sibling repo: $repo" >&2
            exit 1
        fi
        branch="$(git -C "$repo" branch --show-current 2>/dev/null || echo '')"
        porcelain="$(git -C "$repo" status --porcelain 2>/dev/null || echo '')"
        if [ -n "$porcelain" ]; then
            dirty="yes"
        else
            dirty="no"
        fi
        if [ "$branch" != "main" ] || [ -n "$porcelain" ]; then
            violators+=("$repo_name (branch=$branch, dirty=$dirty)")
        fi
    done
    # Also check service Dockerfiles exist (aux repos don't have one).
    for svc in "${TARGETS[@]}"; do
        if [ ! -f "$WORKSPACE_ROOT/$svc/Dockerfile" ]; then
            echo "missing Dockerfile: $WORKSPACE_ROOT/$svc/Dockerfile" >&2
            exit 1
        fi
    done
    if [ "${#violators[@]}" -gt 0 ]; then
        echo "sibling repos not clean + on main:" >&2
        for v in "${violators[@]}"; do echo "  - $v" >&2; done
        echo "Use --allow-dirty to override (for iteration only)." >&2
        exit 1
    fi
}

if [ "$ALLOW_DIRTY" -eq 0 ]; then
    check_sibling_clean
fi

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

echo "VERSION    = $PUBLISHED (last published)"
echo "target     = $TARGET (about to push)"
echo "registry   = $REGISTRY"
echo "targets    = ${TARGETS[*]}"
echo "allow-dirty=$ALLOW_DIRTY  no-bump=$NO_BUMP  dry-run=$DRY_RUN"
echo

for svc in "${TARGETS[@]}"; do
    repo="$WORKSPACE_ROOT/$svc"
    rev="$(git -C "$repo" rev-parse HEAD 2>/dev/null || echo 'unknown')"
    ctx_args=()
    for ctx in ${EXTRA_CONTEXTS[$svc]:-}; do
        ctx_args+=(--build-context "$ctx=$WORKSPACE_ROOT/$ctx")
    done
    if [ "${#ctx_args[@]}" -gt 0 ]; then
        echo "→ build $svc (rev=$rev, ctx=${EXTRA_CONTEXTS[$svc]})"
    else
        echo "→ build $svc (rev=$rev)"
    fi
    run sudo -n docker build \
        --platform=linux/amd64 \
        "${ctx_args[@]}" \
        --label "org.opencontainers.image.source=https://github.com/vanteguardlabs/$svc" \
        --label "org.opencontainers.image.version=$TARGET" \
        --label "org.opencontainers.image.revision=$rev" \
        -t "$REGISTRY/$svc:$TARGET" \
        -t "$REGISTRY/$svc:latest" \
        "$repo"
done

for svc in "${TARGETS[@]}"; do
    echo "→ push $svc:$TARGET"
    run sudo -n docker push "$REGISTRY/$svc:$TARGET"
    echo "→ push $svc:latest"
    run sudo -n docker push "$REGISTRY/$svc:latest"
done

echo
for svc in "${TARGETS[@]}"; do
    echo "pushed $REGISTRY/$svc:$TARGET"
done

if [ "$NO_BUMP" -eq 1 ]; then
    echo "(no bump — re-pushed :$TARGET; VERSION stays at $PUBLISHED)"
    exit 0
fi
if [ "$DRY_RUN" -eq 1 ]; then
    echo "(no bump — --dry-run set)"
    exit 0
fi

printf '%s\n' "$TARGET" > "$VERSION_FILE"
# Use ~ as sed delimiter — | collides with regex alternation per
# CLAUDE.md recurring gotchas.
sed -i -E "s~^appVersion:.*~appVersion: \"$TARGET\"~" "$CHART_FILE"

git -C "$CHART_REPO" add VERSION charts/warden/Chart.yaml
git -c user.name=VanteguardLabs -c user.email=vanteguardlabs@gmail.com \
    -C "$CHART_REPO" commit -m "publish images $TARGET"
git -C "$CHART_REPO" push origin main

echo
echo "VERSION updated: $PUBLISHED → $TARGET (now on GHCR)"
