import React, { useEffect, useRef, useState } from 'react'
import { api, fmtDur } from '../services/api.js'

export default function RenderPanel({ projectId, project, selected, options, set, notify,
                                      timeline, renderSignal }) {
  const [renders, setRenders] = useState([])
  const [job, setJob] = useState(null)          // active job status
  const pollRef = useRef(null)

  useEffect(() => {
    api.projectRenders(projectId).then(setRenders).catch(() => {})
  }, [projectId])

  useEffect(() => () => clearInterval(pollRef.current), [])

  // an AI command can say "render this" — bump renderSignal to trigger it
  const sigRef = useRef(0)
  useEffect(() => {
    if (renderSignal > sigRef.current) { sigRef.current = renderSignal; startRender() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderSignal])

  const startRender = async () => {
    if (!selected) return
    try {
      const r = await api.render(projectId, { ...selected }, options, timeline)
      notify?.('Render started — this stays on the server, you can keep editing', 'info')
      setJob({ id: r.id, status: 'queued', progress: 0, message: 'Queued' })
      clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        try {
          const s = await api.renderStatus(r.id)
          setJob(s)
          if (s.status === 'done' || s.status === 'error') {
            clearInterval(pollRef.current)
            api.projectRenders(projectId).then(setRenders).catch(() => {})
            if (s.status === 'done') notify?.('Render finished — preview and download below', 'success')
            else notify?.(s.message || 'Render failed', 'error')
          }
        } catch { /* keep polling */ }
      }, 900)
    } catch (e) {
      notify?.(e.message, 'error')
    }
  }

  const active = job && (job.status === 'queued' || job.status === 'running')
  const pct = Math.round((job?.progress || 0) * 100)

  return (
    <div className="panel card">
      <div className="panel-head">
        <h3>Export</h3>
        <span className="muted">
          9:16 · {options.resolution === '720' ? '720p fast' : '1080p'}
        </span>
      </div>

      <div className="render-row">
        <div className="seg res-seg">
          <button className={options.resolution === '1080' ? 'active' : ''}
                  aria-pressed={options.resolution === '1080'}
                  onClick={() => set({ resolution: '1080', fast: false })}>1080p</button>
          <button className={options.resolution === '720' ? 'active' : ''}
                  aria-pressed={options.resolution === '720'}
                  onClick={() => set({ resolution: '720', fast: true })}>720p fast</button>
        </div>

        <button className="btn primary wide" disabled={!selected || active} onClick={startRender}>
          {active
            ? `Rendering… ${pct}%`
            : selected
              ? `Render ${(selected.end - selected.start).toFixed(0)}s clip`
              : 'Select a clip first'}
        </button>
      </div>

      {job && (
        <div className={`render-job ${job.status}`} aria-live="polite">
          {active && (
            <div className="bar big">
              <div className="bar-fill" style={{ width: `${pct}%` }} />
            </div>
          )}
          <div className="render-msg">
            <span>
              {job.status === 'done' ? '✅ ' : job.status === 'error' ? '❌ ' : ''}
              {job.message}
            </span>
            {job.status === 'done' && job.url && (
              <>
                <video className="render-preview" src={job.url} controls playsInline />
                <div className="render-actions">
                  <a className="btn secondary" href={`/api/renders/${job.id}/file`} download>⬇ Download MP4</a>
                  <a className="btn ghost" href={job.url} target="_blank" rel="noreferrer">Open</a>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {renders.length > 0 && (
        <div className="render-history">
          <div className="muted">Recent renders</div>
          {renders.slice(0, 6).map((r) => (
            <div key={r.id} className="render-item">
              <span className={`status-dot ${r.status}`} title={r.status} />
              <span className="render-name">
                {r.clip?.title || 'clip'}
                {r.duration ? ` · ${fmtDur(r.duration)}` : ''}
                {r.width ? ` · ${r.width}×${r.height}` : ''}
              </span>
              {r.status === 'done' && r.url
                ? <a href={r.url} target="_blank" rel="noreferrer" className="chip link">open</a>
                : <span className="muted">{r.status}</span>}
            </div>
          ))}
        </div>
      )}

      <div className="panel-foot muted">
        Rendered locally with FFmpeg from {project?.width}×{project?.height} source.
      </div>
    </div>
  )
}
