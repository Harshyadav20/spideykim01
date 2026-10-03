import React, { useMemo, useState } from 'react'
import { fmtMMSS } from '../services/api.js'

export default function TranscriptPanel({ analysis, selected, onSeek }) {
  const [query, setQuery] = useState('')
  const segs = analysis.transcript || []

  const highlighted = useMemo(() => {
    if (!selected) return new Set()
    return new Set(
      segs.filter((s) => s.end > selected.start && s.start < selected.end).map((s) => s.start))
  }, [segs, selected])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? segs.filter((s) => s.text.toLowerCase().includes(q)) : segs
  }, [segs, query])

  return (
    <section className="panel card transcript">
      <div className="panel-head">
        <h3>Transcript</h3>
        <span className="muted">{segs.length} segments · click any line to seek</span>
        <input
          className="text-input"
          style={{ maxWidth: 220 }}
          placeholder="Search words…"
          value={query}
          aria-label="Search transcript"
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="transcript-body">
        {shown.length === 0 && <div className="muted">No segment matches “{query}”.</div>}
        {shown.map((s, i) => (
          <span
            key={`${s.start}-${i}`}
            className={`tseg ${highlighted.has(s.start) ? 'inclip' : ''}`}
            role="button"
            tabIndex={0}
            onClick={() => onSeek(s.start)}
            onKeyDown={(e) => e.key === 'Enter' && onSeek(s.start)}
          >
            <span className="tseg-time">{fmtMMSS(s.start)}</span> {s.text}
          </span>
        ))}
      </div>
    </section>
  )
}
