import { useState } from "react"
import { api } from "../lib/api"
import { t, getLang, setLang, LANGS } from "../lib/i18n"

export default function Login({ onLogin }) {
  const [mode, setMode]       = useState("credentials")  // "credentials" | "token"
  const [username, setUser]   = useState("")
  const [password, setPass]   = useState("")
  const [token, setToken]     = useState("")
  const [error, setError]     = useState("")
  const [loading, setLoading] = useState(false)
  const [, forceUpdate]       = useState(0)

  async function submit(e) {
    e.preventDefault()
    setLoading(true); setError("")
    try {
      if (mode === "credentials") {
        const res = await api.loginWithCredentials(username, password)
        api.setToken(res.token)
      } else {
        api.setToken(token)
        await api.dashboard()   // verify token works
      }
      onLogin()
    } catch {
      setError(t("invalidCredentials"))
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
        borderRadius: 12, padding: 40, width: 380, boxShadow: "var(--shadow)",
      }}>
        <div style={{ fontSize: 24, fontWeight: 700, marginBottom: 4, color: "var(--accent2)" }}>
          NVR Fleet
        </div>
        <div style={{ color: "var(--text2)", fontSize: 13, marginBottom: 24 }}>
          {t("loginSubtitle")}
        </div>

        {/* Language switcher */}
        <div style={{ display: "flex", gap: 6, marginBottom: 20 }}>
          {LANGS.map(l => (
            <button key={l.code}
              className={"lang-btn" + (getLang() === l.code ? " active" : "")}
              onClick={() => { setLang(l.code); forceUpdate(n => n + 1) }}>
              {l.label}
            </button>
          ))}
        </div>

        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        <form onSubmit={submit}>
          {mode === "credentials" ? (
            <>
              <div className="form-group" style={{ marginBottom: 12 }}>
                <label className="form-label">{t("loginUsername")}</label>
                <input
                  className="form-input"
                  type="text"
                  value={username}
                  onChange={e => setUser(e.target.value)}
                  autoFocus
                  required
                  autoComplete="username"
                />
              </div>
              <div className="form-group" style={{ marginBottom: 20 }}>
                <label className="form-label">{t("loginPassword")}</label>
                <input
                  className="form-input"
                  type="password"
                  value={password}
                  onChange={e => setPass(e.target.value)}
                  required
                  autoComplete="current-password"
                />
              </div>
            </>
          ) : (
            <div className="form-group" style={{ marginBottom: 20 }}>
              <label className="form-label">{t("adminToken")}</label>
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
          )}

          <button type="submit" className="btn btn-primary" style={{ width: "100%" }} disabled={loading}>
            {loading ? <><span className="spinner" /> {t("verifying")}</> : t("loginButton")}
          </button>
        </form>

        <div style={{ textAlign: "center", marginTop: 16, fontSize: 12, color: "var(--text2)" }}>
          {mode === "credentials" ? (
            <button className="btn btn-ghost btn-sm" onClick={() => setMode("token")}>
              {t("loginLegacy")}
            </button>
          ) : (
            <button className="btn btn-ghost btn-sm" onClick={() => setMode("credentials")}>
              {t("loginWithUser")}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
