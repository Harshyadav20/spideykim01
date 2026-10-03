import React from 'react'
import { fmtMMSS } from '../services/api.js'

const TAGS = {
  fire: '🔥', bulb: '💡', star: '⭐', target: '🎯', laugh: '😂', scissors: '✂️',
}

export default function ClipCard({ clip, active, rank, onSelect, onDelete }) {
  const dur = Math.round(clip.end - clip.start)
  return (
    <div
      className={`clip-card ${active ? 'active' : ''}`}
      role="button"
      tabIndex={0}
      aria-pressed={active}
      onClick={onSelect}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect())}
      title="Use this clip"
    >
      <div className="clip-row1">
        {rank != null && <span className="clip-rank">#{rank}</span>}
        <span className="clip-tag" aria-hidden="true">{TAGS[clip.tag] || '⭐'}</span>
        <span className="clip-title">{clip.title || 'Untitled clip'}</span>
        <span className="chip">{dur}s</span>
        {onDelete && (
          <button className="icon-btn danger" title="Delete this clip"
                  aria-label="Delete clip"
                  onClick={(e) => { e.stopPropagation(); onDelete() }}>🗑</button>
        )}
      </div>

      {clip.hook && <div className="clip-hook">“{clip.hook}”</div>}

      <div className="clip-row2">
        <span className="clip-time">{fmtMMSS(clip.start)} → {fmtMMSS(clip.end)}</span>
        {clip.source && (
          <span className={`src-chip ${clip.source}`}>
            {clip.source === 'ai' ? 'AI pick' : clip.source}
          </span>
        )}
      </div>

      {clip.reason && <div className="clip-reason">{clip.reason}</div>}

      {clip.score != null && (
        <div className="score" title={`Moment score ${Math.round(clip.score)}/100`}>
          <div className="score-fill" style={{ width: `${Math.max(4, Math.min(100, clip.score))}%` }} />
        </div>
      )}
    </div>
  )
}
