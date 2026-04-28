import { useState } from "react"
import { api } from "./lib/api"
import Dashboard from "./pages/Dashboard"
import Sites from "./pages/Sites"
import SiteDetail from "./pages/SiteDetail"
import NetworkMap from "./pages/NetworkMap"
import Traffic from "./pages/Traffic"
import System from "./pages/System"
import Login from "./pages/Login"

export default function App() {
  const [authed, setAuthed] = useState(!!api.getToken())
  const [page, setPage] = useState("dashboard")
  const [selectedSite, setSelectedSite] = useState(null)

  if (!authed) return <Login onLogin={() => setAuthed(true)} />

  function navigate(nextPage, site = null) {
    setPage(nextPage)
    if (site) setSelectedSite(site)
  }

  return (
    <div className="app">
      <Sidebar page={page} navigate={navigate} />
      <main className="content">
        {page === "dashboard" && <Dashboard navigate={navigate} />}
        {page === "sites" && <Sites navigate={navigate} />}
        {page === "site" && selectedSite && <SiteDetail siteId={selectedSite} navigate={navigate} />}
        {page === "map" && <NetworkMap navigate={navigate} />}
        {page === "traffic" && <Traffic />}
        {page === "system" && <System />}
      </main>
    </div>
  )
}

function Sidebar({ page, navigate }) {
  const links = [
    { id: "dashboard", icon: "D", label: "Dashboard" },
    { id: "sites", icon: "S", label: "Sites" },
    { id: "map", icon: "M", label: "Network Map" },
    { id: "traffic", icon: "T", label: "Traffic" },
    { id: "system", icon: "C", label: "System" },
  ]

  return (
    <nav className="sidebar">
      <div className="sidebar-logo">NVR Fleet</div>
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
    </nav>
  )
}
