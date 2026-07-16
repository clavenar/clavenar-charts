#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
push-images.sh is permanently disabled.

The former publisher could overwrite the current semantic image tags, publish
mutable latest tags, and expose a partially pushed service graph. Release
artifacts now come only from clavenar-e2e's manual "Protected immutable stack
release" workflow. That workflow derives all builds and named contexts from
the exact clean signed source BOM, stages packages/images/SBOMs/provenance by
digest, and creates one semantic stack-BOM reference only after completeness
and signature verification pass.

Do not restore --only, --allow-dirty, --no-bump, :latest, or direct component
semantic-tag publication. WP-14.5 will move chart installation to BOM digests;
until then VERSION and Chart.appVersion remain the last legacy image set.
EOF

exit 1
