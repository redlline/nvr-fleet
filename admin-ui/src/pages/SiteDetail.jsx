import { useEffect, useState } from "react"
import { api } from "../lib/api"
import { t } from "../lib/i18n"
import ArchiveTab from "../components/ArchiveTab"
import CamerasTab from "../components/CamerasTab"
import StreamsTab from "../components/StreamsTab"
import TrafficChart from "../components/TrafficChart"

const VENDOR_OPTIONS = [
  { value: "hikvision", label: "Hikvision" },
  { value: "dahua", label: "Dahua" },
  { value: "unv", label: "UNV / Uniview" },
  { value: "onvif", label: "ONVIF (generic)" },
]

function siteSummary(site) {
  const parts = []
  if (site.city) parts.push(site.city)
  parts.push(site.nvr_vendor)
  if (site.is_configured) {
    parts.push(`NVR RTSP: ${site.nvr_ip}:${site.nvr_port}`)
    parts.push(`API: ${site.nvr_http_port}`)
    parts.push(`Control: ${site.nvr_control_port}`)
  } else {
    parts.push("NVR pending local setup")
  }
  parts.push(`${site.channel_count} channels`)
  parts.push(`stream: ${site.stream_type}`)
  return parts.join(" | ")
}

export default function SiteDetail({ siteId, navigate }) {
  const [site, setSite] = useState(null)
  const [tab, setTab] = useState("cameras")
  const [loading, setLoading] = useState(true)
  const [showEdit, setShowEdit] = useState(false)
  const [actionMsg, setActionMsg] = useState("")

  async function load() {
    try {
      setSite(await api.getSite(siteId))
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const timer = setInterval(load, 15000)
    return () => clearInterval(timer)
  }, [siteId])

  async function handleDeploy() {
    setActionMsg("")
    const result = await api.deploySite(siteId)
    setActionMsg(result.sent ? "Config deployed to agent" : "Agent is offline, config will apply on reconnect")
    setTimeout(() => setActionMsg(""), 4000)
  }

  async function handleRestart() {
    if (!confirm("Restart go2rtc on the agent?")) return
    const result = await api.restartAgent(siteId)
    setActionMsg(result.sent ? "Restart command sent" : "Agent is offline")
    setTimeout(() => setActionMsg(""), 4000)
  }

  async function handleDrainRedeploy() {
    if (!confirm("Drain active streams, archive sessions and tunnels, then redeploy config?")) return
    setActionMsg("")
    try {
      const result = await api.drainRedeploySite(siteId)
      setActionMsg(result.message || "Agent drained and config redeployed")
      await load()
    } catch (err) {
      setActionMsg(err.message)
    } finally {
      setTimeout(() => setActionMsg(""), 5000)
    }
  }

  async function handleSaveEdit(form) {
    await api.updateSite(siteId, form)
    setShowEdit(false)
    load()
  }

  if (loading) return <div className="empty-state"><div className="spinner" /></div>
  if (!site) return <div className="empty-state">Site not found</div>

  const isConfigured = site.is_configured

  return (
    <div>
      <button className="back-btn" onClick={() => navigate("sites")}>{t("backToSites")}</button>

      <div className="page-header" style={{ alignItems: "flex-start" }}>
        <div>
          <div className="page-title" style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {site.name}
            <span className={`badge ${site.agent_online ? "badge-green" : "badge-red"}`}>
              <span className={`dot ${site.agent_online ? "dot-green" : "dot-red"}`} />
              {site.agent_online ? t("online") : t("offline")}
            </span>
            {!isConfigured && <span className="badge badge-gray">Draft</span>}
          </div>
          <div className="page-sub">{siteSummary(site)}</div>
          {site.agent_last_seen && (
            <div style={{ color: "var(--text2)", fontSize: 12, marginTop: 4 }}>
              Last seen: {new Date(site.agent_last_seen).toLocaleString()}
            </div>
          )}
        </div>
        <div className="btn-group" style={{ flexShrink: 0 }}>
          <button className="btn btn-ghost btn-sm" onClick={() => setShowEdit(true)}>{t("edit")}</button>
          <button className="btn btn-ghost btn-sm" onClick={handleDeploy}>{t("deployConfig")}</button>
          <button className="btn btn-ghost btn-sm" onClick={handleDrainRedeploy}>{t("drainRedeploy")}</button>
          <button className="btn btn-ghost btn-sm" onClick={handleRestart}>{t("restartAgent")}</button>
        </div>
      </div>

      {actionMsg && <div className="alert alert-info" style={{ marginBottom: 16 }}>{actionMsg}</div>}

      <div className="stat-grid" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: 20 }}>
        <div className="stat-card">
          <div className="stat-value">{site.camera_count}</div>
          <div className="stat-label">{t("cameras")}</div>
        </div>
        <div className={`stat-card ${site.online_streams > 0 ? "green" : ""}`}>
          <div className="stat-value">{site.online_streams}</div>
          <div className="stat-label">Live streams</div>
        </div>
        <div className={`stat-card ${isConfigured ? "" : "amber"}`}>
          <div className="stat-value" style={{ fontSize: 18 }}>{isConfigured ? site.nvr_vendor : "Draft"}</div>
          <div className="stat-label">{isConfigured ? "Archive adapter" : "Onboarding state"}</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ fontSize: 18 }}>{site.id}</div>
          <div className="stat-label">Site ID</div>
        </div>
      </div>

      {!isConfigured && (
        <div className="alert alert-info" style={{ marginBottom: 20 }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>Finish setup from the mini-PC in local LAN.</div>
          <div style={{ color: "var(--text2)", fontSize: 13, lineHeight: 1.5 }}>
            1. Install the agent on the mini-PC for this site.
            <br />
            2. Open the local panel on `http://MINI_PC_IP:7070`
            <br />
            3. Find the NVR in LAN or enter its local IP manually
            <br />
            4. Enter credentials, run autodiscovery, save cameras
            <br />
            5. The server will receive the NVR settings and activate live/archive for this site
          </div>
        </div>
      )}

      <div className="card" style={{ padding: 16, marginBottom: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>{t("thickClient")}</div>
        <div style={{ color: "var(--text2)", fontSize: 12, marginBottom: 10 }}>
          Use the public server host and these per-site ports in iVMS-4200 or a similar client.
          {!isConfigured && " The ports are already reserved; the actual NVR target becomes active after local setup on the mini-PC."}
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <span className="badge badge-gray">Host: {window.location.hostname}</span>
          <span className="badge badge-gray">HTTP: {site.tunnel_http_port}</span>
          <span className="badge badge-gray">Control: {site.tunnel_control_port}</span>
          <span className="badge badge-gray">RTSP: {site.tunnel_rtsp_port}</span>
        </div>
      </div>

      <div className="tabs">
        {[
          { id: "cameras", label: "Cameras" },
          { id: "streams", label: "Streams" },
          { id: "archive", label: "Archive" },
          { id: "traffic", label: "Traffic" },
        ].map((item) => (
          <button
            key={item.id}
            className={`tab ${tab === item.id ? "active" : ""}`}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "cameras" && <CamerasTab siteId={siteId} site={site} />}
      {tab === "streams" && <StreamsTab siteId={siteId} publicHost={window.location.hostname} />}
      {tab === "archive" && <ArchiveTab siteId={siteId} publicHost={window.location.hostname} />}
      {tab === "traffic" && (
        <div>
          <TrafficChart siteId={siteId} hours={1} title="Traffic - last 1 hour" />
          <TrafficChart siteId={siteId} hours={24} title="Traffic - last 24 hours" />
        </div>
      )}

      {showEdit && (
        <EditSiteModal site={site} onClose={() => setShowEdit(false)} onSave={handleSaveEdit} />
      )}
    </div>
  )
}

function EditSiteModal({ site, onClose, onSave }) {
  const [form, setForm] = useState({
    name: site.name,
    city: site.city,
    lat: site.lat,
    lon: site.lon,
    nvr_vendor: site.nvr_vendor,
    nvr_ip: site.nvr_ip,
    nvr_http_port: site.nvr_http_port,
    nvr_control_port: site.nvr_control_port,
    nvr_user: site.nvr_user,
    nvr_pass: "",
    nvr_port: site.nvr_port,
    stream_type: site.stream_type,
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  function upd(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  async function submit(event) {
    event.preventDefault()
    setSaving(true)
    setError("")
    try {
      const payload = {
        ...form,
        lat: parseFloat(form.lat) || 0,
        lon: parseFloat(form.lon) || 0,
        nvr_http_port: parseInt(form.nvr_http_port, 10) || 80,
        nvr_control_port: parseInt(form.nvr_control_port, 10) || 8000,
        nvr_port: parseInt(form.nvr_port, 10) || 554,
      }
      if (!payload.nvr_pass) delete payload.nvr_pass
      await onSave(payload)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-title">Edit site - {site.name}</div>
        {error && <div className="alert alert-error">{error}</div>}
        <form onSubmit={submit}>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Site name</label>
              <input className="form-input" value={form.name} onChange={(e) => upd("name", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">City</label>
              <input className="form-input" value={form.city} onChange={(e) => upd("city", e.target.value)} />
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Latitude</label>
              <input className="form-input" type="number" step="any" value={form.lat} onChange={(e) => upd("lat", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">Longitude</label>
              <input className="form-input" type="number" step="any" value={form.lon} onChange={(e) => upd("lon", e.target.value)} />
            </div>
          </div>
          <div style={{ color: "var(--text2)", fontSize: 12, marginTop: -8, marginBottom: 14 }}>
            Coordinates are optional and only used for the Network Map.
          </div>

          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Archive adapter</label>
              <select className="form-input" value={form.nvr_vendor} onChange={(e) => upd("nvr_vendor", e.target.value)}>
                {VENDOR_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Default stream type</label>
              <select className="form-input" value={form.stream_type} onChange={(e) => upd("stream_type", e.target.value)}>
                <option value="main">Main (high quality)</option>
                <option value="sub">Sub (low bandwidth)</option>
              </select>
            </div>
          </div>

          <div className="alert alert-info" style={{ marginBottom: 16 }}>
            You can leave NVR IP empty here and finish NVR setup from the mini-PC local panel later.
          </div>

          <div className="form-row-3">
            <div className="form-group" style={{ gridColumn: "1/3" }}>
              <label className="form-label">NVR IP</label>
              <input className="form-input" value={form.nvr_ip} onChange={(e) => upd("nvr_ip", e.target.value)} placeholder="Leave blank for local setup flow" />
            </div>
            <div className="form-group">
              <label className="form-label">RTSP port</label>
              <input className="form-input" type="number" value={form.nvr_port} onChange={(e) => upd("nvr_port", e.target.value)} />
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label className="form-label">NVR API port</label>
              <input className="form-input" type="number" value={form.nvr_http_port} onChange={(e) => upd("nvr_http_port", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">NVR control port</label>
              <input className="form-input" type="number" value={form.nvr_control_port} onChange={(e) => upd("nvr_control_port", e.target.value)} />
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label className="form-label">NVR username</label>
              <input className="form-input" value={form.nvr_user} onChange={(e) => upd("nvr_user", e.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">NVR password (leave blank to keep)</label>
              <input
                className="form-input"
                type="password"
                value={form.nvr_pass}
                onChange={(e) => upd("nvr_pass", e.target.value)}
                placeholder="********"
              />
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? <><span className="spinner" /> Saving...</> : "Save changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}


