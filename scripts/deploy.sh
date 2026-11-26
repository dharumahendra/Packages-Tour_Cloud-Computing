#!/usr/bin/env bash
# Deploy microservices paket wisata ke ECS Fargate (dipakai GitHub Actions; butuh Docker + AWS CLI + python3/boto3).
# Pakai: ADMIN_KEY=... [ALLOWED_IPS=...] ./scripts/deploy.sh
set -euo pipefail
: "${ADMIN_KEY:?Isi ADMIN_KEY (huruf dan angka saja)}"
export AWS_DEFAULT_REGION=${AWS_REGION:-us-east-1}
export STACK_PREFIX=${STACK_PREFIX:-tour}
cd "$(dirname "$0")/.."

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY=$ACCOUNT.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com

echo "== 1. Repo ECR + bucket S3"
BUCKET=$(python3 scripts/provision.py base)

echo "== 2. Build & push image"
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
image_for() {
  local svc=$1 tag repo
  tag=$(git rev-parse "HEAD:shared" "HEAD:services/$svc" | sha1sum | cut -c1-12)
  repo=$STACK_PREFIX-$svc
  if aws ecr describe-images --repository-name "$repo" --image-ids imageTag="$tag" >/dev/null 2>&1; then
    echo "   $repo:$tag sudah ada, build dilewati" >&2
  else
    echo "   build $repo:$tag" >&2
    docker build -q -f "services/$svc/Dockerfile" -t "$REGISTRY/$repo:$tag" . >&2
    docker push -q "$REGISTRY/$repo:$tag" >&2
  fi
  echo "$REGISTRY/$repo:$tag"
}
CATALOG_IMAGE=$(image_for catalog)
OPERATOR_IMAGE=$(image_for operator)

echo "== 3. ALB + ECS + CodeDeploy"
API=$(python3 scripts/provision.py app --catalog-image "$CATALOG_IMAGE" --operator-image "$OPERATOR_IMAGE" --bucket "$BUCKET")

echo "== Selesai. ApiUrl:"
echo "$API"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then echo "**ApiUrl:** $API" >> "$GITHUB_STEP_SUMMARY"; fi
