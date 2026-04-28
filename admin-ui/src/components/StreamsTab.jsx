import { useState, useEffect } from "react"
import { api } from "../lib/api"

const VIEWER_AUTH = "viewer:VIEWER_PASS@"

export default function StreamsTab({ siteId, publicHost }) {
  const [streams, setStreams] = useState([])
  const [cameras, setCameras] = useState([])
  const [loading, setLoading] = useState(true)
  const webScheme = window.location.protocol === "https:" ? "https" : "http"

  async function load() {
    try {
      const [s, c] = await Promise.all([api.getStreams(siteId), api.listCameras(siteId)])
      setStreams(s)
      setCameras(c.filter(c => c.enabled))
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }

  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t) }, [siteId])

  if (loading) return <div className="empty-state"><div className="spinner" /></div>

  // merge cameras with stream status
  const rows = cameras.map(cam => {
    const path   = `site${siteId}/cam${String(cam.channel).padStart(2, "0")}`
    const stat   = streams.find(s => s.stream_path === path)
    return { cam, path, ready: stat?.ready ?? false, updated: stat?.updated }
  })

  const onlineCount = rows.filter(r => r.ready).length

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <span className={`badge ${onlineCount > 0 ? "badge-green" : "badge-gray"}`}>
          {onlineCount} / {rows.length} live
        </span>
        <button className="btn btn-ghost btn-sm" onClick={load}>↻ Refresh</button>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Camera</th>
              <th>Stream path</th>
              <th>RTSP URL</th>
              <th>HLS URL</th>
              <th>WebRTC</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={7} className="empty-state">No enabled cameras</td></tr>
            )}
            {rows.map(({ cam, path, ready, updated }) => {
              const rtsp    = `rtsp://${VIEWER_AUTH}${publicHost}:8554/${path}`
              const hls     = `${webScheme}://${VIEWER_AUTH}${publicHost}/hls/${path}/index.m3u8`
              const webrtc  = `${webScheme}://${VIEWER_AUTH}${publicHost}/webrtc/${path}`

              return (
                <tr key={cam.id}>
                  <td>
                    <span className={`badge ${ready ? "badge-green" : "badge-red"}`}>
                      <span className={`dot ${ready ? "dot-green" : "dot-red"}`} />
                      {ready ? "Live" : "Offline"}
                    </span>
                  </td>
                  <td>
                    <div style={{ fontWeight: 500 }}>{cam.name || `Cam ${cam.channel}`}</div>
                    <div style={{ fontSize: 11, color: "var(--text2)" }}>CH {cam.channel} · {cam.stream_type}</div>
                  </td>
                  <td>
                    <code style={{ fontSize: 11 }}>{path}</code>
                  </td>
                  <td>
                    <CopyField value={rtsp} label="RTSP" />
                  </td>
                  <td>
                    <CopyField value={hls} label="HLS" />
                  </td>
                  <td>
                    <a href={webrtc} target="_blank" rel="noreferrer"
                      className="btn btn-ghost btn-sm" style={{ fontSize: 11 }}>
                      ▶ Watch
                    </a>
                  </td>
                  <td style={{ color: "var(--text2)", fontSize: 11 }}>
                    {updated ? new Date(updated).toLocaleTimeString() : "—"}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function CopyField({ value, label }) {
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard.writeText(value)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <button className="btn btn-ghost btn-sm" onClick={copy}
      title={value} style={{ fontSize: 11, maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis" }}>
      {copied ? "✅ Copied" : `📋 ${label}`}
    </button>
  )
}
