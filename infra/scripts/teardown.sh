#!/usr/bin/env bash
# Removes the Azure resource group (all resources inside it)
set -euo pipefail
ENV="${1:-prod}"
PREFIX="${2:-acopilot}"
RG_NAME="${PREFIX}-${ENV}-rg"

echo "WARNING: This will permanently delete resource group: ${RG_NAME}"
read -r -p "Type the resource group name to confirm: " confirm
if [ "$confirm" != "${RG_NAME}" ]; then
  echo "Aborted."
  exit 1
fi

az group delete --name "${RG_NAME}" --yes --no-wait
echo "Deletion initiated for ${RG_NAME}. This may take a few minutes."
