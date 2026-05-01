import { useState, useEffect, useRef } from "react"
import { api } from "../lib/api"
import { t } from "../lib/i18n"

// Leaflet loaded from CDN via index.html
const L = () => window.L

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;")
}

export default function NetworkMap({ navigate }) {
  const mapRef    = useRef(null)
  const leafRef   = useRef(null)
  const markerRef = useRef({})
  const [sites, setSites]   = useState([])
  const [selected, setSelected] = useState(null)
  const [loading, setLoading]   = useState(true)

  async function load() {
    try {
      const data = await api.mapData()
      setSites(data)
      return data
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }

  useEffect(() => {
    // Init Leaflet map
    const map = L().map(mapRef.current, {
      center: [40, 60],
      zoom: 4,
      zoomControl: true,
    })

    L().tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors",
      className: "map-tiles",
    }).addTo(map)

    leafRef.current = map

    load().then(data => {
      if (!data || data.length === 0) return
      // fit bounds to all sites that have coordinates
      const valid = data.filter(s => s.lat && s.lon)
      if (valid.length > 0) {
        map.fitBounds(valid.map(s => [s.lat, s.lon]), { padding: [40, 40] })
      }
      updateMarkers(map, data)
    })

    const t = setInterval(async () => {
      const data = await load()
      if (data && leafRef.current) updateMarkers(leafRef.current, data)
    }, 15000)

    return () => {
      clearInterval(t)
      map.remove()
    }
  }, [])

  function updateMarkers(map, data) {
    // Remove old markers not in new data
    const ids = new Set(data.map(s => s.id))
    for (const [id, m] of Object.entries(markerRef.current)) {
      if (!ids.has(id)) { m.remove(); delete markerRef.current[id] }
    }

    for (const site of data) {
      if (!site.lat || !site.lon) continue

      const color  = site.online ? "#22c55e" : "#ef4444"
      const icon   = L().divIcon({
        html: `
          <div style="
            width:32px; height:32px;
            background:${color};
            border:3px solid white;
            border-radius:50%;
            display:flex; align-items:center; justify-content:center;
            font-size:12px; font-weight:700; color:white;
            box-shadow:0 2px 8px rgba(0,0,0,0.4);
            ${site.online ? "animation:pulse 2s infinite" : ""}
          ">${site.cameras}</div>
        `,
        className: "",
        iconSize: [32, 32],
        iconAnchor: [16, 16],
      })

      if (markerRef.current[site.id]) {
        markerRef.current[site.id].setIcon(icon)
      } else {
        const marker = L().marker([site.lat, site.lon], { icon })
          .addTo(map)
          .on("click", () => setSelected(site))
        markerRef.current[site.id] = marker
      }

      // Update popup
      const safeName = escapeHtml(site.name)
      const safeCity = escapeHtml(site.city || "")
      markerRef.current[site.id].bindPopup(`
        <div style="font-family:sans-serif; min-width:160px">
          <div style="font-weight:600; font-size:14px; margin-bottom:4px">${safeName}</div>
          <div style="color:#666; font-size:12px">${safeCity}</div>
          <div style="margin-top:8px; font-size:12px">
            <span style="color:${site.online ? "#22c55e" : "#ef4444"}">
              ● ${site.online ? t("online") : t("offline")}
            </span>
          </div>
          <div style="font-size:12px; margin-top:4px">
            📷 ${site.cameras} cameras · 
            📡 ${site.online_streams} {t("live")}
          </div>
        </div>
      `)
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">{t("networkMap")}</div>
          <div className="page-sub">{t("networkMapSub")}</div>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center", fontSize: 13 }}>
          <span style={{ color: "#22c55e" }}>● {t("online")}: {sites.filter(s => s.online).length}</span>
          <span style={{ color: "#ef4444" }}>● {t("offline")}: {sites.filter(s => !s.online).length}</span>
        </div>
      </div>

      <div style={{ display: "flex", gap: 20 }}>
        {/* Map */}
        <div className="map-container" style={{ flex: 1 }}>
          <style>{`
            .map-tiles { filter: brightness(0.7) saturate(0.6); }
            @keyframes pulse { 0%,100%{box-shadow:0 0 0 0 rgba(34,197,94,0.5)} 50%{box-shadow:0 0 0 8px rgba(34,197,94,0)} }
            .leaflet-popup-content-wrapper { background:#1a1d27; color:#e2e8f0; border:1px solid #2d3148; border-radius:8px; }
            .leaflet-popup-tip { background:#1a1d27; }
          `}</style>
          <div ref={mapRef} style={{ width: "100%", height: "100%" }} />
        </div>

        {/* Side panel */}
        <div style={{ width: 280, display: "flex", flexDirection: "column", gap: 10, overflowY: "auto", maxHeight: 500 }}>
          {loading && <div className="empty-state"><div className="spinner" /></div>}
          {sites.map(s => (
            <div key={s.id}
              className="table-wrap"
              style={{
                padding: "12px 14px",
                cursor: "pointer",
                border: selected?.id === s.id ? "1px solid var(--accent)" : undefined,
              }}
              onClick={() => {
                setSelected(s)
                if (s.lat && s.lon && leafRef.current) {
                  leafRef.current.setView([s.lat, s.lon], 10)
                  markerRef.current[s.id]?.openPopup()
                }
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontWeight: 500 }}>{s.name}</div>
                <span className={`badge ${s.online ? "badge-green" : "badge-red"}`} style={{ fontSize: 11 }}>
                  <span className={`dot ${s.online ? "dot-green" : "dot-red"}`} />
                  {s.online ? t("online") : t("offline")}
                </span>
              </div>
              <div style={{ color: "var(--text2)", fontSize: 12, marginTop: 4 }}>
                {s.city || "No city"} · {s.cameras} {t("cameras")} · {s.online_streams} {t("live")}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Selected site detail */}
      {selected && (
        <div className="table-wrap" style={{ marginTop: 16, padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 600, fontSize: 15 }}>{selected.name}</div>
            <button className="btn btn-primary btn-sm" onClick={() => navigate("site", selected.id)}>
              Open site →
            </button>
          </div>
          <div style={{ display: "flex", gap: 24, marginTop: 12, fontSize: 13, color: "var(--text2)" }}>
            <span>📍 {selected.lat?.toFixed(4)}, {selected.lon?.toFixed(4)}</span>
            <span>📷 {selected.cameras} cameras</span>
            <span>📡 {selected.online_streams} {t("live")} streams</span>
          </div>
        </div>
      )}
    </div>
  )
}


