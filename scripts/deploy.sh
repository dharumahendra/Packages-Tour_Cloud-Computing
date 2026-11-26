#!/usr/bin/env bash
# Deploy manual dari laptop / CloudShell.
# Pakai: LAB_ROLE_ARN=... ADMIN_KEY=... [DEPLOY_ROLE_ARN=...] [ENABLE_CANARY=false] ./scripts/deploy.sh
set -euo pipefail
: "${LAB_ROLE_ARN:?Isi LAB_ROLE_ARN dengan ARN role untuk Lambda}"
: "${ADMIN_KEY:?Isi ADMIN_KEY (huruf dan angka saja)}"
STACK_NAME=${STACK_NAME:-tour-app}

PARAMS="LabRoleArn=$LAB_ROLE_ARN AdminKey=$ADMIN_KEY EnableCanary=${ENABLE_CANARY:-true}"
if [ -n "${DEPLOY_ROLE_ARN:-}" ]; then PARAMS="$PARAMS DeployRoleArn=$DEPLOY_ROLE_ARN"; fi
if [ -n "${ALLOWED_IPS:-}" ]; then PARAMS="$PARAMS AllowedIps=$ALLOWED_IPS"; fi

cd "$(dirname "$0")/.."
# Kalau Python 3.12 tidak ada (misalnya di CloudShell), pakai SKIP_BUILD=1:
# kode tidak punya dependensi, jadi sam deploy bisa langsung mem-paket folder sumber.
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  sam build
  TEMPLATE=.aws-sam/build/template.yaml
else
  rm -rf .aws-sam
  TEMPLATE=template.yaml
fi
sam deploy \
  --template-file "$TEMPLATE" \
  --stack-name "$STACK_NAME" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides $PARAMS

aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text
