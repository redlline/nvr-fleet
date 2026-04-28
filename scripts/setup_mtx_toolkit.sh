#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDON_DIR="${ROOT_DIR}/addons/mtx-toolkit"
REPO_URL="${MTX_TOOLKIT_REPO_URL:-https://github.com/dan246/mtx-toolkit.git}"
REPO_REF="${MTX_TOOLKIT_REPO_REF:-main}"

mkdir -p "${ROOT_DIR}/addons"

if [ ! -d "${ADDON_DIR}/.git" ]; then
  git clone --depth 1 --branch "${REPO_REF}" "${REPO_URL}" "${ADDON_DIR}"
else
  git -C "${ADDON_DIR}" fetch origin "${REPO_REF}" --depth 1
  git -C "${ADDON_DIR}" checkout "${REPO_REF}"
  git -C "${ADDON_DIR}" pull --ff-only origin "${REPO_REF}"
fi

FRONTEND_NGINX_CONF="${ADDON_DIR}/frontend/nginx.conf"
if [ -f "${FRONTEND_NGINX_CONF}" ]; then
  sed -i 's/host\.docker\.internal:8893/host.docker.internal:8888/g' "${FRONTEND_NGINX_CONF}"
fi

echo "MTX Toolkit source is ready in: ${ADDON_DIR}"
echo "Start add-on with:"
echo "  docker compose -f docker-compose.mtx-toolkit.yml up -d --build"
