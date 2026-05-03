import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { t } from "../lib/i18n"

export default function CamerasTab({ siteId, site }) {
  const [cameras, setCameras]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [saving, setSaving]     = useState(false)
  const [showAdd, setShowAdd]   = useState(false)
  const [edited, setEdited]     = useState({})   // camId -> {name, stream_type, enabled}
  const [dirty, setDirty]       = useState(false)
  const [msg, setMsg]           = useState("")

  async function load() {
    try { setCameras(await api.listCameras(siteId)) }
    catch (e) { console.error(e) }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [siteId])

  function updateLocal(camId, key, value) {
    setEdited(p => ({ ...p, [camId]: { ...(p[camId] || {}), [key]: value } }))
    setDirty(true)
  }

  function getVal(cam, key) {
    return edited[cam.id]?.[key] !== undefined ? edited[cam.id][key] : cam[key]
  }

  async function saveAll() {
    setSaving(true); setMsg("")
    try {
      const updates = cameras
        .filter(c => edited[c.id])
        .map(c => ({ id: c.id, ...edited[c.id] }))
      if (updates.length > 0) {
        await api.bulkCameras(siteId, updates)
      }
      setEdited({})
      setDirty(false)
      setMsg("✅ Saved and deployed to agent")
      setTimeout(() => setMsg(""), 3000)
      load()
    } catch (e) { setMsg("❌ " + e.message) }
    finally { setSaving(false) }
  }

  async function deleteCamera(cam) {
    if (!confirm(`Delete camera "${cam.name}" (channel ${cam.channel})?`)) return
    await api.deleteCamera(siteId, cam.id)
    load()
  }

  async function handleAdd(data) {
    await api.addCamera(siteId, data)
    setShowAdd(false)
    load()
  }

  async function toggleAll(enabled) {
    cameras.forEach(c => updateLocal(c.id, "enabled", enabled))
  }

  async function switchAllStream(type) {
    cameras.forEach(c => updateLocal(c.id, "stream_type", type))
  }

  if (loading) return <div className="empty-state"><div className="spinner" /></div>

  const enabledCount = cameras.filter(c => getVal(c, "enabled")).length

  return (
    <div>
      {msg && <div className="alert alert-info" style={{ marginBottom: 16 }}>{msg}</div>}

      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ color: "var(--text2)", fontSize: 13 }}>
          {enabledCount} / {cameras.length} enabled
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn btn-ghost btn-sm" onClick={() => toggleAll(true)}>{t("enableAll")}</button>
          <button className="btn btn-ghost btn-sm" onClick={() => toggleAll(false)}>{t("disableAll")}</button>
          <button className="btn btn-ghost btn-sm" onClick={() => switchAllStream("main")}>All → Main</button>
          <button className="btn btn-ghost btn-sm" onClick={() => switchAllStream("sub")}>All → Sub</button>
          <button className="btn btn-ghost btn-sm" onClick={() => setShowAdd(true)}>+ Add camera</button>
          {dirty && (
            <button className="btn btn-primary btn-sm" onClick={saveAll} disabled={saving}>
              {saving ? <><span className="spinner" /> Saving...</> : "💾 Save & deploy"}
            </button>
          )}
        </div>
      </div>

      {/* Cameras table */}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>CH</th>
              <th{t("camName")}</th>
              <th{t("camChannelId")}</th>
              <th{t("camStream")}</th>
              <th{t("camRtspPath")}</th>
              <th{t("camEnabled")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {cameras.length === 0 && (
              <tr><td colSpan={7} className="empty-state">{t("noCamerasConfigured")}</</td></tr>
            )}
            {cameras.map(cam => {
              const name    = getVal(cam, "name")
              const stype   = getVal(cam, "stream_type")
              const enabled = getVal(cam, "enabled")
              const isEdited = !!edited[cam.id]
              const streamPath = `site${siteId}/cam${String(cam.channel).padStart(2, "0")}`

              return (
                <tr key={cam.id} style={{ opacity: enabled ? 1 : 0.5 }}>
                  <td style={{ fontWeight: 600, color: "var(--accent2)" }}>
                    {isEdited && <span style={{ color: "var(--amber)", marginRight: 4 }}>●</span>}
                    {cam.channel}
                  </td>
                  <td>
                    <input
                      className="form-input"
                      style={{ padding: "4px 8px", fontSize: 13, width: 140 }}
                      value={name}
                      onChange={e => updateLocal(cam.id, "name", e.target.value)}
                    />
                  </td>
                  <td style={{ fontFamily: "monospace", fontSize: 12, color: "var(--text2)" }}>
                    /Streaming/Channels/{cam.channel_id}
                  </td>
                  <td>
                    <select
                      className="form-input"
                      style={{ padding: "4px 8px", fontSize: 13, width: 90 }}
                      value={stype}
                      onChange={e => updateLocal(cam.id, "stream_type", e.target.value)}
                    >
                      <option value="main">Main</option>
                      <option value="sub">Sub</option>
                    </select>
                  </td>
                  <td>
                    <span style={{ fontFamily: "monospace", fontSize: 11, color: "var(--text2)" }}>
                      {streamPath}
                    </span>
                  </td>
                  <td>
                    <label className="toggle">
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={e => updateLocal(cam.id, "enabled", e.target.checked)}
                      />
                      <div className="toggle-track" />
                      <div className="toggle-thumb" />
                    </label>
                  </td>
                  <td>
                    <button className="btn btn-danger btn-sm" onClick={() => deleteCamera(cam)}>
                      ✕
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {dirty && (
        <div style={{ marginTop: 12, display: "flex", justifyContent: "flex-end" }}>
          <button className="btn btn-ghost btn-sm" onClick={() => { setEdited({}); setDirty(false) }}>
            Discard changes
          </button>
          <button className="btn btn-primary btn-sm" style={{ marginLeft: 8 }} onClick={saveAll} disabled={saving}>
            {saving ? <><span className="spinner" /> Saving...</> : "💾 Save & deploy"}
          </button>
        </div>
      )}

      {showAdd && <AddCameraModal onClose={() => setShowAdd(false)} onSave={handleAdd} existingChannels={cameras.map(c => c.channel)} />}
    </div>
  )
}

function AddCameraModal({ onClose, onSave, existingChannels }) {
  const [form, setForm] = useState({ name: "", channel: "", stream_type: "main", enabled: true })
  const [saving, setSaving] = useState(false)
  const [error, setError]   = useState("")

  function upd(k, v) { setForm(p => ({ ...p, [k]: v })) }

  async function submit(e) {
    e.preventDefault()
    const ch = parseInt(form.channel)
    if (existingChannels.includes(ch)) { setError(`Channel ${ch} already exists`); return }
    setSaving(true); setError("")
    try {
      await onSave({ ...form, channel: ch })
    } catch (err) { setError(err.message) }
    finally { setSaving(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ width: 400 }}>
        <div className="modal-title">{t("addCamera")}</</div>
        {error && <div className="alert alert-error">{error}</div>}
        <form onSubmit={submit}>
          <div className="form-group">
            <label className="form-label">Camera name</label>
            <input className="form-input" value={form.name} onChange={e => upd("name", e.target.value)} required placeholder=t("egEntrance") />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Channel number</label>
              <input className="form-input" type="number" min={1} max={64} value={form.channel} onChange={e => upd("channel", e.target.value)} required placeholder="1" />
            </div>
            <div className="form-group">
              <label className="form-label">Stream type</label>
              <select className="form-input" value={form.stream_type} onChange={e => upd("stream_type", e.target.value)}>
                <option value="main">Main (HD)</option>
                <option value="sub">Sub (SD)</option>
              </select>
            </div>
          </div>
          <div className="form-group" style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <label className="toggle">
              <input type="checkbox" checked={form.enabled} onChange={e => upd("enabled", e.target.checked)} />
              <div className="toggle-track" /><div className="toggle-thumb" />
            </label>
            <span className="form-label" style={{ margin: 0 }}>{t("camEnabled")}</span>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-ghost" onClick={onClose}>{t("cancel")}</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? <><span className="spinner" /> Adding...</> : t("addCamera")}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}


