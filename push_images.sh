#!/usr/bin/env bash
# Build both images (backend, ui) and push them to Azure Container Registry.
# Usage: ACR_NAME=<from terraform output acr_name> ./push_images.sh [tag]   (default tag: v1)
set -euo pipefail

ACR_NAME="${ACR_NAME:?Set ACR_NAME (see: terraform output acr_name)}"
TAG="${IMAGE_TAG:-${1:-v1}}"

REGISTRY="${ACR_NAME}.azurecr.io"
cd "$(dirname "$0")"

echo ">> Logging in to ${REGISTRY}"
az acr login --name "${ACR_NAME}"

for service in backend ui; do
  IMAGE="${REGISTRY}/databench-${service}:${TAG}"
  echo ">> Building ${IMAGE}"
  # App Service Linux runs amd64; be explicit so the build is right even on an ARM laptop.
  docker build --platform linux/amd64 -f "${service}/Dockerfile" -t "${IMAGE}" .
  echo ">> Pushing ${IMAGE}"
  docker push "${IMAGE}"
done

echo
echo "Done. If this is the first push, restart both Web Apps so they pick up the image:"
cat <<EOF
  az webapp restart -g <resource_group> -n <backend-app-name>
  az webapp restart -g <resource_group> -n <ui-app-name>
EOF
