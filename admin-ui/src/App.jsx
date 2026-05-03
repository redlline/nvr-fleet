import { useEffect, useState } from "react"
import { api } from "./lib/api"
import Dashboard from "./pages/Dashboard"
import Sites from "./pages/Sites"
import SiteDetail from "./pages/SiteDetail"
import NetworkMap from "./pages/NetworkMap"
import Traffic from "./pages/Traffic"
import System from "./pages/System"
import Login from "./pages/Login"
import WatchPage from "./pages/WatchPage"
import UsersPage from "./pages/UsersPage"
import {
  LayoutDashboard, MapPin, Map, BarChart2,
  Settings, LogOut, Users, Video,
} from "lucide-react"
import { t, getLang, setLang, LANGS } from "./lib/i18n"

const BRAND = {
  name:      "CCTV CONNECT",
  logoIcon:  "/logo.png",
  copyright: "Created by Bullet2267 © 2026 NVR Fleet",
}

function readRoute() {
  const url = new URL(window.location.href)
  const page = url.searchParams.get("page")
  const selectedSite = url.searchParams.get("site")
  const watchPath = url.searchParams.get("watch")
  const watchLabel = url.searchParams.get("label") || watchPath || ""

  if (watchPath) return { page: "watch", selectedSite, watchPath, watchLabel }
  if (page === "site" && selectedSite) return { page: "site", selectedSite, watchPath: "", watchLabel: "" }

  const allowedPages = new Set(["dashboard", "sites", "map", "traffic", "system", "users"])
  return { page: allowedPages.has(page) ? page : "dashboard", selectedSite: null, watchPath: "", watchLabel: "" }
}

function writeRoute(nextPage, selectedSite = null, watchPath = "", watchLabel = "") {
  const url = new URL(window.location.href)
  url.search = ""
  if (nextPage === "site" && selectedSite) {
    url.searchParams.set("page", "site")
    url.searchParams.set("site", selectedSite)
  } else if (nextPage === "watch" && watchPath) {
    url.searchParams.set("page", "watch")
    url.searchParams.set("watch", watchPath)
    if (selectedSite) url.searchParams.set("site", selectedSite)
    if (watchLabel) url.searchParams.set("label", watchLabel)
  } else if (nextPage !== "dashboard") {
    url.searchParams.set("page", nextPage)
  }
  window.history.pushState({}, "", url.toString())
}

function decodeRoleFromToken(token) {
  if (!token) return "viewer"
  try {
    const [, payload] = token.split(".")
    if (!payload) return "viewer"
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/")
    const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4)
    const json = JSON.parse(window.atob(padded))
    return json?.role || "viewer"
  } catch {
    return "viewer"
  }
}

export default function App() {
  const [authed, setAuthed] = useState(!!api.getToken())
  const [route, setRoute] = useState(() => readRoute())
  const [role, setRole] = useState(() => decodeRoleFromToken(api.getToken()))
  const [, forceUpdate] = useState(0)

  useEffect(() => {
    document.title = BRAND.name
    const onPopState = () => setRoute(readRoute())
    window.addEventListener("popstate", onPopState)
    const onLang = () => forceUpdate(n => n + 1)
    window.addEventListener("langchange", onLang)
    return () => {
      window.removeEventListener("popstate", onPopState)
      window.removeEventListener("langchange", onLang)
    }
  }, [])

  useEffect(() => {
    if (!authed) {
      setRole("viewer")
      return
    }

    const tokenRole = decodeRoleFromToken(api.getToken())
    setRole(tokenRole)

    api.getMe()
      .then((me) => {
        if (me?.role) setRole(me.role)
      })
      .catch(() => {
        setRole(tokenRole)
      })
  }, [authed])

  if (!authed) return <Login onLogin={() => {
    setRole(decodeRoleFromToken(api.getToken()))
    setAuthed(true)
  }} brand={BRAND} />

  function navigate(nextPage, site = null, extra = {}) {
    const nextRoute = { page: nextPage, selectedSite: site, watchPath: extra.watchPath || "", watchLabel: extra.watchLabel || "" }
    setRoute(nextRoute)
    writeRoute(nextRoute.page, nextRoute.selectedSite, nextRoute.watchPath, nextRoute.watchLabel)
  }

  return (
    <div className="app">
      <Sidebar page={route.page} navigate={navigate} brand={BRAND} role={role} />
      <main className="content">
        {route.page === "dashboard" && <Dashboard navigate={navigate} />}
        {route.page === "sites"     && <Sites navigate={navigate} role={role} />}
        {route.page === "site"      && route.selectedSite && <SiteDetail siteId={route.selectedSite} navigate={navigate} role={role} />}
        {route.page === "watch"     && route.watchPath && (
          <WatchPage siteId={route.selectedSite} streamPath={route.watchPath} streamLabel={route.watchLabel} navigate={navigate} />
        )}
        {route.page === "map"     && <NetworkMap navigate={navigate} />}
        {route.page === "traffic" && <Traffic />}
        {route.page === "system"  && <System />}
        {route.page === "users"   && <UsersPage />}
      </main>
    </div>
  )
}

function Sidebar({ page, navigate, brand, role }) {
  const links = [
    { id: "dashboard", icon: <LayoutDashboard size={16} />, label: t("dashboard") },
    { id: "sites",     icon: <MapPin size={16} />,          label: t("sites") },
    { id: "map",       icon: <Map size={16} />,             label: t("networkMap") },
    { id: "traffic",   icon: <BarChart2 size={16} />,       label: t("traffic") },
    { id: "system",    icon: <Settings size={16} />,        label: t("system") },
    { id: "users",     icon: <Users size={16} />,           label: t("users") },
  ].filter((link) => role === "admin" || link.id !== "users")

  return (
    <nav className="sidebar">
      <div className="sidebar-logo">
        {brand.logoIcon && (
          typeof brand.logoIcon === "string"
            ? <img src={brand.logoIcon} alt="logo" className="sidebar-logo-img"
                onError={e => { e.target.style.display = "none" }} />
            : <span className="sidebar-logo-icon">{brand.logoIcon}</span>
        )}
        <span className="sidebar-logo-text">{brand.name}</span>
      </div>

      {links.map(link => (
        <button key={link.id}
          className={`sidebar-link ${page === link.id || (page === "site" && link.id === "sites") ? "active" : ""}`}
          onClick={() => navigate(link.id)}>
          <span className="sidebar-icon">{link.icon}</span>
          {link.label}
        </button>
      ))}

      <button className="sidebar-link logout"
        onClick={() => { api.setToken(""); localStorage.removeItem("nvr_role"); window.location.reload() }}>
        <span className="sidebar-icon"><LogOut size={16} /></span>
        {t("logout")}
      </button>

      <div className="sidebar-lang">
        {LANGS.map(l => (
          <button key={l.code}
            className={"lang-btn" + (getLang() === l.code ? " active" : "")}
            onClick={() => setLang(l.code)}>
            {l.label}
          </button>
        ))}
      </div>

      {brand.copyright && <div className="sidebar-copyright">{brand.copyright}</div>}
    </nav>
  )
}
