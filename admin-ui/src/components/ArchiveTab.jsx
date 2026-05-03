import { useEffect, useMemo, useRef, useState } from "react"
import { api } from "../lib/api"
import { t } from "../lib/i18n"

function toLocalInputValue(date) {
  const pad = (n) => String(n).padStart(2, "0")
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join("-") + `T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function toIsoOrNull(value) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString()
}

function formatRange(start, end) {
  return `${new Date(start).toLocaleString()} - ${new Date(end).toLocaleString()}`
}

export default function ArchiveTab({ siteId, publicHost }) {
  const now = useMemo(() => new Date(), [])
  const dayAgo = useMemo(() => new Date(now.getTime() - 24 * 60 * 60 * 1000), [now])
  const [cameras, setCameras] = useState([])
  const [filters, setFilters] = useState({
    camera_id: "",
    start: toLocalInputValue(dayAgo),
    end: toLocalInputValue(now),
  })
  const [recordings, setRecordings] = useState([])
  const [loading, setLoading] = useState(true)
  const [searching, setSearching] = useState(false)
  const [starting, setStarting] = useState(null)
  const [error, setError] = useState("")
  const [session, setSession] = useState(null)
  const videoRef = useRef(null)
  const hlsRef = useRef(null)

  async function loadCameras() {
    const items = await api.listCameras(siteId)
    setCameras(items)
  }

  async function searchArchive() {
    setSearching(true)
    setError("")
    try {
      const items = await api.listArchive(siteId, {
        camera_id: filters.camera_id || undefined,
        start: toIsoOrNull(filters.start),
        end: toIsoOrNull(filters.end),
        limit: 500,
      })
      setRecordings(items)
    } catch (err) {
      setError(err.message)
      setRecordings([])
    } finally {
      setSearching(false)
      setLoading(false)
    }
  }

  async function stopSession() {
    if (!session) return
    try {
      await api.stopArchivePlayback(siteId, session.session_id)
    } catch (err) {
      console.error(err)
    }
    setSession(null)
  }

  async function playRecording(item) {
    setStarting(item.start)
    setError("")
    try {
      if (session?.session_id) {
        await api.stopArchivePlayback(siteId, session.session_id)
      }
      const playback = await api.startArchivePlayback(siteId, {
        camera_id: item.camera_id,
        start: item.start,
        end: item.end,
      })
      setSession({
        ...playback,
        camera_name: item.camera_name,
        range_label: formatRange(item.start, item.end),
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setStarting(null)
    }
  }

  useEffect(() => {
    Promise.all([loadCameras(), searchArchive()]).catch((err) => {
      console.error(err)
      setError(err.message)
      setLoading(false)
    })
  }, [siteId])

  useEffect(() => {
    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy()
        hlsRef.current = null
      }
      stopSession().catch(() => {})
    }
  }, [session?.session_id])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    let cancelled = false
    if (hlsRef.current) {
      hlsRef.current.destroy()
      hlsRef.current = null
    }
    if (!session?.hls_url) {
      video.removeAttribute("src")
      video.load()
      return
    }
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = session.hls_url
      return
    }
    ;(async () => {
      const { default: Hls } = await import("hls.js/light")
      if (cancelled || !Hls.isSupported()) return
      const hls = new Hls({
        lowLatencyMode: false,
      })
      hls.loadSource(session.hls_url)
      hls.attachMedia(video)
      hlsRef.current = hls
    })().catch((err) => {
      console.error(err)
    })
    return () => {
      cancelled = true
      if (hlsRef.current) {
        hlsRef.current.destroy()
        hlsRef.current = null
      }
    }
  }, [session])

  if (loading) {
    return <div className="empty-state"><div className="spinner" /></div>
  }

  return (
    <div>
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div className="form-row-3">
          <div className="form-group">
            <label className="form-label">{t("camera")}</label>
            <select
              className="form-input"
              value={filters.camera_id}
              onChange={(e) => setFilters((prev) => ({ ...prev, camera_id: e.target.value }))}
            >
              <option value="">{t("allCameras")}</option>
              {cameras.map((camera) => (
                <option key={camera.id} value={camera.id}>
                  CH {camera.channel} - {camera.name}
                </option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">{t("start")}</label>
            <input
              className="form-input"
              type="datetime-local"
              value={filters.start}
              onChange={(e) => setFilters((prev) => ({ ...prev, start: e.target.value }))}
            />
          </div>
          <div className="form-group">
            <label className="form-label">{t("end")}</label>
            <input
              className="form-input"
              type="datetime-local"
              value={filters.end}
              onChange={(e) => setFilters((prev) => ({ ...prev, end: e.target.value }))}
            />
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8 }}>
          <button className="btn btn-primary" onClick={searchArchive} disabled={searching}>
            {searching ? <><span className="spinner" /> Searching...</> : t("searchArchive")}
          </button>
          <span className="badge badge-gray">{recordings.length} segments</span>
          <span style={{ color: "var(--text2)", fontSize: 12 }}>
            Source: NVR archive via site agent
          </span>
        </div>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

      {session && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16, marginBottom: 12 }}>
            <div>
              <div style={{ fontWeight: 600 }}>{session.camera_name}</div>
              <div style={{ color: "var(--text2)", fontSize: 12 }}>{session.range_label}</div>
            </div>
            <div className="btn-group">
              <a className="btn btn-ghost btn-sm" href={session.webrtc_url} target="_blank" rel="noreferrer">
                WebRTC
              </a>
              <button className="btn btn-ghost btn-sm" onClick={stopSession}>{t("stop")}</button>
            </div>
          </div>
          <video
            ref={videoRef}
            controls
            autoPlay
            playsInline
            style={{ width: "100%", maxHeight: 520, background: "#000", borderRadius: 8 }}
          />
          <div style={{ display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
            <CopyButton label="RTSP" value={session.rtsp_url} />
            <CopyButton label="HLS" value={session.hls_url} />
            <span style={{ color: "var(--text2)", fontSize: 12 }}>
              Session expires: {new Date(session.expires_at).toLocaleString()}
            </span>
          </div>
        </div>
      )}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th{t("camera")}</th>
              <th{t("archiveType")}</th>
              <th{t("start")}</th>
              <th{t("end")}</th>
              <th{t("source")}</th>
              <th{t("action")}</th>
            </tr>
          </thead>
          <tbody>
            {recordings.length === 0 && (
              <tr><td colSpan={6} className="empty-state">{t("noRecordings")}</</td></tr>
            )}
            {recordings.map((item) => (
              <tr key={`${item.camera_id}-${item.start}-${item.end}`}>
                <td>
                  <div style={{ fontWeight: 500 }}>{item.camera_name}</div>
                  <div style={{ fontSize: 11, color: "var(--text2)" }}>CH {item.channel} - {item.stream_type}</div>
                </td>
                <td>
                  <span className="badge badge-gray">{item.recording_type}</span>
                </td>
                <td style={{ fontSize: 12 }}>{new Date(item.start).toLocaleString()}</td>
                <td style={{ fontSize: 12 }}>{new Date(item.end).toLocaleString()}</td>
                <td>
                  <span className="badge badge-gray">{item.vendor}</span>
                </td>
                <td>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => playRecording(item)}
                    disabled={starting === item.start}
                  >
                    {starting === item.start ? <><span className="spinner" /> Starting...</> : t("play")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function CopyButton({ label, value }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <button className="btn btn-ghost btn-sm" onClick={copy} title={value}>
      {copied ? "Copied" : label}
    </button>
  )
}


