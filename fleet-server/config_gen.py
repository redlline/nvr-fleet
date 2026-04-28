"""
Generates go2rtc.yaml for each site and updates mediamtx.yml paths section.
"""
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
            "user": "any",
            "pass": "",
            "ips": ["127.0.0.1", "::1"],
            "permissions": [
                {"action": "api"},
                {"action": "metrics"},
                {"action": "pprof"},
            ],
        },
    ]

    for site in sites:
        auth_users.append({
            "user": site_publish_user(site.id),
            "pass": site_publish_pass(site.id),
            "ips": [],
            "permissions": [
                {"action": "publish", "path": site_path_pattern(site.id)},
            ],
        })

    cfg["paths"] = paths
    cfg["authMethod"] = "internal"
    cfg["authInternalUsers"] = auth_users

    Path(mediamtx_yml_path).parent.mkdir(parents=True, exist_ok=True)
    with open(mediamtx_yml_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
