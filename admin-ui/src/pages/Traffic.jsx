import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { t } from "../lib/i18n"
import TrafficChart from "../components/TrafficChart"

export default function Traffic() {
  const [sites, setSites]     = useState([])
  const [hours, setHours]     = useState(24)
  const [source, setSource]   = useState("go2rtc") // "go2rtc" | "mediamtx"

  useEffect(() => {
    api.listSites().then(setSites).catch(console.error)
  }, [])

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">{t("traffic")}</div>
          <div className="page-sub">{t("trafficSub")}</div>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <select className="form-input" style={{ width: 160 }} value={source}
            onChange={e => setSource(e.target.value)}>
            <option value="go2rtc">Source: go2rtc</option>
            <option value="mediamtx">Source: MediaMTX</option>
          </select>
          <select className="form-input" style={{ width: 140 }} value={hours}
            onChange={e => setHours(Number(e.target.value))}>
            <option value={1}>Last 1 hour</option>
            <option value={6}>Last 6 hours</option>
            <option value={24}>Last 24 hours</option>
            <option value={168}>Last 7 days</option>
          </select>
        </div>
      </div>

      <TrafficChart siteId={null} hours={hours} source={source} title="{t("totalTraffic")}" />

      <div className="section-title" style={{ marginTop: 24 }}>{t("perSite")}</div>

      {sites.map(s => (
        <TrafficChart key={s.id} siteId={s.id} hours={hours} source={source}
          title={`${s.name} (${s.city || s.id})`} />
      ))}
    </div>
  )
}

