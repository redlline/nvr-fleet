import { useState, useEffect } from "react"
import { api } from "../lib/api"

export default function Dashboard({ navigate }) {
  const [stats, setStats]       = useState(null)
  const [sites, setSites]       = useState([])
  const [realtime, setRealtime] = useState({ rx_bps: 0, tx_bps: 0 })
  const [loading, setLoading]   = useState(true)

  async function load() {
    try {
      const [s, sitesData] = await Promise.all([api.dashboard(), api.listSites()])
      setStats(s)
      setSites(sitesData)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  async function loadRealtime() {
    try {
      const rt = await api.getRealtimeTraffic()
      setRealtime(rt)
    } catch (e) {
      // silently ignore - mediamtx may not be available
    }
  }

  useEffect(() => {
    load()
    loadRealtime()
    const t1 = setInterval(load, 15000)
    const t2 = setInterval(loadRealtime, 5000) // real-time every 5s
    return () => { clearInterval(t1); clearInterval(t2) }
  }, [])

  if (loading) return <div className="empty-state"><div className="spinner" /></div>

  const offlineSites = sites.filter(s => !s.agent_online)

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">Dashboard</div>
          <div className="page-sub">System overview — auto-refresh every 15s</div>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => { load(); loadRealtime() }}>↻ Refresh</button>
      </div>

      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-value">{stats?.total_sites ?? "—"}</div>
          <div className="stat-label">Total sites</div>
        </div>
        <div className="stat-card green">
          <div className="stat-value">{stats?.online_agents ?? "—"}</div>
          <div className="stat-label">Online agents</div>
        </div>
        <div className={`stat-card ${offlineSites.length > 0 ? "red" : "green"}`}>
          <div className="stat-value">{(stats?.total_sites ?? 0) - (stats?.online_agents ?? 0)}</div>
          <div className="stat-label">Offline agents</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{stats?.total_cameras ?? "—"}</div>
          <div className="stat-label">Total cameras</div>
        </div>
        <div className="stat-card green">
          <div className="stat-value">{stats?.online_streams ?? "—"}</div>
          <div className="stat-label">Live streams</div>
        </div>
        <div className="stat-card amber">
          <div className="stat-value">{fmtBps(realtime.rx_bps)}</div>
          <div className="stat-label">Incoming (↓)</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{fmtBps(realtime.tx_bps)}</div>
          <div className="stat-label">Outgoing (↑)</div>
        </div>
      </div>

      {offlineSites.length > 0 && (
        <div className="alert alert-error" style={{ marginBottom: 20 }}>
          ⚠ {offlineSites.length} site(s) offline: {offlineSites.map(s => s.name).join(", ")}
        </div>
      )}

      <div className="section-title">All sites</div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Site</th>
              <th>City</th>
              <th>Agent</th>
              <th>Cameras</th>
              <th>Live streams</th>
              <th>Last seen</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sites.length === 0 && (
              <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text2)", padding: 32 }}>
                No sites yet — <button className="btn btn-primary btn-sm" onClick={() => navigate("sites")}>Add site</button>
              </td></tr>
            )}
            {sites.map(s => (
              <tr key={s.id}>
                <td>
                  <div style={{ fontWeight: 500 }}>{s.name}</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>ID: {s.id}</div>
                </td>
                <td style={{ color: "var(--text2)" }}>{s.city || "—"}</td>
                <td>
                  <span className={`badge ${s.agent_online ? "badge-green" : "badge-red"}`}>
                    <span className={`dot ${s.agent_online ? "dot-green" : "dot-red"}`} />
                    {s.agent_online ? "Online" : "Offline"}
                  </span>
                </td>
                <td>{s.camera_count}</td>
                <td>
                  <span className={s.online_streams > 0 ? "badge badge-green" : "badge badge-gray"}>
                    {s.online_streams} / {s.camera_count}
                  </span>
                </td>
                <td style={{ color: "var(--text2)", fontSize: 12 }}>
                  {s.agent_last_seen ? relTime(s.agent_last_seen) : "never"}
                </td>
                <td>
                  <button className="btn btn-ghost btn-sm" onClick={() => navigate("site", s.id)}>
                    Details →
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

function fmtBps(bps) {
  if (!bps) return "0 b/s"
  if (bps > 1e6) return (bps / 1e6).toFixed(1) + " Mb/s"
  if (bps > 1e3) return (bps / 1e3).toFixed(0) + " Kb/s"
  return bps + " b/s"
}

function relTime(ts) {
  const d = (Date.now() - new Date(ts).getTime()) / 1000
  if (d < 60) return "just now"
  if (d < 3600) return Math.floor(d / 60) + "m ago"
  return Math.floor(d / 3600) + "h ago"
}
