import React, { useRef, useState } from 'react'
import { api } from '../services/api.js'

const EXAMPLES = [
  'Add a hook saying "WAIT FOR IT" for the first 3 seconds',
  'Glitch effect only on the second half, no captions',
  'Chill music for the first half and use the funniest part',
  'Make this a 30 second viral reel',
]

export default function CommandBox({ projectId, onResult, disabled, total }) {
  // the timeline length changes as you edit — keep it in a ref so the
  // request always carries the current output duration
  const totalRef = useRef(total)
  totalRef.current = total
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  const run = async (cmd) => {
    if (!cmd?.trim() || busy) return
    setBusy(true)
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const res = await api.intent(projectId, cmd, totalRef.current)
        onResult(res)
        setText('')
        break
      } catch (e) {
        // transient failure (server restarted mid-click) — retry once
        const transient = /fetch|network|load failed/i.test(e.message || '')
        if (attempt === 0 && transient) {
          await new Promise((r) => setTimeout(r, 1200))
          continue
        }
        onResult({ message: e.message, patch: {}, error: true })
        break
      }
    }
    setBusy(false)
  }

  return (
    <div className="command-box">
      <div className="command-title">
        <span aria-hidden="true">✨</span> AI director
        <span className="muted">describe the edit in plain English</span>
      </div>
      <div className="command-row">
        <input
          className="text-input"
          placeholder='e.g. "add a hook saying ‘wait for it’ for the first 3 seconds"'
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && run(text)}
          disabled={disabled}
          aria-label="AI edit command"
        />
        <button className="btn primary" onClick={() => run(text)} disabled={disabled || busy || !text.trim()}>
          {busy ? '…' : 'Apply'}
        </button>
      </div>
      <div className="command-examples">
        {EXAMPLES.map((ex) => (
          <button key={ex} className="example-chip" disabled={disabled || busy}
                  onClick={() => { setText(ex); run(ex) }}>{ex}</button>
        ))}
      </div>
    </div>
  )
}
