#!/usr/bin/env python3
"""
NVR Fleet Agent. Runs on each mini-PC in the same LAN as the NVR.
"""

import asyncio
import base64
import html
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import uvicorn
import websockets
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

SITE_ID = os.environ["SITE_ID"]
AGENT_TOKEN = os.environ["AGENT_TOKEN"]
SERVER_HOST = os.environ.get("SERVER_HOST", "localhost")
SERVER_WS = os.environ.get("SERVER_WS", f"wss://{SERVER_HOST}/ws/agent/{SITE_ID}")
SERVER_API = os.environ.get(
    "SERVER_API",
    f"{'https' if SERVER_WS.startswith('wss://') else 'http'}://{SERVER_HOST}",
)

GO2RTC_YAML = os.environ.get("GO2RTC_YAML", "/etc/go2rtc/go2rtc.yaml")
GO2RTC_SVC = os.environ.get("GO2RTC_SVC", "go2rtc")
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "/usr/bin/ffmpeg")
SERVER_RTSP_PORT = os.environ.get("SERVER_RTSP_PORT", "8554")
AGENT_ADMIN_HOST = os.environ.get("AGENT_ADMIN_HOST", "0.0.0.0")
AGENT_ADMIN_PORT = int(os.environ.get("AGENT_ADMIN_PORT", "7070"))
AGENT_STATE_DIR = os.environ.get("AGENT_STATE_DIR", "/var/lib/nvr-fleet-agent")
BUNDLE_CACHE_PATH = os.environ.get("BUNDLE_CACHE_PATH", f"{AGENT_STATE_DIR}/bundle-cache.json")

VERSION = "1.3.0"

_ffmpeg_procs: dict[str, subprocess.Popen] = {}
_publisher_targets: dict[str, str] = {}
_last_traffic_totals: dict[str, tuple[int, int]] = {}
_archive_sessions: dict[str, dict] = {}
_tcp_tunnels: dict[str, dict] = {}
_admin_server: uvicorn.Server | None = None
_ws_send_lock: asyncio.Lock | None = None


def _get_ws_send_lock() -> asyncio.Lock:
    global _ws_send_lock
    if _ws_send_lock is None:
        _ws_send_lock = asyncio.Lock()
    return _ws_send_lock


async def ws_send(ws, payload: dict) -> None:
    async with _get_ws_send_lock():
        await ws.send(json.dumps(payload))


class AdapterError(RuntimeError):
    pass


def _bundle_cache_file() -> Path:
    path = Path(BUNDLE_CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _bundle_from_site_and_cameras(site: dict | None, cameras: list[dict] | None, existing: dict | None = None) -> dict:
    site_payload = dict((existing or {}).get("site") or {})
    if site:
        site_payload.update(site)

    site_payload.setdefault("id", SITE_ID)
    site_payload.setdefault("name", SITE_ID)
    site_payload.setdefault("city", "")
    site_payload.setdefault("vendor", "hikvision")
    site_payload.setdefault("channel_count", 0)
    site_payload.setdefault("is_configured", False)
    site_payload.setdefault("nvr_ip", "")
    site_payload.setdefault("nvr_http_port", 80)
    site_payload.setdefault("nvr_control_port", 8000)
    site_payload.setdefault("nvr_user", "admin")
    site_payload.setdefault("nvr_pass", "")
    site_payload.setdefault("nvr_port", 554)
    site_payload.setdefault("public_host", SERVER_HOST)
    site_payload.setdefault("tunnel_http_port", None)
    site_payload.setdefault("tunnel_control_port", None)
    site_payload.setdefault("tunnel_rtsp_port", None)
    site_payload.setdefault("stream_type", "main")

    camera_payload = list(cameras if cameras is not None else (existing or {}).get("cameras") or [])
    thick_client = {
        "host": site_payload.get("public_host") or SERVER_HOST,
        "http_port": site_payload.get("tunnel_http_port"),
        "control_port": site_payload.get("tunnel_control_port"),
        "rtsp_port": site_payload.get("tunnel_rtsp_port"),
    }
    return {
        "site": site_payload,
        "cameras": camera_payload,
        "thick_client": thick_client,
    }


def _read_cached_bundle() -> dict | None:
    path = _bundle_cache_file()
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        log.warning("Cannot read cached bundle: %s", exc)
    return None


def _write_cached_bundle(bundle: dict) -> None:
    path = _bundle_cache_file()
    path.write_text(json.dumps(bundle, ensure_ascii=True, indent=2), encoding="utf-8")


def _cache_bundle_payload(payload: dict | None, *, site: dict | None = None, cameras: list[dict] | None = None) -> dict | None:
    if not isinstance(payload, dict):
        return payload
    existing = _read_cached_bundle()
    merged = _bundle_from_site_and_cameras(
        payload.get("site") if site is None else site,
        payload.get("cameras") if cameras is None else cameras,
        existing=existing | payload if existing else payload,
    )
    if isinstance(payload.get("thick_client"), dict):
        merged["thick_client"] = payload["thick_client"]
    if "warning" in payload:
        merged["warning"] = payload["warning"]
    _write_cached_bundle(merged)
    return merged


class LocalCameraItem(BaseModel):
    id: int | None = None
    name: str
    channel: int
    source_ref: str | None = None
    profile_ref: str | None = None
    stream_type: str = "main"
    enabled: bool = True


class LocalSiteConfigItem(BaseModel):
    nvr_vendor: str = "hikvision"
    nvr_ip: str = ""
    nvr_http_port: int = 80
    nvr_control_port: int = 8000
    nvr_user: str = "admin"
    nvr_pass: str | None = None
    nvr_port: int = 554
    stream_type: str = "main"
    channel_count: int | None = None


LOCAL_ADMIN_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>NVR Fleet Agent</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
    .wrap { max-width: 1120px; margin: 0 auto; padding: 24px; }
    h1 { margin: 0 0 8px; font-size: 28px; }
    .sub { color: #94a3b8; margin-bottom: 24px; }
    .panel { background: #111827; border: 1px solid #334155; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    .row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
    .muted { color: #94a3b8; font-size: 13px; }
    button { background: #2563eb; color: white; border: 0; border-radius: 6px; padding: 10px 14px; cursor: pointer; }
    button.secondary { background: #334155; }
    button.danger { background: #b91c1c; }
    button:disabled { opacity: 0.55; cursor: wait; }
    input, select { width: 100%; box-sizing: border-box; background: #0b1220; color: #e2e8f0; border: 1px solid #334155; border-radius: 6px; padding: 9px 10px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px 8px; border-bottom: 1px solid #1e293b; text-align: left; vertical-align: middle; }
    th { color: #94a3b8; font-size: 12px; text-transform: uppercase; }
    code { background: #0b1220; border: 1px solid #334155; padding: 2px 6px; border-radius: 4px; }
    .status { margin-top: 8px; min-height: 20px; color: #93c5fd; }
    .error { color: #fca5a5; }
    .pill { display: inline-block; border: 1px solid #334155; border-radius: 999px; padding: 4px 10px; margin-right: 8px; font-size: 12px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
    .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
    .field-label { display: block; margin-bottom: 6px; color: #94a3b8; font-size: 13px; }
    .help { color: #94a3b8; font-size: 12px; margin-top: 6px; }
    .hint { color: #fbbf24; font-size: 13px; margin-top: 6px; }
    .device-row { padding: 10px 0; border-bottom: 1px solid #1e293b; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Local Agent Admin</h1>
    <div class="sub">Manage the NVR from the mini-PC in the same LAN. Configure it here first, then push the working config to the server.</div>

    <div class="panel">
      <div class="grid">
        <div>
          <div class="muted">Site</div>
          <div id="siteName">-</div>
        </div>
        <div>
          <div class="muted">NVR</div>
          <div id="nvrInfo">-</div>
        </div>
        <div>
          <div class="muted">Thick client host</div>
          <div id="thickHost">-</div>
        </div>
        <div>
          <div class="muted">Thick client ports</div>
          <div id="thickPorts">-</div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="row">
        <button onclick="loadBundle()">Refresh</button>
        <button onclick="saveSiteConfig()" id="saveSiteBtn">Save NVR settings</button>
        <button class="secondary" onclick="autoDiscover()">Autodiscover</button>
        <button class="secondary" onclick="discoverDevices()">Find NVRs in LAN</button>
        <button class="secondary" onclick="addRow()">Add camera</button>
        <button onclick="saveRows()" id="saveBtn">Save cameras</button>
      </div>
      <div class="status" id="status"></div>
    </div>

    <div class="panel">
      <div style="font-weight: 600; margin-bottom: 12px;">NVR settings</div>
      <div class="help" style="margin-bottom: 12px;">
        Recommended flow: find the NVR in LAN, apply it here, enter credentials, run Autodiscover, then save cameras.
      </div>
      <div class="grid-2">
        <div>
          <label class="field-label" for="nvrVendor">Archive adapter</label>
          <select id="nvrVendor" onchange="updateSiteField('vendor', this.value)">
            <option value="hikvision">Hikvision</option>
            <option value="dahua">Dahua</option>
            <option value="onvif">ONVIF</option>
          </select>
        </div>
        <div>
          <label class="field-label" for="streamType">Default stream</label>
          <select id="streamType" onchange="updateSiteField('stream_type', this.value)">
            <option value="main">main</option>
            <option value="sub">sub</option>
          </select>
        </div>
        <div>
          <label class="field-label" for="nvrIp">NVR IP address</label>
          <input id="nvrIp" placeholder="192.168.1.64" oninput="updateSiteField('nvr_ip', this.value)" />
        </div>
        <div>
          <label class="field-label" for="nvrHttpPort">NVR API port</label>
          <input id="nvrHttpPort" type="number" min="1" oninput="updateSiteField('nvr_http_port', this.value)" />
        </div>
        <div>
          <label class="field-label" for="nvrControlPort">NVR control port</label>
          <input id="nvrControlPort" type="number" min="1" oninput="updateSiteField('nvr_control_port', this.value)" />
        </div>
        <div>
          <label class="field-label" for="nvrRtspPort">RTSP port</label>
          <input id="nvrRtspPort" type="number" min="1" oninput="updateSiteField('nvr_port', this.value)" />
        </div>
        <div>
          <label class="field-label" for="nvrUser">NVR username</label>
          <input id="nvrUser" placeholder="admin" oninput="updateSiteField('nvr_user', this.value)" />
        </div>
        <div>
          <label class="field-label" for="nvrPass">NVR password</label>
          <input id="nvrPass" type="password" placeholder="Leave blank to keep current password" oninput="updateSiteField('nvr_pass', this.value)" />
        </div>
      </div>
      <div id="nvrHint" class="help"></div>
    </div>

    <div class="panel">
      <div class="muted" style="margin-bottom: 10px;">LAN device discovery via ONVIF WS-Discovery</div>
      <div id="deviceRows" class="muted">No device scan yet</div>
    </div>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Channel</th>
            <th>Stream</th>
            <th>Binding</th>
            <th>Enabled</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  </div>

  <script>
    let bundle = null
    let rows = []
    let deviceRows = []
    let siteForm = {
      vendor: "hikvision",
      nvr_ip: "",
      nvr_http_port: 80,
      nvr_control_port: 8000,
      nvr_user: "admin",
      nvr_pass: "",
      nvr_port: 554,
      stream_type: "main",
      channel_count: 0,
    }

    function setStatus(text, isError = false) {
      const node = document.getElementById("status")
      node.textContent = text || ""
      node.className = isError ? "status error" : "status"
    }

    function defaultControlPort(vendor) {
      if (vendor === "dahua") return 37777
      if (vendor === "unv" || vendor === "uniview") return 37777
      return 8000
    }

    function syncChannelCount() {
      siteForm.channel_count = rows.reduce((max, row) => Math.max(max, Number(row.channel) || 0), 0)
    }

    function populateSiteForm() {
      if (!bundle) return
      siteForm = {
        vendor: bundle.site.vendor || "hikvision",
        nvr_ip: bundle.site.nvr_ip || "",
        nvr_http_port: Number(bundle.site.nvr_http_port) || 80,
        nvr_control_port: Number(bundle.site.nvr_control_port) || defaultControlPort(bundle.site.vendor || "hikvision"),
        nvr_user: bundle.site.nvr_user || "admin",
        nvr_pass: "",
        nvr_port: Number(bundle.site.nvr_port) || 554,
        stream_type: bundle.site.stream_type || "main",
        channel_count: Number(bundle.site.channel_count) || 0,
      }
    }

    function renderHeader() {
      if (!bundle) return
      document.getElementById("siteName").textContent = `${bundle.site.name || bundle.site.id} (${bundle.site.id})`
      document.getElementById("nvrInfo").textContent = bundle.site.is_configured
        ? `${bundle.site.vendor} / ${bundle.site.nvr_ip}:${bundle.site.nvr_port} / API ${bundle.site.nvr_http_port} / Control ${bundle.site.nvr_control_port}`
        : "Pending local setup"
      document.getElementById("thickHost").textContent = bundle.thick_client.host
      document.getElementById("thickPorts").innerHTML = `
        <span class="pill">HTTP ${bundle.thick_client.http_port}</span>
        <span class="pill">Control ${bundle.thick_client.control_port}</span>
        <span class="pill">RTSP ${bundle.thick_client.rtsp_port}</span>
      `
    }

    function renderSiteConfig() {
      document.getElementById("nvrVendor").value = siteForm.vendor || "hikvision"
      document.getElementById("streamType").value = siteForm.stream_type || "main"
      document.getElementById("nvrIp").value = siteForm.nvr_ip || ""
      document.getElementById("nvrHttpPort").value = siteForm.nvr_http_port || 80
      document.getElementById("nvrControlPort").value = siteForm.nvr_control_port || defaultControlPort(siteForm.vendor)
      document.getElementById("nvrRtspPort").value = siteForm.nvr_port || 554
      document.getElementById("nvrUser").value = siteForm.nvr_user || "admin"
      document.getElementById("nvrPass").value = siteForm.nvr_pass || ""

      const hint = document.getElementById("nvrHint")
      if (siteForm.nvr_ip) {
        hint.className = "help"
        hint.textContent = `Saved target: ${siteForm.nvr_ip}:${siteForm.nvr_port} | current max channel: ${siteForm.channel_count || 0}`
      } else {
        hint.className = "hint"
        hint.textContent = "NVR is not configured yet. Use 'Find NVRs in LAN' or enter the local IP manually on this mini-PC."
      }
    }

    function renderRows() {
      const body = document.getElementById("rows")
      body.innerHTML = ""
      rows.sort((a, b) => Number(a.channel) - Number(b.channel))
      syncChannelCount()
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="6" class="muted">No cameras configured</td></tr>'
        renderSiteConfig()
        return
      }
      rows.forEach((row, index) => {
        const binding = row.source_ref
          ? `${row.source_ref}${row.profile_ref ? ` / ${row.profile_ref}` : ""}`
          : "Manual"
        const tr = document.createElement("tr")
        tr.innerHTML = `
          <td><input value="${escapeHtml(row.name || "")}" onchange="updateRow(${index}, 'name', this.value)" /></td>
          <td><input type="number" min="1" value="${Number(row.channel) || ""}" onchange="updateRow(${index}, 'channel', Number(this.value))" /></td>
          <td>
            <select onchange="updateRow(${index}, 'stream_type', this.value)">
              <option value="main" ${row.stream_type === "main" ? "selected" : ""}>main</option>
              <option value="sub" ${row.stream_type === "sub" ? "selected" : ""}>sub</option>
            </select>
          </td>
          <td><code title="${escapeHtml(binding)}">${escapeHtml(binding)}</code></td>
          <td><input type="checkbox" ${row.enabled ? "checked" : ""} onchange="updateRow(${index}, 'enabled', this.checked)" /></td>
          <td><button class="danger" onclick="removeRow(${index})">Delete</button></td>
        `
        body.appendChild(tr)
      })
      renderSiteConfig()
    }

    function renderDevices() {
      const node = document.getElementById("deviceRows")
      if (!deviceRows.length) {
        node.className = "muted"
        node.textContent = "No device scan yet"
        return
      }
      node.className = ""
      node.innerHTML = deviceRows.map((item, index) => `
        <div class="device-row">
          <div><strong>${escapeHtml(item.vendor || "onvif")}</strong> ${escapeHtml(item.ip || "-")} ${item.http_port ? ` / API ${escapeHtml(item.http_port)}` : ""}</div>
          <div class="muted">${escapeHtml(item.scopes || item.xaddrs || "WS-Discovery response")}</div>
          <div style="margin-top: 8px;">
            <button class="secondary" onclick="applyDevice(${index})">Use this device</button>
          </div>
        </div>
      `).join("")
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;")
    }

    function updateRow(index, key, value) {
      rows[index] = { ...rows[index], [key]: value }
    }

    function updateSiteField(key, value) {
      const next = { ...siteForm, [key]: value }
      const currentDefault = defaultControlPort(siteForm.vendor || "hikvision")
      if (key === "vendor" && (!siteForm.nvr_control_port || Number(siteForm.nvr_control_port) === currentDefault)) {
        next.nvr_control_port = defaultControlPort(value)
      }
      siteForm = next
      renderSiteConfig()
    }

    function applyDevice(index) {
      const device = deviceRows[index]
      if (!device) return
      siteForm = {
        ...siteForm,
        vendor: device.vendor || siteForm.vendor || "onvif",
        nvr_ip: device.ip || siteForm.nvr_ip,
        nvr_http_port: Number(device.http_port) || siteForm.nvr_http_port || 80,
        nvr_control_port: defaultControlPort(device.vendor || siteForm.vendor || "onvif"),
      }
      renderSiteConfig()
      setStatus(`Applied ${device.vendor || "ONVIF"} device ${device.ip} to NVR settings`)
    }

    function removeRow(index) {
      rows.splice(index, 1)
      renderRows()
    }

    function addRow() {
      const maxChannel = rows.reduce((max, row) => Math.max(max, Number(row.channel) || 0), 0)
      rows.push({
        id: null,
        name: `Cam ${String(maxChannel + 1).padStart(2, "0")}`,
        channel: maxChannel + 1,
        source_ref: null,
        profile_ref: null,
        stream_type: siteForm.stream_type || "main",
        enabled: true,
      })
      renderRows()
    }

    async function apiJson(url, options) {
      const response = await fetch(url, options)
      const raw = await response.text()
      let data = {}
      if (raw) {
        try {
          data = JSON.parse(raw)
        } catch (error) {
          if (!response.ok) {
            throw new Error(raw.trim() || `Request failed (${response.status})`)
          }
          throw new Error(`Invalid JSON response from ${url}`)
        }
      }
      if (!response.ok) {
        throw new Error(data.detail || data.error || raw.trim() || `Request failed (${response.status})`)
      }
      return data
    }

    async function loadBundle() {
      setStatus("Loading...")
      try {
        bundle = await apiJson("/api/bundle")
        rows = bundle.cameras.map((camera) => ({ ...camera }))
        populateSiteForm()
        renderHeader()
        renderSiteConfig()
        renderRows()
        renderDevices()
        setStatus(bundle.warning || `Loaded ${rows.length} cameras`)
      } catch (error) {
        console.error(error)
        setStatus(error.message, true)
      }
    }

    async function saveSiteConfig(showSuccess = true) {
      const saveBtn = document.getElementById("saveSiteBtn")
      saveBtn.disabled = true
      if (showSuccess) setStatus("Saving NVR settings...")
      try {
        syncChannelCount()
        const payload = {
          nvr_vendor: siteForm.vendor || "hikvision",
          nvr_ip: (siteForm.nvr_ip || "").trim(),
          nvr_http_port: Number(siteForm.nvr_http_port) || 80,
          nvr_control_port: Number(siteForm.nvr_control_port) || defaultControlPort(siteForm.vendor || "hikvision"),
          nvr_user: (siteForm.nvr_user || "admin").trim() || "admin",
          nvr_port: Number(siteForm.nvr_port) || 554,
          stream_type: siteForm.stream_type || "main",
          channel_count: siteForm.channel_count || 0,
        }
        if ((siteForm.nvr_pass || "").trim()) {
          payload.nvr_pass = siteForm.nvr_pass
        }
        const data = await apiJson("/api/site", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
        bundle.site = { ...bundle.site, ...data.site }
        if (data.thick_client) bundle.thick_client = data.thick_client
        populateSiteForm()
        renderHeader()
        renderSiteConfig()
        if (showSuccess) setStatus("NVR settings saved to server")
        return data
      } catch (error) {
        console.error(error)
        setStatus(error.message, true)
        throw error
      } finally {
        saveBtn.disabled = false
      }
    }

    async function autoDiscover() {
      if (!(siteForm.nvr_ip || "").trim()) {
        setStatus("Set the local NVR IP first, then run autodiscovery.", true)
        return
      }
      setStatus("Saving NVR settings and discovering channels on NVR...")
      try {
        await saveSiteConfig(false)
        const data = await apiJson("/api/discover")
        const byChannel = new Map(rows.map((row) => [Number(row.channel), row]))
        for (const item of data.items) {
          const existing = byChannel.get(Number(item.channel))
          if (!existing) {
            rows.push({
              id: null,
              name: item.name,
              channel: Number(item.channel),
              source_ref: item.source_ref || null,
              profile_ref: item.profile_ref || null,
              stream_type: siteForm.stream_type || "main",
              enabled: true,
            })
          } else {
            existing.name = existing.name || item.name
            existing.source_ref = existing.source_ref || item.source_ref || null
            existing.profile_ref = existing.profile_ref || item.profile_ref || null
          }
        }
        renderRows()
        setStatus(`Discovered ${data.items.length} channels via ${data.protocol || "NVR API"}`)
      } catch (error) {
        console.error(error)
        setStatus(error.message, true)
      }
    }

    async function discoverDevices() {
      setStatus("Scanning LAN for ONVIF devices...")
      try {
        const data = await apiJson("/api/discover-devices")
        deviceRows = data.items || []
        renderDevices()
        setStatus(`Found ${deviceRows.length} ONVIF devices in LAN`)
      } catch (error) {
        console.error(error)
        setStatus(error.message, true)
      }
    }

    async function saveRows() {
      const saveBtn = document.getElementById("saveBtn")
      saveBtn.disabled = true
      setStatus("Saving...")
      try {
        await saveSiteConfig(false)
        const payload = rows.map((row) => ({
          id: row.id || null,
          name: row.name,
          channel: Number(row.channel),
          source_ref: row.source_ref || null,
          profile_ref: row.profile_ref || null,
          stream_type: row.stream_type,
          enabled: !!row.enabled,
        }))
        const data = await apiJson("/api/cameras", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
        rows = data.cameras.map((camera) => ({ ...camera }))
        if (data.site) {
          bundle.site = { ...bundle.site, ...data.site }
        }
        if (data.thick_client) bundle.thick_client = data.thick_client
        renderHeader()
        renderRows()
        setStatus("NVR settings and camera configuration saved to server")
      } catch (error) {
        console.error(error)
        setStatus(error.message, true)
      } finally {
        saveBtn.disabled = false
      }
    }

    loadBundle()
  </script>
</body>
</html>
"""


def local_tzinfo():
    return datetime.now().astimezone().tzinfo or timezone.utc


def parse_server_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=local_tzinfo())
    return dt.astimezone(local_tzinfo())


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tzinfo())
    return dt.astimezone(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return to_utc(dt).replace(microsecond=0).isoformat()


def hik_search_time(dt: datetime) -> str:
    return to_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def hik_rtsp_time(dt: datetime) -> str:
    return to_utc(dt).strftime("%Y%m%dT%H%M%SZ").lower()


def parse_hik_datetime(value: str) -> datetime:
    text = value.strip()
    patterns = (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y%m%dT%H%M%SZ",
        "%Y%m%dt%H%M%Sz",
    )
    for pattern in patterns:
        try:
            parsed = datetime.strptime(text, pattern)
            if pattern.endswith(("Z", "z")):
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.replace(tzinfo=local_tzinfo())
        except ValueError:
            continue
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tzinfo())
    return parsed


def xml_local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def xml_child_text(node: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in list(node):
        if xml_local_name(child.tag) in wanted and child.text:
            return child.text.strip()
    return ""


def xml_desc_text(node: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in node.iter():
        if xml_local_name(child.tag) in wanted and child.text:
            return child.text.strip()
    return ""


def xml_desc_texts(node: ET.Element, *names: str) -> list[str]:
    wanted = set(names)
    items = []
    for child in node.iter():
        if xml_local_name(child.tag) in wanted and child.text:
            value = child.text.strip()
            if value:
                items.append(value)
    return items


def xml_has_desc(node: ET.Element, name: str) -> bool:
    return any(xml_local_name(child.tag) == name for child in node.iter())


def xml_escape(value: str) -> str:
    return html.escape(str(value), quote=True)


def parse_xml_datetime(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("Empty datetime value")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=local_tzinfo())
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return parse_hik_datetime(text).astimezone(timezone.utc)


def best_effort_channel_number(*values: str, fallback: int) -> int:
    for value in values:
        if not value:
            continue
        match = re.search(r"(?<!\d)(\d{1,3})(?!\d)", value)
        if match:
            number = int(match.group(1))
            if number > 0:
                return number
    return fallback


def rewrite_rtsp_uri(uri: str, host: str, port: int, user: str, password: str) -> str:
    parsed = urlsplit(uri)
    scheme = parsed.scheme or "rtsp"
    path = parsed.path or "/"
    query = parsed.query
    user_text = quote(user, safe="")
    pass_text = quote(password, safe="")
    netloc = f"{user_text}:{pass_text}@{host}:{int(port)}"
    return urlunsplit((scheme, netloc, path, query, parsed.fragment))


def write_config(yaml_content: str) -> None:
    path = Path(GO2RTC_YAML)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_content, encoding="utf-8")
    log.info("go2rtc config updated")


def restart_go2rtc() -> None:
    result = subprocess.run(
        ["systemctl", "restart", GO2RTC_SVC],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("systemctl restart go2rtc: %s", result.stderr)
    else:
        log.info("go2rtc restarted")


def get_go2rtc_streams() -> dict[str, bool]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:1984/api/streams", timeout=2) as response:
            data = json.loads(response.read())
            return {name: bool(info.get("producers")) for name, info in data.items()}
    except Exception:
        return {}


def public_stream_path(stream_name: str) -> str:
    if stream_name.startswith("site") and "_cam" in stream_name:
        prefix, _, suffix = stream_name.partition("_")
        return f"{prefix}/{suffix}"
    return stream_name


def publish_url(stream_name: str) -> str:
    path = public_stream_path(stream_name)
    return f"rtsp://site{SITE_ID}:PASS_{SITE_ID}@{SERVER_HOST}:{SERVER_RTSP_PORT}/{path}"


def archive_stream_path(camera: dict, session_id: str) -> str:
    return f"site{SITE_ID}/archive/{session_id}/cam{camera['channel']:02d}"


def archive_publish_url(stream_path: str) -> str:
    return f"rtsp://site{SITE_ID}:PASS_{SITE_ID}@{SERVER_HOST}:{SERVER_RTSP_PORT}/{stream_path}"


def load_configured_streams() -> dict[str, str]:
    try:
        cfg = yaml.safe_load(Path(GO2RTC_YAML).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log.warning("Failed to read go2rtc config: %s", exc)
        return {}
    streams = cfg.get("streams") or {}
    return {name: publish_url(name) for name in streams}


def stop_publisher(stream_name: str) -> None:
    proc = _ffmpeg_procs.pop(stream_name, None)
    _publisher_targets.pop(stream_name, None)
    if not proc:
        return
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def start_publisher(stream_name: str, target_url: str) -> None:
    local_url = f"rtsp://127.0.0.1:8554/{stream_name}"
    cmd = [
        FFMPEG_BIN,
        "-nostdin",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "tcp",
        "-i",
        local_url,
        "-c",
        "copy",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        target_url,
    ]
    try:
        _ffmpeg_procs[stream_name] = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.error("Failed to start publisher %s: %s", stream_name, exc)
        return
    _publisher_targets[stream_name] = target_url
    log.info("Started publisher %s -> %s", stream_name, target_url)


def sync_publishers() -> None:
    desired = load_configured_streams()
    for stream_name in list(_ffmpeg_procs):
        if stream_name not in desired:
            stop_publisher(stream_name)
    for stream_name, target_url in desired.items():
        proc = _ffmpeg_procs.get(stream_name)
        target_changed = _publisher_targets.get(stream_name) != target_url
        if proc and proc.poll() is None and not target_changed:
            continue
        if proc:
            stop_publisher(stream_name)
        start_publisher(stream_name, target_url)


def publisher_status() -> dict[str, bool]:
    local_streams = get_go2rtc_streams()
    desired = load_configured_streams()
    status = {}
    for stream_name in desired:
        proc = _ffmpeg_procs.get(stream_name)
        proc_ready = proc is not None and proc.poll() is None
        local_ready = local_streams.get(stream_name, False)
        status[public_stream_path(stream_name)] = local_ready and proc_ready
    return status


def collect_traffic() -> dict[str, dict]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:1984/api/streams", timeout=2) as response:
            data = json.loads(response.read())
    except Exception:
        return {}

    totals = {}
    for name, info in data.items():
        consumers = info.get("consumers", [])
        producers = info.get("producers", [])
        # go2rtc stores bytes in receivers[] inside each producer
        rx = 0
        for p in producers:
            for r in p.get("receivers", []):
                rx += r.get("bytes", 0)
            # fallback: top-level recv field (older go2rtc versions)
            rx += p.get("recv", 0)
        tx = 0
        for c in consumers:
            for s in c.get("senders", []):
                tx += s.get("bytes", 0)
            tx += c.get("send", 0)
        totals[public_stream_path(name)] = (rx, tx)

    result = {}
    for path, (rx, tx) in totals.items():
        prev_rx, prev_tx = _last_traffic_totals.get(path, (rx, tx))
        delta_rx = rx - prev_rx if rx >= prev_rx else rx
        delta_tx = tx - prev_tx if tx >= prev_tx else tx
        result[path] = {"rx": max(delta_rx, 0), "tx": max(delta_tx, 0)}

    _last_traffic_totals.clear()
    _last_traffic_totals.update(totals)
    return result


class AuthenticatedHttpClient:
    def __init__(self, user: str, password: str):
        self.user = user
        self.password = password

    def _opener(self, url: str):
        password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        password_mgr.add_password(None, url, self.user, self.password)
        return urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(password_mgr),
            urllib.request.HTTPBasicAuthHandler(password_mgr),
        )

    def request(self, url: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None, timeout: int = 20) -> bytes:
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with self._opener(url).open(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise AdapterError(f"NVR API error {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise AdapterError(f"Cannot reach NVR API: {exc.reason}") from exc


class HikvisionArchiveAdapter:
    vendor = "hikvision"
    discovery_protocol = "hikvision-isapi"

    def __init__(self, site: dict, cameras: list[dict]):
        self.site = site
        self.cameras = cameras
        self.base_url = f"http://{site['nvr_ip']}:{site.get('nvr_http_port') or 80}"
        self.user = site["nvr_user"]
        self.password = site["nvr_pass"]
        self.http = AuthenticatedHttpClient(self.user, self.password)
        self.track_map = {}
        for camera in cameras:
            if camera.get("channel_id") is not None:
                self.track_map[int(camera["channel_id"])] = camera
            self.track_map[int(camera["channel"])] = camera

    def _request(self, path: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None) -> bytes:
        try:
            return self.http.request(f"{self.base_url}{path}", method=method, data=data, headers=headers, timeout=15)
        except AdapterError as exc:
            raise AdapterError(str(exc).replace("NVR API error", "Hikvision API error").replace("Cannot reach NVR API", "Cannot reach Hikvision NVR")) from exc

    def _select_cameras(self, camera_id: int | None) -> list[dict]:
        if camera_id is None:
            return [camera for camera in self.cameras if camera.get("enabled", True)]
        for camera in self.cameras:
            if int(camera["id"]) == int(camera_id):
                return [camera]
        raise AdapterError("Camera not found in adapter payload")

    def live_url(self, camera: dict) -> str:
        user = quote(self.user, safe="")
        password = quote(self.password, safe="")
        return (
            f"rtsp://{user}:{password}@{self.site['nvr_ip']}:{self.site['nvr_port']}"
            f"/Streaming/Channels/{camera['channel_id']}"
        )

    def list_recordings(self, camera_id: int | None, start: datetime, end: datetime, limit: int) -> list[dict]:
        cameras = self._select_cameras(camera_id)
        payload = self._build_search_payload(cameras, start, end, limit)
        body = self._request(
            "/ISAPI/ContentMgmt/search",
            method="POST",
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/xml; charset=UTF-8"},
        )
        return self._parse_search_results(body, limit)

    def discover_channels(self) -> list[dict]:
        errors = []
        for path in ("/ISAPI/ContentMgmt/StreamingProxy/channels", "/ISAPI/Streaming/channels"):
            try:
                body = self._request(path)
                items = self._parse_channel_list(body)
                if items:
                    return items
            except AdapterError as exc:
                errors.append(str(exc))
        if errors:
            raise AdapterError(errors[-1])
        return []

    def playback_input_args(self, camera: dict, start: datetime, end: datetime) -> list[str]:
        user = quote(self.user, safe="")
        password = quote(self.password, safe="")
        url = (
            f"rtsp://{user}:{password}@{self.site['nvr_ip']}:{self.site['nvr_port']}"
            f"/Streaming/tracks/{camera['channel_id']}"
            f"?starttime={hik_rtsp_time(start)}&endtime={hik_rtsp_time(end)}"
        )
        return ["-rtsp_transport", "tcp", "-i", url]

    def _build_search_payload(self, cameras: list[dict], start: datetime, end: datetime, limit: int) -> str:
        track_xml = "".join(f"<trackID>{camera['channel_id']}</trackID>" for camera in cameras)
        search_id = uuid.uuid4().hex
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<CMSearchDescription xmlns="http://www.isapi.org/ver20/XMLSchema" version="2.0">'
            f"<searchID>{search_id}</searchID>"
            "<trackList>"
            f"{track_xml}"
            "</trackList>"
            "<timeSpanList><timeSpan>"
            f"<startTime>{hik_search_time(start)}</startTime>"
            f"<endTime>{hik_search_time(end)}</endTime>"
            "</timeSpan></timeSpanList>"
            f"<maxResults>{limit}</maxResults>"
            "<searchResultPosition>0</searchResultPosition>"
            "<metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>"
            "</CMSearchDescription>"
        )

    def _parse_search_results(self, body: bytes, limit: int) -> list[dict]:
        root = ET.fromstring(body)
        items = []
        for node in root.iter():
            if xml_local_name(node.tag) != "searchMatchItem":
                continue
            track_text = xml_desc_text(node, "trackID")
            if not track_text:
                playback_uri = xml_desc_text(node, "playbackURI")
                match = re.search(r"/tracks/(\d+)", playback_uri)
                track_text = match.group(1) if match else ""
            if not track_text:
                continue
            try:
                track_id = int(track_text)
            except ValueError:
                continue
            camera = self.track_map.get(track_id)
            if not camera:
                continue
            time_span = None
            for child in node.iter():
                if xml_local_name(child.tag) == "timeSpan":
                    time_span = child
                    break
            if time_span is None:
                continue
            start_text = xml_child_text(time_span, "startTime")
            end_text = xml_child_text(time_span, "endTime")
            if not start_text or not end_text:
                continue
            start_dt = parse_hik_datetime(start_text)
            end_dt = parse_hik_datetime(end_text)
            record_type = xml_desc_text(node, "metadataDescriptor", "recordType", "contentType") or "recording"
            items.append({
                "camera_id": int(camera["id"]),
                "camera_name": camera["name"] or f"Cam {camera['channel']:02d}",
                "channel": int(camera["channel"]),
                "stream_type": camera["stream_type"],
                "recording_type": record_type,
                "start": iso_utc(start_dt),
                "end": iso_utc(end_dt),
                "vendor": self.vendor,
            })
        items.sort(key=lambda item: item["start"])
        return items[:limit]

    def _parse_channel_list(self, body: bytes) -> list[dict]:
        root = ET.fromstring(body)
        discovered = {}
        for node in root.iter():
            if xml_local_name(node.tag) not in {"StreamingChannel", "StreamingProxyChannel"}:
                continue
            id_text = xml_child_text(node, "id")
            if not id_text.isdigit():
                continue
            channel_id = int(id_text)
            channel = channel_id // 100 if channel_id >= 100 else channel_id
            stream_num = channel_id % 100 if channel_id >= 100 else 1
            entry = discovered.setdefault(channel, {
                "channel": channel,
                "name": xml_child_text(node, "channelName", "name") or f"Cam {channel:02d}",
                "channel_id": channel * 100 + 1,
                "source_ref": None,
                "profile_ref": None,
                "has_main": False,
                "has_sub": False,
                "vendor": self.vendor,
                "protocol": self.discovery_protocol,
            })
            if stream_num == 1:
                entry["has_main"] = True
                entry["channel_id"] = channel_id
            elif stream_num == 2:
                entry["has_sub"] = True
        return [discovered[key] for key in sorted(discovered)]


class OnvifSoapClient:
    def __init__(self, site: dict):
        self.site = site
        self.user = site["nvr_user"]
        self.password = site["nvr_pass"]
        self.nvr_ip = site["nvr_ip"]
        self.http_port = int(site.get("nvr_http_port") or 80)
        self.base_http = f"http://{self.nvr_ip}:{self.http_port}"
        self.http = AuthenticatedHttpClient(self.user, self.password)
        self._services: dict[str, list[str]] = {}
        self._profiles: list[dict] | None = None
        self._recordings: list[dict] | None = None
        self._stream_uris: dict[str, str] = {}
        self._replay_uris: dict[str, str] = {}

    def _default_service_urls(self, service: str) -> list[str]:
        mapping = {
            "device": [f"{self.base_http}/onvif/device_service"],
            "media": [
                f"{self.base_http}/onvif/media_service",
                f"{self.base_http}/onvif/media2_service",
            ],
            "recording": [f"{self.base_http}/onvif/recording_service"],
            "replay": [f"{self.base_http}/onvif/replay_service"],
        }
        return mapping.get(service, [])

    def _soap_envelope(self, body_xml: str) -> bytes:
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope '
            'xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
            'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
            'xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" '
            'xmlns:trc="http://www.onvif.org/ver10/recording/wsdl" '
            'xmlns:trp="http://www.onvif.org/ver10/replay/wsdl" '
            'xmlns:tt="http://www.onvif.org/ver10/schema">'
            f"<s:Body>{body_xml}</s:Body>"
            "</s:Envelope>"
        )
        return envelope.encode("utf-8")

    def _soap_call(self, url: str, action: str, body_xml: str) -> ET.Element:
        headers = {
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"',
            "SOAPAction": f'"{action}"',
        }
        raw = self.http.request(
            url,
            method="POST",
            data=self._soap_envelope(body_xml),
            headers=headers,
            timeout=20,
        )
        root = ET.fromstring(raw)
        for node in root.iter():
            if xml_local_name(node.tag) == "Fault":
                reason = xml_desc_text(node, "Text", "Reason") or "ONVIF SOAP fault"
                raise AdapterError(reason)
        return root

    def _soap_call_many(self, urls: list[str], action: str, body_xml: str) -> ET.Element:
        errors = []
        seen = set()
        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                return self._soap_call(url, action, body_xml)
            except AdapterError as exc:
                errors.append(f"{url}: {exc}")
        if not errors:
            raise AdapterError(f"No ONVIF {action} endpoint candidates")
        raise AdapterError(errors[-1])

    def _device_service_urls(self) -> list[str]:
        return self._default_service_urls("device")

    def _discover_services(self) -> dict[str, list[str]]:
        if self._services:
            return self._services
        services = {
            "media": self._default_service_urls("media"),
            "recording": self._default_service_urls("recording"),
            "replay": self._default_service_urls("replay"),
        }
        body = "<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>"
        try:
            root = self._soap_call_many(
                self._device_service_urls(),
                "http://www.onvif.org/ver10/device/wsdl/GetCapabilities",
                body,
            )
            for node in root.iter():
                local = xml_local_name(node.tag)
                if local not in {"Media", "Media2", "Recording", "Replay"}:
                    continue
                xaddr = xml_desc_text(node, "XAddr")
                if not xaddr:
                    continue
                key = "media" if local in {"Media", "Media2"} else local.lower()
                services.setdefault(key, [])
                services[key].insert(0, xaddr)
        except AdapterError as exc:
            log.warning("ONVIF capability discovery failed for %s: %s", self.site["nvr_ip"], exc)
        self._services = {
            key: [url for index, url in enumerate(values) if url and url not in values[:index]]
            for key, values in services.items()
        }
        return self._services

    def service_urls(self, service: str) -> list[str]:
        services = self._discover_services()
        values = services.get(service) or self._default_service_urls(service)
        return [url for index, url in enumerate(values) if url and url not in values[:index]]

    def get_profiles(self) -> list[dict]:
        if self._profiles is not None:
            return self._profiles
        attempts = (
            (
                "http://www.onvif.org/ver10/media/wsdl/GetProfiles",
                "<trt:GetProfiles/>",
            ),
            (
                "http://www.onvif.org/ver20/media/wsdl/GetProfiles",
                "<tr2:GetProfiles/>",
            ),
        )
        last_error = None
        root = None
        for action, body in attempts:
            try:
                root = self._soap_call_many(self.service_urls("media"), action, body)
                break
            except AdapterError as exc:
                last_error = exc
        if root is None:
            raise last_error or AdapterError("ONVIF media service is unavailable")

        profiles = []
        for node in root.iter():
            if xml_local_name(node.tag) not in {"Profiles", "Profile"}:
                continue
            token = node.attrib.get("token") or xml_child_text(node, "token", "ProfileToken")
            if not token:
                continue
            source_ref = ""
            for child in node.iter():
                if xml_local_name(child.tag) == "VideoSourceConfiguration":
                    source_ref = xml_child_text(child, "SourceToken", "token")
                    if source_ref:
                        break
            width_text = xml_desc_text(node, "Width")
            height_text = xml_desc_text(node, "Height")
            try:
                area = int(width_text or 0) * int(height_text or 0)
            except ValueError:
                area = 0
            profiles.append({
                "token": token,
                "name": xml_child_text(node, "Name") or f"Profile {token}",
                "source_ref": source_ref or token,
                "area": area,
            })
        self._profiles = profiles
        return profiles

    def get_stream_uri(self, profile_token: str) -> str:
        if profile_token in self._stream_uris:
            return self._stream_uris[profile_token]
        attempts = (
            (
                "http://www.onvif.org/ver10/media/wsdl/GetStreamUri",
                (
                    "<trt:GetStreamUri>"
                    "<trt:StreamSetup>"
                    "<tt:Stream>RTP-Unicast</tt:Stream>"
                    "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
                    "</trt:StreamSetup>"
                    f"<trt:ProfileToken>{xml_escape(profile_token)}</trt:ProfileToken>"
                    "</trt:GetStreamUri>"
                ),
            ),
            (
                "http://www.onvif.org/ver20/media/wsdl/GetStreamUri",
                (
                    "<tr2:GetStreamUri>"
                    "<tr2:Protocol>RTSP</tr2:Protocol>"
                    f"<tr2:ProfileToken>{xml_escape(profile_token)}</tr2:ProfileToken>"
                    "</tr2:GetStreamUri>"
                ),
            ),
        )
        last_error = None
        for action, body in attempts:
            try:
                root = self._soap_call_many(self.service_urls("media"), action, body)
                uri = xml_desc_text(root, "Uri")
                if uri:
                    self._stream_uris[profile_token] = uri
                    return uri
            except AdapterError as exc:
                last_error = exc
        raise last_error or AdapterError("ONVIF media service did not return RTSP URI")

    def get_recordings(self) -> list[dict]:
        if self._recordings is not None:
            return self._recordings
        root = self._soap_call_many(
            self.service_urls("recording"),
            "http://www.onvif.org/ver10/recording/wsdl/GetRecordings",
            "<trc:GetRecordings/>",
        )
        tokens = []
        for node in root.iter():
            local = xml_local_name(node.tag)
            if local == "RecordingItem":
                token = node.attrib.get("token") or xml_child_text(node, "RecordingToken")
                if token:
                    tokens.append(token)
            elif local == "RecordingToken" and node.text:
                tokens.append(node.text.strip())

        recordings = []
        seen = set()
        for token in tokens:
            if not token or token in seen:
                continue
            seen.add(token)
            info = self.get_recording_information(token)
            if info:
                recordings.append(info)
        recordings.sort(key=lambda item: item["start"])
        self._recordings = recordings
        return recordings

    def get_recording_information(self, recording_token: str) -> dict | None:
        root = self._soap_call_many(
            self.service_urls("recording"),
            "http://www.onvif.org/ver10/recording/wsdl/GetRecordingInformation",
            (
                "<trc:GetRecordingInformation>"
                f"<trc:RecordingToken>{xml_escape(recording_token)}</trc:RecordingToken>"
                "</trc:GetRecordingInformation>"
            ),
        )
        start_dt = None
        end_dt = None
        for value in xml_desc_texts(root, "EarliestRecordingTime", "EarliestRecording", "From", "StartTime"):
            try:
                start_dt = parse_xml_datetime(value)
                break
            except ValueError:
                continue
        for value in xml_desc_texts(root, "LatestRecordingTime", "LatestRecording", "Until", "EndTime"):
            try:
                end_dt = parse_xml_datetime(value)
                break
            except ValueError:
                continue
        if not start_dt or not end_dt:
            return None
        source_ref = ""
        for value in xml_desc_texts(root, "SourceToken"):
            if value:
                source_ref = value
                break
        return {
            "recording_token": recording_token,
            "source_ref": source_ref,
            "name": xml_desc_text(root, "SourceName", "Name") or recording_token,
            "start": start_dt,
            "end": end_dt,
        }

    def get_replay_uri(self, recording_token: str) -> str:
        if recording_token in self._replay_uris:
            return self._replay_uris[recording_token]
        root = self._soap_call_many(
            self.service_urls("replay"),
            "http://www.onvif.org/ver10/replay/wsdl/GetReplayUri",
            (
                "<trp:GetReplayUri>"
                "<trp:StreamSetup>"
                "<tt:Stream>RTP-Unicast</tt:Stream>"
                "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
                "</trp:StreamSetup>"
                f"<trp:RecordingToken>{xml_escape(recording_token)}</trp:RecordingToken>"
                "</trp:GetReplayUri>"
            ),
        )
        uri = xml_desc_text(root, "Uri")
        if not uri:
            raise AdapterError("ONVIF replay service did not return replay URI")
        self._replay_uris[recording_token] = uri
        return uri


class OnvifArchiveAdapter:
    vendor = "onvif"
    discovery_protocol = "onvif-media"

    def __init__(self, site: dict, cameras: list[dict]):
        self.site = site
        self.cameras = cameras
        self.client = OnvifSoapClient(site)
        self._profile_state: dict | None = None

    def _select_cameras(self, camera_id: int | None) -> list[dict]:
        if camera_id is None:
            return [camera for camera in self.cameras if camera.get("enabled", True)]
        for camera in self.cameras:
            if int(camera["id"]) == int(camera_id):
                return [camera]
        raise AdapterError("Camera not found in adapter payload")

    def _profile_groups(self) -> dict:
        if self._profile_state is not None:
            return self._profile_state
        profiles = self.client.get_profiles()
        groups = {}
        by_token = {}
        for profile in profiles:
            source_ref = profile["source_ref"] or profile["token"]
            entry = groups.setdefault(source_ref, {
                "source_ref": source_ref,
                "name": profile["name"],
                "profiles": [],
            })
            entry["profiles"].append(profile)
            by_token[profile["token"]] = profile

        used_channels = set()
        ordered = []
        fallback = 1
        for source_ref, entry in groups.items():
            profile_list = sorted(entry["profiles"], key=lambda item: (item["area"], item["name"]), reverse=True)
            main_profile = profile_list[0]
            sub_profile = profile_list[1] if len(profile_list) > 1 else None
            channel = best_effort_channel_number(source_ref, entry["name"], main_profile["name"], fallback=fallback)
            while channel in used_channels:
                channel += 1
            used_channels.add(channel)
            fallback = max(fallback, channel + 1)
            ordered.append({
                "channel": channel,
                "channel_id": channel * 100 + 1,
                "name": entry["name"] or f"Cam {channel:02d}",
                "source_ref": source_ref,
                "main_profile_ref": main_profile["token"],
                "sub_profile_ref": sub_profile["token"] if sub_profile else None,
                "has_main": True,
                "has_sub": sub_profile is not None,
            })

        ordered.sort(key=lambda item: item["channel"])
        self._profile_state = {
            "groups": ordered,
            "by_channel": {item["channel"]: item for item in ordered},
            "by_source": {item["source_ref"]: item for item in ordered},
            "by_profile": by_token,
        }
        return self._profile_state

    def _resolve_group(self, camera: dict) -> dict:
        state = self._profile_groups()
        if camera.get("source_ref"):
            group = state["by_source"].get(camera["source_ref"])
            if group:
                return group
        if camera.get("profile_ref"):
            for group in state["groups"]:
                if camera["profile_ref"] in {group["main_profile_ref"], group.get("sub_profile_ref")}:
                    return group
        group = state["by_channel"].get(int(camera["channel"]))
        if group:
            return group
        raise AdapterError(f"Cannot map camera {camera.get('name') or camera.get('id')} to ONVIF profile")

    def _resolve_profile_ref(self, camera: dict) -> str:
        group = self._resolve_group(camera)
        if camera.get("stream_type") == "sub" and group.get("sub_profile_ref"):
            return group["sub_profile_ref"]
        if group.get("main_profile_ref"):
            return group["main_profile_ref"]
        if camera.get("profile_ref") and camera["profile_ref"] in {group["main_profile_ref"], group.get("sub_profile_ref")}:
            return camera["profile_ref"]
        raise AdapterError("No ONVIF profile token available for selected stream type")

    def _resolve_source_ref(self, camera: dict) -> str:
        if camera.get("source_ref"):
            return camera["source_ref"]
        return self._resolve_group(camera)["source_ref"]

    def live_url(self, camera: dict) -> str:
        profile_ref = self._resolve_profile_ref(camera)
        uri = self.client.get_stream_uri(profile_ref)
        return rewrite_rtsp_uri(uri, self.site["nvr_ip"], int(self.site["nvr_port"]), self.site["nvr_user"], self.site["nvr_pass"])

    def discover_channels(self) -> list[dict]:
        state = self._profile_groups()
        return [{
            "channel": item["channel"],
            "channel_id": item["channel_id"],
            "name": item["name"],
            "source_ref": item["source_ref"],
            "profile_ref": item["main_profile_ref"],
            "has_main": item["has_main"],
            "has_sub": item["has_sub"],
            "vendor": self.vendor,
            "protocol": self.discovery_protocol,
        } for item in state["groups"]]

    def list_recordings(self, camera_id: int | None, start: datetime, end: datetime, limit: int) -> list[dict]:
        start_utc = to_utc(start)
        end_utc = to_utc(end)
        selected = self._select_cameras(camera_id)
        selected_by_source = {}
        for camera in selected:
            try:
                selected_by_source[self._resolve_source_ref(camera)] = camera
            except AdapterError:
                continue

        items = []
        for recording in self.client.get_recordings():
            if recording["end"] <= start_utc or recording["start"] >= end_utc:
                continue
            camera = selected_by_source.get(recording["source_ref"])
            if camera is None:
                if len(selected) == 1 and not selected_by_source:
                    camera = selected[0]
                else:
                    continue
            clip_start = max(recording["start"], start_utc)
            clip_end = min(recording["end"], end_utc)
            if clip_start >= clip_end:
                continue
            items.append({
                "camera_id": int(camera["id"]),
                "camera_name": camera["name"] or f"Cam {camera['channel']:02d}",
                "channel": int(camera["channel"]),
                "stream_type": camera["stream_type"],
                "recording_type": "recording",
                "start": iso_utc(clip_start),
                "end": iso_utc(clip_end),
                "vendor": self.vendor,
            })
        items.sort(key=lambda item: item["start"])
        return items[:limit]

    def _pick_recording(self, camera: dict, start: datetime, end: datetime) -> dict:
        start_utc = to_utc(start)
        end_utc = to_utc(end)
        source_ref = self._resolve_source_ref(camera)
        matches = [
            recording
            for recording in self.client.get_recordings()
            if recording["source_ref"] == source_ref and recording["end"] > start_utc and recording["start"] < end_utc
        ]
        if not matches:
            raise AdapterError("No archive found for requested time range")
        matches.sort(
            key=lambda item: (
                0 if item["start"] <= start_utc <= item["end"] else 1,
                abs((item["start"] - start_utc).total_seconds()),
            )
        )
        return matches[0]

    def playback_input_args(self, camera: dict, start: datetime, end: datetime) -> list[str]:
        recording = self._pick_recording(camera, start, end)
        replay_uri = self.client.get_replay_uri(recording["recording_token"])
        source_url = rewrite_rtsp_uri(
            replay_uri,
            self.site["nvr_ip"],
            int(self.site["nvr_port"]),
            self.site["nvr_user"],
            self.site["nvr_pass"],
        )
        start_utc = max(to_utc(start), recording["start"])
        end_utc = min(to_utc(end), recording["end"])
        if start_utc >= end_utc:
            raise AdapterError("Requested archive interval does not overlap available recording")
        offset = max(int((start_utc - recording["start"]).total_seconds()), 0)
        duration = max(int((end_utc - start_utc).total_seconds()), 1)
        args = ["-rtsp_transport", "tcp", "-i", source_url]
        if offset > 0:
            args.extend(["-ss", str(offset)])
        args.extend(["-t", str(duration)])
        return args


class DahuaArchiveAdapter(OnvifArchiveAdapter):
    vendor = "dahua"
    discovery_protocol = "onvif-media"


class UNVArchiveAdapter(OnvifArchiveAdapter):
    """Uniview (UNV) NVR adapter.

    UNV supports standard ONVIF for both live and recording queries.
    Live RTSP URL pattern: rtsp://<user>:<pass>@<host>:554/unicast/c<CH>/s0/live
    """
    vendor = "unv"
    discovery_protocol = "onvif-media"

    def live_url(self, camera: dict) -> str:
        """UNV RTSP URL uses channel-based path instead of ONVIF profile token."""
        ch = int(camera.get("channel", 1))
        site = self.site
        user = site.get("nvr_user", "admin")
        password = site.get("nvr_password", "")
        host = site.get("nvr_ip", "")
        port = site.get("nvr_rtsp_port", 554)
        # Try standard UNV RTSP path first; fallback to ONVIF GetStreamUri
        return f"rtsp://{user}:{password}@{host}:{port}/unicast/c{ch}/s0/live"


def get_archive_adapter(site: dict, cameras: list[dict]):
    vendor = (site.get("vendor") or "hikvision").strip().lower()
    if vendor == "hikvision":
        return HikvisionArchiveAdapter(site, cameras)
    if vendor == "onvif":
        return OnvifArchiveAdapter(site, cameras)
    if vendor == "dahua":
        return DahuaArchiveAdapter(site, cameras)
    if vendor in ("unv", "uniview"):
        return UNVArchiveAdapter(site, cameras)
    # Fallback: try ONVIF for any unknown vendor
    log.warning("Unknown vendor %r, falling back to ONVIF adapter", vendor)
    return OnvifArchiveAdapter(site, cameras)


def build_go2rtc_yaml(site: dict, cameras: list[dict]) -> str:
    streams = {}
    enabled = [camera for camera in cameras if camera.get("enabled", True)]
    adapter = get_archive_adapter(site, cameras)
    last_error = None
    for camera in enabled:
        stream_name = f"site{SITE_ID}_cam{int(camera['channel']):02d}"
        try:
            streams[stream_name] = [adapter.live_url(camera)]
        except AdapterError as exc:
            last_error = exc
            log.warning("Skipping camera %s during go2rtc config build: %s", camera.get("name") or camera.get("id"), exc)
    if enabled and not streams:
        raise last_error or AdapterError("No camera RTSP sources resolved")
    config = {
        "api": {"listen": "127.0.0.1:1984"},
        "rtsp": {"listen": ":8554"},
        "streams": streams,
    }
    return yaml.dump(config, default_flow_style=False, allow_unicode=True)


def list_archive_items(site: dict, cameras: list[dict], camera_id: int | None, start: datetime, end: datetime, limit: int) -> list[dict]:
    adapter = get_archive_adapter(site, cameras)
    return adapter.list_recordings(camera_id, start, end, limit)


def discover_archive_channels(site: dict, cameras: list[dict]) -> list[dict]:
    adapter = get_archive_adapter(site, cameras)
    return adapter.discover_channels()


def start_archive_session(site: dict, camera: dict, start: datetime, end: datetime) -> dict:
    if start >= end:
        raise AdapterError("Start time must be before end time")
    adapter = get_archive_adapter(site, [camera])
    session_id = uuid.uuid4().hex[:12]
    stream_path = archive_stream_path(camera, session_id)
    target_url = archive_publish_url(stream_path)
    cmd = [
        FFMPEG_BIN,
        "-nostdin",
        "-loglevel",
        "warning",
        *adapter.playback_input_args(camera, start, end),
        "-c",
        "copy",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        target_url,
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise AdapterError(f"Cannot start archive playback process: {exc}") from exc
    time.sleep(1)
    if proc.poll() is not None:
        raise AdapterError("Archive playback process exited immediately")
    now = datetime.now(timezone.utc)
    duration = max(end - start, timedelta(minutes=1))
    expires_at = now + duration + timedelta(minutes=10)
    _archive_sessions[session_id] = {
        "proc": proc,
        "stream_path": stream_path,
        "camera_id": int(camera["id"]),
        "vendor": site.get("vendor", "hikvision"),
        "expires_at": expires_at,
    }
    return {
        "session_id": session_id,
        "stream_path": stream_path,
        "vendor": site.get("vendor", "hikvision"),
        "expires_at": expires_at,
    }


def stop_archive_session(session_id: str) -> bool:
    session = _archive_sessions.pop(session_id, None)
    if not session:
        return False
    proc = session["proc"]
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    return True


def cleanup_archive_sessions() -> None:
    now = datetime.now(timezone.utc)
    for session_id, session in list(_archive_sessions.items()):
        proc = session["proc"]
        expired = session["expires_at"] <= now
        finished = proc.poll() is not None
        if expired or finished:
            stop_archive_session(session_id)


async def open_tcp_tunnel(ws, connection_id: str, target_host: str, target_port: int) -> None:
    reader, writer = await asyncio.open_connection(target_host, int(target_port))
    task = asyncio.create_task(pump_tcp_to_server(ws, connection_id, reader))
    _tcp_tunnels[connection_id] = {
        "writer": writer,
        "task": task,
    }


async def pump_tcp_to_server(ws, connection_id: str, reader: asyncio.StreamReader) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await ws_send(ws, {
                "type": "tcp_data",
                "connection_id": connection_id,
                "data": base64.b64encode(data).decode("ascii"),
            })
    except Exception:
        pass
    finally:
        await close_tcp_tunnel(connection_id, notify=True, ws=ws)


async def write_tcp_tunnel(connection_id: str, encoded_data: str) -> None:
    tunnel = _tcp_tunnels.get(connection_id)
    if not tunnel:
        return
    writer = tunnel["writer"]
    writer.write(base64.b64decode(encoded_data))
    await writer.drain()


async def close_tcp_tunnel(connection_id: str, *, notify: bool = False, ws=None) -> None:
    tunnel = _tcp_tunnels.pop(connection_id, None)
    if not tunnel:
        return
    task = tunnel.get("task")
    if task and task is not asyncio.current_task() and not task.done():
        task.cancel()
    writer = tunnel["writer"]
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
    if notify and ws is not None:
        try:
            await ws_send(ws, {
                "type": "tcp_close",
                "connection_id": connection_id,
            })
        except Exception:
            pass


async def close_all_tcp_tunnels() -> None:
    for connection_id in list(_tcp_tunnels):
        await close_tcp_tunnel(connection_id)


def server_api_request(path: str, *, method: str = "GET", data=None):
    body = None
    headers = {"X-Agent-Token": AGENT_TOKEN}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{SERVER_API.rstrip('/')}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise AdapterError(f"Fleet server error {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise AdapterError(f"Cannot reach fleet server: {exc.reason}") from exc


def fetch_bundle():
    try:
        payload = server_api_request(f"/api/agent/sites/{SITE_ID}/bundle")
        return _cache_bundle_payload(payload)
    except AdapterError as exc:
        cached = _read_cached_bundle()
        if cached:
            fallback = dict(cached)
            fallback["warning"] = f"Server is temporarily unavailable, showing last saved local config: {exc}"
            return fallback
        raise


def save_bundle_site(site_data: dict):
    payload = server_api_request(
        f"/api/agent/sites/{SITE_ID}/site",
        method="PUT",
        data=site_data,
    )
    return _cache_bundle_payload(payload, site=payload.get("site") if isinstance(payload, dict) else None)


def save_bundle_cameras(cameras: list[dict]):
    payload = server_api_request(
        f"/api/agent/sites/{SITE_ID}/cameras/replace",
        method="PUT",
        data=cameras,
    )
    return _cache_bundle_payload(payload, cameras=payload.get("cameras") if isinstance(payload, dict) else cameras)


def guess_vendor_from_text(*values: str) -> str:
    text = " ".join(value for value in values if value).lower()
    if "hikvision" in text or "hik" in text:
        return "hikvision"
    if "dahua" in text:
        return "dahua"
    return "onvif"


def discover_onvif_devices(timeout: float = 2.5) -> list[dict]:
    message_id = f"uuid:{uuid.uuid4()}"
    probe = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        "<e:Header>"
        "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
        f"<w:MessageID>{message_id}</w:MessageID>"
        "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
        "</e:Header>"
        "<e:Body>"
        "<d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe>"
        "</e:Body>"
        "</e:Envelope>"
    ).encode("utf-8")

    items = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.35)
    try:
        sock.sendto(probe, ("239.255.255.250", 3702))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            xaddrs = " ".join(xml_desc_texts(root, "XAddrs"))
            scopes = " ".join(xml_desc_texts(root, "Scopes"))
            endpoint = xml_desc_text(root, "Address")
            vendor = guess_vendor_from_text(scopes, xaddrs, endpoint)
            http_port = None
            first_xaddr = xaddrs.split(" ", 1)[0] if xaddrs else ""
            if first_xaddr:
                parsed = urlsplit(first_xaddr)
                http_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            items[addr[0]] = {
                "ip": addr[0],
                "vendor": vendor,
                "http_port": http_port,
                "xaddrs": xaddrs,
                "scopes": scopes,
                "endpoint": endpoint,
                "protocol": "onvif-ws-discovery",
            }
    finally:
        sock.close()
    return [items[key] for key in sorted(items)]


local_app = FastAPI(title="NVR Fleet Local Admin", version=VERSION)


@local_app.get("/", response_class=HTMLResponse)
async def local_index():
    return HTMLResponse(LOCAL_ADMIN_HTML)


@local_app.get("/api/health")
async def local_health():
    return {"status": "ok", "site_id": SITE_ID, "version": VERSION}


@local_app.get("/api/bundle")
async def local_bundle():
    try:
        return await asyncio.to_thread(fetch_bundle)
    except AdapterError as exc:
        raise HTTPException(502, str(exc)) from exc


@local_app.put("/api/site")
async def local_save_site(payload: LocalSiteConfigItem):
    data = payload.model_dump()
    if not (data.get("nvr_pass") or "").strip():
        data.pop("nvr_pass", None)
    try:
        return await asyncio.to_thread(save_bundle_site, data)
    except AdapterError as exc:
        raise HTTPException(502, str(exc)) from exc


@local_app.get("/api/discover")
async def local_discover():
    try:
        bundle = await asyncio.to_thread(fetch_bundle)
    except AdapterError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not (bundle.get("site", {}).get("nvr_ip") or "").strip():
        raise HTTPException(400, "NVR IP is not configured yet")
    try:
        items = await asyncio.to_thread(discover_archive_channels, bundle["site"], bundle["cameras"])
    except AdapterError as exc:
        raise HTTPException(502, str(exc)) from exc
    protocol = items[0].get("protocol") if items else None
    return {"items": items, "protocol": protocol}


@local_app.get("/api/discover-devices")
async def local_discover_devices():
    try:
        items = await asyncio.to_thread(discover_onvif_devices)
    except AdapterError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"items": items, "protocol": "onvif-ws-discovery"}


@local_app.put("/api/cameras")
async def local_save_cameras(items: list[LocalCameraItem]):
    payload = [item.model_dump() for item in items]
    try:
        return await asyncio.to_thread(save_bundle_cameras, payload)
    except AdapterError as exc:
        raise HTTPException(502, str(exc)) from exc


async def send_reply(ws, request_id: str, *, ok: bool, **payload) -> None:
    await ws_send(ws, {"reply_to": request_id, "ok": ok, **payload})


async def handle_message(ws: websockets.WebSocketClientProtocol, msg: dict):
    msg_type = msg.get("type")
    if msg_type == "ping":
        await ws_send(ws, {"type": "pong"})
        return

    action = msg.get("action")
    request_id = msg.get("request_id")
    try:
        if action == "update_config":
            yaml_content = msg.get("go2rtc_yaml", "")
            if msg.get("site") and msg.get("cameras") is not None:
                await asyncio.to_thread(
                    _cache_bundle_payload,
                    {"site": msg["site"], "cameras": msg["cameras"]},
                )
                yaml_content = await asyncio.to_thread(build_go2rtc_yaml, msg["site"], msg["cameras"])
            write_config(yaml_content)
            restart_go2rtc()
            await asyncio.sleep(2)
            sync_publishers()
            await ws_send(ws, {"type": "stream_status", "streams": publisher_status()})
            if request_id:
                await send_reply(ws, request_id, ok=True)
            return

        if action == "restart":
            restart_go2rtc()
            await asyncio.sleep(2)
            sync_publishers()
            cleanup_archive_sessions()
            if request_id:
                await send_reply(ws, request_id, ok=True)
            return

        if action == "drain":
            for stream_name in list(_ffmpeg_procs):
                stop_publisher(stream_name)
            for session_id in list(_archive_sessions):
                stop_archive_session(session_id)
            await close_all_tcp_tunnels()
            await ws_send(ws, {"type": "stream_status", "streams": publisher_status()})
            if request_id:
                await send_reply(ws, request_id, ok=True)
            return

        if action == "shutdown":
            for stream_name in list(_ffmpeg_procs):
                stop_publisher(stream_name)
            for session_id in list(_archive_sessions):
                stop_archive_session(session_id)
            await close_all_tcp_tunnels()
            sys.exit(0)

        if action == "get_status":
            await ws_send(ws, {"type": "stream_status", "streams": publisher_status()})
            if request_id:
                await send_reply(ws, request_id, ok=True)
            return

        if action == "archive_list":
            items = await asyncio.to_thread(
                list_archive_items,
                msg["site"],
                msg["cameras"],
                msg.get("camera_id"),
                parse_server_time(msg["start"]),
                parse_server_time(msg["end"]),
                int(msg.get("limit", 200)),
            )
            await send_reply(ws, request_id, ok=True, items=items)
            return

        if action == "archive_start_playback":
            session = await asyncio.to_thread(
                start_archive_session,
                msg["site"],
                msg["camera"],
                parse_server_time(msg["start"]),
                parse_server_time(msg["end"]),
            )
            await send_reply(
                ws,
                request_id,
                ok=True,
                session_id=session["session_id"],
                stream_path=session["stream_path"],
                vendor=session["vendor"],
                expires_at=iso_utc(session["expires_at"]),
            )
            return

        if action == "archive_stop_playback":
            stopped = stop_archive_session(msg["session_id"])
            await send_reply(ws, request_id, ok=True, stopped=stopped)
            return

        if action == "tcp_open":
            await open_tcp_tunnel(ws, msg["connection_id"], msg["target_host"], int(msg["target_port"]))
            await send_reply(ws, request_id, ok=True)
            return

        if action == "tcp_data":
            await write_tcp_tunnel(msg["connection_id"], msg["data"])
            return

        if action == "tcp_close":
            await close_tcp_tunnel(msg["connection_id"])
            return

        if request_id:
            await send_reply(ws, request_id, ok=False, error=f"Unsupported action: {action}")
    except AdapterError as exc:
        if request_id:
            await send_reply(ws, request_id, ok=False, error=str(exc))
    except Exception as exc:
        log.exception("Failed action %s", action)
        if request_id:
            await send_reply(ws, request_id, ok=False, error=str(exc))


async def heartbeat_loop(ws):
    while True:
        try:
            uptime = int(time.time() - _start_time)
            await ws_send(ws, {"type": "heartbeat", "version": VERSION, "uptime": uptime})
        except Exception:
            break
        await asyncio.sleep(30)


async def traffic_loop(ws):
    while True:
        await asyncio.sleep(60)
        try:
            sync_publishers()
            cleanup_archive_sessions()
            traffic = collect_traffic()
            if traffic:
                await ws_send(ws, {"type": "traffic", "streams": traffic})
            await ws_send(ws, {"type": "stream_status", "streams": publisher_status()})
        except Exception:
            break


async def run_agent():
    url = f"{SERVER_WS}?token={AGENT_TOKEN}"
    reconnect_delay = 5
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                global _active_ws
                _active_ws = ws
                log.info("Connected to fleet server")
                reconnect_delay = 5
                hb_task = asyncio.create_task(heartbeat_loop(ws))
                traf_task = asyncio.create_task(traffic_loop(ws))
                try:
                    async for raw in ws:
                        await handle_message(ws, json.loads(raw))
                finally:
                    hb_task.cancel()
                    traf_task.cancel()
                    await close_all_tcp_tunnels()
        except asyncio.CancelledError:
            raise
        except (websockets.ConnectionClosed, OSError) as exc:
            log.warning("Disconnected: %s. Reconnecting in %ss", exc, reconnect_delay)
            await close_all_tcp_tunnels()
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        except Exception as exc:
            log.error("Unexpected agent error: %s", exc)
            await asyncio.sleep(reconnect_delay)


async def run_local_admin():
    global _admin_server
    config = uvicorn.Config(
        local_app,
        host=AGENT_ADMIN_HOST,
        port=AGENT_ADMIN_PORT,
        log_level="warning",
        access_log=False,
    )
    _admin_server = uvicorn.Server(config)
    _admin_server.install_signal_handlers = lambda: None
    log.info("Starting local admin on %s:%s", AGENT_ADMIN_HOST, AGENT_ADMIN_PORT)
    await _admin_server.serve()


async def run_services():
    await asyncio.gather(run_agent(), run_local_admin())


_start_time = time.time()


# Global WS reference for graceful shutdown notification
_active_ws = None


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    sync_publishers()

    def _sig(sig, _):
        log.info("Signal %s received, initiating graceful shutdown", sig)
        if _admin_server is not None:
            _admin_server.should_exit = True

        async def _drain():
            global _active_ws
            if _active_ws is not None:
                try:
                    await asyncio.wait_for(
                        ws_send(_active_ws, {"type": "draining", "reason": "SIGTERM"}),
                        timeout=2.0
                    )
                except Exception:
                    pass
            for sn in list(_ffmpeg_procs):
                stop_publisher(sn)
            for sid in list(_archive_sessions):
                stop_archive_session(sid)
            await asyncio.sleep(0.5)
            for task in list(asyncio.all_tasks(loop)):
                task.cancel()

        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_drain(), loop=loop))

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    try:
        loop.run_until_complete(run_services())
    except asyncio.CancelledError:
        pass
    finally:
        loop.run_until_complete(close_all_tcp_tunnels())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    main()
