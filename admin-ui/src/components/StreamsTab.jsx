import { useEffect, useState } from "react"
import { api } from "../lib/api"

export default function StreamsTab({ siteId, publicHost }) {
  const [streams, setStreams] = useState([])
  const [cameras, setCameras] = useState([])
  const [loading, setLoading] = useState(true)
  const webScheme = window.location.protocol === "https:" ? "https" : "http"

  async function load() {
    try {
      const [streamData, cameraData] = await Promise.all([
        api.getStreams(siteId),
        api.listCameras(siteId),
      ])
      setStreams(streamData)
      setCameras(cameraData.filter((camera) => camera.enabled))
    } catch (error) {
      console.error(error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [siteId])

  if (loading) return <div className="empty-state"><div className="spinner" /></div>

  const rows = cameras.map((camera) => {
    const path = `site${siteId}/cam${String(camera.channel).padStart(2, "0")}`
    const streamStat = streams.find((item) => item.stream_path === path)
    return {
      camera,
      path,
      ready: streamStat?.ready ?? false,
      updated: streamStat?.updated,
      streamStat,
    }
  })

  const onlineCount = rows.filter((row) => row.ready).length

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <span className={`badge ${onlineCount > 0 ? "badge-green" : "badge-gray"}`}>
          {onlineCount} / {rows.length} live
        </span>
        <button className="btn btn-ghost btn-sm" onClick={load}>Refresh</button>
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
              <th>Watch</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={7} className="empty-state">No enabled cameras</td></tr>
            )}
            {rows.map(({ camera, path, ready, updated, streamStat }) => {
              // rtsp_url comes from the server — it knows the real viewer credentials
              const rtsp = streamStat?.rtsp_url ?? null
              const hls = `/hls/${path}/index.m3u8`
              const watchUrl = `/?page=watch&site=${encodeURIComponent(siteId)}&watch=${encodeURIComponent(path)}&label=${encodeURIComponent(camera.name || `Cam ${camera.channel}`)}`

              return (
                <tr key={camera.id}>
                  <td>
                    <span className={`badge ${ready ? "badge-green" : "badge-red"}`}>
                      <span className={`dot ${ready ? "dot-green" : "dot-red"}`} />
                      {ready ? "Live" : "Offline"}
                    </span>
                  </td>
                  <td>
                    <div style={{ fontWeight: 500 }}>{camera.name || `Cam ${camera.channel}`}</div>
                    <div style={{ fontSize: 11, color: "var(--text2)" }}>CH {camera.channel} · {camera.stream_type}</div>
                  </td>
                  <td>
                    <code style={{ fontSize: 11 }}>{path}</code>
                  </td>
                  <td>
                    {rtsp ? <CopyField value={rtsp} label="RTSP" /> : <span style={{color:"var(--text2)",fontSize:11}}>—</span>}
                  </td>
                  <td>
                    <CopyField value={hls} label="HLS" />
                  </td>
                  <td>
                    <a
                      href={watchUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="btn btn-ghost btn-sm"
                      style={{ fontSize: 11 }}
                    >
                      Watch
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
    <button
      className="btn btn-ghost btn-sm"
      onClick={copy}
      title={value}
      style={{ fontSize: 11, maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis" }}
    >
      {copied ? "Copied" : label}
    </button>
  )
}

