import { useState } from "react"
import { api } from "../lib/api"

export default function Login({ onLogin }) {
  const [token, setToken] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setLoading(true); setError("")
    try {
      api.setToken(token)
      await api.dashboard()   // verify token works
      onLogin()
    } catch {
      setError("Invalid token")
      api.setToken("")
    } finally { setLoading(false) }
  }

  return (
    <div style={{
      height: "100vh", display: "flex", alignItems: "center",
      justifyContent: "center", background: "var(--bg)",
    }}>
      <div style={{
        background: "var(--bg2)", border: "1px solid var(--border)",
        borderRadius: 12, padding: 40, width: 360, boxShadow: "var(--shadow)",
      }}>
        <div style={{ fontSize: 24, fontWeight: 700, marginBottom: 4, color: "var(--accent2)" }}>
          NVR Fleet
        </div>
        <div style={{ color: "var(--text2)", fontSize: 13, marginBottom: 28 }}>
          Admin panel — enter your token to continue
        </div>
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}
        <form onSubmit={submit}>
          <div className="form-group">
            <label className="form-label">Admin token</label>
            <input
              className="form-input"
              type="password"
              value={token}
              onChange={e => setToken(e.target.value)}
              placeholder="••••••••••••"
              autoFocus
              required
            />
          </div>
          <button type="submit" className="btn btn-primary" style={{ width: "100%" }} disabled={loading}>
            {loading ? <><span className="spinner" /> Verifying...</> : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  )
}
