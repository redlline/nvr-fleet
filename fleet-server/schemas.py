from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class SiteCreate(BaseModel):
    name: str
    city: str = ""
    lat: float = 0.0
    lon: float = 0.0
    nvr_vendor: str = "hikvision"
    nvr_ip: str = ""
    nvr_http_port: int = 80
    nvr_control_port: int = 8000
    nvr_user: str = "admin"
    nvr_pass: str = ""
    nvr_port: int = 554
    channel_count: int = 0
    stream_type: str = "main"


class SiteUpdate(BaseModel):
    name: Optional[str] = None
    city: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    nvr_vendor: Optional[str] = None
    nvr_ip: Optional[str] = None
    nvr_http_port: Optional[int] = None
    nvr_control_port: Optional[int] = None
    nvr_user: Optional[str] = None
    nvr_pass: Optional[str] = None
    nvr_port: Optional[int] = None
    stream_type: Optional[str] = None


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    city: str
    lat: float
    lon: float
    nvr_vendor: str
    nvr_ip: str
    nvr_http_port: int
    nvr_control_port: int
    nvr_user: str
    nvr_port: int
    tunnel_http_port: Optional[int] = None
    tunnel_control_port: Optional[int] = None
    tunnel_rtsp_port: Optional[int] = None
    channel_count: int
    stream_type: str
    created_at: datetime
    is_configured: bool = False
    agent_online: bool = False
    agent_last_seen: Optional[datetime] = None
    camera_count: int = 0
    online_streams: int = 0


class CameraCreate(BaseModel):
    name: str
    channel: int
    source_ref: Optional[str] = None
    profile_ref: Optional[str] = None
    stream_type: str = "main"
    enabled: bool = True


class CameraUpdate(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    channel: Optional[int] = None
    source_ref: Optional[str] = None
    profile_ref: Optional[str] = None
    stream_type: Optional[str] = None
    enabled: Optional[bool] = None


class CameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    site_id: str
    name: str
    channel: int
    channel_id: int
    source_ref: Optional[str] = None
    profile_ref: Optional[str] = None
    stream_type: str
    enabled: bool


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    site_id: str
    online: bool
    last_seen: Optional[datetime]
    version: str
    uptime: int


class StreamStatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    site_id: str
    stream_path: str
    ready: bool
    updated: datetime
    rtsp_url: str | None = None  # populated by server using MEDIAMTX_VIEWER_PASS


class TrafficOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    site_id: str
    stream_path: str
    rx_bytes: int
    tx_bytes: int
    ts: datetime


class InstallResponse(BaseModel):
    site_id: str
    token: str
    install_cmd: str


class DashboardStats(BaseModel):
    total_sites: int
    total_cameras: int
    online_agents: int
    online_streams: int
    total_rx_bps: int
    total_tx_bps: int


class ArchiveRecordingOut(BaseModel):
    camera_id: int
    camera_name: str
    channel: int
    stream_type: str
    recording_type: str
    start: datetime
    end: datetime
    vendor: str


class ArchivePlaybackRequest(BaseModel):
    camera_id: int
    start: datetime
    end: datetime


class ArchivePlaybackOut(BaseModel):
    session_id: str
    stream_path: str
    vendor: str
    rtsp_url: str
    hls_url: str
    webrtc_url: str
    expires_at: datetime


class AgentCameraSyncItem(BaseModel):
    id: Optional[int] = None
    name: str
    channel: int
    source_ref: Optional[str] = None
    profile_ref: Optional[str] = None
    stream_type: str = "main"
    enabled: bool = True


class AgentSiteConfigUpdate(BaseModel):
    nvr_vendor: Optional[str] = None
    nvr_ip: Optional[str] = None
    nvr_http_port: Optional[int] = None
    nvr_control_port: Optional[int] = None
    nvr_user: Optional[str] = None
    nvr_pass: Optional[str] = None
    nvr_port: Optional[int] = None
    stream_type: Optional[str] = None
    channel_count: Optional[int] = None


class TlsUpdateRequest(BaseModel):
    fullchain_pem: str
    privkey_pem: str


class TlsCertificateInfo(BaseModel):
    subject: str
    issuer: str
    san: list[str]
    not_before: datetime
    not_after: datetime
    expires_in_days: int
    fingerprint_sha256: str


class TlsStatus(BaseModel):
    enabled: bool
    files_present: bool
    public_host: str
    public_base_url: str
    install_script_url: str
    auto_reload: bool = True
    cert: Optional[TlsCertificateInfo] = None


class StackServiceStatus(BaseModel):
    key: str
    label: str
    container_name: str
    status: str
    health: str
    probe_ok: Optional[bool] = None
    probe_message: str = ""
    restart_supported: bool = True


class StackStatus(BaseModel):
    docker_available: bool
    docker_message: str = ""
    services: list[StackServiceStatus]


class StackRestartRequest(BaseModel):
    services: Optional[list[str]] = None


class StackRestartResult(BaseModel):
    requested: list[str]
    restarted: list[str]
    scheduled: list[str]
    skipped: list[str]
    message: str = ""


class StackLogsOut(BaseModel):
    service: str
    container_name: str
    tail: int
    text: str


class BackupFileOut(BaseModel):
    filename: str
    size_bytes: int
    created_at: datetime


class BackupListOut(BaseModel):
    directory: str
    keep: int
    items: list[BackupFileOut]


class BackupRotateRequest(BaseModel):
    keep: Optional[int] = None


class BackupRotateResult(BaseModel):
    created: BackupFileOut
    removed: list[str]
    kept: int
    message: str = ""


class BackupImportResult(BaseModel):
    imported_sites: int
    imported_cameras: int
    imported_agents: int
    restored_tls: bool
    message: str = ""


class SiteAgentDrainResult(BaseModel):
    site_id: str
    drained: bool
    deployed: bool
    message: str = ""

