import React, { useCallback, useEffect, useState } from 'react'
import { api, fmtDur } from '../services/api.js'
import VideoUploader from '../components/VideoUploader.jsx'

const FEATURES = [
  { icon: '🧠', title: 'AI clip finder', text: 'Scores hooks, key moments and strong statements across the transcript.' },
  { icon: '💬', title: 'Animated captions', text: 'Word-level pop, highlight, bounce and kinetic text burned in.' },
  { icon: '📱', title: 'True 9:16 reframe', text: 'Center-crop, blur bars or fit, with punch-in zooms on beats.' },
  { icon: '🎵', title: 'Music & SFX', text: 'Royalty-free beds, whoosh and pop synced to the edit.' },
]

export default function Dashboard({ health, notify, onOpen }) {
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [sampleBusy, setSampleBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(() => {
    api.projects()
      .then(setProjects)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(refresh, [refresh])

  const useSample = async () => {
    setSampleBusy(true)
    setError('')
    try {
      const p = await api.useSample()
      notify('Sample project loaded — analysis starts automatically', 'success')
      onOpen(p.id)
    } catch (e) {
      setError(e.message)
    } finally {
      setSampleBusy(false)
    }
  }

  const del = async (p, ev) => {
    ev.stopPropagation()
    if (!window.confirm(`Delete "${p.name}" and all its renders?`)) return
    try {
      await api.deleteProject(p.id)
      notify('Project deleted', 'success')
      refresh()
    } catch (e) {
      notify(e.message, 'error')
    }
  }

  return (
    <main className="dashboard">
      <section className="hero">
        <div className="hero-copy">
          <span className="eyebrow">⚡ Local FFmpeg rendering · no video ever uploaded to a third party</span>
          <h1 className="hero-title">
            Turn long videos into <span className="grad-text">scroll-stopping shorts</span>.
          </h1>
          <p className="hero-sub">
            Upload a podcast, interview or lecture. Clipper AI transcribes it, finds the strongest
            moments and renders captioned 9:16 clips with zooms, animated text and music.
          </p>
        </div>

        <div className="hero-cols">
          <VideoUploader onUploaded={onOpen} maxUploadMb={health?.max_upload_mb} notify={notify} />

          <div className="hero-side">
            <div className="card sample-card">
              <h3>🎙️ No video handy?</h3>
              <p>Load the bundled demo — a 60-second creator podcast with real speech, pauses and hooks.</p>
              <button className="btn secondary" onClick={useSample} disabled={sampleBusy}>
                {sampleBusy ? 'Loading…' : 'Try the sample video'}
              </button>
            </div>

            <div className="card pipeline-card">
              <h3>How it works</h3>
              <ol className="pipeline">
                <li><b>Upload</b> your long video</li>
                <li><b>Vosk / Whisper</b> transcribes locally</li>
                <li><b>AI</b> scores the best moments</li>
                <li><b>FFmpeg</b> renders 9:16 with animated captions</li>
              </ol>
            </div>
          </div>
        </div>

        {error && <div className="error-banner">{error}</div>}
      </section>

      <section className="feature-strip" style={{ marginTop: 22 }}>
        {FEATURES.map((f) => (
          <article key={f.title} className="card feature">
            <span className="feature-ico" aria-hidden="true">{f.icon}</span>
            <div>
              <h4>{f.title}</h4>
              <p>{f.text}</p>
            </div>
          </article>
        ))}
      </section>

      <section className="projects">
        <div className="section-head">
          <h2>Your projects</h2>
          {!loading && projects.length > 0 && <span className="chip">{projects.length}</span>}
          <span className="spacer" />
          <button className="btn ghost tiny" onClick={refresh} aria-label="Refresh projects">⟳ Refresh</button>
        </div>

        {loading ? (
          <ProjectSkeletons />
        ) : projects.length === 0 ? (
          <div className="empty-state">
            <span className="es-icon" aria-hidden="true">🎬</span>
            <h3>No projects yet</h3>
            <p>Drop a video above — or try the sample — and your clips will show up here.</p>
          </div>
        ) : (
          <div className="project-grid">
            {projects.map((p) => (
              <article
                key={p.id}
                className="card project-card"
                role="button"
                tabIndex={0}
                onClick={() => onOpen(p.id)}
                onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onOpen(p.id))}
                aria-label={`Open ${p.name}`}
              >
                <div className="project-thumb">
                  {p.thumb_url
                    ? <img src={p.thumb_url} alt="" loading="lazy" />
                    : <div className="thumb-fallback" aria-hidden="true">🎞️</div>}
                  <span className="chip dur">{fmtDur(p.duration)}</span>
                  {p.status === 'analyzing' && <span className="chip analyzing">Analyzing…</span>}
                  {p.status === 'done' && <span className="chip done">Analyzed</span>}
                  {p.status === 'error' && <span className="chip" style={{ color: '#ffd9d9' }}>Failed</span>}
                </div>
                <div className="project-meta">
                  <div className="project-name">{p.name}</div>
                  <div className="project-sub">
                    {p.width}×{p.height} · {new Date(p.created_at).toLocaleDateString()}
                  </div>
                </div>
                <button className="icon-btn danger" title="Delete project"
                        aria-label={`Delete ${p.name}`}
                        onClick={(e) => del(p, e)}>🗑</button>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}

function ProjectSkeletons() {
  return (
    <div className="project-grid" aria-hidden="true">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="skeleton-card">
          <div className="skeleton sk-thumb" />
          <div className="sk-body">
            <div className="skeleton sk-line" style={{ width: '72%' }} />
            <div className="skeleton sk-line" style={{ width: '46%' }} />
          </div>
        </div>
      ))}
    </div>
  )
}
