import React, { useRef, useState } from 'react'
import { fmtMMSS } from '../services/api.js'

/**
 * Speech / silence map with a draggable clip window.
 * Silence = dark gaps, speech = violet base, selected clip = green window,
 * white playhead follows the preview player.
 */
export default function Timeline({ project, analysis, selected, time = 0, onSelect, onSeek }) {
  const trackRef = useRef(null)
  const dragRef = useRef(null) // {mode: 'start'|'end'|'seek'}
  const [hover, setHover] = useState(null)
  const duration = project.duration || 1
  const silences = analysis?.silences || []

  const pct = (t) => `${(t / duration) * 100}%`

  const timeAt = (clientX) => {
    const rect = trackRef.current?.getBoundingClientRect()
    if (!rect) return 0
    return Math.max(0, Math.min(duration, ((clientX - rect.left) / rect.width) * duration))
  }

  const startDrag = (mode) => (ev) => {
    ev.preventDefault()
    ev.stopPropagation()
    if (mode !== 'seek' && !selected) return
    dragRef.current = { mode }
    const move = (e) => {
      const d = dragRef.current
      if (!d) return
      const t = timeAt(e.clientX)
      setHover(t)
      if (d.mode === 'start') onSelect({ ...selected, start: Math.min(t, selected.end - 3) })
      else if (d.mode === 'end') onSelect({ ...selected, end: Math.max(t, selected.start + 3) })
      else onSeek(t)
    }
    const up = () => {
      dragRef.current = null
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  return (
    <div className="timeline">
      <div className="timeline-labels muted">
        <span>0:00</span>
        <span>speech / silence map · drag the green window to trim</span>
        <span>{fmtMMSS(duration)}</span>
      </div>

      <div
        className="timeline-track"
        ref={trackRef}
        onMouseDown={startDrag('seek')}
        onMouseMove={(e) => setHover(timeAt(e.clientX))}
        onMouseLeave={() => setHover(null)}
        title="Click to seek, drag the window to trim the clip"
      >
        <div className="tl-speech" />

        {silences.map((s, i) => (
          <div key={i} className="tl-silence"
               style={{ left: pct(s.start), width: pct(s.end - s.start) }}
               title={`silence ${s.dur.toFixed(1)}s`} />
        ))}

        {selected && (
          <div className="tl-clip"
               style={{ left: pct(selected.start), width: pct(selected.end - selected.start) }}
               onMouseDown={startDrag('seek')}>
            <div className="tl-handle left" onMouseDown={startDrag('start')}
                 role="slider" aria-label="Clip start" aria-valuenow={Math.round(selected.start)} />
            <div className="tl-handle right" onMouseDown={startDrag('end')}
                 role="slider" aria-label="Clip end" aria-valuenow={Math.round(selected.end)} />
          </div>
        )}

        <div className="tl-playhead" style={{ left: pct(Math.min(time, duration)) }} />
        {hover != null && (
          <div className="tl-tooltip" style={{ left: pct(hover) }}>{fmtMMSS(hover)}</div>
        )}
      </div>
    </div>
  )
}
