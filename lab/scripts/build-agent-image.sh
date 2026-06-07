#!/usr/bin/env bash
# Builds the lab-only Claude-Code-+-clavenarctl image from the local
# clavenar-ctl + clavenar-rust-sdk sibling checkouts. Tags the image at
# ghcr.io/clavenar/clavenar-claude-code-agent:<chart-appVersion>
# and ":latest". Push is opt-in via --push (operator must have run
# `docker login ghcr.io` first).
#
# Usage:
#   lab/scripts/build-agent-image.sh [--push]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHARTS_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKSPACE_ROOT="$(cd "${CHARTS_ROOT}/.." && pwd)"

CHART_YAML="${CHARTS_ROOT}/charts/clavenar/Chart.yaml"
APP_VERSION="$(awk -F'"' '/^appVersion:/ {print $2; exit}' "${CHART_YAML}")"
if [ -z "${APP_VERSION}" ]; then
    echo "Could not parse appVersion from ${CHART_YAML}" >&2
    exit 1
fi

PUSH=0
for arg in "$@"; do
    case "${arg}" in
        --push) PUSH=1 ;;
        *) echo "Unknown argument: ${arg}" >&2; exit 1 ;;
    esac
done

IMAGE="ghcr.io/clavenar/clavenar-claude-code-agent"
DOCKERFILE="${CHARTS_ROOT}/lab/Dockerfile.claude-code"

for repo in clavenar-ctl clavenar-rust-sdk; do
    if [ ! -d "${WORKSPACE_ROOT}/${repo}" ]; then
        echo "Sibling repo missing: ${WORKSPACE_ROOT}/${repo}" >&2
        echo "The image build needs both clavenar-ctl and clavenar-rust-sdk checked" >&2
        echo "out alongside clavenar-charts. Run \`gh repo clone clavenar/${repo}\`." >&2
        exit 1
    fi
done

echo "Building ${IMAGE}:${APP_VERSION} (context: ${WORKSPACE_ROOT})"
sudo -n docker build \
    --platform=linux/amd64 \
    --label "org.opencontainers.image.source=https://github.com/clavenar/clavenar-charts" \
    --label "org.opencontainers.image.version=${APP_VERSION}" \
    -t "${IMAGE}:${APP_VERSION}" \
    -t "${IMAGE}:latest" \
    -f "${DOCKERFILE}" \
    "${WORKSPACE_ROOT}"

if [ "${PUSH}" -eq 1 ]; then
    echo "Pushing ${IMAGE}:${APP_VERSION} and :latest"
    sudo -n docker push "${IMAGE}:${APP_VERSION}"
    sudo -n docker push "${IMAGE}:latest"
fi

echo "Done."
