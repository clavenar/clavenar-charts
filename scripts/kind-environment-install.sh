#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 <dev|prod> <clavenar-e2e checkout>" >&2
}

die() {
  echo "kind environment gate: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

assert_count() {
  local description="$1"
  local expected="$2"
  local actual="$3"

  if [[ "$actual" != "$expected" ]]; then
    die "${description}: expected ${expected}, got ${actual}"
  fi
}

if [[ "$#" -ne 2 ]]; then
  usage
  exit 64
fi

environment="$1"
case "$environment" in
  dev | prod) ;;
  *)
    usage
    exit 64
    ;;
esac

require_command helm
require_command kind
require_command kubectl

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "${script_dir}/.." && pwd -P)"
e2e_root="$(cd -- "$2" 2>/dev/null && pwd -P)" ||
  die "clavenar-e2e checkout does not exist: $2"

core_chart="${repository_root}/charts/clavenar"
overlay_chart="${e2e_root}/k8s/charts/clavenar-env"
core_values="${e2e_root}/k8s/${environment}/clavenar-values.yaml"
core_runtime_values="${e2e_root}/k8s/tests/${environment}-core-runtime-values.yaml"
overlay_values="${e2e_root}/k8s/${environment}/env-values.yaml"
overlay_runtime_values="${e2e_root}/k8s/tests/${environment}-runtime-values.yaml"
crd_fixture="${repository_root}/tests/kind-environment-crds.yaml"

for input_file in \
  "${core_chart}/Chart.yaml" \
  "${overlay_chart}/Chart.yaml" \
  "$core_values" \
  "$core_runtime_values" \
  "$overlay_values" \
  "$overlay_runtime_values" \
  "$crd_fixture"; do
  [[ -f "$input_file" && ! -L "$input_file" ]] ||
    die "required regular, non-symlink input is missing: ${input_file}"
done

kind_node_image="kindest/node:v1.30.8@sha256:17cd608b3971338d9180b00776cb766c50d0a0b6b904ab4ff52fd3fc5c6369bf"
cluster_name="clavenar-${environment}-$$"
context_name="kind-${cluster_name}"
core_release="clavenar-${environment}"
overlay_release="clavenar-env-${environment}"
work_dir="$(mktemp -d)"
cluster_created=false

cleanup() {
  local original_status=$?
  local cleanup_status=0

  trap - EXIT
  if [[ "$cluster_created" == true ]]; then
    kind delete cluster --name "$cluster_name" || cleanup_status=$?
  fi
  rm -rf -- "$work_dir"

  if [[ "$original_status" -ne 0 ]]; then
    exit "$original_status"
  fi
  exit "$cleanup_status"
}
trap cleanup EXIT

helm dependency build "$core_chart"

kind create cluster \
  --name "$cluster_name" \
  --image "$kind_node_image" \
  --wait 120s
cluster_created=true

for namespace in clavenar clavenar-demo observability; do
  kubectl --context "$context_name" create namespace "$namespace"
done

kubectl --context "$context_name" apply -f "$crd_fixture"
kubectl --context "$context_name" wait \
  --for=condition=Established \
  --timeout=60s \
  -f "$crd_fixture"

core_manifest="${work_dir}/core.yaml"
overlay_manifest="${work_dir}/overlay.yaml"

helm template "$core_release" "$core_chart" \
  --namespace clavenar \
  -f "$core_values" \
  -f "$core_runtime_values" \
  >"$core_manifest"
helm template "$overlay_release" "$overlay_chart" \
  --namespace clavenar-demo \
  -f "$overlay_values" \
  -f "$overlay_runtime_values" \
  >"$overlay_manifest"

# Validate the complete render, including lifecycle hooks, against the live
# Kubernetes API before installing only the non-hook workload inventory.
kubectl --context "$context_name" --namespace clavenar \
  apply --dry-run=server -f "$core_manifest" >/dev/null
kubectl --context "$context_name" \
  apply --dry-run=server -f "$overlay_manifest" >/dev/null

# Hooks perform runtime initialization and intentionally require running
# dependencies. This CI gate tests Kubernetes admission and Helm ownership;
# lifecycle behavior remains covered by the environment E2E suites.
helm install "$core_release" "$core_chart" \
  --kube-context "$context_name" \
  --namespace clavenar \
  --no-hooks \
  -f "$core_values" \
  -f "$core_runtime_values"
helm install "$overlay_release" "$overlay_chart" \
  --kube-context "$context_name" \
  --namespace clavenar-demo \
  --no-hooks \
  -f "$overlay_values" \
  -f "$overlay_runtime_values"

for release_namespace in \
  "${core_release}:clavenar" \
  "${overlay_release}:clavenar-demo"; do
  release="${release_namespace%%:*}"
  namespace="${release_namespace##*:}"
  status="$(helm status "$release" \
    --kube-context "$context_name" \
    --namespace "$namespace" \
    --output json |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["status"])')"
  [[ "$status" == "deployed" ]] ||
    die "release ${release} is not deployed: ${status}"
done

core_deployments="$(kubectl --context "$context_name" --namespace clavenar \
  get deployments \
  -l "app.kubernetes.io/instance=${core_release}" \
  --output name | wc -l)"
overlay_deployments="$(kubectl --context "$context_name" --namespace clavenar-demo \
  get deployments \
  -l "app.kubernetes.io/instance=${overlay_release}" \
  --output name | wc -l)"
assert_count "core deployment inventory" "$([[ "$environment" == dev ]] && echo 11 || echo 10)" "$core_deployments"
assert_count "overlay demo deployment inventory" 3 "$overlay_deployments"

observability_deployments="$(kubectl --context "$context_name" --namespace observability \
  get deployments \
  -l "app.kubernetes.io/instance=${overlay_release}" \
  --output name | wc -l)"
reset_cronjobs="$(kubectl --context "$context_name" --namespace clavenar \
  get cronjobs \
  -l "app.kubernetes.io/instance=${overlay_release}" \
  --output name | wc -l)"
if [[ "$environment" == prod ]]; then
  assert_count "observability deployment inventory" 1 "$observability_deployments"
  assert_count "reset CronJob inventory" 1 "$reset_cronjobs"
else
  assert_count "observability deployment inventory" 0 "$observability_deployments"
  assert_count "reset CronJob inventory" 0 "$reset_cronjobs"
fi

echo "kind environment gate: ${environment} posture admitted and installed"
echo "kind environment gate: core deployments=${core_deployments}, overlay deployments=${overlay_deployments}, observability deployments=${observability_deployments}, reset cronjobs=${reset_cronjobs}"
