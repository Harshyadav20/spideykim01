import React, { useCallback, useEffect, useRef, useState } from 'react'
import { fmtMMSS } from '../services/api.js'

/**
 * Preview of the ORIGINAL video with a 9:16 crop guide overlay so the user
 * sees exactly what the vertical render will keep.
 *
 * Keyboard: space = play/pause, ←/→ = ∓1s (shift: ∓5s), I / O = set clip
 * in / out point at the playhead.
 */
export default function VideoPlayer({
  project, selected, seekTo, onSeekHandled, aspect = 'crop', cropX = 0.5,
  onTimeUpdate, onSetStart, onSetEnd, onNudgeEnd,
}) {
  const ref = useRef(null)
  const boxRef = useRef(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const stopAtRef = useRef(null)

  const report = useCallback((t) => {
    setTime(t)
    onTimeUpdate?.(t)
  }, [onTimeUpdate])

  useEffect(() => {
    if (seekTo == null || !ref.current) return
    ref.current.currentTime = seekTo
    report(seekTo)
    onSeekHandled?.()
  }, [seekTo, onSeekHandled, report])

  const toggle = useCallback(() => {
    const v = ref.current
    if (!v) return
    if (v.paused) {
      stopAtRef.current = null
      v.play()
      setPlaying(true)
    } else {
      v.pause()
      setPlaying(false)
    }
  }, [])

  const playClip = useCallback(() => {
    const v = ref.current
    if (!v || !selected) return
    if (v.currentTime < selected.start || v.currentTime >= selected.end - 0.05) {
      v.currentTime = selected.start
    }
    stopAtRef.current = selected.end
    v.play()
    setPlaying(true)
  }, [selected])

  const seekToRatio = useCallback((ratio) => {
    const v = ref.current
    if (!v) return
    const t = Math.max(0, Math.min(project.duration || 0, ratio * (project.duration || 0)))
    v.currentTime = t
    report(t)
  }, [project.duration, report])

  const scrubAt = (clientX) => {
    const el = boxRef.current?.querySelector('.scrub')
    if (!el) return
    const rect = el.getBoundingClientRect()
    seekToRatio((clientX - rect.left) / rect.width)
  }

  // ---------------------------------------------------------- shortcuts
  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target?.tagName || '').toLowerCase()
      if (['input', 'textarea', 'select'].includes(tag) || e.target?.isContentEditable) return
      const v = ref.current
      if (!v) return
      const step = e.shiftKey ? 5 : 1
      if (e.code === 'Space') { e.preventDefault(); toggle() }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); v.currentTime = Math.max(0, v.currentTime - step) }
      else if (e.key === 'ArrowRight') { e.preventDefault(); v.currentTime = Math.min(project.duration || 1e9, v.currentTime + step) }
      else if (e.key === 'i' || e.key === 'I') { e.preventDefault(); onSetStart?.() }
      else if (e.key === 'o' || e.key === 'O') { e.preventDefault(); onSetEnd?.() }
      else if (e.key === '[') { e.preventDefault(); onNudgeEnd?.(-0.5) }
      else if (e.key === ']') { e.preventDefault(); onNudgeEnd?.(0.5) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggle, project.duration, onSetStart, onSetEnd, onNudgeEnd])

  const pct = project.duration ? Math.min(100, (time / project.duration) * 100) : 0
  const clipPct = (t) => (project.duration ? Math.min(100, (t / project.duration) * 100) : 0)
  const showGuide = aspect === 'crop'

  return (
    <div className="player-wrap">
      <div className="player-box" ref={boxRef}>
        <video
          ref={ref}
          src={project.url}
          playsInline
          preload="metadata"
          onTimeUpdate={(e) => {
            const t = e.currentTarget.currentTime
            report(t)
            if (stopAtRef.current != null && t >= stopAtRef.current) {
              e.currentTarget.pause()
              setPlaying(false)
              stopAtRef.current = null
            }
          }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onClick={toggle}
        />

        {showGuide && (
          <div className="crop-guide" style={{ left: `${Math.max(0, Math.min(1, cropX)) * 100}%` }}>
            <span className="crop-label">9:16 crop</span>
          </div>
        )}

        <div className="player-hud">
          <div className="hud-row">
            <button className="play-btn" onClick={selected ? playClip : toggle}
                    aria-label={playing ? 'Pause' : 'Play'}>
              {playing ? '⏸' : '▶'}
            </button>
            <span className="time">{fmtMMSS(time)} / {fmtMMSS(project.duration)}</span>
            {selected && (
              <>
                <span className="chip">clip {fmtMMSS(selected.start)}–{fmtMMSS(selected.end)}</span>
                <button className="btn tiny secondary" onClick={onSetStart} title="Set clip start to playhead (I)">⇤ In</button>
                <button className="btn tiny secondary" onClick={onSetEnd} title="Set clip end to playhead (O)">Out ⇥</button>
              </>
            )}
            <span className="spacer" />
            <button className="btn tiny ghost" onClick={toggle} title="Play / pause (space)">
              {playing ? 'Pause' : 'Play'}
            </button>
          </div>

          <div className="scrub" role="slider" aria-label="Playhead"
               aria-valuemin={0} aria-valuemax={Math.round(project.duration || 0)}
               aria-valuenow={Math.round(time)} tabIndex={0}
               onMouseDown={(e) => {
                 scrubAt(e.clientX)
                 const move = (ev) => scrubAt(ev.clientX)
                 const up = () => {
                   window.removeEventListener('mousemove', move)
                   window.removeEventListener('mouseup', up)
                 }
                 window.addEventListener('mousemove', move)
                 window.addEventListener('mouseup', up)
               }}>
            <div className="scrub-track">
              <div className="scrub-fill" style={{ width: `${pct}%` }} />
              {selected && (
                <div className="scrub-clip"
                     style={{ left: `${clipPct(selected.start)}%`, width: `${clipPct(selected.end - selected.start)}%` }} />
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="player-caption-hint muted">
        Preview shows the original {project.width}×{project.height} — the dashed guide marks the
        9:16 crop window. Captions, zoom and music are burned in when you render.
      </div>
      <div className="kbd-hints">
        <span><kbd>space</kbd>play / pause</span>
        <span><kbd>←</kbd><kbd>→</kbd>seek 1s (<kbd>shift</kbd> 5s)</span>
        <span><kbd>I</kbd><kbd>O</kbd>set clip in / out</span>
        <span><kbd>[</kbd><kbd>]</kbd>trim end</span>
      </div>
    </div>
  )
}
