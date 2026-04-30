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
import { 
  LayoutDashboard, 
  Server, 
  Map, 
  Activity, 
  Settings,
  LogOut,
  Users,
  Globe,
} from "lucide-react"
import { t, getLang, setLang, LANGS } from "./lib/i18n"
import UsersPage from "./pages/UsersPage"

// ── Branding (edit these to rebrand) ──────────────────────────────────────────
const BRAND = {
  name:      "CCTV CONNECT",
  // Логотип: путь к файлу public/logo.png или эмодзи
  logoIcon:  "/logo.png",
  copyright: "Created by Bullet2267 © 2026 NVR Fleet",
}
// ─────────────────────────────────────────────────────────────────────────────

function readRoute() {
  const url = new URL(window.location.href)
  const page = url.searchParams.get("page")
  const selectedSite = url.searchParams.get("site")
  const watchPath = url.searchParams.get("watch")
  const watchLabel = url.searchParams.get("label") || watchPath || ""

  if (watchPath) {
    return { page: "watch", selectedSite, watchPath, watchLabel }
  }

  if (page === "site" && selectedSite) {
    return { page: "site", selectedSite, watchPath: "", watchLabel: "" }
  }

  const allowedPages = new Set(["dashboard", "sites", "map", "traffic", "system", "users"])
  return {
    page: allowedPages.has(page) ? page : "dashboard",
    selectedSite: null,
    watchPath: "",
    watchLabel: "",
  }
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

export default function App() {
  const [authed, setAuthed] = useState(!!api.getToken())
  const [route, setRoute] = useState(() => readRoute())

  useEffect(() => {
    document.title = BRAND.name
  }, [])

  useEffect(() => {
    const onPopState = () => setRoute(readRoute())
    window.addEventListener("popstate", onPopState)
    return () => window.removeEventListener("popstate", onPopState)
  }, [])

  if (!authed) return <Login onLogin={() => setAuthed(true)} brandName={BRAND.name} />

  function navigate(nextPage, site = null, extra = {}) {
    const nextRoute = {
      page: nextPage,
      selectedSite: site,
      watchPath: extra.watchPath || "",
      watchLabel: extra.watchLabel || "",
    }
    setRoute(nextRoute)
    writeRoute(nextRoute.page, nextRoute.selectedSite, nextRoute.watchPath, nextRoute.watchLabel)
  }

  return (
    <div className="app">
      <Sidebar page={route.page} navigate={navigate} brand={BRAND} />
      <main className="content">
        {route.page === "dashboard" && <Dashboard navigate={navigate} />}
        {route.page === "sites" && <Sites navigate={navigate} />}
        {route.page === "site" && route.selectedSite && <SiteDetail siteId={route.selectedSite} navigate={navigate} />}
        {route.page === "watch" && route.watchPath && (
          <WatchPage
            siteId={route.selectedSite}
            streamPath={route.watchPath}
            streamLabel={route.watchLabel}
            navigate={navigate}
          />
        )}
        {route.page === "map" && <NetworkMap navigate={navigate} />}
        {route.page === "traffic" && <Traffic />}
        {route.page === "system" && <System />}
        {route.page === "users" && <UsersPage />}
      </main>
    </div>
  )
}

function Sidebar({ page, navigate, brand }) {
  const [, forceUpdate] = useState(0)
  useEffect(() => {
    const handler = () => forceUpdate(n => n + 1)
    window.addEventListener("langchange", handler)
    return () => window.removeEventListener("langchange", handler)
  }, [])

  const links = [
    { id: "dashboard", icon: LayoutDashboard, label: t("dashboard") },
    { id: "sites",     icon: Server,          label: t("sites") },
    { id: "map",       icon: Map,             label: t("networkMap") },
    { id: "traffic",   icon: Activity,        label: t("traffic") },
    { id: "system",    icon: Settings,        label: t("system") },
    { id: "users",     icon: Users,           label: t("users") },
  ]

  return (
    <nav className="sidebar">
      <div className="sidebar-logo">
        {brand.logoIcon && (
          typeof brand.logoIcon === "string" ? (
            <img src={brand.logoIcon} alt="logo" className="sidebar-logo-img"
              onError={e => { e.target.style.display = "none" }} />
          ) : (
            <span className="sidebar-logo-icon">{brand.logoIcon}</span>
          )
        )}
        <span className="sidebar-logo-text">{brand.name}</span>
      </div>
      
      {links.map((link) => {
        const Icon = link.icon
        const isActive = page === link.id || (page === "site" && link.id === "sites")
        return (
          <button
            key={link.id}
            className={`sidebar-link ${isActive ? "active" : ""}`}
            onClick={() => navigate(link.id)}
          >
            <span className="sidebar-icon">
              <Icon size={18} strokeWidth={1.5} />
            </span>
            {link.label}
          </button>
        )
      })}
      
      <button
        className="sidebar-link logout"
        onClick={() => {
          api.setToken("")
          window.location.reload()
        }}
      >
        <span className="sidebar-icon">
          <LogOut size={18} strokeWidth={1.5} />
        </span>
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

      {brand.copyright && (
        <div className="sidebar-copyright">{brand.copyright}</div>
      )}
    </nav>
  )
}

