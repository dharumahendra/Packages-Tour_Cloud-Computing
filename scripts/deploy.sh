#!/usr/bin/env bash
# Deploy microservices paket wisata ke ECS Fargate (dipakai GitHub Actions; butuh Docker + AWS CLI + python3/boto3).
# Pakai: ADMIN_KEY=... [ALLOWED_IPS=...] [TASK_ROLE_ARN=...] [DEPLOY_ROLE_ARN=...] ./scripts/deploy.sh
#
# 1. stack <prefix>-base : repo ECR + bucket S3 data paket
# 2. build & push image (tag = hash isi folder shared/ + services/<svc>/, jadi hanya service yang berubah yang di-build)
# 3. stack <prefix>-app  : dibuat sekali (ALB, ECS, CodeDeploy). Setelah ada, rilis berikutnya lewat
#    CodeDeploy blue/green canary (scripts/ecs_deploy.py) hanya untuk service yang image/env-nya berubah.
set -euo pipefail
: "${ADMIN_KEY:?Isi ADMIN_KEY (huruf dan angka saja)}"
export AWS_DEFAULT_REGION=${AWS_REGION:-us-east-1}
PREFIX=${STACK_PREFIX:-tour}
BASE_STACK=$PREFIX-base
APP_STACK=$PREFIX-app
ALLOWED_IPS=${ALLOWED_IPS:-}
cd "$(dirname "$0")/.."

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
TASK_ROLE_ARN=${TASK_ROLE_ARN:-arn:aws:iam::$ACCOUNT:role/PipelineRole}
DEPLOY_ROLE_ARN=${DEPLOY_ROLE_ARN:-arn:aws:iam::$ACCOUNT:role/DeployRole}
REGISTRY=$ACCOUNT.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com

output() {
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}
stack_status() {
  aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo NONE
}
clear_failed_stack() {
  if [ "$(stack_status "$1")" = ROLLBACK_COMPLETE ]; then
    echo "== Stack $1 ROLLBACK_COMPLETE (pembuatan sebelumnya gagal), dihapus dulu"
    aws cloudformation delete-stack --stack-name "$1"
    aws cloudformation wait stack-delete-complete --stack-name "$1"
  fi
}

echo "== 1. Stack $BASE_STACK (ECR + S3)"
clear_failed_stack "$BASE_STACK"
aws cloudformation deploy --stack-name "$BASE_STACK" --template-file infra/base.yaml \
  --parameter-overrides Prefix="$PREFIX" --no-fail-on-empty-changeset
BUCKET=$(output "$BASE_STACK" PackagesBucket)

echo "== 2. Build & push image"
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
image_for() {
  local svc=$1 tag repo
  tag=$(git rev-parse "HEAD:shared" "HEAD:services/$svc" | sha1sum | cut -c1-12)
  repo=$PREFIX-$svc
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

echo "== 3. Stack $APP_STACK (ALB + ECS + CodeDeploy)"
clear_failed_stack "$APP_STACK"
if [ "$(stack_status "$APP_STACK")" = NONE ]; then
  # Pakai VPC bawaan lab (VPC tempat MonolithicAppServer), atau default VPC, kecuali VPC_ID/SUBNET_IDS diisi
  if [ -z "${VPC_ID:-}" ]; then
    VPC_ID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=MonolithicAppServer \
      --query 'Reservations[0].Instances[0].VpcId' --output text)
    if [ -z "$VPC_ID" ] || [ "$VPC_ID" = None ]; then
      VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
    fi
  fi
  if [ -z "${SUBNET_IDS:-}" ]; then
    SUBNET_IDS=$(aws ec2 describe-subnets \
      --filters Name=vpc-id,Values="$VPC_ID" Name=map-public-ip-on-launch,Values=true \
      --query 'Subnets[].[AvailabilityZone,SubnetId]' --output text | sort -u -k1,1 | head -2 | awk '{print $2}' | paste -sd, -)
  fi
  echo "   VPC $VPC_ID, subnet $SUBNET_IDS"
  if [ "$(echo "$SUBNET_IDS" | tr ',' '\n' | grep -c .)" -lt 2 ]; then
    echo "Butuh 2 subnet publik di AZ berbeda. Isi SUBNET_IDS=subnet-a,subnet-b secara manual." >&2
    exit 1
  fi
  aws cloudformation deploy --stack-name "$APP_STACK" --template-file infra/app.yaml \
    --parameter-overrides VpcId="$VPC_ID" SubnetIds="$SUBNET_IDS" \
      TaskRoleArn="$TASK_ROLE_ARN" DeployRoleArn="$DEPLOY_ROLE_ARN" \
      CatalogImage="$CATALOG_IMAGE" OperatorImage="$OPERATOR_IMAGE" \
      PackagesBucket="$BUCKET" AdminKey="$ADMIN_KEY" AllowedIps="$ALLOWED_IPS" \
      DeploymentConfig="${DEPLOYMENT_CONFIG:-CodeDeployDefault.ECSCanary10Percent5Minutes}"
else
  CLUSTER=$(output "$APP_STACK" ClusterName)
  APP=$(output "$APP_STACK" CodeDeployApplication)
  IDS=()
  ID=$(python3 scripts/ecs_deploy.py deploy --cluster "$CLUSTER" --service "$(output "$APP_STACK" CatalogServiceName)" \
    --container catalog --image "$CATALOG_IMAGE" --app "$APP" --group "$(output "$APP_STACK" CatalogDeploymentGroup)")
  [ -n "$ID" ] && IDS+=("$ID")
  ID=$(python3 scripts/ecs_deploy.py deploy --cluster "$CLUSTER" --service "$(output "$APP_STACK" OperatorServiceName)" \
    --container operator --image "$OPERATOR_IMAGE" --app "$APP" --group "$(output "$APP_STACK" OperatorDeploymentGroup)" \
    --env "ADMIN_KEY=$ADMIN_KEY" --env "ALLOWED_IPS=$ALLOWED_IPS")
  [ -n "$ID" ] && IDS+=("$ID")
  if [ ${#IDS[@]} -gt 0 ]; then
    echo "   Menunggu CodeDeploy (canary 10% selama 5 menit, lalu 100%)..."
    python3 scripts/ecs_deploy.py wait "${IDS[@]}"
  fi
fi

echo "== Selesai. ApiUrl:"
output "$APP_STACK" ApiUrl
