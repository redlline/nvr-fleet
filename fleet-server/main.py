import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import requests
import secrets
import socket
import ssl
import subprocess
import tempfile
import time
import uuid
import zipfile
from http.cookies import SimpleCookie
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Header, Request, status, BackgroundTasks, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from database import SessionLocal, engine, Base
from models import Site, Camera, Agent, StreamStat, TrafficSample
from schemas import (
    SiteCreate, SiteUpdate, SiteOut,
    CameraCreate, CameraUpdate, CameraOut,
    AgentOut, StreamStatOut, TrafficOut,
    InstallResponse, DashboardStats,
    ArchiveRecordingOut, ArchivePlaybackRequest, ArchivePlaybackOut,
    AgentCameraSyncItem, AgentSiteConfigUpdate,
    TlsUpdateRequest, TlsStatus, TlsCertificateInfo,
    StackStatus, StackServiceStatus, StackRestartRequest, StackRestartResult,
    StackLogsOut,
    BackupFileOut, BackupListOut, BackupRotateRequest, BackupRotateResult, BackupImportResult,
    SiteAgentDrainResult,
)
from config_gen import (
    mediamtx_internal_api_user,
    mediamtx_internal_api_pass,
    generate_go2rtc_yaml,
    normalize_stream_path,
    public_stream_path,
    update_mediamtx_paths,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _ensure_db_schema() -> None:
    Base.metadata.create_all(bind=engine)
    insp = inspect(engine)
    if "sites" not in insp.get_table_names():
        return
    columns = {col["name"] for col in insp.get_columns("sites")}
    statements = []
    if "nvr_vendor" not in columns:
        statements.append("ALTER TABLE sites ADD COLUMN nvr_vendor VARCHAR DEFAULT 'hikvision'")
    if "nvr_http_port" not in columns:
        statements.append("ALTER TABLE sites ADD COLUMN nvr_http_port INTEGER DEFAULT 80")
    if "nvr_control_port" not in columns:
        statements.append("ALTER TABLE sites ADD COLUMN nvr_control_port INTEGER DEFAULT 8000")
    if "tunnel_http_port" not in columns:
        statements.append("ALTER TABLE sites ADD COLUMN tunnel_http_port INTEGER")
    if "tunnel_control_port" not in columns:
        statements.append("ALTER TABLE sites ADD COLUMN tunnel_control_port INTEGER")
    if "tunnel_rtsp_port" not in columns:
        statements.append("ALTER TABLE sites ADD COLUMN tunnel_rtsp_port INTEGER")
    camera_columns = {col["name"] for col in insp.get_columns("cameras")} if "cameras" in insp.get_table_names() else set()
    if "source_ref" not in camera_columns:
        statements.append("ALTER TABLE cameras ADD COLUMN source_ref VARCHAR")
    if "profile_ref" not in camera_columns:
        statements.append("ALTER TABLE cameras ADD COLUMN profile_ref VARCHAR")
    if not statements:
        return
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


_ensure_db_schema()

app = FastAPI(title="NVR Fleet Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "admin-secret-change-me")
MEDIAMTX_YAML = os.environ.get("MEDIAMTX_YAML", "/app/mediamtx.yml")
MEDIAMTX_API  = os.environ.get("MEDIAMTX_API",  "http://localhost:9997")
PUBLIC_HOST   = os.environ.get("PUBLIC_HOST",    "localhost")
RTSP_PORT     = os.environ.get("RTSP_PORT",      "8554")
TLS_CERT_DIR = os.environ.get("TLS_CERT_DIR", "/app/tls")
TLS_FULLCHAIN_PATH = os.environ.get("TLS_FULLCHAIN_PATH", os.path.join(TLS_CERT_DIR, "fullchain.pem"))
TLS_PRIVKEY_PATH = os.environ.get("TLS_PRIVKEY_PATH", os.path.join(TLS_CERT_DIR, "privkey.pem"))
PUBLIC_WEB_SCHEME = os.environ.get("PUBLIC_WEB_SCHEME", "").strip().lower()
MTX_TOOLKIT_API = os.environ.get("MTX_TOOLKIT_API", "http://host.docker.internal:5002").rstrip("/")
MTX_TOOLKIT_RTSP_URL = os.environ.get("MTX_TOOLKIT_RTSP_URL", f"rtsp://viewer:VIEWER_PASS@host.docker.internal:{RTSP_PORT}")
MTX_TOOLKIT_NODE_NAME = os.environ.get("MTX_TOOLKIT_NODE_NAME", f"MediaMTX {PUBLIC_HOST}")
MTX_TOOLKIT_ENVIRONMENT = os.environ.get("MTX_TOOLKIT_ENVIRONMENT", "production")
MEDIAMTX_HLS_PROXY_TARGET = os.environ.get("MEDIAMTX_HLS_PROXY_TARGET", "http://host.docker.internal:8888").rstrip("/")
TUNNEL_HTTP_START = int(os.environ.get("TUNNEL_HTTP_START", "20080"))
TUNNEL_HTTP_END = int(os.environ.get("TUNNEL_HTTP_END", "20179"))
TUNNEL_CONTROL_START = int(os.environ.get("TUNNEL_CONTROL_START", "28000"))
TUNNEL_CONTROL_END = int(os.environ.get("TUNNEL_CONTROL_END", "28099"))
TUNNEL_RTSP_START = int(os.environ.get("TUNNEL_RTSP_START", "25554"))
TUNNEL_RTSP_END = int(os.environ.get("TUNNEL_RTSP_END", "25653"))


# Cache for MediaMTX metrics delta calculation
_mtx_metrics_cache: dict = {}
# active agent WebSocket connections: site_id -> WebSocket
active_agents: dict[str, WebSocket] = {}
agent_send_locks: dict[str, asyncio.Lock] = {}
pending_agent_requests: dict[str, tuple[str, asyncio.Future]] = {}
# traffic accumulators: site_id -> bytes this minute
traffic_acc:   dict[str, int] = {}
APP_ROOT = os.path.dirname(os.path.abspath(__file__))


def _tls_files_present() -> bool:
    return os.path.exists(TLS_FULLCHAIN_PATH) and os.path.exists(TLS_PRIVKEY_PATH)


def _parse_cert_time(value: str) -> datetime:
    normalized = " ".join(value.strip().split())
    return datetime.strptime(normalized, "%b %d %H:%M:%S %Y %Z")


def _load_tls_info_from_text(fullchain_pem: str, privkey_pem: str) -> TlsCertificateInfo:
    cert_dir = Path(TLS_CERT_DIR)
    cert_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cert_dir) as temp_dir:
        cert_path = Path(temp_dir) / "fullchain.pem"
        key_path = Path(temp_dir) / "privkey.pem"
        cert_path.write_text(fullchain_pem, encoding="utf-8")
        key_path.write_text(privkey_pem, encoding="utf-8")
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(str(cert_path), str(key_path))
        decoded = ssl._ssl._test_decode_cert(str(cert_path))

    subject_items = []
    for group in decoded.get("subject", []):
        for key, value in group:
            subject_items.append(f"{key}={value}")
    issuer_items = []
    for group in decoded.get("issuer", []):
        for key, value in group:
            issuer_items.append(f"{key}={value}")

    not_before = _parse_cert_time(decoded["notBefore"])
    not_after = _parse_cert_time(decoded["notAfter"])
    der_bytes = ssl.PEM_cert_to_DER_cert(fullchain_pem)
    fingerprint = hashlib.sha256(der_bytes).hexdigest()
    san = [value for key, value in decoded.get("subjectAltName", []) if key == "DNS"]

    return TlsCertificateInfo(
        subject=", ".join(subject_items) or "Unknown",
        issuer=", ".join(issuer_items) or "Unknown",
        san=san,
        not_before=not_before,
        not_after=not_after,
        expires_in_days=max((not_after - datetime.utcnow()).days, 0),
        fingerprint_sha256=fingerprint,
    )


def _read_tls_info() -> Optional[TlsCertificateInfo]:
    if not _tls_files_present():
        return None
    try:
        fullchain_pem = Path(TLS_FULLCHAIN_PATH).read_text(encoding="utf-8")
        privkey_pem = Path(TLS_PRIVKEY_PATH).read_text(encoding="utf-8")
        return _load_tls_info_from_text(fullchain_pem, privkey_pem)
    except Exception as exc:
        logger.warning("TLS certificate files exist but are invalid: %s", exc)
        return None


def _public_scheme() -> str:
    if PUBLIC_WEB_SCHEME in {"http", "https"}:
        return PUBLIC_WEB_SCHEME
    return "https" if _read_tls_info() else "http"


def _public_base_url() -> str:
    return f"{_public_scheme()}://{PUBLIC_HOST}"


def _public_hls_url(stream_path: str) -> str:
    return f"{_public_base_url()}/hls/{stream_path}/index.m3u8"


def _public_webrtc_url(stream_path: str) -> str:
    return f"{_public_base_url()}/webrtc/{stream_path}"


def _ws_scheme() -> str:
    return "wss" if _public_scheme() == "https" else "ws"


def _tls_status_payload() -> TlsStatus:
    cert = _read_tls_info()
    return TlsStatus(
        enabled=cert is not None,
        files_present=_tls_files_present(),
        public_host=PUBLIC_HOST,
        public_base_url=_public_base_url(),
        install_script_url=f"{_public_base_url()}/install.sh",
        cert=cert,
    )


BACKUP_VERSION = 1
DOCKER_SOCKET_PATH = os.environ.get("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
BACKUP_ROTATE_DIR = os.environ.get("BACKUP_ROTATE_DIR", "/app/backups")
BACKUP_ROTATE_KEEP = int(os.environ.get("BACKUP_ROTATE_KEEP", "10"))
BACKUP_ROTATE_PREFIX = "nvr-fleet-backup-"
STACK_SERVICE_SPECS = [
    {
        "key": "nginx",
        "label": "Nginx",
        "container_name": "nvr-nginx",
        "probe_kind": "http",
        "probe_target": "http://nginx/",
    },
    {
        "key": "fleet-server",
        "label": "Fleet Server",
        "container_name": "fleet-server",
        "probe_kind": "http",
        "probe_target": "http://fleet-server:8765/install.sh",
    },
    {
        "key": "admin-ui",
        "label": "Admin UI",
        "container_name": "nvr-admin-ui",
        "probe_kind": "http",
        "probe_target": "http://admin-ui/",
    },
    {
        "key": "mediamtx",
        "label": "MediaMTX",
        "container_name": "mediamtx",
        "probe_kind": "http",
        "probe_target": f"{MEDIAMTX_API}/v3/paths/list",
    },
    {
        "key": "mtx-toolkit-ui",
        "label": "MTX Toolkit UI",
        "container_name": "mtx-toolkit-frontend",
        "probe_kind": "http",
        "probe_target": "http://host.docker.internal:3001/",
    },
    {
        "key": "mtx-toolkit-api",
        "label": "MTX Toolkit API",
        "container_name": "mtx-toolkit-backend",
        "probe_kind": "http",
        "probe_target": "http://host.docker.internal:5002/api/health/",
    },
    {
        "key": "mtx-toolkit-worker",
        "label": "MTX Toolkit Worker",
        "container_name": "mtx-toolkit-celery-worker",
        "probe_kind": "none",
        "probe_target": "",
    },
    {
        "key": "mtx-toolkit-beat",
        "label": "MTX Toolkit Beat",
        "container_name": "mtx-toolkit-celery-beat",
        "probe_kind": "none",
        "probe_target": "",
    },
    {
        "key": "mtx-toolkit-postgres",
        "label": "MTX Toolkit Postgres",
        "container_name": "mtx-toolkit-postgres",
        "probe_kind": "tcp",
        "probe_target": ("host.docker.internal", 15433),
    },
    {
        "key": "mtx-toolkit-redis",
        "label": "MTX Toolkit Redis",
        "container_name": "mtx-toolkit-redis",
        "probe_kind": "tcp",
        "probe_target": ("host.docker.internal", 6380),
    },
]
STACK_SERVICE_BY_KEY = {item["key"]: item for item in STACK_SERVICE_SPECS}


def _resolve_runtime_path(*candidate_paths: tuple[str, ...]) -> str:
    candidates = []
    for rel_parts in candidate_paths:
        candidates.append(os.path.join(APP_ROOT, *rel_parts))
        candidates.append(os.path.join(os.path.dirname(APP_ROOT), *rel_parts))
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


INSTALL_SCRIPT_PATH = _resolve_runtime_path(("scripts", "install.sh"))
AGENT_SCRIPT_PATH = _resolve_runtime_path(
    ("agent", "agent.py"),
    ("fleet-agent", "agent.py"),
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_admin(creds: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if not creds or creds.credentials != ADMIN_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return creds.credentials


def _load_docker_client():
    if not os.path.exists(DOCKER_SOCKET_PATH):
        return None, f"Docker socket is not mounted at {DOCKER_SOCKET_PATH}"
    try:
        import docker as docker_sdk
    except ImportError:
        return None, "docker SDK is not installed in fleet-server runtime"
    try:
        client = docker_sdk.DockerClient(base_url=f"unix://{DOCKER_SOCKET_PATH}")
        client.ping()
        return client, ""
    except Exception as exc:
        return None, f"Cannot connect to Docker daemon: {exc}"


def _http_probe(url: str, timeout: int = 3) -> tuple[Optional[bool], str]:
    import urllib.error
    import urllib.parse
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        parsed = urllib.parse.urlsplit(url)
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        probe_url = urllib.parse.urlunsplit((
            parsed.scheme,
            netloc,
            parsed.path or "/",
            parsed.query,
            parsed.fragment,
        ))
        req = urllib.request.Request(probe_url, method="GET")
        if parsed.username is not None:
            username = urllib.parse.unquote(parsed.username)
            password = urllib.parse.unquote(parsed.password or "")
            basic_token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {basic_token}")
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(req, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            return True, f"HTTP {exc.code}"
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def _tcp_probe(host: str, port: int, timeout: int = 3) -> tuple[Optional[bool], str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "TCP connect ok"
    except Exception as exc:
        return False, str(exc)


def _probe_service(spec: dict) -> tuple[Optional[bool], str]:
    kind = spec.get("probe_kind")
    target = spec.get("probe_target")
    if kind == "http" and isinstance(target, str):
        return _http_probe(target)
    if kind == "tcp" and isinstance(target, tuple):
        host, port = target
        return _tcp_probe(host, int(port))
    return None, ""


def _stack_status_payload() -> StackStatus:
    client, docker_message = _load_docker_client()
    docker_available = client is not None
    containers = {}
    if client is not None:
        try:
            for container in client.containers.list(all=True):
                containers[container.name] = container
        except Exception as exc:
            docker_available = False
            docker_message = f"Cannot inspect Docker containers: {exc}"
        finally:
            client.close()

    services = []
    for spec in STACK_SERVICE_SPECS:
        probe_ok, probe_message = _probe_service(spec)
        status_text = "unknown"
        health_text = "unknown"
        if docker_available:
            container = containers.get(spec["container_name"])
            if container is None:
                status_text = "missing"
                health_text = "missing"
            else:
                attrs = container.attrs.get("State", {})
                status_text = attrs.get("Status", "unknown")
                health_text = attrs.get("Health", {}).get("Status", "unknown")
                if svc.get("probe_kind") == "none":
                    health_text = "N/A"
                elif health_text == "unknown":
                    if probe_ok is True:
                        health_text = "reachable"
                    elif probe_ok is False:
                        health_text = "unreachable"
        else:
            if probe_ok is True:
                status_text = "running"
                health_text = "reachable"
            elif probe_ok is False:
                status_text = "unavailable"
                health_text = "unreachable"

        services.append(StackServiceStatus(
            key=spec["key"],
            label=spec["label"],
            container_name=spec["container_name"],
            status=status_text,
            health=health_text,
            probe_ok=probe_ok,
            probe_message=probe_message,
            restart_supported=docker_available,
        ))

    return StackStatus(
        docker_available=docker_available,
        docker_message=docker_message,
        services=services,
    )


def _restart_stack_services_now(service_keys: list[str]) -> tuple[list[str], list[str]]:
    client, docker_message = _load_docker_client()
    if client is None:
        raise HTTPException(503, docker_message)
    restarted = []
    skipped = []
    try:
        for key in service_keys:
            spec = STACK_SERVICE_BY_KEY.get(key)
            if spec is None:
                skipped.append(key)
                continue
            try:
                container = client.containers.get(spec["container_name"])
                container.restart(timeout=10)
                restarted.append(key)
            except Exception as exc:
                skipped.append(f"{key}: {exc}")
    finally:
        client.close()
    return restarted, skipped


def _stack_logs_payload(service_key: str, tail: int) -> StackLogsOut:
    spec = STACK_SERVICE_BY_KEY.get(service_key)
    if spec is None:
        raise HTTPException(404, "Unknown service")

    client, docker_message = _load_docker_client()
    if client is None:
        raise HTTPException(503, docker_message)

    try:
        container = client.containers.get(spec["container_name"])
        raw = container.logs(
            tail=max(1, min(int(tail), 2000)),
            stdout=True,
            stderr=True,
            timestamps=True,
        )
    except Exception as exc:
        raise HTTPException(502, f"Cannot read logs for {service_key}: {exc}") from exc
    finally:
        client.close()

    return StackLogsOut(
        service=service_key,
        container_name=spec["container_name"],
        tail=max(1, min(int(tail), 2000)),
        text=raw.decode("utf-8", errors="replace"),
    )


def _schedule_self_restart() -> None:
    try:
        client, docker_message = _load_docker_client()
        if client is None:
            logger.warning("Cannot schedule fleet-server restart: %s", docker_message)
            return
        try:
            container = client.containers.get("fleet-server")
            container.restart(timeout=10)
        finally:
            client.close()
    except Exception as exc:
        logger.warning("fleet-server self-restart failed: %s", exc)


def _serialize_site(site: Site) -> dict:
    return {
        "id": site.id,
        "name": site.name,
        "city": site.city,
        "lat": site.lat,
        "lon": site.lon,
        "nvr_vendor": site.nvr_vendor,
        "nvr_ip": site.nvr_ip,
        "nvr_http_port": site.nvr_http_port,
        "nvr_control_port": site.nvr_control_port,
        "nvr_user": site.nvr_user,
        "nvr_pass": site.nvr_pass,
        "nvr_port": site.nvr_port,
        "tunnel_http_port": site.tunnel_http_port,
        "tunnel_control_port": site.tunnel_control_port,
        "tunnel_rtsp_port": site.tunnel_rtsp_port,
        "channel_count": site.channel_count,
        "stream_type": site.stream_type,
        "created_at": site.created_at.isoformat() if site.created_at else None,
    }


def _serialize_camera(camera: Camera) -> dict:
    return {
        "id": camera.id,
        "site_id": camera.site_id,
        "name": camera.name,
        "channel": camera.channel,
        "channel_id": camera.channel_id,
        "source_ref": camera.source_ref,
        "profile_ref": camera.profile_ref,
        "stream_type": camera.stream_type,
        "enabled": camera.enabled,
    }


def _serialize_agent(agent: Agent) -> dict:
    return {
        "site_id": agent.site_id,
        "token": agent.token,
        "version": agent.version,
        "uptime": agent.uptime,
    }


def _parse_datetime_value(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _build_backup_payload(db: Session) -> dict:
    return {
        "version": BACKUP_VERSION,
        "exported_at": datetime.utcnow().isoformat(),
        "sites": [_serialize_site(site) for site in db.query(Site).order_by(Site.created_at, Site.id).all()],
        "cameras": [_serialize_camera(camera) for camera in db.query(Camera).order_by(Camera.site_id, Camera.channel).all()],
        "agents": [_serialize_agent(agent) for agent in db.query(Agent).order_by(Agent.site_id).all()],
    }


def _backup_zip_bytes(db: Session) -> bytes:
    payload = _build_backup_payload(db)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backup.json", json.dumps(payload, ensure_ascii=False, indent=2))
        if os.path.exists(TLS_FULLCHAIN_PATH):
            archive.writestr("tls/fullchain.pem", Path(TLS_FULLCHAIN_PATH).read_text(encoding="utf-8"))
        if os.path.exists(TLS_PRIVKEY_PATH):
            archive.writestr("tls/privkey.pem", Path(TLS_PRIVKEY_PATH).read_text(encoding="utf-8"))
    return buffer.getvalue()


def _normalized_backup_keep(keep: Optional[int] = None) -> int:
    value = BACKUP_ROTATE_KEEP if keep is None else int(keep)
    return max(1, min(value, 100))


def _ensure_backup_rotate_dir() -> Path:
    path = Path(BACKUP_ROTATE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _backup_file_out(path: Path) -> BackupFileOut:
    stat = path.stat()
    return BackupFileOut(
        filename=path.name,
        size_bytes=stat.st_size,
        created_at=datetime.utcfromtimestamp(stat.st_mtime),
    )


def _list_rotated_backup_paths() -> list[Path]:
    base_dir = _ensure_backup_rotate_dir()
    items = [path for path in base_dir.glob(f"{BACKUP_ROTATE_PREFIX}*.zip") if path.is_file()]
    items.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return items


def _list_rotated_backups() -> list[BackupFileOut]:
    return [_backup_file_out(path) for path in _list_rotated_backup_paths()]


def _write_rotated_backup(db: Session, keep: Optional[int] = None) -> tuple[BackupFileOut, list[str], int]:
    target_dir = _ensure_backup_rotate_dir()
    keep_count = _normalized_backup_keep(keep)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    filename = f"{BACKUP_ROTATE_PREFIX}{timestamp}.zip"
    path = target_dir / filename
    if path.exists():
        path = target_dir / f"{BACKUP_ROTATE_PREFIX}{timestamp}-{secrets.token_hex(2)}.zip"
    path.write_bytes(_backup_zip_bytes(db))

    removed = []
    for old_path in _list_rotated_backup_paths()[keep_count:]:
        try:
            old_name = old_path.name
            old_path.unlink(missing_ok=True)
            removed.append(old_name)
        except OSError:
            continue

    return _backup_file_out(path), removed, keep_count


def _resolve_backup_file(filename: str) -> Path:
    safe_name = os.path.basename(filename)
    if safe_name != filename or not safe_name.startswith(BACKUP_ROTATE_PREFIX) or not safe_name.endswith(".zip"):
        raise HTTPException(404, "Backup file not found")
    path = _ensure_backup_rotate_dir() / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Backup file not found")
    return path


def _load_backup_archive(raw_bytes: bytes) -> tuple[dict, Optional[str], Optional[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as archive:
            if "backup.json" not in archive.namelist():
                raise HTTPException(400, "Backup archive must contain backup.json")
            payload = json.loads(archive.read("backup.json").decode("utf-8"))
            fullchain_pem = None
            privkey_pem = None
            if "tls/fullchain.pem" in archive.namelist():
                fullchain_pem = archive.read("tls/fullchain.pem").decode("utf-8")
            if "tls/privkey.pem" in archive.namelist():
                privkey_pem = archive.read("tls/privkey.pem").decode("utf-8")
            return payload, fullchain_pem, privkey_pem
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, "Backup file is not a valid ZIP archive") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "backup.json is not valid JSON") from exc


def _restore_backup_payload(db: Session, payload: dict) -> tuple[int, int, int]:
    _ensure_db_schema()
    if int(payload.get("version", 0)) < 1:
        raise HTTPException(400, "Unsupported backup version")

    sites = payload.get("sites") or []
    cameras = payload.get("cameras") or []
    agents = payload.get("agents") or []

    db.query(TrafficSample).delete()
    db.query(StreamStat).delete()
    db.query(Agent).delete()
    db.query(Camera).delete()
    db.query(Site).delete()
    db.flush()

    for item in sites:
        db.add(Site(
            id=item["id"],
            name=item["name"],
            city=item.get("city", ""),
            lat=item.get("lat", 0.0),
            lon=item.get("lon", 0.0),
            nvr_vendor=item.get("nvr_vendor", "hikvision"),
            nvr_ip=item["nvr_ip"],
            nvr_http_port=item.get("nvr_http_port", 80),
            nvr_control_port=item.get("nvr_control_port", _default_control_port(item.get("nvr_vendor", "hikvision"))),
            nvr_user=item.get("nvr_user", "admin"),
            nvr_pass=item.get("nvr_pass", ""),
            nvr_port=item.get("nvr_port", 554),
            tunnel_http_port=item.get("tunnel_http_port"),
            tunnel_control_port=item.get("tunnel_control_port"),
            tunnel_rtsp_port=item.get("tunnel_rtsp_port"),
            channel_count=item.get("channel_count", 0),
            stream_type=item.get("stream_type", "main"),
            created_at=_parse_datetime_value(item.get("created_at")) or datetime.utcnow(),
        ))
    db.flush()

    for item in cameras:
        db.add(Camera(
            id=item["id"],
            site_id=item["site_id"],
            name=item.get("name", ""),
            channel=item["channel"],
            channel_id=item.get("channel_id", _camera_channel_id(item["channel"], item.get("stream_type", "main"))),
            source_ref=item.get("source_ref"),
            profile_ref=item.get("profile_ref"),
            stream_type=item.get("stream_type", "main"),
            enabled=bool(item.get("enabled", True)),
        ))

    for item in agents:
        db.add(Agent(
            site_id=item["site_id"],
            token=item["token"],
            online=False,
            last_seen=None,
            version=item.get("version", ""),
            uptime=0,
        ))

    _ensure_all_site_defaults(db)
    db.flush()
    return len(sites), len(cameras), len(agents)


def _default_control_port(vendor: str) -> int:
    vendor_name = (vendor or "hikvision").strip().lower()
    if vendor_name == "dahua":
        return 37777
    return 8000


def _allocate_site_port(
    db: Session,
    field_name: str,
    start: int,
    end: int,
    *,
    exclude_site_id: Optional[str] = None,
) -> int:
    sites = db.query(Site).all()
    used = set()
    for site in sites:
        if exclude_site_id and site.id == exclude_site_id:
            continue
        value = getattr(site, field_name, None)
        if value:
            used.add(value)
    for port in range(start, end + 1):
        if port not in used:
            return port
    raise RuntimeError(f"No free ports left in range {start}-{end} for {field_name}")


def _ensure_site_defaults(site: Site, db: Session) -> bool:
    changed = False
    if not site.nvr_vendor:
        site.nvr_vendor = "hikvision"
        changed = True
    if not site.nvr_http_port:
        site.nvr_http_port = 80
        changed = True
    if not site.nvr_control_port:
        site.nvr_control_port = _default_control_port(site.nvr_vendor)
        changed = True
    if not site.tunnel_http_port:
        site.tunnel_http_port = _allocate_site_port(
            db, "tunnel_http_port", TUNNEL_HTTP_START, TUNNEL_HTTP_END, exclude_site_id=site.id
        )
        changed = True
    if not site.tunnel_control_port:
        site.tunnel_control_port = _allocate_site_port(
            db, "tunnel_control_port", TUNNEL_CONTROL_START, TUNNEL_CONTROL_END, exclude_site_id=site.id
        )
        changed = True
    if not site.tunnel_rtsp_port:
        site.tunnel_rtsp_port = _allocate_site_port(
            db, "tunnel_rtsp_port", TUNNEL_RTSP_START, TUNNEL_RTSP_END, exclude_site_id=site.id
        )
        changed = True
    return changed


def _ensure_all_site_defaults(db: Session) -> bool:
    changed = False
    for site in db.query(Site).all():
        changed = _ensure_site_defaults(site, db) or changed
    return changed


def require_agent_site(
    site_id: str,
    x_agent_token: Optional[str] = Header(None, alias="X-Agent-Token"),
    db: Session = Depends(get_db),
) -> str:
    if not x_agent_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing agent token")
    agent = db.query(Agent).filter_by(site_id=site_id, token=x_agent_token).first()
    if not agent:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token")
    _ensure_site_exists(site_id, db)
    return site_id


class SiteTunnelManager:
    def __init__(self) -> None:
        self.listeners: dict[tuple[str, str], asyncio.AbstractServer] = {}
        self.listener_specs: dict[tuple[str, str], dict] = {}
        self.connections: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def _specs_for_sites(self, sites: list[Site]) -> dict[tuple[str, str], dict]:
        specs = {}
        for site in sites:
            if not _site_is_configured(site):
                continue
            specs[(site.id, "http")] = {
                "site_id": site.id,
                "protocol": "http",
                "public_port": site.tunnel_http_port,
                "target_host": site.nvr_ip,
                "target_port": site.nvr_http_port,
            }
            specs[(site.id, "control")] = {
                "site_id": site.id,
                "protocol": "control",
                "public_port": site.tunnel_control_port,
                "target_host": site.nvr_ip,
                "target_port": site.nvr_control_port,
            }
            specs[(site.id, "rtsp")] = {
                "site_id": site.id,
                "protocol": "rtsp",
                "public_port": site.tunnel_rtsp_port,
                "target_host": site.nvr_ip,
                "target_port": site.nvr_port,
            }
        return specs

    async def sync(self, sites: list[Site]) -> None:
        desired = self._specs_for_sites(sites)
        async with self._lock:
            current_keys = set(self.listeners)
            desired_keys = set(desired)

            for key in current_keys - desired_keys:
                await self._close_listener(key)

            for key in desired_keys:
                spec = desired[key]
                current_spec = self.listener_specs.get(key)
                if current_spec == spec and key in self.listeners:
                    continue
                if key in self.listeners:
                    await self._close_listener(key)
                server = await asyncio.start_server(
                    lambda reader, writer, spec=spec: self._handle_client(spec, reader, writer),
                    host="0.0.0.0",
                    port=spec["public_port"],
                )
                self.listeners[key] = server
                self.listener_specs[key] = spec
                logger.info(
                    "Tunnel listener ready for site %s %s on port %s",
                    spec["site_id"],
                    spec["protocol"],
                    spec["public_port"],
                )

    async def _close_listener(self, key: tuple[str, str]) -> None:
        server = self.listeners.pop(key, None)
        self.listener_specs.pop(key, None)
        if server:
            server.close()
            await server.wait_closed()

    async def _handle_client(self, spec: dict, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        site_id = spec["site_id"]
        connection_id = uuid.uuid4().hex
        peer = writer.get_extra_info("peername")
        self.connections[connection_id] = {
            "site_id": site_id,
            "protocol": spec["protocol"],
            "writer": writer,
        }
        opened = False
        try:
            await call_agent(site_id, {
                "action": "tcp_open",
                "connection_id": connection_id,
                "protocol": spec["protocol"],
                "target_host": spec["target_host"],
                "target_port": spec["target_port"],
                "peer": repr(peer),
            }, timeout=15)
            opened = True

            while True:
                data = await reader.read(65536)
                if not data:
                    break
                sent = await send_to_agent(site_id, {
                    "action": "tcp_data",
                    "connection_id": connection_id,
                    "data": base64.b64encode(data).decode("ascii"),
                })
                if not sent:
                    break
        except HTTPException:
            pass
        except Exception as exc:
            logger.warning("Tunnel client %s/%s failed: %s", site_id, spec["protocol"], exc)
        finally:
            if opened:
                await send_to_agent(site_id, {
                    "action": "tcp_close",
                    "connection_id": connection_id,
                })
            await self.close_connection(connection_id)

    async def handle_agent_message(self, site_id: str, msg: dict) -> None:
        connection_id = msg.get("connection_id")
        if not connection_id:
            return
        entry = self.connections.get(connection_id)
        if not entry or entry["site_id"] != site_id:
            return
        mtype = msg.get("type")
        if mtype == "tcp_data":
            writer = entry["writer"]
            try:
                writer.write(base64.b64decode(msg.get("data", "")))
                await writer.drain()
            except Exception:
                await self.close_connection(connection_id)
        elif mtype == "tcp_close":
            await self.close_connection(connection_id)

    async def close_connection(self, connection_id: str) -> None:
        entry = self.connections.pop(connection_id, None)
        if not entry:
            return
        writer = entry["writer"]
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    async def close_site_connections(self, site_id: str) -> None:
        for connection_id, entry in list(self.connections.items()):
            if entry["site_id"] == site_id:
                await self.close_connection(connection_id)

    async def shutdown(self) -> None:
        for connection_id in list(self.connections):
            await self.close_connection(connection_id)
        for key in list(self.listeners):
            await self._close_listener(key)


tunnel_manager = SiteTunnelManager()


def _build_site_out(site: Site, db: Session) -> SiteOut:
    _ensure_site_defaults(site, db)
    agent = db.query(Agent).filter_by(site_id=site.id).first()
    so = SiteOut.model_validate(site)
    so.is_configured = _site_is_configured(site)
    so.agent_online = agent.online if agent else False
    so.agent_last_seen = agent.last_seen if agent else None
    so.camera_count = db.query(Camera).filter_by(site_id=site.id).count()
    so.online_streams = db.query(StreamStat).filter_by(site_id=site.id, ready=True).count()
    return so


def _update_stream_stats(site_id: str, streams: dict[str, bool], db: Session) -> None:
    now = datetime.utcnow()
    normalized = {normalize_stream_path(path): ready for path, ready in streams.items()}
    existing = {
        stat.stream_path: stat
        for stat in db.query(StreamStat).filter_by(site_id=site_id).all()
    }

    for path, ready in normalized.items():
        stat = existing.get(path)
        if stat:
            stat.ready = ready
            stat.updated = now
        else:
            db.add(StreamStat(
                site_id=site_id,
                stream_path=path,
                ready=ready,
                updated=now,
            ))

    for path, stat in existing.items():
        if path not in normalized:
            stat.ready = False
            stat.updated = now


def _camera_channel_id(channel: int, stream_type: str) -> int:
    subtype = 2 if stream_type == "sub" else 1
    return channel * 100 + subtype


def _camera_stream_path(site_id: str, camera: Camera) -> str:
    return public_stream_path(site_id, camera.channel)


def _site_is_configured(site: Site) -> bool:
    return bool((site.nvr_ip or "").strip())


def _normalize_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _normalize_site_patch(payload: dict) -> dict:
    normalized = dict(payload)
    text_fields = {"name", "city", "nvr_vendor", "nvr_ip", "nvr_user", "nvr_pass", "stream_type"}
    for key in text_fields:
        if key in normalized and normalized[key] is not None:
            normalized[key] = _normalize_text(normalized[key])

    if "nvr_vendor" in normalized and normalized["nvr_vendor"]:
        normalized["nvr_vendor"] = normalized["nvr_vendor"].lower()

    if "nvr_http_port" in normalized and not normalized["nvr_http_port"]:
        normalized["nvr_http_port"] = 80
    if "nvr_port" in normalized and not normalized["nvr_port"]:
        normalized["nvr_port"] = 554
    if "nvr_control_port" in normalized and not normalized["nvr_control_port"]:
        vendor = normalized.get("nvr_vendor", "hikvision")
        normalized["nvr_control_port"] = _default_control_port(vendor)
    if "channel_count" in normalized and normalized["channel_count"] is not None:
        normalized["channel_count"] = max(0, int(normalized["channel_count"]))

    return normalized


def _ensure_site_exists(site_id: str, db: Session) -> Site:
    site = db.query(Site).filter_by(id=site_id).first()
    if not site:
        raise HTTPException(404, "Site not found")
    return site


def _ensure_unique_camera_channel(db: Session, site_id: str, channel: int, exclude_id: Optional[int] = None):
    query = db.query(Camera).filter_by(site_id=site_id, channel=channel)
    if exclude_id is not None:
        query = query.filter(Camera.id != exclude_id)
    if query.first():
        raise HTTPException(409, f"Channel {channel} already exists")


def _get_site_camera(db: Session, site_id: str, camera_id: int) -> Camera:
    cam = db.query(Camera).filter_by(id=camera_id, site_id=site_id).first()
    if not cam:
        raise HTTPException(404, "Camera not found")
    return cam


def _site_payload(site: Site) -> dict:
    return {
        "id": site.id,
        "name": site.name,
        "city": site.city,
        "vendor": site.nvr_vendor,
        "channel_count": site.channel_count,
        "is_configured": _site_is_configured(site),
        "nvr_ip": site.nvr_ip,
        "nvr_http_port": site.nvr_http_port,
        "nvr_control_port": site.nvr_control_port,
        "nvr_user": site.nvr_user,
        "nvr_pass": site.nvr_pass,
        "nvr_port": site.nvr_port,
        "public_host": PUBLIC_HOST,
        "tunnel_http_port": site.tunnel_http_port,
        "tunnel_control_port": site.tunnel_control_port,
        "tunnel_rtsp_port": site.tunnel_rtsp_port,
        "stream_type": site.stream_type,
    }


def _camera_payload(camera: Camera) -> dict:
    return {
        "id": camera.id,
        "site_id": camera.site_id,
        "name": camera.name,
        "channel": camera.channel,
        "channel_id": camera.channel_id,
        "source_ref": camera.source_ref,
        "profile_ref": camera.profile_ref,
        "stream_type": camera.stream_type,
        "enabled": camera.enabled,
    }


async def _sync_tunnel_listeners(db: Session) -> None:
    if _ensure_all_site_defaults(db):
        db.commit()
    sites = db.query(Site).all()
    await tunnel_manager.sync(sites)


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(_mtx_metrics_poll_loop())
    db = SessionLocal()
    try:
        mediamtx_changed = _rebuild_mediamtx(db)
        await _sync_tunnel_listeners(db)
        if mediamtx_changed:
            try:
                await asyncio.to_thread(_restart_stack_services_now, ["mediamtx"])
                await asyncio.sleep(2)
            except Exception as exc:
                logger.warning("MediaMTX restart on startup sync failed: %s", exc)
        await asyncio.to_thread(_sync_mtx_toolkit_node_streams)
    finally:
        db.close()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await tunnel_manager.shutdown()


# ─── WebSocket: Agent ────────────────────────────────────────────────────────

@app.websocket("/ws/agent/{site_id}")
async def agent_ws(ws: WebSocket, site_id: str, db: Session = Depends(get_db)):
    token = ws.query_params.get("token", "")
    agent = db.query(Agent).filter(Agent.site_id == site_id, Agent.token == token).first()
    if not agent:
        await ws.close(code=4403)
        return

    await ws.accept()
    active_agents[site_id] = ws
    agent_send_locks[site_id] = asyncio.Lock()
    agent.online = True
    agent.last_seen = datetime.utcnow()
    db.commit()
    logger.info(f"Agent connected: {site_id}")
    await _deploy_config(site_id, db)

    try:
        async for raw in ws.iter_text():
            msg = json.loads(raw)
            mtype = msg.get("type")
            reply_to = msg.get("reply_to")

            if reply_to:
                pending = pending_agent_requests.get(reply_to)
                if pending:
                    _, future = pending
                    if not future.done():
                        future.set_result(msg)
                continue

            if mtype == "heartbeat":
                agent.last_seen = datetime.utcnow()
                agent.version   = msg.get("version", "")
                agent.uptime    = msg.get("uptime", 0)
                db.commit()

            elif mtype == "traffic":
                # {"type":"traffic","streams":{"site1/cam01":{"rx":1024,"tx":2048}}}
                for path, stats in msg.get("streams", {}).items():
                    sample = TrafficSample(
                        site_id=site_id,
                        stream_path=path,
                        rx_bytes=stats.get("rx", 0),
                        tx_bytes=stats.get("tx", 0),
                        ts=datetime.utcnow(),
                    )
                    db.add(sample)
                db.commit()

            elif mtype == "stream_status":
                _update_stream_stats(site_id, msg.get("streams", {}), db)
                db.commit()
                asyncio.create_task(_schedule_mtx_toolkit_sync(1.5))

            elif mtype in {"tcp_data", "tcp_close"}:
                await tunnel_manager.handle_agent_message(site_id, msg)

    except WebSocketDisconnect:
        pass
    finally:
        active_agents.pop(site_id, None)
        agent_send_locks.pop(site_id, None)
        agent.online = False
        now = datetime.utcnow()
        for stat in db.query(StreamStat).filter_by(site_id=site_id).all():
            stat.ready = False
            stat.updated = now
        for request_id, pending in list(pending_agent_requests.items()):
            pending_site_id, future = pending
            if pending_site_id == site_id and not future.done():
                future.set_result({"ok": False, "error": "Agent disconnected"})
        await tunnel_manager.close_site_connections(site_id)
        db.commit()
        asyncio.create_task(_schedule_mtx_toolkit_sync(0.5))
        logger.info(f"Agent disconnected: {site_id}")


async def send_to_agent(site_id: str, payload: dict) -> bool:
    ws = active_agents.get(site_id)
    if not ws:
        return False
    try:
        lock = agent_send_locks.setdefault(site_id, asyncio.Lock())
        async with lock:
            await ws.send_text(json.dumps(payload))
        return True
    except Exception as e:
        logger.warning(f"send_to_agent {site_id}: {e}")
        return False


async def call_agent(site_id: str, payload: dict, timeout: int = 20) -> dict:
    request_id = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_agent_requests[request_id] = (site_id, future)
    sent = await send_to_agent(site_id, {**payload, "request_id": request_id})
    if not sent:
        pending_agent_requests.pop(request_id, None)
        raise HTTPException(503, "Agent is offline")
    try:
        reply = await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "Agent request timed out") from exc
    finally:
        pending_agent_requests.pop(request_id, None)
    if not reply.get("ok", False):
        raise HTTPException(502, reply.get("error", "Agent request failed"))
    return reply


# ─── Sites ───────────────────────────────────────────────────────────────────

@app.get("/api/sites", response_model=list[SiteOut])
def list_sites(db: Session = Depends(get_db), _=Depends(require_admin)):
    sites = db.query(Site).all()
    return [_build_site_out(site, db) for site in sites]


@app.post("/api/sites", response_model=InstallResponse)
async def create_site(data: SiteCreate, db: Session = Depends(get_db), _=Depends(require_admin)):
    payload = _normalize_site_patch(data.model_dump())
    if not payload["name"]:
        raise HTTPException(422, "Site name is required")
    site = Site(
        name       = payload["name"],
        city       = payload["city"],
        lat        = data.lat,
        lon        = data.lon,
        nvr_vendor = payload["nvr_vendor"],
        nvr_ip     = payload["nvr_ip"],
        nvr_http_port = payload["nvr_http_port"],
        nvr_control_port = payload["nvr_control_port"],
        nvr_user   = payload["nvr_user"],
        nvr_pass   = payload["nvr_pass"],
        nvr_port   = payload["nvr_port"],
        channel_count = payload["channel_count"],
        stream_type   = payload["stream_type"],
        created_at    = datetime.utcnow(),
    )
    db.add(site)
    db.flush()

    # Auto-generate cameras
    for ch in range(1, payload["channel_count"] + 1):
        subtype = 2 if payload["stream_type"] == "sub" else 1
        channel_id = ch * 100 + subtype
        cam = Camera(
            site_id    = site.id,
            name       = f"Cam {ch:02d}",
            channel    = ch,
            channel_id = channel_id,
            source_ref = None,
            profile_ref = None,
            stream_type= payload["stream_type"],
            enabled    = True,
        )
        db.add(cam)

    token = secrets.token_urlsafe(32)
    agent = Agent(site_id=site.id, token=token, online=False)
    db.add(agent)
    _ensure_site_defaults(site, db)
    db.commit()
    db.refresh(site)

    _rebuild_mediamtx(db)
    await _sync_tunnel_listeners(db)

    install_cmd = (
        f"curl -fsSL {_public_base_url()}/install.sh | "
        f"bash -s -- --site {site.id} --token {token} --server {PUBLIC_HOST} --scheme {_public_scheme()}"
    )
    return InstallResponse(site_id=site.id, token=token, install_cmd=install_cmd)


@app.get("/api/sites/{site_id}", response_model=SiteOut)
def get_site(site_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    site = db.query(Site).filter_by(id=site_id).first()
    if not site:
        raise HTTPException(404, "Site not found")
    return _build_site_out(site, db)


@app.put("/api/sites/{site_id}", response_model=SiteOut)
async def update_site(site_id: str, data: SiteUpdate, db: Session = Depends(get_db), _=Depends(require_admin)):
    site = _ensure_site_exists(site_id, db)
    payload = _normalize_site_patch(data.model_dump(exclude_none=True))
    if "name" in payload and not payload["name"]:
        raise HTTPException(422, "Site name is required")
    for k, v in payload.items():
        setattr(site, k, v)
    _ensure_site_defaults(site, db)
    db.commit()
    db.refresh(site)
    await _sync_tunnel_listeners(db)
    await _deploy_config(site_id, db)
    return _build_site_out(site, db)


@app.delete("/api/sites/{site_id}")
async def delete_site(site_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    site = db.query(Site).filter_by(id=site_id).first()
    if not site:
        raise HTTPException(404)
    await send_to_agent(site_id, {"action": "shutdown"})
    db.query(Camera).filter_by(site_id=site_id).delete()
    db.query(Agent).filter_by(site_id=site_id).delete()
    db.query(StreamStat).filter_by(site_id=site_id).delete()
    db.query(TrafficSample).filter_by(site_id=site_id).delete()
    db.delete(site)
    db.commit()
    _rebuild_mediamtx(db)
    await _sync_tunnel_listeners(db)
    return {"status": "deleted"}


# ─── Cameras ─────────────────────────────────────────────────────────────────

@app.get("/api/sites/{site_id}/cameras", response_model=list[CameraOut])
def list_cameras(site_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    _ensure_site_exists(site_id, db)
    return db.query(Camera).filter_by(site_id=site_id).order_by(Camera.channel).all()


@app.post("/api/sites/{site_id}/cameras", response_model=CameraOut)
async def add_camera(site_id: str, data: CameraCreate, db: Session = Depends(get_db), _=Depends(require_admin)):
    _ensure_site_exists(site_id, db)
    _ensure_unique_camera_channel(db, site_id, data.channel)
    cam = Camera(
        site_id=site_id,
        name=data.name,
        channel=data.channel,
        channel_id=_camera_channel_id(data.channel, data.stream_type),
        source_ref=data.source_ref,
        profile_ref=data.profile_ref,
        stream_type=data.stream_type,
        enabled=data.enabled,
    )
    db.add(cam)
    db.commit()
    db.refresh(cam)
    await _deploy_config(site_id, db)
    return cam


@app.put("/api/sites/{site_id}/cameras/{cam_id}", response_model=CameraOut)
async def update_camera(site_id: str, cam_id: int, data: CameraUpdate,
                        db: Session = Depends(get_db), _=Depends(require_admin)):
    cam = db.query(Camera).filter_by(id=cam_id, site_id=site_id).first()
    if not cam:
        raise HTTPException(404)
    next_channel = data.channel if data.channel is not None else cam.channel
    next_stream_type = data.stream_type if data.stream_type is not None else cam.stream_type
    if next_channel != cam.channel:
        _ensure_unique_camera_channel(db, site_id, next_channel, exclude_id=cam.id)
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(cam, k, v)
    if data.channel is not None or data.stream_type is not None:
        cam.channel_id = _camera_channel_id(next_channel, next_stream_type)
    db.commit()
    db.refresh(cam)
    await _deploy_config(site_id, db)
    return cam


@app.delete("/api/sites/{site_id}/cameras/{cam_id}")
async def delete_camera(site_id: str, cam_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    cam = db.query(Camera).filter_by(id=cam_id, site_id=site_id).first()
    if not cam:
        raise HTTPException(404)
    db.delete(cam)
    db.commit()
    await _deploy_config(site_id, db)
    return {"status": "deleted"}


@app.post("/api/sites/{site_id}/cameras/bulk")
async def bulk_update_cameras(site_id: str, cameras: list[CameraUpdate],
                               db: Session = Depends(get_db), _=Depends(require_admin)):
    """Batch enable/disable/rename cameras"""
    _ensure_site_exists(site_id, db)
    existing = {
        cam.id: cam
        for cam in db.query(Camera).filter_by(site_id=site_id).all()
    }
    planned_channels = {cam.id: cam.channel for cam in existing.values()}

    for upd in cameras:
        if upd.id and upd.id in existing and upd.channel is not None:
            planned_channels[upd.id] = upd.channel

    used_channels = [channel for channel in planned_channels.values()]
    if len(used_channels) != len(set(used_channels)):
        raise HTTPException(409, "Duplicate camera channels are not allowed")

    for upd in cameras:
        if upd.id:
            cam = existing.get(upd.id)
            if cam:
                next_channel = planned_channels[cam.id]
                next_stream_type = upd.stream_type if upd.stream_type is not None else cam.stream_type
                for k, v in upd.model_dump(exclude_none=True).items():
                    setattr(cam, k, v)
                if upd.channel is not None or upd.stream_type is not None:
                    cam.channel_id = _camera_channel_id(next_channel, next_stream_type)
    db.commit()
    await _deploy_config(site_id, db)
    return {"status": "ok"}


def _replace_site_cameras(site: Site, items: list[AgentCameraSyncItem], db: Session) -> list[Camera]:
    existing = db.query(Camera).filter_by(site_id=site.id).order_by(Camera.channel).all()
    by_id = {cam.id: cam for cam in existing}
    by_channel = {cam.channel: cam for cam in existing}
    used_channels = [item.channel for item in items]
    if len(used_channels) != len(set(used_channels)):
        raise HTTPException(409, "Duplicate camera channels are not allowed")

    kept_ids: set[int] = set()
    for item in items:
        cam = None
        if item.id is not None:
            cam = by_id.get(item.id)
            if not cam:
                raise HTTPException(404, f"Camera {item.id} not found")
        else:
            cam = by_channel.get(item.channel)

        if cam:
            cam.name = item.name
            cam.channel = item.channel
            cam.source_ref = item.source_ref
            cam.profile_ref = item.profile_ref
            cam.stream_type = item.stream_type
            cam.enabled = item.enabled
            cam.channel_id = _camera_channel_id(item.channel, item.stream_type)
        else:
            cam = Camera(
                site_id=site.id,
                name=item.name,
                channel=item.channel,
                channel_id=_camera_channel_id(item.channel, item.stream_type),
                source_ref=item.source_ref,
                profile_ref=item.profile_ref,
                stream_type=item.stream_type,
                enabled=item.enabled,
            )
            db.add(cam)
            db.flush()

        kept_ids.add(cam.id)

    for cam in existing:
        if cam.id not in kept_ids:
            db.delete(cam)

    site.channel_count = max([item.channel for item in items], default=0)
    db.flush()
    return db.query(Camera).filter_by(site_id=site.id).order_by(Camera.channel).all()


@app.get("/api/agent/sites/{site_id}/bundle")
def get_agent_bundle(site_id: str, _=Depends(require_agent_site), db: Session = Depends(get_db)):
    site = _ensure_site_exists(site_id, db)
    cameras = db.query(Camera).filter_by(site_id=site.id).order_by(Camera.channel).all()
    return {
        "site": _site_payload(site),
        "cameras": [_camera_payload(camera) for camera in cameras],
        "thick_client": {
            "host": PUBLIC_HOST,
            "http_port": site.tunnel_http_port,
            "control_port": site.tunnel_control_port,
            "rtsp_port": site.tunnel_rtsp_port,
        },
    }


@app.put("/api/agent/sites/{site_id}/site")
async def update_agent_site_config(
    site_id: str,
    data: AgentSiteConfigUpdate,
    _=Depends(require_agent_site),
    db: Session = Depends(get_db),
):
    site = _ensure_site_exists(site_id, db)
    payload = _normalize_site_patch(data.model_dump(exclude_none=True))
    if "name" in payload and not payload["name"]:
        raise HTTPException(422, "Site name is required")
    for key, value in payload.items():
        setattr(site, key, value)
    _ensure_site_defaults(site, db)
    db.commit()
    db.refresh(site)
    await _sync_tunnel_listeners(db)
    await _deploy_config(site_id, db)
    return {"status": "ok", "site": _site_payload(site)}


@app.put("/api/agent/sites/{site_id}/cameras/replace")
async def replace_agent_cameras(
    site_id: str,
    items: list[AgentCameraSyncItem],
    _=Depends(require_agent_site),
    db: Session = Depends(get_db),
):
    site = _ensure_site_exists(site_id, db)
    cameras = _replace_site_cameras(site, items, db)
    db.commit()
    await _deploy_config(site_id, db)
    return {
        "status": "ok",
        "cameras": [CameraOut.model_validate(camera).model_dump() for camera in cameras],
    }


# ─── Streams & Traffic ────────────────────────────────────────────────────────

@app.get("/api/sites/{site_id}/streams")
def get_stream_stats(site_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    _ensure_site_exists(site_id, db)
    stats = db.query(StreamStat).filter_by(site_id=site_id).all()
    return [StreamStatOut.model_validate(s) for s in stats]


@app.get("/api/sites/{site_id}/traffic")
def get_traffic(site_id: str, hours: int = 1, db: Session = Depends(get_db), _=Depends(require_admin)):
    _ensure_site_exists(site_id, db)
    since = datetime.utcnow() - timedelta(hours=hours)
    samples = (db.query(TrafficSample)
               .filter(TrafficSample.site_id == site_id, TrafficSample.ts >= since)
               .order_by(TrafficSample.ts)
               .all())
    return [TrafficOut.model_validate(s) for s in samples]


@app.get("/api/traffic/total")
def get_total_traffic(hours: int = 24, db: Session = Depends(get_db), _=Depends(require_admin)):
    since = datetime.utcnow() - timedelta(hours=hours)
    samples = (db.query(TrafficSample)
               .filter(TrafficSample.ts >= since)
               .order_by(TrafficSample.ts)
               .all())
    return [TrafficOut.model_validate(s) for s in samples]



@app.get("/api/sites/{site_id}/traffic/mtx")
def get_site_traffic_mtx(site_id: str, hours: int = 1, db: Session = Depends(get_db), _=Depends(require_admin)):
    """Traffic from MediaMTX metrics API (per site)."""
    samples = _parse_mtx_metrics(site_id, hours)
    return samples


@app.get("/api/traffic/total/mtx")
def get_total_traffic_mtx(hours: int = 24, db: Session = Depends(get_db), _=Depends(require_admin)):
    """Traffic from MediaMTX metrics API (all sites)."""
    samples = _parse_mtx_metrics(None, hours)
    return samples


# Background MediaMTX metrics poller
_mtx_samples: list[dict] = []
_mtx_last_poll: dict = {}

def _poll_mtx_metrics():
    """Poll MediaMTX metrics and store delta samples. Called by background scheduler."""
    import re
    global _mtx_samples, _mtx_last_poll
    try:
        resp = requests.get(
            f"{MEDIAMTX_HLS_PROXY_TARGET.replace(':8888', ':9998')}/metrics",
            auth=(mediamtx_internal_api_user(), mediamtx_internal_api_pass()),
            timeout=3,
        )
        resp.raise_for_status()
    except Exception:
        return

    now = datetime.utcnow()
    rx_bytes = {}
    tx_bytes = {}

    for line in resp.text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = re.match(r'paths_bytes_received\{name="([^"]+)"[^}]*\}\s+([\d.]+)', line)
        if m:
            rx_bytes[m.group(1)] = int(float(m.group(2)))
        m = re.match(r'paths_bytes_sent\{name="([^"]+)"[^}]*\}\s+([\d.]+)', line)
        if m:
            tx_bytes[m.group(1)] = int(float(m.group(2)))

    all_paths = set(list(rx_bytes.keys()) + list(tx_bytes.keys()))
    for path in all_paths:
        cur_rx = rx_bytes.get(path, 0)
        cur_tx = tx_bytes.get(path, 0)
        prev = _mtx_last_poll.get(path, {})
        delta_rx = max(cur_rx - prev.get("rx", cur_rx), 0)
        delta_tx = max(cur_tx - prev.get("tx", cur_tx), 0)
        _mtx_samples.append({
            "ts": now.isoformat() + "Z",
            "rx_bytes": delta_rx,
            "tx_bytes": delta_tx,
            "stream_path": path,
        })

    # Keep only last 24h of samples
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat() + "Z"
    _mtx_samples = [s for s in _mtx_samples if s["ts"] >= cutoff]

    _mtx_last_poll = {
        path: {"rx": rx_bytes.get(path, 0), "tx": tx_bytes.get(path, 0)}
        for path in all_paths
    }
    _mtx_last_poll["__ts__"] = datetime.utcnow().timestamp()


def _parse_mtx_metrics(site_id, hours: int):
    """Return stored MediaMTX traffic samples filtered by site_id and hours."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat() + "Z"
    result = []
    for s in _mtx_samples:
        if s["ts"] < cutoff:
            continue
        if site_id is None or s["stream_path"].startswith(f"site{site_id}/"):
            result.append(s)
    return result

async def _mtx_metrics_poll_loop():
    """Background loop: poll MediaMTX metrics every 30s."""
    global _mtx_samples, _mtx_last_poll
    await asyncio.sleep(10)  # initial delay
    while True:
        try:
            import re as _re
            _target = MEDIAMTX_HLS_PROXY_TARGET.replace(':8888', ':9998')
            import requests as _requests
            _resp = await asyncio.to_thread(
                lambda: _requests.get(
                    f"{_target}/metrics",
                    auth=(mediamtx_internal_api_user(), mediamtx_internal_api_pass()),
                    timeout=3,
                )
            )
            _resp.raise_for_status()
            _now = datetime.utcnow()
            _rx = {}
            _tx = {}
            for _line in _resp.text.splitlines():
                if _line.startswith("#") or not _line.strip():
                    continue
                _m = _re.match(r'paths_bytes_received\{name="([^"]+)"[^}]*\}\s+([\d.]+)', _line)
                if _m:
                    _rx[_m.group(1)] = int(float(_m.group(2)))
                _m = _re.match(r'paths_bytes_sent\{name="([^"]+)"[^}]*\}\s+([\d.]+)', _line)
                if _m:
                    _tx[_m.group(1)] = int(float(_m.group(2)))
            _all = set(list(_rx.keys()) + list(_tx.keys()))
            for _path in _all:
                _prev = _mtx_last_poll.get(_path, {})
                _drx = max(_rx.get(_path, 0) - _prev.get("rx", _rx.get(_path, 0)), 0)
                _dtx = max(_tx.get(_path, 0) - _prev.get("tx", _tx.get(_path, 0)), 0)
                _mtx_samples.append({
                    "ts": _now.isoformat() + "Z",
                    "rx_bytes": _drx,
                    "tx_bytes": _dtx,
                    "stream_path": _path,
                })
            _cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat() + "Z"
            _mtx_samples = [s for s in _mtx_samples if s["ts"] >= _cutoff]
            _mtx_last_poll = {p: {"rx": _rx.get(p, 0), "tx": _tx.get(p, 0)} for p in _all}
            _mtx_last_poll["__ts__"] = datetime.utcnow().timestamp()
            logger.info("MTX poll: %d paths, samples=%d", len(_all), len(_mtx_samples))
        except Exception as exc:
            logger.info("MTX metrics poll error: %s", exc)
        await asyncio.sleep(30)




@app.get("/api/traffic/realtime")
def get_traffic_realtime(_=Depends(require_admin)):
    """Real-time traffic: latest MTX metrics sample (rx/tx bytes per second)."""
    import re
    try:
        resp = requests.get(
            f"{MEDIAMTX_HLS_PROXY_TARGET.replace(':8888', ':9998')}/metrics",
            auth=(mediamtx_internal_api_user(), mediamtx_internal_api_pass()),
            timeout=3,
        )
        resp.raise_for_status()
    except Exception:
        return {"rx_bps": 0, "tx_bps": 0, "streams": {}}

    rx_bytes = {}
    tx_bytes = {}
    for line in resp.text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = re.match(r'paths_bytes_received\{name="([^"]+)"[^}]*\}\s+([\d.]+)', line)
        if m:
            rx_bytes[m.group(1)] = int(float(m.group(2)))
        m = re.match(r'paths_bytes_sent\{name="([^"]+)"[^}]*\}\s+([\d.]+)', line)
        if m:
            tx_bytes[m.group(1)] = int(float(m.group(2)))

    global _mtx_last_poll
    now_ts = datetime.utcnow().timestamp()
    prev_ts = _mtx_last_poll.get("__ts__", now_ts - 30)
    interval = max(now_ts - prev_ts, 1)

    streams = {}
    total_rx_bps = 0
    total_tx_bps = 0
    for path in set(list(rx_bytes.keys()) + list(tx_bytes.keys())):
        prev = _mtx_last_poll.get(path, {})
        delta_rx = max(rx_bytes.get(path, 0) - prev.get("rx", 0), 0)
        delta_tx = max(tx_bytes.get(path, 0) - prev.get("tx", 0), 0)
        rx_bps = int(delta_rx / interval)
        tx_bps = int(delta_tx / interval)
        streams[path] = {"rx_bps": rx_bps, "tx_bps": tx_bps}
        total_rx_bps += rx_bps
        total_tx_bps += tx_bps

    return {"rx_bps": total_rx_bps, "tx_bps": total_tx_bps, "streams": streams}



@app.get("/api/sites/{site_id}/archive", response_model=list[ArchiveRecordingOut])
async def list_archive(
    site_id: str,
    camera_id: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    site = _ensure_site_exists(site_id, db)
    cameras = db.query(Camera).filter_by(site_id=site_id).order_by(Camera.channel).all()
    if camera_id is not None:
        _get_site_camera(db, site_id, camera_id)
    end = end or datetime.utcnow()
    start = start or (end - timedelta(hours=24))
    if start >= end:
        raise HTTPException(400, "Start time must be before end time")
    reply = await call_agent(site_id, {
        "action": "archive_list",
        "site": _site_payload(site),
        "cameras": [_camera_payload(camera) for camera in cameras],
        "camera_id": camera_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "limit": max(1, min(limit, 1000)),
    })
    return [ArchiveRecordingOut.model_validate(item) for item in reply.get("items", [])]


@app.post("/api/sites/{site_id}/archive/playback", response_model=ArchivePlaybackOut)
async def start_archive_playback(
    site_id: str,
    data: ArchivePlaybackRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    site = _ensure_site_exists(site_id, db)
    camera = _get_site_camera(db, site_id, data.camera_id)
    if data.start >= data.end:
        raise HTTPException(400, "Start time must be before end time")
    reply = await call_agent(site_id, {
        "action": "archive_start_playback",
        "site": _site_payload(site),
        "camera": _camera_payload(camera),
        "start": data.start.isoformat(),
        "end": data.end.isoformat(),
    }, timeout=30)
    stream_path = reply["stream_path"]
    return ArchivePlaybackOut(
        session_id=reply["session_id"],
        stream_path=stream_path,
        vendor=reply.get("vendor", site.nvr_vendor),
        rtsp_url=f"rtsp://viewer:VIEWER_PASS@{PUBLIC_HOST}:{RTSP_PORT}/{stream_path}",
        hls_url=_public_hls_url(stream_path),
        webrtc_url=_public_webrtc_url(stream_path),
        expires_at=datetime.fromisoformat(reply["expires_at"]),
    )


@app.delete("/api/sites/{site_id}/archive/playback/{session_id}")
async def stop_archive_playback(
    site_id: str,
    session_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    site = _ensure_site_exists(site_id, db)
    await call_agent(site_id, {
        "action": "archive_stop_playback",
        "site": _site_payload(site),
        "session_id": session_id,
    })
    return {"status": "stopped"}


@app.get("/api/system/tls", response_model=TlsStatus)
def get_tls_status(_=Depends(require_admin)):
    return _tls_status_payload()


@app.put("/api/system/tls", response_model=TlsStatus)
def update_tls_certificates(data: TlsUpdateRequest, _=Depends(require_admin)):
    fullchain_pem = data.fullchain_pem.strip()
    privkey_pem = data.privkey_pem.strip()
    if "BEGIN CERTIFICATE" not in fullchain_pem:
        raise HTTPException(400, "fullchain_pem must contain a PEM certificate chain")
    if "PRIVATE KEY" not in privkey_pem:
        raise HTTPException(400, "privkey_pem must contain a PEM private key")
    try:
        _write_tls_files(fullchain_pem, privkey_pem)
    except Exception as exc:
        raise HTTPException(400, f"Invalid TLS certificate or key: {exc}") from exc
    return _tls_status_payload()


@app.delete("/api/system/tls", response_model=TlsStatus)
def delete_tls_certificates(_=Depends(require_admin)):
    for path in (TLS_FULLCHAIN_PATH, TLS_PRIVKEY_PATH):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    return _tls_status_payload()


@app.get("/api/system/stack", response_model=StackStatus)
def get_stack_status(_=Depends(require_admin)):
    return _stack_status_payload()


@app.get("/api/system/stack/logs", response_model=StackLogsOut)
def get_stack_logs(service: str, tail: int = 200, _=Depends(require_admin)):
    return _stack_logs_payload(service, tail)


@app.post("/api/system/stack/restart", response_model=StackRestartResult)
def restart_stack_services(
    data: StackRestartRequest,
    background_tasks: BackgroundTasks,
    _=Depends(require_admin),
):
    requested = data.services or [item["key"] for item in STACK_SERVICE_SPECS]
    unknown = [key for key in requested if key not in STACK_SERVICE_BY_KEY]
    if unknown:
        raise HTTPException(400, f"Unknown services: {', '.join(unknown)}")

    immediate = [key for key in requested if key != "fleet-server"]
    restarted, skipped = _restart_stack_services_now(immediate) if immediate else ([], [])
    scheduled = []
    message = ""
    if "fleet-server" in requested:
        background_tasks.add_task(_schedule_self_restart)
        scheduled.append("fleet-server")
        message = "fleet-server restart has been scheduled and may briefly interrupt the UI connection"

    return StackRestartResult(
        requested=requested,
        restarted=restarted,
        scheduled=scheduled,
        skipped=skipped,
        message=message,
    )


@app.get("/api/system/backup/export")
def export_backup(db: Session = Depends(get_db), _=Depends(require_admin)):
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    payload = _backup_zip_bytes(db)
    filename = f"nvr-fleet-backup-{timestamp}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(io.BytesIO(payload), media_type="application/zip", headers=headers)


@app.get("/api/system/backup/list", response_model=BackupListOut)
def list_rotated_backups(_=Depends(require_admin)):
    return BackupListOut(
        directory=BACKUP_ROTATE_DIR,
        keep=_normalized_backup_keep(),
        items=_list_rotated_backups(),
    )


@app.post("/api/system/backup/rotate", response_model=BackupRotateResult)
def rotate_backup(data: Optional[BackupRotateRequest] = None, db: Session = Depends(get_db), _=Depends(require_admin)):
    keep = data.keep if data else None
    created, removed, keep_count = _write_rotated_backup(db, keep)
    return BackupRotateResult(
        created=created,
        removed=removed,
        kept=keep_count,
        message=f"Backup stored on server as {created.filename}",
    )


@app.get("/api/system/backup/files/{filename}")
def download_rotated_backup(filename: str, _=Depends(require_admin)):
    path = _resolve_backup_file(filename)
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.post("/api/system/backup/import", response_model=BackupImportResult)
async def import_backup(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(400, "Backup file is empty")

    payload, fullchain_pem, privkey_pem = _load_backup_archive(raw_bytes)
    imported_sites = imported_cameras = imported_agents = 0
    restored_tls = False
    try:
        imported_sites, imported_cameras, imported_agents = _restore_backup_payload(db, payload)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, f"Cannot import backup: {exc}") from exc

    if fullchain_pem and privkey_pem:
        try:
            _write_tls_files(fullchain_pem, privkey_pem)
            restored_tls = True
        except Exception as exc:
            raise HTTPException(400, f"Backup imported, but TLS restore failed: {exc}") from exc

    _rebuild_mediamtx(db)
    await _sync_tunnel_listeners(db)
    for site in db.query(Site).order_by(Site.id).all():
        await _deploy_config(site.id, db)

    return BackupImportResult(
        imported_sites=imported_sites,
        imported_cameras=imported_cameras,
        imported_agents=imported_agents,
        restored_tls=restored_tls,
        message="Backup imported successfully",
    )


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), _=Depends(require_admin)):
    total_sites    = db.query(Site).count()
    total_cameras  = db.query(Camera).count()
    online_agents  = db.query(Agent).filter_by(online=True).count()
    online_streams = db.query(StreamStat).filter_by(ready=True).count()
    since = datetime.utcnow() - timedelta(minutes=5)
    recent = db.query(TrafficSample).filter(TrafficSample.ts >= since).all()
    total_rx = sum(s.rx_bytes for s in recent)
    total_tx = sum(s.tx_bytes for s in recent)
    return DashboardStats(
        total_sites=total_sites,
        total_cameras=total_cameras,
        online_agents=online_agents,
        online_streams=online_streams,
        total_rx_bps=total_rx // 300,
        total_tx_bps=total_tx // 300,
    )


# ─── Agent control ────────────────────────────────────────────────────────────

@app.post("/api/sites/{site_id}/deploy")
async def deploy_config(site_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    _ensure_site_exists(site_id, db)
    sent = await _deploy_config(site_id, db)
    return {"sent": sent}


@app.post("/api/sites/{site_id}/restart")
async def restart_agent(site_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    _ensure_site_exists(site_id, db)
    sent = await send_to_agent(site_id, {"action": "restart"})
    return {"sent": sent}


@app.post("/api/sites/{site_id}/drain-redeploy", response_model=SiteAgentDrainResult)
async def drain_redeploy_site(site_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    _ensure_site_exists(site_id, db)
    await call_agent(site_id, {"action": "drain"}, timeout=30)
    deployed = await _deploy_config(site_id, db)
    return SiteAgentDrainResult(
        site_id=site_id,
        drained=True,
        deployed=deployed,
        message="Agent drained and fresh config redeployed",
    )


# ─── Map ─────────────────────────────────────────────────────────────────────

@app.get("/api/map")
def get_map_data(db: Session = Depends(get_db), _=Depends(require_admin)):
    sites = db.query(Site).all()
    result = []
    for s in sites:
        agent  = db.query(Agent).filter_by(site_id=s.id).first()
        cams   = db.query(Camera).filter_by(site_id=s.id, enabled=True).count()
        online = db.query(StreamStat).filter_by(site_id=s.id, ready=True).count()
        result.append({
            "id": s.id, "name": s.name, "city": s.city,
            "lat": s.lat, "lon": s.lon,
            "online": agent.online if agent else False,
            "cameras": cams, "online_streams": online,
        })
    return result


# ─── Install script endpoint ──────────────────────────────────────────────────

@app.api_route("/hls/{proxy_path:path}", methods=["GET", "HEAD", "OPTIONS"])
def hls_proxy(proxy_path: str, request: Request):
    if request.method == "OPTIONS":
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "Range",
            },
        )
    return _hls_proxy_request(
        proxy_path,
        str(request.query_params),
        method=request.method,
        range_header=request.headers.get("range", ""),
    )


@app.get("/install.sh", response_class=PlainTextResponse)
def install_script():
    with open(INSTALL_SCRIPT_PATH, encoding="utf-8") as fh:
        script = fh.read()
    return PlainTextResponse(script, media_type="text/x-sh")


@app.get("/agent/agent.py", response_class=PlainTextResponse)
def agent_script():
    with open(AGENT_SCRIPT_PATH, encoding="utf-8") as fh:
        script = fh.read()
    return PlainTextResponse(script, media_type="text/x-python")


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _deploy_config(site_id: str, db: Session) -> bool:
    site = db.query(Site).filter_by(id=site_id).first()
    if not site:
        return False
    cameras = db.query(Camera).filter_by(site_id=site_id).all()
    mediamtx_changed = _rebuild_mediamtx(db)
    if mediamtx_changed:
        try:
            await asyncio.to_thread(_restart_stack_services_now, ["mediamtx"])
            await asyncio.sleep(2)
        except Exception as exc:
            logger.warning("MediaMTX restart after config rebuild failed: %s", exc)

    yaml_content = generate_go2rtc_yaml(site, [camera for camera in cameras if camera.enabled])
    sent = await send_to_agent(site_id, {
        "action": "update_config",
        "go2rtc_yaml": yaml_content,
        "site": _site_payload(site),
        "cameras": [_camera_payload(camera) for camera in cameras],
    })
    if sent:
        asyncio.create_task(_schedule_mtx_toolkit_sync(4.0))
    else:
        asyncio.create_task(_schedule_mtx_toolkit_sync(1.0))
    return sent


def _rebuild_mediamtx(db: Session):
    sites   = db.query(Site).all()
    cameras = db.query(Camera).filter_by(enabled=True).all()
    mediamtx_dir = os.path.dirname(MEDIAMTX_YAML)
    if mediamtx_dir and not os.path.exists(mediamtx_dir):
        try:
            os.makedirs(mediamtx_dir, exist_ok=True)
        except OSError as exc:
            logger.warning("Skipping mediamtx rebuild in this environment: %s", exc)
            return
    before = None
    if os.path.exists(MEDIAMTX_YAML):
        try:
            with open(MEDIAMTX_YAML, encoding="utf-8") as fh:
                before = fh.read()
        except OSError:
            before = None

    try:
        update_mediamtx_paths(MEDIAMTX_YAML, sites, cameras)
    except Exception as e:
        logger.error(f"mediamtx rebuild: {e}")
        return False

    after = None
    try:
        with open(MEDIAMTX_YAML, encoding="utf-8") as fh:
            after = fh.read()
    except OSError:
        after = None
    return before != after


def _viewer_basic_auth() -> str:
    token = base64.b64encode(b"viewer:VIEWER_PASS").decode("ascii")
    return f"Basic {token}"


def _normalize_hls_upstream_url(location: str, current_url: str) -> str:
    next_url = urljoin(current_url, location)
    parsed = urlsplit(next_url)
    upstream = urlsplit(MEDIAMTX_HLS_PROXY_TARGET)
    path = parsed.path or "/"
    if path.startswith("/hls/"):
        path = path[4:] or "/"
    return urlunsplit((
        upstream.scheme,
        upstream.netloc,
        path,
        parsed.query,
        parsed.fragment,
    ))


def _merge_set_cookie(cookie_jar: dict[str, str], headers) -> None:
    for raw_cookie in headers.get_all("Set-Cookie", []):
        parsed = SimpleCookie()
        parsed.load(raw_cookie)
        for morsel in parsed.values():
            cookie_jar[morsel.key] = morsel.value


def _is_hls_muxer_pending(proxy_path: str, status_code: int, content: bytes, media_type: str) -> bool:
    if not proxy_path.endswith(".m3u8"):
        return False
    if status_code not in {200, 404}:
        return False
    if "json" not in media_type and "text/plain" not in media_type:
        return False
    body = content.decode("utf-8", errors="ignore").lower()
    return "muxer instance not available" in body


def _hls_proxy_request(proxy_path: str, query_string: str, method: str = "GET", range_header: str = "") -> Response:
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    safe_path = proxy_path.lstrip("/")
    current_url = f"{MEDIAMTX_HLS_PROXY_TARGET}/{safe_path}"
    if query_string:
        current_url = f"{current_url}?{query_string}"

    cookies: dict[str, str] = {}
    opener = urllib.request.build_opener(_NoRedirect)

    attempt_count = 1 if method == "HEAD" else 8
    last_payload: tuple[int, bytes, str, dict[str, str]] | None = None

    for attempt in range(attempt_count):
        attempt_url = current_url
        for _ in range(6):
            request_headers = {
                "Authorization": _viewer_basic_auth(),
                "User-Agent": "NVR-Fleet-HLS-Proxy/1.0",
            }
            if range_header:
                request_headers["Range"] = range_header
            if cookies:
                request_headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
            req = urllib.request.Request(attempt_url, headers=request_headers, method=method)
            try:
                upstream = opener.open(req, timeout=10)
            except urllib.error.HTTPError as exc:
                upstream = exc
            except Exception as exc:
                raise HTTPException(502, f"HLS proxy upstream error: {exc}") from exc

            status_code = getattr(upstream, "status", getattr(upstream, "code", 502))
            _merge_set_cookie(cookies, upstream.headers)
            if 300 <= status_code < 400 and upstream.headers.get("Location"):
                attempt_url = _normalize_hls_upstream_url(upstream.headers["Location"], attempt_url)
                continue

            content = b"" if method == "HEAD" else upstream.read()
            media_type = upstream.headers.get_content_type()
            response_headers = {
                "Cache-Control": upstream.headers.get("Cache-Control", "no-cache"),
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "Range",
                "Access-Control-Expose-Headers": "Content-Length, Content-Range",
            }
            for header_name in ("Content-Range", "Accept-Ranges"):
                value = upstream.headers.get(header_name)
                if value:
                    response_headers[header_name] = value

            if attempt < attempt_count - 1 and _is_hls_muxer_pending(proxy_path, status_code, content, media_type):
                time.sleep(0.35 * (attempt + 1))
                current_url = attempt_url
                last_payload = (status_code, content, media_type, response_headers)
                break

            return Response(
                content=content,
                status_code=status_code,
                media_type=media_type,
                headers=response_headers,
            )
        else:
            raise HTTPException(502, "HLS proxy exceeded redirect limit")

    if last_payload is not None:
        status_code, content, media_type, response_headers = last_payload
        return Response(
            content=content,
            status_code=status_code,
            media_type=media_type,
            headers=response_headers,
        )

    raise HTTPException(502, "HLS proxy did not return a response")

def _write_tls_files(fullchain_pem: str, privkey_pem: str) -> TlsCertificateInfo:
    cert_info = _load_tls_info_from_text(fullchain_pem, privkey_pem)
    cert_dir = Path(TLS_CERT_DIR)
    cert_dir.mkdir(parents=True, exist_ok=True)
    fullchain_tmp = cert_dir / "fullchain.pem.tmp"
    privkey_tmp = cert_dir / "privkey.pem.tmp"
    fullchain_tmp.write_text(fullchain_pem.strip() + "\n", encoding="utf-8")
    privkey_tmp.write_text(privkey_pem.strip() + "\n", encoding="utf-8")
    os.replace(fullchain_tmp, TLS_FULLCHAIN_PATH)
    os.replace(privkey_tmp, TLS_PRIVKEY_PATH)
    return cert_info


def _mtx_toolkit_request(path: str, *, method: str = "GET", data: Optional[dict] = None, timeout: int = 5):
    import urllib.error
    import urllib.request

    if not MTX_TOOLKIT_API:
        raise RuntimeError("MTX Toolkit API URL is not configured")

    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(f"{MTX_TOOLKIT_API}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        if not raw:
            return None
        return json.loads(raw)


def _ensure_mtx_toolkit_node() -> Optional[int]:
    try:
        payload = _mtx_toolkit_request("/api/fleet/nodes?active_only=false")
    except Exception as exc:
        logger.info("MTX Toolkit node bootstrap skipped: %s", exc)
        return None

    nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
    node_data = {
        "name": MTX_TOOLKIT_NODE_NAME,
        "api_url": MEDIAMTX_API,
        "rtsp_url": MTX_TOOLKIT_RTSP_URL,
        "environment": MTX_TOOLKIT_ENVIRONMENT,
        "is_active": True,
    }

    for node in nodes:
        if node.get("name") == MTX_TOOLKIT_NODE_NAME or node.get("api_url") == MEDIAMTX_API:
            node_id = int(node["id"])
            try:
                _mtx_toolkit_request(f"/api/fleet/nodes/{node_id}", method="PUT", data=node_data)
            except Exception as exc:
                logger.info("MTX Toolkit node update skipped: %s", exc)
                return None
            return node_id

    try:
        created = _mtx_toolkit_request("/api/fleet/nodes", method="POST", data=node_data)
    except Exception as exc:
        logger.info("MTX Toolkit node creation skipped: %s", exc)
        return None
    if isinstance(created, dict) and created.get("id"):
        return int(created["id"])
    return None


def _sync_mtx_toolkit_node_streams() -> None:
    node_id = _ensure_mtx_toolkit_node()
    if not node_id:
        return
    try:
        _mtx_toolkit_request(f"/api/fleet/nodes/{node_id}/sync", method="POST", data={})
    except Exception as exc:
        logger.info("MTX Toolkit stream sync skipped: %s", exc)


async def _schedule_mtx_toolkit_sync(delay: float = 4.0) -> None:
    await asyncio.sleep(delay)
    await asyncio.to_thread(_sync_mtx_toolkit_node_streams)










