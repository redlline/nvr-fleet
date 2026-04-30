import { useEffect, useMemo, useRef, useState } from "react"
import Hls from "hls.js"

export default function WatchPage({ siteId, streamPath, streamLabel, navigate }) {
  const videoRef = useRef(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [playing, setPlaying] = useState(false)
  const hlsUrl = useMemo(
    () => `${window.location.origin}/hls/${streamPath}/index.m3u8`,
    [streamPath],
  )

  useEffect(() => {
    const video = videoRef.current
    if (!video) return undefined

    let hls
    let startupTimer = 0
    setError("")
    setLoading(true)
    setPlaying(false)

    const handleReady = () => {
      setLoading(false)
      setPlaying(true)
    }
    const handleWaiting = () => setLoading(true)
    const handleError = () => {
      setLoading(false)
      setPlaying(false)
      setError("Browser player could not start the stream")
    }

    video.addEventListener("canplay", handleReady)
    video.addEventListener("playing", handleReady)
    video.addEventListener("waiting", handleWaiting)
    video.addEventListener("error", handleError)

    // Prefer hls.js in Chromium/Yandex/Firefox. Native HLS can report
    // partial support and still fail to play live streams.
    if (Hls.isSupported()) {
      hls = new Hls({
        enableWorker: true,
        lowLatencyMode: true,
      })
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(() => {})
      })
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data?.fatal) {
          if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
            hls.startLoad()
            return
          }
          if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
            hls.recoverMediaError()
            return
          }
          setLoading(false)
          setPlaying(false)
          setError(data.details || "HLS playback error")
          hls.destroy()
        }
      })
      hls.loadSource(hlsUrl)
      hls.attachMedia(video)
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = hlsUrl
      video.play().catch(() => {})
    } else {
      setLoading(false)
      setError("This browser does not support HLS playback")
    }

    startupTimer = window.setTimeout(() => {
      if (!video.currentTime) {
        setLoading(false)
        setPlaying(false)
        setError("Stream is reachable but browser player did not start")
      }
    }, 12000)

    return () => {
      window.clearTimeout(startupTimer)
      video.pause()
      video.removeAttribute("src")
      video.load()
      video.removeEventListener("canplay", handleReady)
      video.removeEventListener("playing", handleReady)
      video.removeEventListener("waiting", handleWaiting)
      video.removeEventListener("error", handleError)
      if (hls) hls.destroy()
    }
  }, [hlsUrl])

  return (
    <div>
      <button className="back-btn" onClick={() => navigate(siteId ? "site" : "dashboard", siteId || null)}>
        Back
      </button>

      <div className="page-header" style={{ alignItems: "flex-start" }}>
        <div>
          <div className="page-title">{streamLabel || streamPath}</div>
          <div className="page-sub">{streamPath}</div>
        </div>
        <div className="btn-group" style={{ flexShrink: 0 }}>
          <a className="btn btn-ghost btn-sm" href={hlsUrl} target="_blank" rel="noreferrer">
            Open HLS
          </a>
        </div>
      </div>

      <div className="card" style={{ padding: 18 }}>
        <div style={{ position: "relative", background: "#000", borderRadius: 8, overflow: "hidden" }}>
          <video
            ref={videoRef}
            controls
            autoPlay
            muted
            playsInline
            style={{ width: "100%", aspectRatio: "16 / 9", display: "block", background: "#000" }}
          />
          {loading && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#fff",
                background: "rgba(0,0,0,0.45)",
                fontSize: 14,
              }}
            >
              Loading stream...
            </div>
          )}
        </div>

        <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 14, flexWrap: "wrap" }}>
          <span className={`badge ${playing ? "badge-green" : "badge-gray"}`}>
            {playing ? "Playing" : "Connecting"}
          </span>
          <code style={{ fontSize: 12 }}>{hlsUrl}</code>
        </div>

        {error && (
          <div className="alert alert-error" style={{ marginTop: 14 }}>
            {error}
          </div>
        )}
      </div>
    </div>
  )
}
