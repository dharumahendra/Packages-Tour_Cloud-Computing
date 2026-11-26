#!/usr/bin/env bash
# Deploy MONOLIT (Fase 2) sebagai stack terpisah: tour-monolith.
# Pakai: LAB_ROLE_ARN=... ./scripts/deploy-monolith.sh
set -euo pipefail
: "${LAB_ROLE_ARN:?Isi LAB_ROLE_ARN dengan ARN role untuk Lambda}"
cd "$(dirname "$0")/../monolith"
sam deploy \
  --template-file template.yaml \
  --stack-name tour-monolith \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides LabRoleArn=$LAB_ROLE_ARN

aws cloudformation describe-stacks --stack-name tour-monolith \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text
