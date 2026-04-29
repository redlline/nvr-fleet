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

// ── Branding (edit these to rebrand) ──────────────────────────────────────────
const BRAND = {
  name:      "NVR Fleet",          // Panel title in sidebar
  logoIcon:  "📹",                  // Emoji or text icon next to name
  copyright: "© 2026 NVR Fleet",   // Footer copyright
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

  const allowedPages = new Set(["dashboard", "sites", "map", "traffic", "system"])
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
      </main>
    </div>
  )
}

function Sidebar({ page, navigate, brand }) {
  const links = [
    { id: "dashboard", icon: "D", label: "Dashboard" },
    { id: "sites",     icon: "S", label: "Sites" },
    { id: "map",       icon: "M", label: "Network Map" },
    { id: "traffic",   icon: "T", label: "Traffic" },
    { id: "system",    icon: "C", label: "System" },
  ]

  return (
    <nav className="sidebar">
      <div className="sidebar-logo">
        {brand.logoIcon && <span className="sidebar-logo-icon">{brand.logoIcon}</span>}
        {brand.name}
      </div>
      {links.map((link) => (
        <button
          key={link.id}
          className={`sidebar-link ${page === link.id || (page === "site" && link.id === "sites") ? "active" : ""}`}
          onClick={() => navigate(link.id)}
        >
          <span className="sidebar-icon">{link.icon}</span>
          {link.label}
        </button>
      ))}
      <button
        className="sidebar-link logout"
        onClick={() => {
          api.setToken("")
          window.location.reload()
        }}
      >
        <span className="sidebar-icon">X</span>
        Logout
      </button>
      {brand.copyright && (
        <div className="sidebar-copyright">{brand.copyright}</div>
      )}
    </nav>
  )
}
