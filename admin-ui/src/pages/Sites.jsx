import { useEffect, useState } from "react"
import { api } from "../lib/api"

const VENDOR_OPTIONS = [
  { value: "hikvision", label: "Hikvision" },
  { value: "dahua", label: "Dahua" },
  { value: "onvif", label: "ONVIF" },
]

export default function Sites({ navigate }) {
  const [sites, setSites] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [installInfo, setInstallInfo] = useState(null)
  const [deleting, setDeleting] = useState(null)

  async function load() {
    try {
      setSites(await api.listSites())
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function handleAdd(formData) {
    const result = await api.createSite(formData)
    setShowAdd(false)
    setInstallInfo(result)
    load()
  }

  async function handleDelete(site) {
    if (!confirm(`Delete site "${site.name}"? This cannot be undone.`)) return
    setDeleting(site.id)
    try {
      await api.deleteSite(site.id)
      load()
    } finally {
      setDeleting(null)
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">Sites</div>
          <div className="page-sub">Manage NVR locations and agents</div>
        </div>
        <button className="btn btn-primary" onClick={() => setShowAdd(true)}>Add site</button>
      </div>

      {installInfo && (
        <div className="alert alert-success" style={{ marginBottom: 20 }}>
          <div style={{ marginBottom: 8 }}>
            Site created. Run this command on the mini-PC at the site:
          </div>
          <div className="code-block">
            {installInfo.install_cmd}
            <button className="copy-btn" onClick={() => navigator.clipboard.writeText(installInfo.install_cmd)}>
              Copy
            </button>
          </div>
          <button className="btn btn-ghost btn-sm" style={{ marginTop: 10 }} onClick={() => setInstallInfo(null)}>
            Dismiss
          </button>
        </div>
      )}

      {loading ? (
        <div className="empty-state"><div className="spinner" /></div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>City</th>
                <th>Vendor</th>
                <th>NVR</th>
                <th>API port</th>
                <th>Control port</th>
                <th>Channels</th>
                <th>Agent</th>
                <th>Live</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {sites.length === 0 && (
                <tr><td colSpan={10} className="empty-state">No sites. Add your first site.</td></tr>
              )}
              {sites.map((site) => (
                <tr key={site.id}>
                  <td>
                    <button className="btn btn-ghost btn-sm" style={{ fontWeight: 600 }} onClick={() => navigate("site", site.id)}>
                      {site.name}
                    </button>
                  </td>
                  <td style={{ color: "var(--text2)" }}>{site.city || "-"}</td>
                  <td><span className="badge badge-gray">{site.nvr_vendor}</span></td>
                  <td style={{ fontFamily: "monospace", fontSize: 12 }}>{site.nvr_ip}:{site.nvr_port}</td>
                  <td>{site.nvr_http_port}</td>
                  <td>{site.nvr_control_port}</td>
                  <td>{site.channel_count}</td>
                  <td>
                    <span className={`badge ${site.agent_online ? "badge-green" : "badge-red"}`}>
                      <span className={`dot ${site.agent_online ? "dot-green" : "dot-red"}`} />
                      {site.agent_online ? "Online" : "Offline"}
                    </span>
                  </td>
                  <td>
                    <span className={site.online_streams > 0 ? "badge badge-green" : "badge badge-gray"}>
                      {site.online_streams}/{site.camera_count}
                    </span>
                  </td>
                  <td>
                    <div className="btn-group">
                      <button className="btn btn-ghost btn-sm" onClick={() => navigate("site", site.id)}>Edit</button>
                      <button
                        className="btn btn-danger btn-sm"
                        disabled={deleting === site.id}
                        onClick={() => handleDelete(site)}
                      >
                        {deleting === site.id ? <span className="spinner" /> : "Delete"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showAdd && <AddSiteModal onClose={() => setShowAdd(false)} onSave={handleAdd} />}
    </div>
  )
}

function AddSiteModal({ onClose, onSave }) {
  const [form, setForm] = useState({
    name: "",
    city: "",
    lat: "",
    lon: "",
    nvr_vendor: "hikvision",
    nvr_ip: "",
    nvr_http_port: 80,
    nvr_control_port: 8000,
    nvr_user: "admin",
    nvr_pass: "",
    nvr_port: 554,
    channel_count: 16,
    stream_type: "main",
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
      await onSave({
        ...form,
        lat: parseFloat(form.lat) || 0,
        lon: parseFloat(form.lon) || 0,
        nvr_http_port: parseInt(form.nvr_http_port, 10) || 80,
        nvr_control_port: parseInt(form.nvr_control_port, 10) || 8000,
        nvr_port: parseInt(form.nvr_port, 10) || 554,
        channel_count: parseInt(form.channel_count, 10) || 16,
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-title">Add new site</div>
        {error && <div className="alert alert-error">{error}</div>}
        <form onSubmit={submit}>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Site name *</label>
              <input className="form-input" value={form.name} onChange={(e) => upd("name", e.target.value)} required placeholder="Warehouse A" />
            </div>
            <div className="form-group">
              <label className="form-label">City</label>
              <input className="form-input" value={form.city} onChange={(e) => upd("city", e.target.value)} placeholder="Tashkent" />
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
              <label className="form-label">Default stream</label>
              <select className="form-input" value={form.stream_type} onChange={(e) => upd("stream_type", e.target.value)}>
                <option value="main">Main (high quality)</option>
                <option value="sub">Sub (low bandwidth)</option>
              </select>
            </div>
          </div>

          <div className="form-row-3">
            <div className="form-group" style={{ gridColumn: "1/3" }}>
              <label className="form-label">NVR IP address *</label>
              <input className="form-input" value={form.nvr_ip} onChange={(e) => upd("nvr_ip", e.target.value)} required placeholder="192.168.1.64" />
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
              <label className="form-label">Channels (cameras)</label>
              <input className="form-input" type="number" min={1} max={64} value={form.channel_count} onChange={(e) => upd("channel_count", e.target.value)} />
            </div>
            <div className="form-group" />
          </div>

          <div className="form-row">
            <div className="form-group">
              <label className="form-label">NVR username *</label>
              <input className="form-input" value={form.nvr_user} onChange={(e) => upd("nvr_user", e.target.value)} required />
            </div>
            <div className="form-group">
              <label className="form-label">NVR password *</label>
              <input className="form-input" type="password" value={form.nvr_pass} onChange={(e) => upd("nvr_pass", e.target.value)} required />
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? <><span className="spinner" /> Creating...</> : "Create site"}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
