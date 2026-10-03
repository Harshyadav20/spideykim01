import React, { useState } from 'react'
import { api } from '../services/api.js'

const IDEAS = [
  'MrBeast style but pink and blue',
  'dreamy sunset aesthetic, soft and slow',
  'cyberpunk glitch with neon purple',
  'talking head storytime, clean and calm',
]

/** AI style generator — describe a trend, get a renderable preset. */
export default function StyleGen({ onGenerated }) {
  const [open, setOpen] = useState(false)
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = async () => {
    if (!prompt.trim() || busy) return
    setBusy(true)
    setError('')
    try {
      const style = await api.generateStyle(prompt.trim())
      onGenerated?.(style)
      setOpen(false)
      setPrompt('')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stylegen">
      {!open ? (
        <button className="btn tiny secondary stylegen-btn" onClick={() => setOpen(true)}>
          ✨ Generate a trending style
        </button>
      ) : (
        <div className="stylegen-box">
          <div className="command-title">Describe the trend</div>
          <div className="command-row">
            <input
              className="text-input"
              autoFocus
              placeholder='e.g. "MrBeast style but pink"'
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && run()}
            />
            <button className="btn primary" onClick={run} disabled={busy}>
              {busy ? '…' : 'Create'}
            </button>
            <button className="btn ghost" onClick={() => setOpen(false)}>✕</button>
          </div>
          <div className="command-examples">
            {IDEAS.map((ex) => (
              <button key={ex} className="example-chip"
                      onClick={() => setPrompt(ex)}>{ex}</button>
            ))}
          </div>
          {error && <div className="error-banner small">{error}</div>}
          <div className="muted" style={{ marginTop: 6 }}>
            Remixes the built-in trend recipes locally — nothing leaves your machine.
          </div>
        </div>
      )}
    </div>
  )
}
