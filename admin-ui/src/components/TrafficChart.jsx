import { useState, useEffect, useRef } from "react"
import { api } from "../lib/api"

export default function TrafficChart({ siteId, hours, source = "go2rtc", title }) {
  const [data, setData] = useState([])
  const canvasRef = useRef(null)

  async function load() {
    try {
      let samples
      if (source === "mediamtx") {
        samples = siteId
          ? await api.getTrafficMtx(siteId, hours)
          : await api.getTotalTrafficMtx(hours)
      } else {
        samples = siteId
          ? await api.getTraffic(siteId, hours)
          : await api.getTotalTraffic(hours)
      }
      setData(samples)
    } catch (e) { console.error(e) }
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [siteId, hours, source])

  useEffect(() => {
    if (!canvasRef.current || data.length === 0) return
    draw(canvasRef.current, data)
  }, [data])

  function aggregate(samples) {
    const map = {}
    for (const s of samples) {
      const minute = new Date(s.ts).toISOString().slice(0, 16)
      if (!map[minute]) map[minute] = { ts: minute, rx: 0, tx: 0 }
      map[minute].rx += s.rx_bytes
      map[minute].tx += s.tx_bytes
    }
    return Object.values(map).sort((a, b) => a.ts.localeCompare(b.ts))
  }

  function draw(canvas, rawData) {
    const agg = aggregate(rawData)
    if (agg.length === 0) return

    const dpr = window.devicePixelRatio || 1
    const W   = canvas.offsetWidth
    const H   = 160
    canvas.width  = W * dpr
    canvas.height = H * dpr
    canvas.style.height = H + "px"

    const ctx = canvas.getContext("2d")
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, W, H)

    const pad   = { top: 10, right: 20, bottom: 30, left: 60 }
    const inner = { w: W - pad.left - pad.right, h: H - pad.top - pad.bottom }
    const maxVal = Math.max(...agg.map(d => Math.max(d.rx, d.tx)), 1)

    function xOf(i) { return pad.left + (i / (agg.length - 1)) * inner.w }
    function yOf(v) { return pad.top + inner.h - (v / maxVal) * inner.h }

    // grid
    ctx.strokeStyle = "rgba(255,255,255,0.05)"
    ctx.lineWidth = 1
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (inner.h / 4) * i
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + inner.w, y); ctx.stroke()
      const val = maxVal * (1 - i / 4)
      ctx.fillStyle = "rgba(255,255,255,0.3)"
      ctx.font = "10px sans-serif"
      ctx.textAlign = "right"
      ctx.fillText(fmtBytes(val), pad.left - 4, y + 4)
    }

    // RX line (teal)
    ctx.beginPath()
    ctx.strokeStyle = "#14b8a6"
    ctx.lineWidth = 2
    agg.forEach((d, i) => {
      i === 0 ? ctx.moveTo(xOf(i), yOf(d.rx)) : ctx.lineTo(xOf(i), yOf(d.rx))
    })
    ctx.stroke()

    // RX fill
    ctx.beginPath()
    agg.forEach((d, i) => {
      i === 0 ? ctx.moveTo(xOf(i), yOf(d.rx)) : ctx.lineTo(xOf(i), yOf(d.rx))
    })
    ctx.lineTo(xOf(agg.length - 1), pad.top + inner.h)
    ctx.lineTo(pad.left, pad.top + inner.h)
    ctx.closePath()
    ctx.fillStyle = "rgba(20,184,166,0.12)"
    ctx.fill()

    // TX line (amber)
    ctx.beginPath()
    ctx.strokeStyle = "#f59e0b"
    ctx.lineWidth = 2
    agg.forEach((d, i) => {
      i === 0 ? ctx.moveTo(xOf(i), yOf(d.tx)) : ctx.lineTo(xOf(i), yOf(d.tx))
    })
    ctx.stroke()

    // X axis labels
    ctx.fillStyle = "rgba(255,255,255,0.3)"
    ctx.font = "10px sans-serif"
    ctx.textAlign = "center"
    const step = Math.max(1, Math.floor(agg.length / 6))
    for (let i = 0; i < agg.length; i += step) {
      const t = agg[i].ts.slice(11, 16)
      ctx.fillText(t, xOf(i), H - 8)
    }
  }

  const totalRx = data.reduce((a, s) => a + s.rx_bytes, 0)
  const totalTx = data.reduce((a, s) => a + s.tx_bytes, 0)

  return (
    <div className="chart-wrap">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <div className="chart-title">{title}</div>
        <div style={{ display: "flex", gap: 16, fontSize: 12 }}>
          <span style={{ color: "#14b8a6" }}>↓ RX {fmtBytes(totalRx)}</span>
          <span style={{ color: "#f59e0b" }}>↑ TX {fmtBytes(totalTx)}</span>
        </div>
      </div>
      {data.length === 0
        ? <div style={{ textAlign: "center", padding: 40, color: "var(--text2)", fontSize: 13 }}>No traffic data yet</div>
        : <canvas ref={canvasRef} style={{ width: "100%", display: "block" }} />
      }
    </div>
  )
}

function fmtBytes(b) {
  if (b > 1e9) return (b / 1e9).toFixed(1) + " GB"
  if (b > 1e6) return (b / 1e6).toFixed(1) + " MB"
  if (b > 1e3) return (b / 1e3).toFixed(0) + " KB"
  return Math.round(b) + " B"
}
