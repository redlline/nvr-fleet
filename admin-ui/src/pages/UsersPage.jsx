import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { t } from "../lib/i18n"

const ROLES = ["admin", "operator", "viewer"]
const ROLE_COLORS = { admin: "badge-red", operator: "badge-amber", viewer: "badge-green" }

export default function UsersPage({ role = "viewer" }) {
  const isAdmin = role === "admin"
  const [users, setUsers]     = useState([])
  const [editing, setEditing] = useState(null)  // null | "new" | user object
  const [form, setForm]       = useState({ username: "", password: "", role: "viewer", is_active: true })
  const [error, setError]     = useState("")

  async function load() {
    try { setUsers(await api.listUsers()) } catch (e) { console.error(e) }
  }

  useEffect(() => { load() }, [])

  function openNew() {
    setForm({ username: "", password: "", role: "viewer", is_active: true })
    setEditing("new")
    setError("")
  }

  function openEdit(u) {
    setForm({ username: u.username, password: "", role: u.role, is_active: u.is_active })
    setEditing(u)
    setError("")
  }

  async function save() {
    setError("")
    try {
      if (editing === "new") {
        await api.createUser(form)
      } else {
        await api.updateUser(editing.id, form)
      }
      setEditing(null)
      load()
    } catch (e) {
      setError(e.message || "Error")
    }
  }

  async function remove(u) {
    if (!confirm(`Delete user "${u.username}"?`)) return
    try { await api.deleteUser(u.id); load() } catch (e) { alert(e.message) }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">{t("usersTitle")}</div>
          <div className="page-sub">{t("usersSub")}</div>
        </div>
        {isAdmin && <button className="btn btn-primary btn-sm" onClick={openNew}>+ {t("addUser")}</button>}
      </div>

      {/* Role legend */}
      <div style={{ display: "flex", gap: 16, marginBottom: 20, flexWrap: "wrap" }}>
        {ROLES.map(r => (
          <div key={r} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
            <span className={`badge ${ROLE_COLORS[r]}`}>{t("role" + r.charAt(0).toUpperCase() + r.slice(1))}</span>
            <span style={{ color: "var(--text2)" }}>{t("roleDesc")[r]}</span>
          </div>
        ))}
      </div>

      {/* Edit/Create form */}
      {editing && (
        <div className="card" style={{ marginBottom: 20, padding: 20 }}>
          <div style={{ fontWeight: 600, marginBottom: 16 }}>
            {editing === "new" ? t("addUser") : t("editUser")}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
            <div>
              <label className="form-label">{t("username")}</label>
              <input className="form-input" value={form.username} disabled={editing !== "new"}
                onChange={e => setForm(f => ({ ...f, username: e.target.value }))} />
            </div>
            <div>
              <label className="form-label">{t("password")} {editing !== "new" && "(оставьте пустым чтобы не менять)"}</label>
              <input className="form-input" type="password" value={form.password}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))} />
            </div>
            <div>
              <label className="form-label">{t("role")}</label>
              <select className="form-input" value={form.role}
                onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
                {ROLES.map(r => (
                  <option key={r} value={r}>
                    {t("role" + r.charAt(0).toUpperCase() + r.slice(1))}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {editing !== "new" && (
            <div style={{ marginTop: 12 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input type="checkbox" checked={form.is_active}
                  onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))} />
                {t("active")}
              </label>
            </div>
          )}
          {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button className="btn btn-primary btn-sm" onClick={save}>{t("save")}</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>{t("cancel")}</button>
          </div>
        </div>
      )}

      {/* Users table */}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("username")}</th>
              <th>{t("role")}</th>
              <th>{t("active")}</th>
              <th>{t("actions")}</th>
            </tr>
          </thead>
          <tbody>
            {users.length === 0 && (
              <tr><td colSpan={4} style={{ textAlign: "center", padding: 32, color: "var(--text2)" }}>
                {t("noUsers")}
              </td></tr>
            )}
            {users.map(u => (
              <tr key={u.id}>
                <td style={{ fontWeight: 500 }}>{u.username}</td>
                <td>
                  <span className={`badge ${ROLE_COLORS[u.role] || "badge-gray"}`}>
                    {t("role" + u.role.charAt(0).toUpperCase() + u.role.slice(1))}
                  </span>
                </td>
                <td>
                  <span className={`badge ${u.is_active ? "badge-green" : "badge-gray"}`}>
                    {u.is_active ? "●" : "○"}
                  </span>
                </td>
                <td style={{ display: "flex", gap: 8 }}>
                  {isAdmin && <button className="btn btn-ghost btn-sm" onClick={() => openEdit(u)}>{t("editUser")}</button>}
                  {isAdmin && <button className="btn btn-ghost btn-sm" style={{ color: "var(--red)" }}
                    onClick={() => remove(u)}>{t("deleteUser")}</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

