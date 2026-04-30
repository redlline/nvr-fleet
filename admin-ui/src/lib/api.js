const BASE = import.meta.env.VITE_API_URL || ""
const TOKEN = () => localStorage.getItem("admin_token") || ""

function authHeaders(extra = {}) {
  return {
    Authorization: `Bearer ${TOKEN()}`,
    ...extra,
  }
}

async function parseError(res) {
  const err = await res.json().catch(() => ({ detail: res.statusText }))
  throw new Error(err.detail || res.statusText)
}

async function req(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: authHeaders({
      "Content-Type": "application/json",
    }),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) await parseError(res)
  if (res.status === 204) return null
  return res.json()
}

async function uploadFile(path, file) {
  const form = new FormData()
  form.append("file", file)
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  })
  if (!res.ok) await parseError(res)
  return res.json()
}

async function downloadFile(path) {
  const res = await fetch(`${BASE}${path}`, {
    method: "GET",
    headers: authHeaders(),
  })
  if (!res.ok) await parseError(res)
  return res.blob()
}

export const api = {
  setToken: (token) => localStorage.setItem("admin_token", token),
  getToken: TOKEN,

  dashboard: () => req("GET", "/api/dashboard"),

  listSites: () => req("GET", "/api/sites"),
  getSite: (id) => req("GET", `/api/sites/${id}`),
  createSite: (data) => req("POST", "/api/sites", data),
  updateSite: (id, data) => req("PUT", `/api/sites/${id}`, data),
  deleteSite: (id) => req("DELETE", `/api/sites/${id}`),

  listCameras: (siteId) => req("GET", `/api/sites/${siteId}/cameras`),
  addCamera: (siteId, data) => req("POST", `/api/sites/${siteId}/cameras`, data),
  updateCamera: (siteId, camId, data) => req("PUT", `/api/sites/${siteId}/cameras/${camId}`, data),
  deleteCamera: (siteId, camId) => req("DELETE", `/api/sites/${siteId}/cameras/${camId}`),
  bulkCameras: (siteId, cameras) => req("POST", `/api/sites/${siteId}/cameras/bulk`, cameras),

  getStreams: (siteId) => req("GET", `/api/sites/${siteId}/streams`),
  getTraffic: (siteId, hours = 1) => req("GET", `/api/sites/${siteId}/traffic?hours=${hours}`),
  getTotalTraffic: (hours = 24) => req("GET", `/api/traffic/total?hours=${hours}`),
  listArchive: (siteId, params = {}) => {
    const query = new URLSearchParams()
    if (params.camera_id) query.set("camera_id", params.camera_id)
    if (params.start) query.set("start", params.start)
    if (params.end) query.set("end", params.end)
    if (params.limit) query.set("limit", params.limit)
    const suffix = query.toString() ? `?${query}` : ""
    return req("GET", `/api/sites/${siteId}/archive${suffix}`)
  },
  startArchivePlayback: (siteId, data) => req("POST", `/api/sites/${siteId}/archive/playback`, data),
  stopArchivePlayback: (siteId, sessionId) => req("DELETE", `/api/sites/${siteId}/archive/playback/${sessionId}`),

  deploySite: (siteId) => req("POST", `/api/sites/${siteId}/deploy`),
  restartAgent: (siteId) => req("POST", `/api/sites/${siteId}/restart`),
  drainRedeploySite: (siteId) => req("POST", `/api/sites/${siteId}/drain-redeploy`),

  getTlsStatus: () => req("GET", "/api/system/tls"),
  updateTls: (data) => req("PUT", "/api/system/tls", data),
  deleteTls: () => req("DELETE", "/api/system/tls"),
  getStackStatus: () => req("GET", "/api/system/stack"),
  getStackLogs: (service, tail = 200) => req("GET", `/api/system/stack/logs?service=${encodeURIComponent(service)}&tail=${tail}`),
  restartStack: (data) => req("POST", "/api/system/stack/restart", data),
  exportBackup: () => downloadFile("/api/system/backup/export"),
  listRotatedBackups: () => req("GET", "/api/system/backup/list"),
  rotateBackup: (data = {}) => req("POST", "/api/system/backup/rotate", data),
  downloadRotatedBackup: (filename) => downloadFile(`/api/system/backup/files/${encodeURIComponent(filename)}`),
  importBackup: (file) => uploadFile("/api/system/backup/import", file),

  mapData: () => req("GET", "/api/map"),
  getTrafficMtx: (siteId, hours = 1) => req("GET", `/api/sites/${siteId}/traffic/mtx?hours=${hours}`),
  getTotalTrafficMtx: (hours = 24) => req("GET", `/api/traffic/total/mtx?hours=${hours}`),
  getRealtimeTraffic: () => req("GET", "/api/traffic/realtime"),

  // Users
  listUsers:    ()            => req("GET",    "/api/users"),
  createUser:   (data)        => req("POST",   "/api/users", data),
  updateUser:   (id, data)    => req("PUT",    `/api/users/${id}`, data),
  deleteUser:   (id)          => req("DELETE", `/api/users/${id}`),
  getMe:        ()            => req("GET",    "/api/auth/me"),

  // Login with username+password
  loginWithCredentials: (username, password) =>
    req("POST", "/api/auth/login", { username, password }),
}
