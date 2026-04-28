import { useEffect, useState } from "react"
import { api } from "../lib/api"

export default function System() {
  const [tls, setTls] = useState(null)
  const [stack, setStack] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [message, setMessage] = useState("")
  const [tlsSaving, setTlsSaving] = useState(false)
  const [stackBusy, setStackBusy] = useState(false)
  const [backupBusy, setBackupBusy] = useState(false)
  const [backupFile, setBackupFile] = useState(null)
  const [form, setForm] = useState({
    fullchain_pem: "",
    privkey_pem: "",
  })

  async function load() {
    try {
      setLoading(true)
      setError("")
      const [tlsStatus, stackStatus] = await Promise.all([
        api.getTlsStatus(),
        api.getStackStatus(),
      ])
      setTls(tlsStatus)
      setStack(stackStatus)
    } catch (e) {
      console.error(e)
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  function updateField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  async function loadPemFromFile(key, file) {
    if (!file) return
    updateField(key, await file.text())
  }

  async function saveTls(e) {
    e.preventDefault()
    setTlsSaving(true)
    setError("")
    setMessage("")
    try {
      const next = await api.updateTls(form)
      setTls(next)
      setMessage("Certificate saved. Nginx will pick it up automatically.")
      setForm({ fullchain_pem: "", privkey_pem: "" })
    } catch (e) {
      console.error(e)
      setError(e.message)
    } finally {
      setTlsSaving(false)
    }
  }

  async function removeTls() {
    if (!confirm("Delete current certificate and return to HTTP-only mode?")) return
    setTlsSaving(true)
    setError("")
    setMessage("")
    try {
      const next = await api.deleteTls()
      setTls(next)
      setMessage("TLS files removed. Nginx will fall back to HTTP-only mode.")
    } catch (e) {
      console.error(e)
      setError(e.message)
    } finally {
      setTlsSaving(false)
    }
  }

  async function restartServices(services, label) {
    if (!confirm(`Restart ${label}?`)) return
    setStackBusy(true)
    setError("")
    setMessage("")
    try {
      const result = await api.restartStack({ services })
      setMessage(result.message || `Restart requested for: ${result.requested.join(", ")}`)
      await load()
    } catch (e) {
      console.error(e)
      setError(e.message)
    } finally {
      setStackBusy(false)
    }
  }

  async function exportBackup() {
    setBackupBusy(true)
    setError("")
    setMessage("")
    try {
      const blob = await api.exportBackup()
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = `nvr-fleet-backup-${new Date().toISOString().slice(0, 19).replaceAll(":", "-")}.zip`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      setMessage("Backup exported.")
    } catch (e) {
      console.error(e)
      setError(e.message)
    } finally {
      setBackupBusy(false)
    }
  }

  async function importBackup() {
    if (!backupFile) return
    if (!confirm("Import backup and replace the current configuration?")) return
    setBackupBusy(true)
    setError("")
    setMessage("")
    try {
      const result = await api.importBackup(backupFile)
      setBackupFile(null)
      setMessage(result.message || "Backup imported successfully.")
      await load()
    } catch (e) {
      console.error(e)
      setError(e.message)
    } finally {
      setBackupBusy(false)
    }
  }

  if (loading) return <div className="empty-state"><div className="spinner" /></div>

  const cert = tls?.cert

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">System</div>
          <div className="page-sub">TLS, stack control and backups without SSH</div>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={load}>Refresh</button>
      </div>

      {message && <div className="alert alert-success">{message}</div>}
      {error && <div className="alert alert-error">{error}</div>}

      <div className="stat-grid" style={{ marginBottom: 20 }}>
        <div className={`stat-card ${tls?.enabled ? "green" : "amber"}`}>
          <div className="stat-value" style={{ fontSize: 24 }}>{tls?.enabled ? "HTTPS" : "HTTP"}</div>
          <div className="stat-label">Current web mode</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ fontSize: 18, wordBreak: "break-all" }}>{tls?.public_base_url}</div>
          <div className="stat-label">Base URL</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ fontSize: 18, wordBreak: "break-all" }}>{tls?.install_script_url}</div>
          <div className="stat-label">Installer URL</div>
        </div>
      </div>

      <Section title="Certificate">
        {cert ? (
          <div className="form-row">
            <Info label="Subject" value={cert.subject} />
            <Info label="Issuer" value={cert.issuer} />
            <Info label="Valid from" value={new Date(cert.not_before).toLocaleString()} />
            <Info label="Valid until" value={new Date(cert.not_after).toLocaleString()} />
            <Info label="Days left" value={String(cert.expires_in_days)} />
            <Info label="SAN" value={cert.san.join(", ") || "-"} />
          </div>
        ) : (
          <div style={{ color: "var(--text2)", marginBottom: 16 }}>
            No TLS certificate is loaded yet. You can start in HTTP mode, open this page, and upload the PEM files here.
          </div>
        )}

        <form onSubmit={saveTls}>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Fullchain PEM file</label>
              <input className="form-input" type="file" accept=".pem,.crt,.cer,.txt" onChange={(e) => loadPemFromFile("fullchain_pem", e.target.files?.[0])} />
            </div>
            <div className="form-group">
              <label className="form-label">Private key PEM file</label>
              <input className="form-input" type="file" accept=".pem,.key,.txt" onChange={(e) => loadPemFromFile("privkey_pem", e.target.files?.[0])} />
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Fullchain PEM</label>
            <textarea className="form-input form-textarea" rows={10} value={form.fullchain_pem} onChange={(e) => updateField("fullchain_pem", e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" />
          </div>

          <div className="form-group">
            <label className="form-label">Private key PEM</label>
            <textarea className="form-input form-textarea" rows={10} value={form.privkey_pem} onChange={(e) => updateField("privkey_pem", e.target.value)} placeholder="-----BEGIN PRIVATE KEY-----" />
          </div>

          <div className="alert alert-info">
            Before the first deploy you can also place files into `nginx/certs/fullchain.pem` and `nginx/certs/privkey.pem`.
            After the platform is up, renewals can be handled here in the UI.
          </div>

          <div className="modal-footer" style={{ marginTop: 0 }}>
            {tls?.files_present && (
              <button type="button" className="btn btn-danger" onClick={removeTls} disabled={tlsSaving}>
                Remove TLS
              </button>
            )}
            <button type="submit" className="btn btn-primary" disabled={tlsSaving}>
              {tlsSaving ? <><span className="spinner" /> Saving...</> : "Save certificate"}
            </button>
          </div>
        </form>
      </Section>

      <Section title="Stack">
        {!stack?.docker_available && (
          <div className="alert alert-error" style={{ marginBottom: 16 }}>
            Docker control is unavailable: {stack?.docker_message || "unknown reason"}
          </div>
        )}

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
          <button className="btn btn-ghost btn-sm" onClick={() => restartServices(null, "the whole stack")} disabled={stackBusy || !stack?.docker_available}>
            {stackBusy ? <><span className="spinner" /> Working...</> : "Restart full stack"}
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => restartServices(["nginx", "admin-ui", "fleet-server"], "web services")} disabled={stackBusy || !stack?.docker_available}>
            Restart web layer
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => restartServices(["mediamtx", "mtx-toolkit"], "media services")} disabled={stackBusy || !stack?.docker_available}>
            Restart media layer
          </button>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Service</th>
                <th>Container</th>
                <th>Status</th>
                <th>Health</th>
                <th>Probe</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(stack?.services || []).map((service) => (
                <tr key={service.key}>
                  <td>{service.label}</td>
                  <td><code>{service.container_name}</code></td>
                  <td><Badge state={service.status} /></td>
                  <td><Badge state={service.health} /></td>
                  <td style={{ color: "var(--text2)", fontSize: 12 }}>{service.probe_message || "-"}</td>
                  <td>
                    <button className="btn btn-ghost btn-sm" onClick={() => restartServices([service.key], service.label)} disabled={stackBusy || !service.restart_supported}>
                      Restart
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Backups">
        <div className="alert alert-info">
          Export includes sites, cameras, agent tokens and TLS files. Import replaces the current configuration and redeploys the restored sites.
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn btn-primary btn-sm" onClick={exportBackup} disabled={backupBusy}>
            {backupBusy ? <><span className="spinner" /> Working...</> : "Export backup"}
          </button>
          <input className="form-input" style={{ maxWidth: 360 }} type="file" accept=".zip" onChange={(e) => setBackupFile(e.target.files?.[0] || null)} />
          <button className="btn btn-ghost btn-sm" onClick={importBackup} disabled={backupBusy || !backupFile}>
            Import backup
          </button>
        </div>
      </Section>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div className="table-wrap" style={{ padding: 20, marginBottom: 20 }}>
      <div className="section-title" style={{ marginBottom: 14 }}>{title}</div>
      {children}
    </div>
  )
}

function Info({ label, value }) {
  return (
    <div className="form-group" style={{ marginBottom: 12 }}>
      <div className="form-label">{label}</div>
      <div style={{ wordBreak: "break-word" }}>{value}</div>
    </div>
  )
}

function Badge({ state }) {
  const value = String(state || "unknown").toLowerCase()
  const variant = value === "running" || value === "healthy" || value === "reachable"
    ? "badge-green"
    : value === "unknown" || value === "starting"
      ? "badge-amber"
      : "badge-red"
  return <span className={`badge ${variant}`}>{state}</span>
}
