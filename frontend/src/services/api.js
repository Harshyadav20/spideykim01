const BASE = ''

async function j(res) {
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* non-JSON error body */ }
    throw new Error(detail || `Request failed (${res.status})`)
  }
  return res.json()
}

const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  health: () => fetch(`${BASE}/api/health`).then(j),
  projects: () => fetch(`${BASE}/api/projects`).then(j),
  project: (id) => fetch(`${BASE}/api/projects/${id}`).then(j),
  deleteProject: (id) => fetch(`${BASE}/api/projects/${id}`, { method: 'DELETE' }).then(j),
  useSample: () => fetch(`${BASE}/api/sample`, { method: 'POST' }).then(j),
  analyze: (id, targets) => fetch(`${BASE}/api/projects/${id}/analyze`, json({ targets })).then(j),
  analysis: (id) => fetch(`${BASE}/api/projects/${id}/analysis`).then(j),
  addClip: (id, clip) => fetch(`${BASE}/api/projects/${id}/clips`, json(clip)).then(j),
  deleteClip: (id, cid) => fetch(`${BASE}/api/projects/${id}/clips/${cid}`, { method: 'DELETE' }).then(j),
  styles: () => fetch(`${BASE}/api/styles`).then(j),
  assets: () => fetch(`${BASE}/api/assets`).then(j),
  packs: () => fetch(`${BASE}/api/packs`).then(j),
  packBuildState: () => fetch(`${BASE}/api/packs/build`).then(j),
  buildVfxPack: (force = false) =>
    fetch(`${BASE}/api/packs/build?force=${force ? 'true' : 'false'}`, { method: 'POST' }).then(j),
  refreshAssets: () => fetch(`${BASE}/api/packs/refresh`, { method: 'POST' }).then(j),
  generateStyle: (prompt) => fetch(`${BASE}/api/styles/generate`, json({ prompt })).then(j),
  render: (id, clip, options, timeline) =>
    fetch(`${BASE}/api/projects/${id}/render`, json({ clip, options, timeline })).then(j),
  renderStatus: (rid) => fetch(`${BASE}/api/renders/${rid}`).then(j),
  projectRenders: (id) => fetch(`${BASE}/api/projects/${id}/renders`).then(j),
  command: (id, text) => fetch(`${BASE}/api/projects/${id}/command`, json({ text })).then(j),
  intent: (id, text, total) =>
    fetch(`${BASE}/api/projects/${id}/intent`, json({ text, total })).then(j),
  captionPreview: (id, body) => fetch(`${BASE}/api/projects/${id}/captions/preview`, json(body)).then(j),
}

const CHUNK = 8 * 1024 * 1024        // 8 MB slices — passes any proxy body limit

// Chunked upload: proxied/hosted deployments can reject large single-request
// bodies, so the file goes up in slices (each retried on transient failures).
export async function uploadFile(file, onProgress) {
  const nChunks = Math.max(1, Math.ceil(file.size / CHUNK))
  const uploadId = 'up' + Math.random().toString(36).slice(2, 12)
  for (let i = 0; i < nChunks; i++) {
    const blob = file.slice(i * CHUNK, Math.min(file.size, (i + 1) * CHUNK))
    let done = false
    let lastError = null
    for (let attempt = 0; attempt < 3 && !done; attempt++) {
      try {
        const r = await fetch(`${BASE}/api/upload/chunk?upload_id=${uploadId}&index=${i}`,
          { method: 'POST', body: blob, headers: { 'Content-Type': 'application/octet-stream' } })
        if (!r.ok) {
          const d = await r.json().catch(() => ({}))
          throw new Error(d.detail || `Upload failed (${r.status})`)
        }
        done = true
      } catch (e) {
        lastError = e
        if (!/fetch|network|load failed/i.test(e.message || '')) throw e   // real error — don't retry
        await new Promise((res) => setTimeout(res, 1200 * (attempt + 1)))
      }
    }
    if (!done) throw new Error(lastError?.message || 'Network error during upload — try again')
    onProgress?.((i + 1) / nChunks)
  }
  const r = await fetch(`${BASE}/api/upload/complete`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ upload_id: uploadId, filename: file.name, size: file.size }),
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || `Upload failed (${r.status})`)
  return data
}

export const fmtMMSS = (s) => {
  s = Math.max(0, Math.round(s || 0))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export const fmtDur = (s) => {
  s = Math.max(0, Math.round(s || 0))
  const m = Math.floor(s / 60)
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`
}

export const fmtBytes = (b) => {
  if (!b) return '—'
  const u = ['B', 'KB', 'MB', 'GB']
  const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024)))
  return `${(b / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${u[i]}`
}
