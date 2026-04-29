"""
Generates go2rtc.yaml for each site and updates mediamtx.yml paths section.
"""
import os
from pathlib import Path
import re
from urllib.parse import quote

import yaml
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import Site, Camera


def local_stream_name(site_id: str, channel: int) -> str:
    return f"site{site_id}_cam{channel:02d}"


def public_stream_path(site_id: str, channel: int) -> str:
    return f"site{site_id}/cam{channel:02d}"


def site_publish_user(site_id: str) -> str:
    return f"site{site_id}"


def site_publish_pass(site_id: str) -> str:
    return f"PASS_{site_id}"


def mediamtx_internal_api_user() -> str:
    return os.environ.get("MEDIAMTX_API_USER", "mediamtx-internal")


def mediamtx_internal_api_pass() -> str:
    return os.environ.get("MEDIAMTX_API_PASS", "MEDIAMTX_INTERNAL_PASS")


def normalize_stream_path(stream_name: str) -> str:
    if stream_name.startswith("site") and "_cam" in stream_name:
        prefix, _, suffix = stream_name.partition("_")
        return f"{prefix}/{suffix}"
    return stream_name


def site_path_pattern(site_id: str) -> str:
    return f"~^{re.escape(f'site{site_id}')}/.+$"


def generate_go2rtc_yaml(site, cameras) -> str:
    streams = {}
    for cam in cameras:
        if not cam.enabled:
            continue
        if not (site.nvr_ip or "").strip():
            continue
        stream_name = local_stream_name(site.id, cam.channel)
        nvr_user = quote(site.nvr_user, safe="")
        nvr_pass = quote(site.nvr_pass, safe="")
        local_rtsp  = (
            f"rtsp://{nvr_user}:{nvr_pass}"
            f"@{site.nvr_ip}:{site.nvr_port}"
            f"/Streaming/Channels/{cam.channel_id}"
        )
        streams[stream_name] = [local_rtsp]

    config = {
        "api":    {"listen": "127.0.0.1:1984"},
        "rtsp":   {"listen": ":8554"},
        "streams": streams,
    }
    return yaml.dump(config, default_flow_style=False, allow_unicode=True)


def update_mediamtx_paths(mediamtx_yml_path: str, sites, cameras) -> None:
    """Rewrites the paths section in mediamtx.yml based on current DB state."""
    try:
        with open(mediamtx_yml_path) as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        cfg = {}

    if "protocols" in cfg and "rtspTransports" not in cfg:
        cfg["rtspTransports"] = cfg.pop("protocols")
    else:
        cfg.pop("protocols", None)

    paths = cfg.get("paths", {}) or {}

    # Remove old fleet-managed path entries. MediaMTX now uses authInternalUsers
    # for publish/read restrictions, but we still clean out legacy keys.
    paths = {k: v for k, v in paths.items()
             if not (
                 (k.startswith("site") and "cam~" in k) or
                 k.startswith("~^site")
             )}

    auth_users = [
        {
            "user": "viewer",
            "pass": "VIEWER_PASS",
            "ips": [],
            "permissions": [
                {"action": "read"},
                {"action": "playback"},
            ],
        },
        {
            "user": mediamtx_internal_api_user(),
            "pass": mediamtx_internal_api_pass(),
            "ips": [],
            "permissions": [
                {"action": "api"},
                {"action": "metrics"},
                {"action": "pprof"},
            ],
        },
    ]

    enabled_cameras = [camera for camera in cameras if getattr(camera, "enabled", False)]

    for site in sites:
        auth_users.append({
            "user": site_publish_user(site.id),
            "pass": site_publish_pass(site.id),
            "ips": [],
            "permissions": [
                {"action": "publish", "path": site_path_pattern(site.id)},
            ],
        })
        for camera in enabled_cameras:
            if camera.site_id != site.id:
                continue
            paths[public_stream_path(site.id, camera.channel)] = {}

    cfg["paths"] = paths
    cfg["authMethod"] = "internal"
    cfg["authInternalUsers"] = auth_users
    cfg["api"] = True
    cfg["apiAddress"] = ":9997"
    cfg["metrics"] = True
    cfg["metricsAddress"] = ":9998"

    path_defaults = cfg.get("pathDefaults", {}) or {}
    for legacy_key in ("publishUser", "publishPass", "publishIPs", "readUser", "readPass", "readIPs"):
        path_defaults.pop(legacy_key, None)
    if path_defaults:
        cfg["pathDefaults"] = path_defaults
    else:
        cfg.pop("pathDefaults", None)

    Path(mediamtx_yml_path).parent.mkdir(parents=True, exist_ok=True)
    with open(mediamtx_yml_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
