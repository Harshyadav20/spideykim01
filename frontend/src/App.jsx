import React, { useCallback, useEffect, useState } from 'react'
import Dashboard from './pages/Dashboard.jsx'
import Editor from './pages/Editor.jsx'
import { ToastStack, useToasts } from './components/Toasts.jsx'
import { api } from './services/api.js'

export default function App() {
  const [view, setView] = useState({ name: 'dashboard' })
  const [health, setHealth] = useState(null)
  const [healthFailed, setHealthFailed] = useState(false)
  const [noticeOpen, setNoticeOpen] = useState(true)
  const { toasts, notify, dismiss } = useToasts()

  const loadHealth = useCallback(() => {
    api.health()
      .then((h) => { setHealth(h); setHealthFailed(false) })
      .catch(() => setHealthFailed(true))
  }, [])

  useEffect(loadHealth, [loadHealth])

  const showNotice = noticeOpen && health?.ephemeral_storage

  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={() => setView({ name: 'dashboard' })}
                aria-label="Clipper AI home">
          <BrandMark />
          <span className="brand-text">
            <span className="brand-name">Clipper <span className="ai">AI</span></span>
            <span className="brand-sub">AI Video Clipper &amp; Editor</span>
          </span>
        </button>

        <div className="topbar-right">
          {healthFailed && (
            <span className="badge bad" title="The API did not answer /api/health">
              API offline
            </span>
          )}
          {health && (
            <div className="engine-badges">
              <span className={`badge ${health.ffmpeg ? 'ok' : 'bad'}`}
                    title={health.ffmpeg ? `FFmpeg: ${health.ffmpeg_path}` : 'FFmpeg binary not found — rendering is disabled'}>
                FFmpeg {health.ffmpeg ? 'ready' : 'missing'}
              </span>
              <span className={`badge ${health.stt_engine === 'none' ? 'warn' : 'ok'}`}
                    title="Speech-to-text engine used for transcripts and captions">
                STT · {health.stt_engine}
              </span>
              <span className="badge ok" title="Clip-finding engine">
                AI · {health.ai_engine}
              </span>
              <span className="badge" title={`App version ${health.version}`}>
                v{health.version}
              </span>
            </div>
          )}
        </div>
      </header>

      {showNotice && (
        <div className="notice-bar" role="note">
          <span aria-hidden="true">💾</span>
          <span>
            Demo storage: uploads and renders are cleared when this hosting instance
            restarts. Download the clips you want to keep.
          </span>
          <button onClick={() => setNoticeOpen(false)} aria-label="Dismiss storage notice">✕</button>
        </div>
      )}

      {view.name === 'dashboard' ? (
        <Dashboard
          health={health}
          notify={notify}
          onOpen={(id) => setView({ name: 'editor', id })}
        />
      ) : (
        <Editor
          id={view.id}
          notify={notify}
          onBack={() => setView({ name: 'dashboard' })}
        />
      )}

      <ToastStack toasts={toasts} onDismiss={dismiss} />
    </div>
  )
}

function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 64 64" role="img" aria-label="Clipper AI">
      <defs>
        <linearGradient id="clipper-brand" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6d5cff" />
          <stop offset="0.55" stopColor="#a855f7" />
          <stop offset="1" stopColor="#ff5cae" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill="url(#clipper-brand)" />
      <path
        d="M27 19.2v25.6c0 1.6 1.8 2.6 3.2 1.7l19-12.7c1.3-.9 1.3-2.6 0-3.4l-19-12.7c-1.4-1-3.2 0-3.2 1.6Z"
        fill="#fff"
      />
      <g stroke="#fff" strokeOpacity="0.9" strokeWidth="3.4" strokeLinecap="round">
        <path d="M14.5 22.5h6.5" />
        <path d="M14.5 32h6.5" />
        <path d="M14.5 41.5h6.5" />
      </g>
    </svg>
  )
}
