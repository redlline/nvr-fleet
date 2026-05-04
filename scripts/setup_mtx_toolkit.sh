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
  git -C "${ADDON_DIR}" reset --hard "origin/${REPO_REF}"
  git -C "${ADDON_DIR}" clean -fd
fi

FRONTEND_NGINX_CONF="${ADDON_DIR}/frontend/nginx.conf"
CUSTOM_FRONTEND_NGINX_CONF="${ROOT_DIR}/mtx-toolkit/frontend.nginx.conf"
if [ -f "${CUSTOM_FRONTEND_NGINX_CONF}" ]; then
  cp "${CUSTOM_FRONTEND_NGINX_CONF}" "${FRONTEND_NGINX_CONF}"
fi

python3 - "$ADDON_DIR" <<'PY'
from pathlib import Path
import sys

addon_dir = Path(sys.argv[1])

def patch_text(path_str, old, new):
    path = addon_dir / path_str
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if old not in text:
        return
    path.write_text(text.replace(old, new), encoding="utf-8")

patch_text(
    "backend/app/services/fleet_manager.py",
    '                    stream.source_url = source.get("id")\n',
    '                    source_id = source.get("id")\n'
    '                    stream.source_url = source_id if isinstance(source_id, str) and "://" in source_id else None\n',
)

patch_text(
    "backend/app/services/health_checker.py",
    "        if stream.source_url:\n            url = stream.source_url\n",
    '        if stream.source_url and "://" in stream.source_url:\n'
    '            url = stream.source_url\n',
)

patch_text(
    "backend/app/services/thumbnail_service.py",
    '# HLS port (same as in streams.py)\nHLS_PORT = int(os.getenv("MEDIAMTX_HLS_PORT", "8893"))\n',
    '# HLS proxy (go through fleet-server so auth and cookie handling stay centralized)\n'
    'HLS_PROXY_BASE = os.getenv("MEDIAMTX_HLS_PROXY_BASE", "http://fleet-server:8765/hls").rstrip("/")\n'
    'HLS_PORT = int(os.getenv("MEDIAMTX_HLS_PORT", "8888"))\n',
)

patch_text(
    "backend/app/services/thumbnail_service.py",
    '    def _get_hls_url(self, stream_path: str, node_api_url: str) -> str:\n'
    '        """Derive HLS URL from node API URL."""\n'
    '        parsed = urlparse(node_api_url)\n'
    '        return f"http://{parsed.hostname}:{HLS_PORT}/{stream_path}/index.m3u8"\n',
    '    def _get_hls_url(self, stream_path: str, node_api_url: str) -> str:\n'
    '        """Derive HLS URL from fleet proxy or node API URL."""\n'
    '        if HLS_PROXY_BASE:\n'
    '            return f"{HLS_PROXY_BASE}/{stream_path}/index.m3u8"\n'
    '        parsed = urlparse(node_api_url)\n'
    '        return f"http://{parsed.hostname}:{HLS_PORT}/{stream_path}/index.m3u8"\n',
)

# Fix: cascade delete streams when a node is deleted.
# Without this, deleting a node raises IntegrityError because Stream.node_id is NOT NULL
# and SQLAlchemy tries to SET NULL instead of DELETE the child rows.
patch_text(
    "backend/app/models/stream.py",
    '    streams = db.relationship("Stream", backref="node", lazy="dynamic")',
    '    streams = db.relationship("Stream", backref="node", lazy="dynamic", cascade="all, delete-orphan")',
)
PY

echo "MTX Toolkit source is ready in: ${ADDON_DIR}"
echo "Start add-on with:"
echo "  docker compose -f docker-compose.mtx-toolkit.yml up -d --build"
